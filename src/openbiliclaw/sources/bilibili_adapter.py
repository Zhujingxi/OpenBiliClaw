"""Bilibili source adapter — wraps the four existing discovery strategies."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent, DiscoveryStrategy
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)


class BilibiliAdapter:
    """Adapter that delegates to the existing Bilibili discovery strategies.

    This is a thin wrapper that makes the legacy strategy-based pipeline
    accessible through the unified :class:`SourceAdapter` interface without
    rewriting any strategy logic.
    """

    def __init__(
        self,
        *,
        search: DiscoveryStrategy | None = None,
        trending: DiscoveryStrategy | None = None,
        related_chain: DiscoveryStrategy | None = None,
        explore: DiscoveryStrategy | None = None,
    ) -> None:
        self._strategies: dict[str, DiscoveryStrategy] = {}
        if search is not None:
            self._strategies["search"] = search
        if trending is not None:
            self._strategies["trending"] = trending
        if related_chain is not None:
            self._strategies["related_chain"] = related_chain
        if explore is not None:
            self._strategies["explore"] = explore

    # ── SourceAdapter protocol ──────────────────────────────────────

    @property
    def source_type(self) -> str:
        return "bilibili"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Delegate to the strategy named by ``recipe.strategy``."""
        strategy = self._strategies.get(recipe.strategy)
        if strategy is None:
            logger.warning(
                "BilibiliAdapter: unknown strategy %r (available: %s)",
                recipe.strategy,
                list(self._strategies),
            )
            return []

        items = await strategy.discover(profile, limit=limit)

        # Ensure multi-source fields are populated for every item
        for item in items:
            if not item.source_platform:
                item.source_platform = "bilibili"
            if not item.content_id and item.bvid:
                item.content_id = item.bvid
            if not item.content_url and item.bvid:
                item.content_url = f"https://www.bilibili.com/video/{item.bvid}"
            if not item.author_name and item.up_name:
                item.author_name = item.up_name

        return items

    # ── Convenience helpers ─────────────────────────────────────────

    @property
    def available_strategies(self) -> list[str]:
        """Strategy names this adapter can handle."""
        return list(self._strategies)
