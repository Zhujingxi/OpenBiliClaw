"""Deterministic discovery planning and optional query generation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import ConfigDict, Field

from openbiliclaw.content.integration.identity import ProviderId  # noqa: TC001
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.understanding.projections import DiscoveryProfile  # noqa: TC001

from .query_agent import QueryBatch
from .strategies import StrategyConfig, strategy_query


class PlannedQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: ProviderId
    text: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=80)


QueryGenerator = Callable[[], Awaitable[QueryBatch]]


class DiscoveryPlanner:
    async def plan(
        self,
        profile: DiscoveryProfile,
        manifests: tuple[ProviderManifest, ...],
        *,
        inventory_count: int,
        target_inventory: int,
        provider_quota: int,
        generate: QueryGenerator | None = None,
    ) -> tuple[PlannedQuery, ...]:
        if target_inventory < 1 or provider_quota < 1:
            raise ValueError("inventory target and quota must be positive")
        available = tuple(
            m
            for m in manifests
            if m.availability is ProviderAvailability.AVAILABLE
            and CapabilityKind.SEARCH in m.capabilities
        )
        if inventory_count >= target_inventory or not available:
            return ()
        defaults = tuple(profile.interests[:5]) or ("high quality recent content",)
        texts = defaults
        if generate is not None:
            try:
                generated = await generate()
                supplied = tuple(
                    item.text.strip() for item in generated.suggestions if item.text.strip()
                )
                if supplied:
                    texts = supplied
            except Exception:
                pass
        seen: set[str] = set()
        unique: list[str] = []
        for text in texts:
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(text)
        result = []
        for manifest in sorted(available, key=lambda item: item.provider_id.value):
            for text in unique[:provider_quota]:
                result.append(PlannedQuery(provider_id=manifest.provider_id, text=text, topic=text))
        return tuple(result)

    def dispatch(
        self, strategies: tuple[StrategyConfig, ...], generated_query: str
    ) -> tuple[PlannedQuery, ...]:
        return tuple(
            PlannedQuery(
                provider_id=config.provider_id,
                text=strategy_query(config, generated_query),
                topic=config.kind.value,
            )
            for config in strategies
            for _index in range(config.quota)
        )
