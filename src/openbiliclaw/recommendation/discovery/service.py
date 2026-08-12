"""Direct capability discovery, without agent tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openbiliclaw.content.integration.capabilities import PageRequest, SearchCapability, SearchQuery

if TYPE_CHECKING:
    from openbiliclaw.access.models import AccessHandle
    from openbiliclaw.content.integration.identity import ProviderId
    from openbiliclaw.content.integration.projections import ContentPreview

    from .planner import PlannedQuery


class SearchResolver(Protocol):
    def __call__(self, provider_id: ProviderId) -> tuple[SearchCapability, AccessHandle]: ...


class DiscoveryService:
    def __init__(self, resolver: SearchResolver) -> None:
        self._resolver = resolver

    async def discover(
        self, plans: tuple[PlannedQuery, ...], *, limit: int = 20
    ) -> tuple[ContentPreview, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        result: list[ContentPreview] = []
        for plan in plans:
            capability, access = self._resolver(plan.provider_id)
            page = await capability.search(
                SearchQuery(text=plan.text, page=PageRequest(limit=limit)), access
            )
            result.extend(page.items)
        return tuple(result[:50])
