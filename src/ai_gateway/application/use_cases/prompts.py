"""Prompt management use cases."""

from __future__ import annotations

from ai_gateway.application.dto import (
    PromptPublishCommand,
    PromptVersionView,
    PromptView,
    RequestContext,
)
from ai_gateway.application.ports.repositories import UnitOfWork
from ai_gateway.application.use_cases.base import GatewayServices, load_prompt
from ai_gateway.domain.entities.prompt import PromptTemplate
from ai_gateway.domain.entities.tenant import Permission


def _to_view(prompt: PromptTemplate) -> PromptView:
    """Project a prompt aggregate into its read model.

    Args:
        prompt: The aggregate.

    Returns:
        The read model.
    """
    return PromptView(
        id=str(prompt.id),
        name=prompt.name,
        description=prompt.description,
        active_version=prompt.active_version,
        versions=tuple(
            PromptVersionView(
                version=version.version,
                template=version.template,
                system_prompt=version.system_prompt,
                safety_prompt=version.safety_prompt,
                required_variables=tuple(sorted(version.required_variables)),
                created_at=version.created_at,
                created_by=version.created_by,
                notes=version.notes,
            )
            for version in prompt.history()
        ),
        labels=dict(prompt.labels),
        updated_at=prompt.updated_at,
    )


class PublishPromptUseCase:
    """Serves ``POST /v1/prompts``.

    Publishing is additive: an existing prompt gains a new immutable version rather than
    being mutated, which preserves the audit trail and lets callers pin a version.
    """

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators.
        """
        self._s = services

    async def execute(self, command: PromptPublishCommand, context: RequestContext) -> PromptView:
        """Create a prompt or publish a new version of an existing one.

        Args:
            command: Caller request.
            context: Request context.

        Returns:
            The updated prompt read model.
        """
        context.principal.require(Permission.PROMPTS_WRITE)
        context.tenant.assert_active()

        async with self._s.uow_factory() as uow:
            prompt = await uow.prompts.get_by_name(context.tenant_id, command.name)
            if prompt is None:
                prompt = PromptTemplate(
                    tenant_id=context.tenant_id,
                    name=command.name,
                    description=command.description,
                    labels=dict(command.labels),
                )
            elif command.description is not None:
                prompt.description = command.description

            version = prompt.publish(
                template=command.template,
                system_prompt=command.system_prompt,
                safety_prompt=command.safety_prompt,
                required_variables=command.required_variables,
                created_by=context.principal.subject,
                notes=command.notes,
                activate=command.activate,
                now=self._s.clock.now(),
            )
            await uow.prompts.save(prompt)
            await self._s.audit.record(
                uow,
                context,
                action="prompts.publish",
                resource=prompt.name,
                attributes={"version": version.version, "activated": command.activate},
            )
            await uow.commit()
            return _to_view(prompt)


class GetPromptUseCase:
    """Serves ``GET /v1/prompts/{name}``."""

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators.
        """
        self._s = services

    async def execute(self, name: str, context: RequestContext) -> PromptView:
        """Fetch a prompt and its version history.

        Args:
            name: Prompt name.
            context: Request context.

        Returns:
            The prompt read model.
        """
        context.principal.require(Permission.PROMPTS_READ)
        async with self._s.uow_factory() as uow:
            prompt = await load_prompt(uow, context.tenant_id, name)
            return _to_view(prompt)


class ListPromptsUseCase:
    """Serves ``GET /v1/prompts``."""

    def __init__(self, services: GatewayServices) -> None:
        """Initialise the use case.

        Args:
            services: Shared collaborators.
        """
        self._s = services

    async def execute(
        self, context: RequestContext, *, limit: int = 100, offset: int = 0
    ) -> tuple[PromptView, ...]:
        """List the tenant's prompts.

        Args:
            context: Request context.
            limit: Maximum rows to return.
            offset: Rows to skip.

        Returns:
            The prompt read models.
        """
        context.principal.require(Permission.PROMPTS_READ)
        async with self._s.uow_factory() as uow:
            prompts = await self._list(uow, context, limit=limit, offset=offset)
            return tuple(_to_view(prompt) for prompt in prompts)

    @staticmethod
    async def _list(
        uow: UnitOfWork, context: RequestContext, *, limit: int, offset: int
    ) -> list[PromptTemplate]:
        rows = await uow.prompts.list_for_tenant(context.tenant_id, limit=limit, offset=offset)
        return list(rows)


__all__ = ["GetPromptUseCase", "ListPromptsUseCase", "PublishPromptUseCase"]
