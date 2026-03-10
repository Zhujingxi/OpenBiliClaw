"""Content Discovery Engine.

Coordinates multiple discovery strategies to find content
that matches the user's soul profile.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


class SupportsStructuredTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> object: ...


@dataclass
class DiscoveredContent:
    """A piece of content discovered by the engine."""

    bvid: str = ""  # Bilibili video ID
    title: str = ""
    up_name: str = ""  # UP主 name
    up_mid: int = 0  # UP主 ID
    cover_url: str = ""
    duration: int = 0  # seconds
    view_count: int = 0
    like_count: int = 0
    tags: list[str] = field(default_factory=list)
    description: str = ""
    source_strategy: str = ""  # Which strategy found this
    relevance_score: float = 0.0  # 0.0 - 1.0 (based on user soul)
    relevance_reason: str = ""  # Why this is relevant to the user
    candidate_tier: str = "primary"  # Primary discovery vs backfill supply
    discovered_at: str = ""  # Cache timestamp for recency-aware ranking
    last_scored_at: str = ""  # Last relevance scoring timestamp


class DiscoveryStrategy(ABC):
    """Base class for content discovery strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        ...

    @abstractmethod
    async def discover(
        self, profile: SoulProfile, limit: int = 20
    ) -> list[DiscoveredContent]:
        """Execute the discovery strategy.

        Args:
            profile: Current user soul profile for relevance guidance.
            limit: Maximum number of items to return.

        Returns:
            List of discovered content items.
        """
        ...

    def create_backfill_strategy(self) -> DiscoveryStrategy | None:
        """Return an expanded/relaxed variant for supply backfill if supported."""
        return None


