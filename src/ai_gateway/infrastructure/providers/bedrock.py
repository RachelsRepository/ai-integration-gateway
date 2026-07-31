"""AWS Bedrock Runtime provider adapter.

Implements a minimal SigV4 signer so the gateway has no hard dependency on boto3. The
adapter posts Anthropic-compatible messages to the Bedrock Runtime endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from ai_gateway.application.ports.llm_provider import (
    EmbeddingsRequest,
    EmbeddingsResponse,
    ProviderCallContext,
    ProviderChatRequest,
    ProviderChatResponse,
    StreamChunk,
)
from ai_gateway.domain.entities.message import Message, MessageRole, ToolCall
from ai_gateway.domain.errors import UnsupportedCapabilityError
from ai_gateway.domain.value_objects.model import ModelSpec
from ai_gateway.domain.value_objects.provider import ProviderName, ProviderStatus
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.providers.base import (
    map_http_error,
    map_transport_error,
    parse_finish_reason,
    require_mapping,
    split_system_messages,
)
from ai_gateway.infrastructure.providers.catalog import StaticModelCatalog


def _sign(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    payload: bytes,
    access_key: str,
    secret_key: str,
    session_token: str | None,
    region: str,
    service: str = "bedrock",
) -> dict[str, str]:
    """Sign an HTTP request with AWS Signature Version 4.

    Args:
        method: HTTP method.
        url: Full request URL.
        headers: Existing headers.
        payload: Raw body.
        access_key: AWS access key id.
        secret_key: AWS secret access key.
        session_token: Optional session token.
        region: AWS region.
        service: AWS service name.

    Returns:
        Headers including the Authorization signature.
    """
    parsed = urlparse(url)
    host = parsed.netloc
    amz_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]
    canonical_headers = f"content-type:application/json\nhost:{host}\nx-amz-date:{amz_date}\n"
    signed_headers = "content-type;host;x-amz-date"
    if session_token:
        canonical_headers += f"x-amz-security-token:{session_token}\n"
        signed_headers += ";x-amz-security-token"
    payload_hash = hashlib.sha256(payload).hexdigest()
    canonical_request = "\n".join(
        [
            method,
            parsed.path or "/",
            parsed.query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(f"AWS4{secret_key}".encode(), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    signed = {
        **headers,
        "Host": host,
        "X-Amz-Date": amz_date,
        "Authorization": authorization,
        "Content-Type": "application/json",
    }
    if session_token:
        signed["X-Amz-Security-Token"] = session_token
    return signed


class BedrockProvider:
    """Adapter for Amazon Bedrock Runtime (Anthropic message models)."""

    def __init__(
        self,
        *,
        region: str = "us-east-1",
        access_key_id: str,
        secret_access_key: str,
        session_token: str | None = None,
        endpoint: str | None = None,
        catalog: StaticModelCatalog | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise the adapter."""
        self._region = region
        self._access_key = access_key_id
        self._secret_key = secret_access_key
        self._session_token = session_token
        self._endpoint = endpoint or f"https://bedrock-runtime.{region}.amazonaws.com"
        catalog = catalog or StaticModelCatalog()
        self._models = tuple(catalog.for_provider(ProviderName.BEDROCK))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=60.0)

    @property
    def name(self) -> ProviderName:
        """Return the provider identifier."""
        return ProviderName.BEDROCK

    def supported_models(self) -> tuple[ModelSpec, ...]:
        """Return supported models."""
        return self._models

    async def chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> ProviderChatResponse:
        """Invoke a Bedrock model with the Anthropic messages schema."""
        started = time.perf_counter()
        payload = self._payload(request)
        data = await self._invoke(request.model.name, payload, context)
        content_blocks = data.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=dict(block.get("input") or {}),
                    )
                )
        usage_raw = data.get("usage") or {}
        return ProviderChatResponse(
            model=request.model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content="".join(text_parts),
                tool_calls=tuple(tool_calls),
            ),
            usage=TokenUsage(
                prompt_tokens=int(usage_raw.get("input_tokens") or 0),
                completion_tokens=int(usage_raw.get("output_tokens") or 0),
            ),
            finish_reason=parse_finish_reason(data.get("stop_reason")),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream_chat(
        self, request: ProviderChatRequest, context: ProviderCallContext
    ) -> AsyncIterator[StreamChunk]:
        """Bedrock streaming is approximated by a single chunk for this adapter."""
        response = await self.chat(request, context)
        if response.content:
            yield StreamChunk(delta=response.content, index=0)
        yield StreamChunk(finish_reason=response.finish_reason, usage=response.usage, index=1)

    async def embed(
        self, request: EmbeddingsRequest, context: ProviderCallContext
    ) -> EmbeddingsResponse:
        """Embeddings are not implemented for this Bedrock adapter."""
        del request, context
        raise UnsupportedCapabilityError("Bedrock adapter does not support embeddings")

    async def health_check(self) -> ProviderStatus:
        """Report healthy when credentials are configured."""
        return ProviderStatus.HEALTHY if self._access_key else ProviderStatus.UNAVAILABLE

    async def aclose(self) -> None:
        """Close owned resources."""
        if self._owns_client:
            await self._client.aclose()

    def _payload(self, request: ProviderChatRequest) -> dict[str, Any]:
        system, rest = split_system_messages(request.messages)
        messages = [
            {
                "role": m.role.value if m.role is not MessageRole.TOOL else "user",
                "content": m.content,
            }
            for m in rest
        ]
        payload: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if system:
            payload["system"] = system
        return payload

    async def _invoke(
        self, model_id: str, payload: dict[str, Any], context: ProviderCallContext
    ) -> dict[str, Any]:
        url = f"{self._endpoint}/model/{model_id}/invoke"
        body = json.dumps(payload).encode("utf-8")
        headers = _sign(
            method="POST",
            url=url,
            headers={"X-Request-ID": str(context.request_id)},
            payload=body,
            access_key=self._access_key,
            secret_key=self._secret_key,
            session_token=self._session_token,
            region=self._region,
            service="bedrock",
        )
        try:
            response = await self._client.post(
                url, content=body, headers=headers, timeout=context.timeout_seconds
            )
            if response.status_code >= 400:
                raise map_http_error(self.name, response)
            return dict(require_mapping(response.json(), self.name))
        except httpx.HTTPError as exc:
            raise map_transport_error(self.name, exc) from exc


__all__ = ["BedrockProvider"]
