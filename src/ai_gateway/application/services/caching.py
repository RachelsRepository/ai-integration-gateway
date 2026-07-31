"""Response and embedding caches.

Caches are always tenant-scoped: the cache key includes the tenant identifier so that one
customer can never observe another customer's completion. Only deterministic requests are
cached, and tool-calling responses are never cached because tool arguments are
side-effecting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ai_gateway.application.ports.cache import Cache
from ai_gateway.application.ports.llm_provider import ProviderChatRequest
from ai_gateway.application.ports.metrics import MetricsRecorder
from ai_gateway.domain.entities.message import FinishReason, Message, MessageRole
from ai_gateway.domain.value_objects.identifiers import TenantId
from ai_gateway.domain.value_objects.model import ModelRef
from ai_gateway.domain.value_objects.tokens import TokenUsage

CACHE_NAMESPACE = "aigw"
_CACHEABLE_TEMPERATURE = 0.0


@dataclass(frozen=True, slots=True)
class CachedCompletion:
    """A completion stored in the response cache.

    Attributes:
        content: Assistant text.
        model: Qualified model reference that produced it.
        usage: Token counters from the original call.
        finish_reason: Normalised stop reason.
    """

    content: str
    model: str
    usage: TokenUsage
    finish_reason: FinishReason = FinishReason.STOP

    def to_message(self) -> Message:
        """Return the cached completion as an assistant message."""
        return Message(role=MessageRole.ASSISTANT, content=self.content)


def _digest(payload: dict[str, Any]) -> str:
    """Return a stable SHA-256 digest of a JSON-serialisable payload.

    Args:
        payload: Structure to hash.

    Returns:
        The hex digest.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ResponseCache:
    """Caches deterministic chat completions per tenant."""

    def __init__(self, cache: Cache, *, metrics: MetricsRecorder, ttl_seconds: int = 300) -> None:
        """Initialise the cache.

        Args:
            cache: Backing key/value store.
            metrics: Metrics sink for hit/miss counters.
            ttl_seconds: Entry lifetime.
        """
        self._cache = cache
        self._metrics = metrics
        self._ttl = ttl_seconds

    async def ping(self) -> bool:
        """Report whether the backing cache is reachable."""
        return await self._cache.ping()

    @staticmethod
    def is_cacheable(request: ProviderChatRequest) -> bool:
        """Report whether a request may be served from cache.

        Args:
            request: Normalised chat request.

        Returns:
            ``True`` when the request is deterministic and side-effect free.
        """
        if request.tools:
            return False
        return request.temperature <= _CACHEABLE_TEMPERATURE or request.seed is not None

    def key(self, tenant_id: TenantId, request: ProviderChatRequest) -> str:
        """Build the tenant-scoped cache key for a request.

        Args:
            tenant_id: Owning tenant.
            request: Normalised chat request.

        Returns:
            The cache key.
        """
        payload = {
            "model": request.model.qualified,
            "messages": [
                {"role": m.role.value, "content": m.content, "name": m.name}
                for m in request.messages
            ],
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stop": list(request.stop),
            "response_format": request.response_format,
            "seed": request.seed,
        }
        return f"{CACHE_NAMESPACE}:resp:{tenant_id}:{_digest(payload)}"

    async def get(
        self, tenant_id: TenantId, request: ProviderChatRequest
    ) -> CachedCompletion | None:
        """Look up a cached completion.

        Args:
            tenant_id: Owning tenant.
            request: Normalised chat request.

        Returns:
            The cached completion, or ``None`` on a miss.
        """
        raw = await self._cache.get(self.key(tenant_id, request))
        if raw is None:
            self._metrics.increment("gateway_cache_events_total", labels={"result": "miss"})
            return None
        try:
            payload = json.loads(raw)
            cached = CachedCompletion(
                content=payload["content"],
                model=payload["model"],
                usage=TokenUsage(**payload["usage"]),
                finish_reason=FinishReason(payload.get("finish_reason", "stop")),
            )
        except (ValueError, KeyError, TypeError):
            await self._cache.delete(self.key(tenant_id, request))
            self._metrics.increment("gateway_cache_events_total", labels={"result": "corrupt"})
            return None
        self._metrics.increment("gateway_cache_events_total", labels={"result": "hit"})
        return cached

    async def set(
        self,
        tenant_id: TenantId,
        request: ProviderChatRequest,
        *,
        content: str,
        model: ModelRef,
        usage: TokenUsage,
        finish_reason: FinishReason = FinishReason.STOP,
    ) -> None:
        """Store a completion.

        Args:
            tenant_id: Owning tenant.
            request: Normalised chat request.
            content: Assistant text to cache.
            model: Model that produced the completion.
            usage: Token counters.
            finish_reason: Normalised stop reason.
        """
        payload = {
            "content": content,
            "model": model.qualified,
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cached_prompt_tokens": usage.cached_prompt_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
            },
            "finish_reason": finish_reason.value,
        }
        await self._cache.set(
            self.key(tenant_id, request),
            json.dumps(payload).encode("utf-8"),
            ttl_seconds=self._ttl,
        )


class EmbeddingCache:
    """Caches embedding vectors per tenant, model and input text."""

    def __init__(
        self, cache: Cache, *, metrics: MetricsRecorder, ttl_seconds: int = 86_400
    ) -> None:
        """Initialise the cache.

        Args:
            cache: Backing key/value store.
            metrics: Metrics sink for hit/miss counters.
            ttl_seconds: Entry lifetime.
        """
        self._cache = cache
        self._metrics = metrics
        self._ttl = ttl_seconds

    def key(self, tenant_id: TenantId, model: ModelRef, text: str) -> str:
        """Build the cache key for one embedding input.

        Args:
            tenant_id: Owning tenant.
            model: Embedding model.
            text: Input text.

        Returns:
            The cache key.
        """
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"{CACHE_NAMESPACE}:emb:{tenant_id}:{model.qualified}:{digest}"

    async def get_many(
        self, tenant_id: TenantId, model: ModelRef, texts: tuple[str, ...]
    ) -> dict[int, tuple[float, ...]]:
        """Fetch cached vectors for a batch of inputs.

        Args:
            tenant_id: Owning tenant.
            model: Embedding model.
            texts: Inputs, in request order.

        Returns:
            Mapping of input index to cached vector, for hits only.
        """
        hits: dict[int, tuple[float, ...]] = {}
        for index, text in enumerate(texts):
            raw = await self._cache.get(self.key(tenant_id, model, text))
            if raw is None:
                continue
            try:
                hits[index] = tuple(float(v) for v in json.loads(raw))
            except (ValueError, TypeError):
                await self._cache.delete(self.key(tenant_id, model, text))
        self._metrics.increment(
            "gateway_cache_events_total",
            value=float(len(hits)),
            labels={"result": "hit"},
        )
        return hits

    async def put_many(
        self,
        tenant_id: TenantId,
        model: ModelRef,
        pairs: list[tuple[str, tuple[float, ...]]],
    ) -> None:
        """Store vectors for a batch of inputs.

        Args:
            tenant_id: Owning tenant.
            model: Embedding model.
            pairs: Input text and vector pairs.
        """
        for text, vector in pairs:
            await self._cache.set(
                self.key(tenant_id, model, text),
                json.dumps(list(vector)).encode("utf-8"),
                ttl_seconds=self._ttl,
            )


__all__ = ["CACHE_NAMESPACE", "CachedCompletion", "EmbeddingCache", "ResponseCache"]