class ContentDiscoveryEngine:
    """Orchestrates multiple discovery strategies.

    Available strategies:
    - Search: keyword-based search from user interests
    - Related: follow related recommendation chains
    - Trending: scan trending/ranking content
    - Comments: mine recommendations from comment sections
    - UPTrack: track followed/discovered UP主
    - Explore: cross-domain surprise discovery
    """

    def __init__(
        self,
        llm_service: SupportsStructuredTask | None = None,
        database: Database | None = None,
        *,
        target_primary_count: int = 12,
        backfill_target_count: int = 18,
    ) -> None:
        self._strategies: list[DiscoveryStrategy] = []
        self._llm_service = llm_service
        self._database = database
        self._target_primary_count = max(1, target_primary_count)
        self._backfill_target_count = max(self._target_primary_count, backfill_target_count)

    def register_strategy(self, strategy: DiscoveryStrategy) -> None:
        """Register a discovery strategy."""
        self._strategies.append(strategy)
        logger.info("Registered discovery strategy: %s", strategy.name)

    async def discover(
        self,
        profile: SoulProfile,
        strategies: list[str] | None = None,
        limit: int = 30,
    ) -> list[DiscoveredContent]:
        """Run discovery with selected (or all) strategies.

        Args:
            profile: User soul profile for relevance evaluation.
            strategies: Optional list of strategy names to run.
                       If None, runs all registered strategies.

        Returns:
            Combined, deduplicated, and scored list of discovered content.
        """
        active = self._strategies
        if strategies:
            active = [s for s in self._strategies if s.name in strategies]

        if not active:
            return []

        effective_limit = max(1, min(limit, self._backfill_target_count))
        primary_results = await self._run_strategies(
            active,
            profile=profile,
            limit=effective_limit,
        )
        final_results = self._merge_and_rank(primary_results)[:effective_limit]

        primary_target = min(self._target_primary_count, effective_limit)
        if len(final_results) < primary_target:
            backfill_results = await self._run_backfill(
                active,
                profile=profile,
                limit=effective_limit,
                existing=final_results,
            )
            final_results = self._merge_and_rank(
                [*final_results, *backfill_results]
            )[:effective_limit]

        self._cache_results(final_results)
        return final_results

    async def evaluate_content(
        self, content: DiscoveredContent, profile: SoulProfile
    ) -> float:
        """Evaluate how relevant a piece of content is for the user.

        The core evaluation is based on the user's Soul — their deep personality
        and interests — not just surface-level metrics.

        Args:
            content: Content to evaluate.
            profile: User's soul profile.

        Returns:
            Relevance score (0.0 - 1.0).
        """
        if self._llm_service is None:
            return 0.0

        from openbiliclaw.llm.prompts import build_content_evaluation_prompt

        messages = build_content_evaluation_prompt(
            profile_summary={
                "personality_portrait": profile.personality_portrait,
                "core_traits": profile.core_traits[:5],
                "deep_needs": profile.deep_needs[:5],
                "interests": [
                    {
                        "name": item.name,
                        "category": item.category,
                        "weight": item.weight,
                    }
                    for item in profile.preferences.interests[:10]
                ],
            },
            content_summary={
                "title": content.title,
                "up_name": content.up_name,
                "description": content.description,
                "duration": content.duration,
                "view_count": content.view_count,
                "source_strategy": content.source_strategy,
            },
        )
        try:
            response = await self._llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
            )
            payload = json.loads(str(getattr(response, "content", "")).strip())
            if not isinstance(payload, dict):
                return 0.0
            score = self._clamp_score(payload.get("score", 0.0))
            reason = str(payload.get("reason", "")).strip()
        except Exception:
            logger.exception("Failed to evaluate discovered content: %s", content.bvid)
            return 0.0

        content.relevance_score = score
        content.relevance_reason = reason
        return score

    @staticmethod
    def _clamp_score(raw_value: object) -> float:
        if isinstance(raw_value, bool | int | float):
            value = float(raw_value)
        elif isinstance(raw_value, str):
            try:
                value = float(raw_value)
            except ValueError:
                value = 0.0
        else:
            value = 0.0
        return max(0.0, min(1.0, round(value, 4)))

    @staticmethod
    def _merge_duplicates(results: list[DiscoveredContent]) -> list[DiscoveredContent]:
        by_bvid: dict[str, DiscoveredContent] = {}
        for item in results:
            existing = by_bvid.get(item.bvid)
            if existing is None or item.relevance_score > existing.relevance_score:
                by_bvid[item.bvid] = item
        return list(by_bvid.values())

    async def _run_strategies(
        self,
        strategies: list[DiscoveryStrategy],
        *,
        profile: SoulProfile,
        limit: int,
    ) -> list[DiscoveredContent]:
        tasks = [strategy.discover(profile, limit=limit) for strategy in strategies]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[DiscoveredContent] = []
        for strategy, outcome in zip(strategies, gathered, strict=True):
            if isinstance(outcome, BaseException):
                logger.exception("Strategy '%s' failed.", strategy.name, exc_info=outcome)
                continue
            if not isinstance(outcome, list):
                logger.error(
                    "Strategy '%s' returned unexpected outcome type: %s",
                    strategy.name,
                    type(outcome).__name__,
                )
                continue
            items: list[DiscoveredContent] = outcome
            results.extend(items)
            logger.info("Strategy '%s' found %d items.", strategy.name, len(items))
        return results

    async def _run_backfill(
        self,
        strategies: list[DiscoveryStrategy],
        *,
        profile: SoulProfile,
        limit: int,
        existing: list[DiscoveredContent],
    ) -> list[DiscoveredContent]:
        remaining = limit - len(existing)
        if remaining <= 0:
            return []

        backfill_strategies: list[DiscoveryStrategy | None] = []
        for strategy in strategies:
            factory = getattr(strategy, "create_backfill_strategy", None)
            if not callable(factory):
                backfill_strategies.append(None)
                continue
            backfill_strategies.append(factory())
        active_backfill = [strategy for strategy in backfill_strategies if strategy is not None]
        results: list[DiscoveredContent] = []
        if active_backfill:
            results.extend(
                await self._run_strategies(
                    active_backfill,
                    profile=profile,
                    limit=remaining,
                )
            )

        merged = self._merge_and_rank([*existing, *results])[:limit]
        if len(merged) >= limit:
            return results

        results.extend(
            self._load_cached_backfill(
                limit=limit,
                exclude_bvids={item.bvid for item in merged},
            )
        )
        return results

    def _load_cached_backfill(
        self,
        *,
        limit: int,
        exclude_bvids: set[str],
    ) -> list[DiscoveredContent]:
        if self._database is None:
            return []

        rows = self._database.get_unrecommended_content(limit=limit)
        candidates: list[DiscoveredContent] = []
        for row in rows:
            bvid = str(row.get("bvid", "")).strip()
            if not bvid or bvid in exclude_bvids:
                continue
            candidates.append(
                DiscoveredContent(
                    bvid=bvid,
                    title=str(row.get("title", "")),
                    up_name=str(row.get("up_name", "")),
                    up_mid=int(row.get("up_mid", 0) or 0),
                    duration=int(row.get("duration", 0) or 0),
                    tags=[],
                    description=str(row.get("description", "")),
                    cover_url=str(row.get("cover_url", "")),
                    view_count=int(row.get("view_count", 0) or 0),
                    like_count=int(row.get("like_count", 0) or 0),
                    source_strategy=str(row.get("source", "")),
                    relevance_score=self._clamp_score(row.get("relevance_score", 0.0)),
                    relevance_reason=str(row.get("relevance_reason", "")),
                    candidate_tier="backfill",
                    discovered_at=str(row.get("discovered_at", "")),
                    last_scored_at=str(row.get("last_scored_at", "")),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    @staticmethod
    def _merge_and_rank(results: list[DiscoveredContent]) -> list[DiscoveredContent]:
        merged = ContentDiscoveryEngine._merge_duplicates(results)
        merged.sort(
            key=lambda item: (
                item.candidate_tier != "primary",
                -item.relevance_score,
                -item.view_count,
                item.bvid,
            )
        )
        return merged

    def _cache_results(self, results: list[DiscoveredContent]) -> None:
        if self._database is None or not results:
            return
        for item in results:
            try:
                self._database.cache_content(
                    item.bvid,
                    title=item.title,
                    up_name=item.up_name,
                    up_mid=item.up_mid,
                    duration=item.duration,
                    tags=item.tags,
                    description=item.description,
                    cover_url=item.cover_url,
                    view_count=item.view_count,
                    like_count=item.like_count,
                    relevance_score=item.relevance_score,
                    relevance_reason=item.relevance_reason,
                    candidate_tier=item.candidate_tier,
                    source=item.source_strategy,
                )
            except Exception:
                logger.exception("Failed to cache discovered content: %s", item.bvid)
