"""SQLAlchemy async unit of work and repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from types import TracebackType
from typing import Any, Self

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ai_gateway.application.ports.repositories import (
    AgentRunRepository,
    ApiKeyRepository,
    AuditRepository,
    ConversationRepository,
    OutboxRepository,
    PromptRepository,
    TenantRepository,
    UsageRepository,
)
from ai_gateway.domain.entities.agent import (
    AgentDefinition,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepType,
    ToolInvocation,
)
from ai_gateway.domain.entities.audit import AuditEvent, AuditOutcome
from ai_gateway.domain.entities.conversation import Conversation, ConversationStatus
from ai_gateway.domain.entities.message import Message, MessageRole, ToolCall
from ai_gateway.domain.entities.prompt import PromptTemplate, PromptVersion
from ai_gateway.domain.entities.tenant import (
    ApiKey,
    Quota,
    QuotaPeriod,
    Role,
    RoutingPreferences,
    Tenant,
    TenantStatus,
)
from ai_gateway.domain.entities.usage import OperationType, UsageAggregate, UsageRecord
from ai_gateway.domain.events import EVENT_SCHEMA_VERSION, DomainEvent, EventType
from ai_gateway.domain.policies.quota import UsageSnapshot
from ai_gateway.domain.value_objects.identifiers import (
    AgentRunId,
    ApiKeyId,
    ConversationId,
    MessageId,
    PromptId,
    RequestId,
    TenantId,
    UserId,
    new_id,
)
from ai_gateway.domain.value_objects.model import ModelRef, ModelTier
from ai_gateway.domain.value_objects.money import Money
from ai_gateway.domain.value_objects.provider import ProviderName
from ai_gateway.domain.value_objects.tokens import TokenUsage
from ai_gateway.infrastructure.persistence.models import (
    AgentRunModel,
    AgentStepModel,
    ApiKeyModel,
    AuditEventModel,
    ConversationModel,
    MessageModel,
    OutboxEventModel,
    PromptModel,
    PromptVersionModel,
    TenantModel,
    UsageAggregateModel,
    UsageRecordModel,
)


def _money_to_json(money: Money | None) -> dict[str, Any] | None:
    if money is None:
        return None
    return {"micros": money.micros, "currency": money.currency}


def _money_from_json(data: dict[str, Any] | None) -> Money | None:
    if data is None:
        return None
    return Money.from_micros(int(data["micros"]), str(data.get("currency", "USD")))


def _token_usage_to_json(usage: TokenUsage) -> dict[str, int]:
    return usage.as_dict()


def _token_usage_from_json(data: dict[str, Any]) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=int(data.get("prompt_tokens", 0)),
        completion_tokens=int(data.get("completion_tokens", 0)),
        cached_prompt_tokens=int(data.get("cached_prompt_tokens", 0)),
        reasoning_tokens=int(data.get("reasoning_tokens", 0)),
    )


def _quota_to_json(quota: Quota) -> dict[str, Any]:
    return {
        "max_requests": quota.max_requests,
        "max_tokens": quota.max_tokens,
        "max_cost": _money_to_json(quota.max_cost),
    }


def _quota_from_json(period: QuotaPeriod, data: dict[str, Any]) -> Quota:
    max_cost = _money_from_json(data.get("max_cost"))
    return Quota(
        period=period,
        max_requests=data.get("max_requests"),
        max_tokens=data.get("max_tokens"),
        max_cost=max_cost,
    )


def _quotas_to_json(quotas: dict[QuotaPeriod, Quota]) -> dict[str, Any]:
    return {period.value: _quota_to_json(quota) for period, quota in quotas.items()}


def _quotas_from_json(data: dict[str, Any]) -> dict[QuotaPeriod, Quota]:
    return {
        QuotaPeriod(key): _quota_from_json(QuotaPeriod(key), value) for key, value in data.items()
    }


def _routing_to_json(routing: RoutingPreferences) -> dict[str, Any]:
    return {
        "allowed_providers": sorted(provider.value for provider in routing.allowed_providers),
        "denied_providers": sorted(provider.value for provider in routing.denied_providers),
        "allowed_models": sorted(routing.allowed_models),
        "preferred_provider": (
            routing.preferred_provider.value if routing.preferred_provider else None
        ),
        "max_tier": routing.max_tier.value,
        "data_residency": routing.data_residency,
        "require_streaming_support": routing.require_streaming_support,
        "max_cost_per_request": _money_to_json(routing.max_cost_per_request),
    }


def _routing_from_json(data: dict[str, Any]) -> RoutingPreferences:
    preferred = data.get("preferred_provider")
    max_cost = _money_from_json(data.get("max_cost_per_request"))
    return RoutingPreferences(
        allowed_providers=frozenset(
            ProviderName.parse(value) for value in data.get("allowed_providers", [])
        ),
        denied_providers=frozenset(
            ProviderName.parse(value) for value in data.get("denied_providers", [])
        ),
        allowed_models=frozenset(data.get("allowed_models", [])),
        preferred_provider=ProviderName.parse(preferred) if preferred else None,
        max_tier=ModelTier(data.get("max_tier", ModelTier.PREMIUM.value)),
        data_residency=data.get("data_residency"),
        require_streaming_support=bool(data.get("require_streaming_support", False)),
        max_cost_per_request=max_cost,
    )


def _tenant_to_model(tenant: Tenant) -> TenantModel:
    return TenantModel(
        id=str(tenant.id),
        name=tenant.name,
        status=tenant.status.value,
        rate_limit_per_minute=tenant.rate_limit_per_minute,
        rate_limit_burst=tenant.rate_limit_burst,
        quotas=_quotas_to_json(tenant.quotas),
        routing=_routing_to_json(tenant.routing),
        pii_redaction_enabled=tenant.pii_redaction_enabled,
        injection_detection_enabled=tenant.injection_detection_enabled,
        audit_retention_days=tenant.audit_retention_days,
        metadata_json=dict(tenant.metadata),
        created_at=tenant.created_at,
    )


def _tenant_from_model(model: TenantModel) -> Tenant:
    return Tenant(
        name=model.name,
        id=TenantId(model.id),
        status=TenantStatus(model.status),
        quotas=_quotas_from_json(model.quotas or {}),
        rate_limit_per_minute=model.rate_limit_per_minute,
        rate_limit_burst=model.rate_limit_burst,
        routing=_routing_from_json(model.routing or {}),
        pii_redaction_enabled=model.pii_redaction_enabled,
        injection_detection_enabled=model.injection_detection_enabled,
        audit_retention_days=model.audit_retention_days,
        created_at=model.created_at,
        metadata={str(k): str(v) for k, v in (model.metadata_json or {}).items()},
    )


def _api_key_to_model(api_key: ApiKey) -> ApiKeyModel:
    return ApiKeyModel(
        id=str(api_key.id),
        tenant_id=str(api_key.tenant_id),
        prefix=api_key.prefix,
        hashed_secret=api_key.hashed_secret,
        name=api_key.name,
        roles=sorted(role.value for role in api_key.roles),
        scopes=sorted(api_key.scopes),
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
    )


def _api_key_from_model(model: ApiKeyModel) -> ApiKey:
    return ApiKey(
        tenant_id=TenantId(model.tenant_id),
        prefix=model.prefix,
        hashed_secret=model.hashed_secret,
        id=ApiKeyId(model.id),
        name=model.name,
        roles=frozenset(Role(value) for value in model.roles),
        scopes=frozenset(model.scopes),
        created_at=model.created_at,
        expires_at=model.expires_at,
        last_used_at=model.last_used_at,
        revoked_at=model.revoked_at,
    )


def _tool_call_to_json(tool_call: ToolCall) -> dict[str, Any]:
    return {"id": tool_call.id, "name": tool_call.name, "arguments": tool_call.arguments}


def _tool_call_from_json(data: dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=str(data["id"]),
        name=str(data["name"]),
        arguments=dict(data.get("arguments", {})),
    )


def _message_to_model(message: Message, conversation_id: str, position: int) -> MessageModel:
    return MessageModel(
        id=str(message.id),
        conversation_id=conversation_id,
        position=position,
        role=message.role.value,
        content=message.content,
        name=message.name,
        tool_calls=[_tool_call_to_json(call) for call in message.tool_calls],
        tool_call_id=message.tool_call_id,
        metadata_json=dict(message.metadata),
        created_at=message.created_at,
    )


def _message_from_model(model: MessageModel) -> Message:
    return Message(
        role=MessageRole(model.role),
        content=model.content,
        id=MessageId(model.id),
        name=model.name,
        tool_calls=tuple(_tool_call_from_json(item) for item in model.tool_calls or []),
        tool_call_id=model.tool_call_id,
        created_at=model.created_at,
        metadata=dict(model.metadata_json or {}),
    )


def _conversation_to_model(conversation: Conversation) -> ConversationModel:
    return ConversationModel(
        id=str(conversation.id),
        tenant_id=str(conversation.tenant_id),
        user_id=str(conversation.user_id) if conversation.user_id else None,
        title=conversation.title,
        status=conversation.status.value,
        cumulative_usage=_token_usage_to_json(conversation.cumulative_usage),
        version=conversation.version,
        metadata_json=dict(conversation.metadata),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _conversation_from_model(model: ConversationModel) -> Conversation:
    return Conversation(
        tenant_id=TenantId(model.tenant_id),
        id=ConversationId(model.id),
        user_id=UserId(model.user_id) if model.user_id else None,
        title=model.title,
        status=ConversationStatus(model.status),
        messages=[_message_from_model(message) for message in model.messages],
        cumulative_usage=_token_usage_from_json(model.cumulative_usage or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
        version=model.version,
        metadata={str(k): str(v) for k, v in (model.metadata_json or {}).items()},
    )


def _update_conversation_model(model: ConversationModel, conversation: Conversation) -> None:
    model.tenant_id = str(conversation.tenant_id)
    model.user_id = str(conversation.user_id) if conversation.user_id else None
    model.title = conversation.title
    model.status = conversation.status.value
    model.cumulative_usage = _token_usage_to_json(conversation.cumulative_usage)
    model.version = conversation.version
    model.metadata_json = dict(conversation.metadata)
    model.created_at = conversation.created_at
    model.updated_at = conversation.updated_at


def _prompt_version_to_model(version: PromptVersion, prompt_id: str) -> PromptVersionModel:
    return PromptVersionModel(
        id=f"{prompt_id}-v{version.version}",
        prompt_id=prompt_id,
        version=version.version,
        template=version.template,
        system_prompt=version.system_prompt,
        safety_prompt=version.safety_prompt,
        required_variables=sorted(version.required_variables),
        created_by=version.created_by,
        notes=version.notes,
        created_at=version.created_at,
    )


def _prompt_version_from_model(model: PromptVersionModel) -> PromptVersion:
    return PromptVersion(
        version=model.version,
        template=model.template,
        system_prompt=model.system_prompt,
        safety_prompt=model.safety_prompt,
        required_variables=frozenset(model.required_variables or []),
        created_at=model.created_at,
        created_by=model.created_by,
        notes=model.notes,
    )


def _prompt_to_model(prompt: PromptTemplate) -> PromptModel:
    model = PromptModel(
        id=str(prompt.id),
        tenant_id=str(prompt.tenant_id),
        name=prompt.name,
        description=prompt.description,
        active_version=prompt.active_version,
        labels=dict(prompt.labels),
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )
    model.versions = [
        _prompt_version_to_model(version, str(prompt.id)) for version in prompt.versions
    ]
    return model


def _prompt_from_model(model: PromptModel) -> PromptTemplate:
    return PromptTemplate(
        tenant_id=TenantId(model.tenant_id),
        name=model.name,
        id=PromptId(model.id),
        description=model.description,
        versions=[_prompt_version_from_model(version) for version in model.versions],
        active_version=model.active_version,
        labels={str(k): str(v) for k, v in (model.labels or {}).items()},
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _update_prompt_model(model: PromptModel, prompt: PromptTemplate) -> None:
    model.tenant_id = str(prompt.tenant_id)
    model.name = prompt.name
    model.description = prompt.description
    model.active_version = prompt.active_version
    model.labels = dict(prompt.labels)
    model.created_at = prompt.created_at
    model.updated_at = prompt.updated_at


def _agent_definition_to_json(definition: AgentDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "instructions": definition.instructions,
        "tools": sorted(definition.tools),
        "max_iterations": definition.max_iterations,
        "model": definition.model,
        "temperature": definition.temperature,
        "max_output_tokens": definition.max_output_tokens,
        "tool_choice": definition.tool_choice,
        "memory_window": definition.memory_window,
    }


def _agent_definition_from_json(data: dict[str, Any]) -> AgentDefinition:
    return AgentDefinition(
        name=str(data["name"]),
        instructions=str(data["instructions"]),
        tools=frozenset(data.get("tools", [])),
        max_iterations=int(data.get("max_iterations", 6)),
        model=data.get("model"),
        temperature=float(data.get("temperature", 0.2)),
        max_output_tokens=int(data.get("max_output_tokens", 1024)),
        tool_choice=str(data.get("tool_choice", "auto")),
        memory_window=int(data.get("memory_window", 20)),
    )


def _tool_invocation_to_json(invocation: ToolInvocation) -> dict[str, Any]:
    return {
        "call": _tool_call_to_json(invocation.call),
        "output": invocation.output,
        "succeeded": invocation.succeeded,
        "duration_ms": invocation.duration_ms,
        "error": invocation.error,
    }


def _tool_invocation_from_json(data: dict[str, Any]) -> ToolInvocation:
    return ToolInvocation(
        call=_tool_call_from_json(data["call"]),
        output=str(data.get("output", "")),
        succeeded=bool(data.get("succeeded", True)),
        duration_ms=int(data.get("duration_ms", 0)),
        error=data.get("error"),
    )


def _agent_step_to_model(step: AgentStep, run_id: str) -> AgentStepModel:
    return AgentStepModel(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"aigw:agent-step:{run_id}:{step.index}")),
        run_id=run_id,
        index=step.index,
        type=step.type.value,
        content=step.content,
        usage=_token_usage_to_json(step.usage),
        cost_micros=step.cost.micros,
        duration_ms=step.duration_ms,
        tool_invocations=[_tool_invocation_to_json(item) for item in step.tool_invocations],
        error=step.error,
        started_at=step.started_at,
    )


def _agent_step_from_model(model: AgentStepModel) -> AgentStep:
    return AgentStep(
        index=model.index,
        type=AgentStepType(model.type),
        started_at=model.started_at,
        duration_ms=model.duration_ms,
        usage=_token_usage_from_json(model.usage or {}),
        cost=Money.from_micros(model.cost_micros),
        content=model.content,
        tool_invocations=tuple(
            _tool_invocation_from_json(item) for item in model.tool_invocations or []
        ),
        error=model.error,
    )


def _agent_run_to_model(run: AgentRun) -> AgentRunModel:
    model = AgentRunModel(
        id=str(run.id),
        tenant_id=str(run.tenant_id),
        conversation_id=str(run.conversation_id) if run.conversation_id else None,
        definition=_agent_definition_to_json(run.definition),
        status=run.status.value,
        output=run.output,
        error=run.error,
        total_usage=_token_usage_to_json(run.total_usage),
        total_cost_micros=run.total_cost.micros,
        currency=run.total_cost.currency,
        metadata_json=dict(run.metadata),
        started_at=run.started_at,
        finished_at=run.finished_at,
    )
    model.steps = [_agent_step_to_model(step, str(run.id)) for step in run.steps]
    return model


def _agent_run_from_model(model: AgentRunModel) -> AgentRun:
    return AgentRun(
        tenant_id=TenantId(model.tenant_id),
        definition=_agent_definition_from_json(model.definition or {}),
        id=AgentRunId(model.id),
        conversation_id=ConversationId(model.conversation_id) if model.conversation_id else None,
        status=AgentRunStatus(model.status),
        steps=[_agent_step_from_model(step) for step in model.steps],
        output=model.output,
        error=model.error,
        started_at=model.started_at,
        finished_at=model.finished_at,
        total_usage=_token_usage_from_json(model.total_usage or {}),
        total_cost=Money.from_micros(model.total_cost_micros, model.currency),
        metadata={str(k): str(v) for k, v in (model.metadata_json or {}).items()},
    )


def _update_agent_run_model(model: AgentRunModel, run: AgentRun) -> None:
    model.tenant_id = str(run.tenant_id)
    model.conversation_id = str(run.conversation_id) if run.conversation_id else None
    model.definition = _agent_definition_to_json(run.definition)
    model.status = run.status.value
    model.output = run.output
    model.error = run.error
    model.total_usage = _token_usage_to_json(run.total_usage)
    model.total_cost_micros = run.total_cost.micros
    model.currency = run.total_cost.currency
    model.metadata_json = dict(run.metadata)
    model.started_at = run.started_at
    model.finished_at = run.finished_at


def _usage_record_to_model(record: UsageRecord) -> UsageRecordModel:
    return UsageRecordModel(
        id=record.id,
        tenant_id=str(record.tenant_id),
        request_id=str(record.request_id),
        operation=record.operation.value,
        provider=record.provider.value if record.provider else record.model.provider.value,
        model=record.model.name,
        prompt_tokens=record.usage.prompt_tokens,
        completion_tokens=record.usage.completion_tokens,
        cached_prompt_tokens=record.usage.cached_prompt_tokens,
        reasoning_tokens=record.usage.reasoning_tokens,
        cost_micros=record.cost.micros,
        currency=record.cost.currency,
        latency_ms=record.latency_ms,
        succeeded=record.succeeded,
        cached=record.cached,
        attempt=record.attempt,
        user_id=str(record.user_id) if record.user_id else None,
        metadata_json=dict(record.metadata),
        aggregated=False,
        occurred_at=record.occurred_at,
    )


def _usage_record_from_model(model: UsageRecordModel) -> UsageRecord:
    provider = ProviderName.parse(model.provider)
    return UsageRecord(
        tenant_id=TenantId(model.tenant_id),
        request_id=RequestId(model.request_id),
        operation=OperationType(model.operation),
        model=ModelRef(provider=provider, name=model.model),
        usage=TokenUsage(
            prompt_tokens=model.prompt_tokens,
            completion_tokens=model.completion_tokens,
            cached_prompt_tokens=model.cached_prompt_tokens,
            reasoning_tokens=model.reasoning_tokens,
        ),
        cost=Money.from_micros(model.cost_micros, model.currency),
        latency_ms=model.latency_ms,
        occurred_at=model.occurred_at,
        id=model.id,
        user_id=UserId(model.user_id) if model.user_id else None,
        provider=provider,
        succeeded=model.succeeded,
        cached=model.cached,
        attempt=model.attempt,
        metadata={str(k): str(v) for k, v in (model.metadata_json or {}).items()},
    )


def _usage_aggregate_to_model(aggregate: UsageAggregate) -> UsageAggregateModel:
    return UsageAggregateModel(
        id=f"{aggregate.tenant_id}:{aggregate.period_key}:{aggregate.model}",
        tenant_id=str(aggregate.tenant_id),
        period_key=aggregate.period_key,
        model=aggregate.model,
        requests=aggregate.requests,
        failed_requests=aggregate.failed_requests,
        cached_requests=aggregate.cached_requests,
        prompt_tokens=aggregate.usage.prompt_tokens,
        completion_tokens=aggregate.usage.completion_tokens,
        cached_prompt_tokens=aggregate.usage.cached_prompt_tokens,
        reasoning_tokens=aggregate.usage.reasoning_tokens,
        cost_micros=aggregate.cost.micros,
        currency=aggregate.cost.currency,
        updated_at=aggregate.updated_at,
    )


def _usage_aggregate_from_model(model: UsageAggregateModel) -> UsageAggregate:
    return UsageAggregate(
        tenant_id=TenantId(model.tenant_id),
        period_key=model.period_key,
        model=model.model,
        requests=model.requests,
        failed_requests=model.failed_requests,
        cached_requests=model.cached_requests,
        usage=TokenUsage(
            prompt_tokens=model.prompt_tokens,
            completion_tokens=model.completion_tokens,
            cached_prompt_tokens=model.cached_prompt_tokens,
            reasoning_tokens=model.reasoning_tokens,
        ),
        cost=Money.from_micros(model.cost_micros, model.currency),
        updated_at=model.updated_at,
    )


def _update_usage_aggregate_model(model: UsageAggregateModel, aggregate: UsageAggregate) -> None:
    model.requests = aggregate.requests
    model.failed_requests = aggregate.failed_requests
    model.cached_requests = aggregate.cached_requests
    model.prompt_tokens = aggregate.usage.prompt_tokens
    model.completion_tokens = aggregate.usage.completion_tokens
    model.cached_prompt_tokens = aggregate.usage.cached_prompt_tokens
    model.reasoning_tokens = aggregate.usage.reasoning_tokens
    model.cost_micros = aggregate.cost.micros
    model.currency = aggregate.cost.currency
    model.updated_at = aggregate.updated_at


def _audit_event_to_model(event: AuditEvent) -> AuditEventModel:
    return AuditEventModel(
        id=event.id,
        tenant_id=str(event.tenant_id),
        action=event.action,
        outcome=event.outcome.value,
        request_id=str(event.request_id) if event.request_id else None,
        actor=event.actor,
        user_id=str(event.user_id) if event.user_id else None,
        resource=event.resource,
        source_ip=event.source_ip,
        user_agent=event.user_agent,
        attributes=dict(event.attributes),
        occurred_at=event.occurred_at,
    )


def _audit_event_from_model(model: AuditEventModel) -> AuditEvent:
    return AuditEvent(
        tenant_id=TenantId(model.tenant_id),
        action=model.action,
        outcome=AuditOutcome(model.outcome),
        id=model.id,
        request_id=RequestId(model.request_id) if model.request_id else None,
        actor=model.actor,
        user_id=UserId(model.user_id) if model.user_id else None,
        resource=model.resource,
        source_ip=model.source_ip,
        user_agent=model.user_agent,
        occurred_at=model.occurred_at,
        attributes=dict(model.attributes or {}),
    )


def _domain_event_to_outbox(event: DomainEvent) -> OutboxEventModel:
    envelope = event.to_dict()
    return OutboxEventModel(
        id=event.id or new_id(),
        event_type=event.type.value,
        topic=event.topic,
        tenant_id=str(event.tenant_id),
        payload=envelope,
    )


def _domain_event_from_outbox(model: OutboxEventModel) -> DomainEvent:
    data = model.payload
    request_id = data.get("request_id")
    return DomainEvent(
        type=EventType(data["type"]),
        tenant_id=TenantId(data["tenant_id"]),
        payload=dict(data.get("payload", {})),
        id=str(data.get("id", model.id)),
        request_id=RequestId(request_id) if request_id else None,
        occurred_at=datetime.fromisoformat(str(data["occurred_at"])),
        schema_version=int(data.get("schema_version", EVENT_SCHEMA_VERSION)),
        trace_id=data.get("trace_id"),
    )


class SqlTenantRepository:
    """SQLAlchemy tenant repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        """Fetch a tenant by identifier."""
        model = await self._session.get(TenantModel, str(tenant_id))
        return _tenant_from_model(model) if model else None

    async def upsert(self, tenant: Tenant) -> None:
        """Insert or update a tenant."""
        existing = await self._session.get(TenantModel, str(tenant.id))
        if existing is None:
            self._session.add(_tenant_to_model(tenant))
            # Flush parents before dependent rows (api_keys) so Postgres FK checks pass
            # even if mapper dependency sorting is ambiguous without a relationship path.
            await self._session.flush()
            return
        existing.name = tenant.name
        existing.status = tenant.status.value
        existing.rate_limit_per_minute = tenant.rate_limit_per_minute
        existing.rate_limit_burst = tenant.rate_limit_burst
        existing.quotas = _quotas_to_json(tenant.quotas)
        existing.routing = _routing_to_json(tenant.routing)
        existing.pii_redaction_enabled = tenant.pii_redaction_enabled
        existing.injection_detection_enabled = tenant.injection_detection_enabled
        existing.audit_retention_days = tenant.audit_retention_days
        existing.metadata_json = dict(tenant.metadata)
        existing.created_at = tenant.created_at

    async def list_active(self) -> Sequence[Tenant]:
        """Return every active tenant."""
        stmt = select(TenantModel).where(TenantModel.status == TenantStatus.ACTIVE.value)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_tenant_from_model(row) for row in rows]


