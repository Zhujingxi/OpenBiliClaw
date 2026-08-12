"""Budgeted PydanticAI native tools over typed provider capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field
from pydantic_ai import Tool

from openbiliclaw.core._pydantic import StrictBaseModel

from .capabilities import (
    FetchCapability,
    PageRequest,
    ProjectionCapability,
    SearchCapability,
    SearchQuery,
)
from .errors import ContentIntegrationError, IntegrationErrorCode
from .identity import ContentKind, ContentRef, ProviderId
from .manifest import ActionDescriptor, CapabilityKind, ProviderManifest

if TYPE_CHECKING:
    from openbiliclaw.access.models import AccessHandle


class ToolBudget(StrictBaseModel):
    """Hard output bounds applied before tool results enter model history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_items: int = Field(default=5, ge=1, le=20)
    max_title_chars: int = Field(default=200, ge=1, le=500)
    max_summary_chars: int = Field(default=1000, ge=1, le=4000)


class ToolPreview(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ContentRef
    title: str
    summary: str


class ToolPreviewPage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ToolPreview, ...]


class PendingActionDescriptor(StrictBaseModel):
    """Non-executing mutation proposal for Application confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ref: ContentRef
    idempotency_key: str = Field(min_length=8, max_length=200)
    confirmation_summary: str = Field(min_length=1, max_length=500)
    requires_confirmation: bool = True


def _bounded(value: str, maximum: int) -> str:
    return value[:maximum]


def _search_tool(
    manifest: ProviderManifest,
    capability: SearchCapability,
    access: AccessHandle,
    budget: ToolBudget,
) -> Tool[None]:
    async def search(query: str, limit: int = 10) -> ToolPreviewPage:
        """Search this provider for content matching a query."""

        bounded_limit = min(max(limit, 1), budget.max_items)
        page = await capability.search(
            SearchQuery(text=query, page=PageRequest(limit=bounded_limit)), access
        )
        return ToolPreviewPage(
            items=tuple(
                ToolPreview(
                    ref=item.ref,
                    title=_bounded(item.title, budget.max_title_chars),
                    summary=_bounded(item.summary, budget.max_summary_chars),
                )
                for item in page.items[: budget.max_items]
            )
        )

    provider_id = manifest.provider_id.value
    return Tool(
        search,
        name=f"{provider_id}_search",
        # Description is generated only from validated ProviderId; untrusted
        # manifest display text and provider response text never become metadata.
        description=f"Search content from provider {provider_id}.",
    )


def _fetch_tool(
    manifest: ProviderManifest,
    fetcher: FetchCapability,
    projector: ProjectionCapability,
    access: AccessHandle,
    budget: ToolBudget,
) -> Tool[None]:
    async def fetch(content_kind: str, provider_content_id: str, canonical_url: str) -> ToolPreview:
        """Fetch bounded details for one canonical provider content reference."""

        ref = ContentRef(
            provider_id=manifest.provider_id,
            content_kind=ContentKind(value=content_kind),
            provider_content_id=provider_content_id,
            canonical_url=canonical_url,
        )
        preview = projector.preview(await fetcher.fetch(ref, access))
        return ToolPreview(
            ref=preview.ref,
            title=_bounded(preview.title, budget.max_title_chars),
            summary=_bounded(preview.summary, budget.max_summary_chars),
        )

    provider_id = manifest.provider_id.value
    return Tool(
        fetch,
        name=f"{provider_id}_fetch",
        description=f"Fetch bounded content details from provider {provider_id}.",
    )


def prepare_pending_action(
    manifest: ProviderManifest,
    descriptor: ActionDescriptor,
    *,
    provider_content_id: str,
    canonical_url: str,
    idempotency_key: str,
) -> PendingActionDescriptor:
    """Build, but never execute, a mutation for Application confirmation."""

    return PendingActionDescriptor(
        provider_id=manifest.provider_id,
        action_id=descriptor.action_id,
        ref=ContentRef(
            provider_id=manifest.provider_id,
            content_kind=descriptor.content_kind,
            provider_content_id=provider_content_id,
            canonical_url=canonical_url,
        ),
        idempotency_key=idempotency_key,
        confirmation_summary=f"{descriptor.label} this content?",
    )


def _pending_action_tool(manifest: ProviderManifest, descriptor: ActionDescriptor) -> Tool[None]:
    async def prepare_action(
        provider_content_id: str,
        canonical_url: str,
        idempotency_key: str,
    ) -> PendingActionDescriptor:
        """Prepare a provider action for explicit application confirmation."""

        return prepare_pending_action(
            manifest,
            descriptor,
            provider_content_id=provider_content_id,
            canonical_url=canonical_url,
            idempotency_key=idempotency_key,
        )

    provider_id = manifest.provider_id.value
    return Tool(
        prepare_action,
        name=f"{provider_id}_{descriptor.action_id}",
        description=(
            f"Prepare {descriptor.action_id} on provider {provider_id}; confirmation required."
        ),
    )


def build_provider_tools(
    manifest: ProviderManifest,
    implementation: object,
    access: AccessHandle,
    *,
    enabled: frozenset[CapabilityKind],
    budget: ToolBudget,
) -> tuple[Tool[None], ...]:
    """Expose only requested, advertised capabilities for this Assistant run."""

    if access.provider_id != manifest.provider_id.value:
        raise ContentIntegrationError(
            IntegrationErrorCode.ACCESS_DENIED,
            "access handle belongs to another provider",
        )
    selected = enabled & manifest.capabilities
    tools: list[Tool[None]] = []
    if CapabilityKind.SEARCH in selected:
        if not isinstance(implementation, SearchCapability):
            raise ContentIntegrationError(
                IntegrationErrorCode.UNAVAILABLE_CAPABILITY,
                "search capability is unavailable",
            )
        tools.append(_search_tool(manifest, implementation, access, budget))
    if CapabilityKind.FETCH in selected:
        if not isinstance(implementation, FetchCapability) or not isinstance(
            implementation, ProjectionCapability
        ):
            raise ContentIntegrationError(
                IntegrationErrorCode.UNAVAILABLE_CAPABILITY,
                "fetch projection capability is unavailable",
            )
        tools.append(_fetch_tool(manifest, implementation, implementation, access, budget))
    if CapabilityKind.ACTION in selected:
        tools.extend(_pending_action_tool(manifest, action) for action in manifest.actions)
    return tuple(tools)