class SqlApiKeyRepository:
    """SQLAlchemy API key repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_prefix(self, prefix: str) -> Sequence[ApiKey]:
        """Fetch candidate keys sharing a non-secret prefix."""
        stmt = select(ApiKeyModel).where(ApiKeyModel.prefix == prefix)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_api_key_from_model(row) for row in rows]

    async def add(self, api_key: ApiKey) -> None:
        """Persist a new credential."""
        self._session.add(_api_key_to_model(api_key))

    async def revoke(self, key_id: str, *, at: datetime) -> None:
        """Revoke a credential."""
        await self._session.execute(
            update(ApiKeyModel).where(ApiKeyModel.id == key_id).values(revoked_at=at)
        )

    async def touch(self, key_id: str, *, at: datetime) -> None:
        """Record successful use of a credential."""
        await self._session.execute(
            update(ApiKeyModel).where(ApiKeyModel.id == key_id).values(last_used_at=at)
        )


class SqlConversationRepository:
    """SQLAlchemy conversation repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, conversation_id: ConversationId, *, tenant_id: TenantId
    ) -> Conversation | None:
        """Fetch a conversation scoped to a tenant."""
        stmt = (
            select(ConversationModel)
            .where(
                ConversationModel.id == str(conversation_id),
                ConversationModel.tenant_id == str(tenant_id),
            )
            .options(selectinload(ConversationModel.messages))
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _conversation_from_model(model) if model else None

    async def save(self, conversation: Conversation) -> None:
        """Insert or update a conversation and its messages."""
        conversation_id = str(conversation.id)
        stmt = (
            select(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .options(selectinload(ConversationModel.messages))
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            model = _conversation_to_model(conversation)
            self._session.add(model)
        else:
            _update_conversation_model(model, conversation)
            model.messages.clear()
        for position, message in enumerate(conversation.messages):
            model.messages.append(_message_to_model(message, conversation_id, position))

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> Sequence[Conversation]:
        """List conversations for a tenant, newest first."""
        stmt = (
            select(ConversationModel)
            .where(ConversationModel.tenant_id == str(tenant_id))
            .order_by(ConversationModel.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(ConversationModel.messages))
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_conversation_from_model(row) for row in rows]

    async def delete_stale(self, *, older_than: datetime, limit: int = 500) -> int:
        """Delete conversations idle since a cutoff."""
        ids_stmt = (
            select(ConversationModel.id)
            .where(ConversationModel.updated_at < older_than)
            .order_by(ConversationModel.updated_at.asc())
            .limit(limit)
        )
        ids = list((await self._session.execute(ids_stmt)).scalars().all())
        if not ids:
            return 0
        await self._session.execute(delete(ConversationModel).where(ConversationModel.id.in_(ids)))
        return len(ids)


class SqlPromptRepository:
    """SQLAlchemy prompt repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_name(self, tenant_id: TenantId, name: str) -> PromptTemplate | None:
        """Fetch a prompt by tenant-scoped name."""
        stmt = (
            select(PromptModel)
            .where(PromptModel.tenant_id == str(tenant_id), PromptModel.name == name)
            .options(selectinload(PromptModel.versions))
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _prompt_from_model(model) if model else None

    async def save(self, prompt: PromptTemplate) -> None:
        """Insert or update a prompt and append any new versions."""
        prompt_id = str(prompt.id)
        stmt = (
            select(PromptModel)
            .where(PromptModel.id == prompt_id)
            .options(selectinload(PromptModel.versions))
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            self._session.add(_prompt_to_model(prompt))
            return
        _update_prompt_model(model, prompt)
        existing_versions = {version.version for version in model.versions}
        for version in prompt.versions:
            if version.version not in existing_versions:
                model.versions.append(_prompt_version_to_model(version, prompt_id))

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 100, offset: int = 0
    ) -> Sequence[PromptTemplate]:
        """List prompts owned by a tenant."""
        stmt = (
            select(PromptModel)
            .where(PromptModel.tenant_id == str(tenant_id))
            .order_by(PromptModel.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(PromptModel.versions))
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_prompt_from_model(row) for row in rows]


class SqlAgentRunRepository:
    """SQLAlchemy agent run repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, run: AgentRun) -> None:
        """Insert or update an agent run."""
        run_id = str(run.id)
        stmt = (
            select(AgentRunModel)
            .where(AgentRunModel.id == run_id)
            .options(selectinload(AgentRunModel.steps))
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            self._session.add(_agent_run_to_model(run))
            return
        _update_agent_run_model(model, run)
        model.steps.clear()
        for step in run.steps:
            model.steps.append(_agent_step_to_model(step, run_id))

    async def get(self, run_id: AgentRunId, *, tenant_id: TenantId) -> AgentRun | None:
        """Fetch an agent run scoped to a tenant."""
        stmt = (
            select(AgentRunModel)
            .where(
                AgentRunModel.id == str(run_id),
                AgentRunModel.tenant_id == str(tenant_id),
            )
            .options(selectinload(AgentRunModel.steps))
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _agent_run_from_model(model) if model else None


class SqlUsageRepository:
    """SQLAlchemy usage repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, usage: UsageRecord) -> None:
        """Append a usage record."""
        self._session.add(_usage_record_to_model(usage))

    async def _aggregate_snapshot(
        self, tenant_id: TenantId, *, start: datetime, end: datetime
    ) -> UsageSnapshot:
        token_total = (
            UsageRecordModel.prompt_tokens
            + UsageRecordModel.completion_tokens
            + UsageRecordModel.reasoning_tokens
        )
        stmt = select(
            func.count(UsageRecordModel.id),
            func.coalesce(func.sum(token_total), 0),
            func.coalesce(func.sum(UsageRecordModel.cost_micros), 0),
        ).where(
            UsageRecordModel.tenant_id == str(tenant_id),
            UsageRecordModel.occurred_at >= start,
            UsageRecordModel.occurred_at < end,
        )
        requests, tokens, cost_micros = (await self._session.execute(stmt)).one()
        return UsageSnapshot(
            period=QuotaPeriod.DAILY,
            requests=int(requests or 0),
            tokens=int(tokens or 0),
            cost=Money.from_micros(int(cost_micros or 0)),
        )

    async def snapshot(
        self, tenant_id: TenantId, *, at: datetime
    ) -> dict[QuotaPeriod, UsageSnapshot]:
        """Return current daily and monthly consumption for a tenant."""
        moment = at.astimezone(UTC)
        day_start = datetime.combine(moment.date(), time.min, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        month_start = datetime(moment.year, moment.month, 1, tzinfo=UTC)
        month_end = datetime(
            moment.year + moment.month // 12,
            moment.month % 12 + 1,
            1,
            tzinfo=UTC,
        )

        daily = await self._aggregate_snapshot(tenant_id, start=day_start, end=day_end)
        monthly = await self._aggregate_snapshot(tenant_id, start=month_start, end=month_end)
        return {
            QuotaPeriod.DAILY: UsageSnapshot(
                period=QuotaPeriod.DAILY,
                requests=daily.requests,
                tokens=daily.tokens,
                cost=daily.cost,
            ),
            QuotaPeriod.MONTHLY: UsageSnapshot(
                period=QuotaPeriod.MONTHLY,
                requests=monthly.requests,
                tokens=monthly.tokens,
                cost=monthly.cost,
            ),
        }

    async def unaggregated(self, *, limit: int = 1000) -> Sequence[UsageRecord]:
        """Fetch usage records that have not yet been rolled up."""
        stmt = (
            select(UsageRecordModel)
            .where(UsageRecordModel.aggregated.is_(False))
            .order_by(UsageRecordModel.occurred_at.asc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_usage_record_from_model(row) for row in rows]

    async def mark_aggregated(self, record_ids: Sequence[str]) -> None:
        """Mark usage records as rolled up."""
        if not record_ids:
            return
        await self._session.execute(
            update(UsageRecordModel)
            .where(UsageRecordModel.id.in_(list(record_ids)))
            .values(aggregated=True)
        )

    async def upsert_aggregate(self, aggregate: UsageAggregate) -> None:
        """Insert or update a roll-up row."""
        stmt = select(UsageAggregateModel).where(
            UsageAggregateModel.tenant_id == str(aggregate.tenant_id),
            UsageAggregateModel.period_key == aggregate.period_key,
            UsageAggregateModel.model == aggregate.model,
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            self._session.add(_usage_aggregate_to_model(aggregate))
            return
        _update_usage_aggregate_model(model, aggregate)

    async def aggregates_for(
        self, tenant_id: TenantId, *, since: date, until: date
    ) -> Sequence[UsageAggregate]:
        """Return roll-ups for a tenant within a date range."""
        since_key = since.isoformat()
        until_key = until.isoformat()
        stmt = select(UsageAggregateModel).where(
            UsageAggregateModel.tenant_id == str(tenant_id),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            _usage_aggregate_from_model(row)
            for row in rows
            if since_key <= row.period_key[:10] <= until_key
        ]


class SqlAuditRepository:
    """SQLAlchemy audit repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AuditEvent) -> None:
        """Persist an audit event."""
        self._session.add(_audit_event_to_model(event))

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 100, offset: int = 0
    ) -> Sequence[AuditEvent]:
        """List audit events for a tenant, newest first."""
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.tenant_id == str(tenant_id))
            .order_by(AuditEventModel.occurred_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_audit_event_from_model(row) for row in rows]

    async def purge_older_than(self, cutoff: datetime, *, limit: int = 1000) -> int:
        """Delete audit events beyond the retention window."""
        ids_stmt = (
            select(AuditEventModel.id)
            .where(AuditEventModel.occurred_at < cutoff)
            .order_by(AuditEventModel.occurred_at.asc())
            .limit(limit)
        )
        ids = list((await self._session.execute(ids_stmt)).scalars().all())
        if not ids:
            return 0
        await self._session.execute(delete(AuditEventModel).where(AuditEventModel.id.in_(ids)))
        return len(ids)


class SqlOutboxRepository:
    """SQLAlchemy transactional outbox repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, event: DomainEvent) -> None:
        """Stage an event for publication in the current transaction."""
        self._session.add(_domain_event_to_outbox(event))

    async def fetch_unpublished(self, *, limit: int = 100) -> Sequence[tuple[str, DomainEvent]]:
        """Claim a batch of pending events."""
        stmt = (
            select(OutboxEventModel)
            .where(OutboxEventModel.published_at.is_(None))
            .order_by(OutboxEventModel.created_at.asc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [(row.id, _domain_event_from_outbox(row)) for row in rows]

    async def mark_published(self, outbox_ids: Sequence[str], *, at: datetime) -> None:
        """Mark events as successfully published."""
        if not outbox_ids:
            return
        await self._session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id.in_(list(outbox_ids)))
            .values(published_at=at)
        )

    async def mark_failed(self, outbox_id: str, *, error: str) -> int:
        """Record a publication failure and increment the attempt counter."""
        stmt = select(OutboxEventModel).where(OutboxEventModel.id == outbox_id)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            return 0
        model.attempts += 1
        model.last_error = error
        return model.attempts


class SqlAlchemyUnitOfWork:
    """SQLAlchemy-backed unit of work with commit/rollback semantics."""

    tenants: TenantRepository
    api_keys: ApiKeyRepository
    conversations: ConversationRepository
    prompts: PromptRepository
    agent_runs: AgentRunRepository
    usage: UsageRepository
    audit: AuditRepository
    outbox: OutboxRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialise the unit of work.

        Args:
            session_factory: Factory that produces async database sessions.
        """
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._active = False

    def _bind_repos(self) -> None:
        session = self._session
        if session is None:
            return
        self.tenants = SqlTenantRepository(session)
        self.api_keys = SqlApiKeyRepository(session)
        self.conversations = SqlConversationRepository(session)
        self.prompts = SqlPromptRepository(session)
        self.agent_runs = SqlAgentRunRepository(session)
        self.usage = SqlUsageRepository(session)
        self.audit = SqlAuditRepository(session)
        self.outbox = SqlOutboxRepository(session)

    async def __aenter__(self) -> Self:
        """Begin a transaction."""
        self._session = self._session_factory()
        self._bind_repos()
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Commit on success, roll back on failure."""
        if self._session is None:
            return
        try:
            if exc_type is None and self._active:
                await self.commit()
            elif self._active:
                await self.rollback()
        finally:
            await self._session.close()
            self._session = None
            self._active = False

    async def commit(self) -> None:
        """Flush and commit the transaction."""
        if self._session is None:
            return
        await self._session.flush()
        await self._session.commit()
        self._active = False

    async def rollback(self) -> None:
        """Abandon the transaction."""
        if self._session is None:
            return
        await self._session.rollback()
        self._active = False

    async def execute_raw(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a raw statement, used only by health probes and maintenance jobs."""
        if self._session is None:
            raise RuntimeError("Unit of work is not active")
        result = await self._session.execute(text(statement), params or {})
        return result.scalar()


__all__ = ["SqlAlchemyUnitOfWork"]
