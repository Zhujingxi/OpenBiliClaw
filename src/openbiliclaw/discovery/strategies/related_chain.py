"""Related-chain content discovery strategy."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    DiscoveryStrategy,
    SupportsStructuredTask,
)
from openbiliclaw.discovery.strategies._utils import (
    SupportsMemoryManager,
    SupportsRelatedClient,
    SupportsSeedStrategy,
    _gather_bounded,
    clean_text,
    parse_duration,
    to_int,
)

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile

logger = logging.getLogger(__name__)


@dataclass
class RelatedChainStrategy(DiscoveryStrategy):
    """Discover content by following related recommendation chains."""

    bilibili_client: SupportsRelatedClient
    llm_service: SupportsStructuredTask
    memory_manager: SupportsMemoryManager
    search_strategy: SupportsSeedStrategy | None = None
    trending_strategy: SupportsSeedStrategy | None = None
    concurrency: DiscoveryConcurrencyController | None = None
    score_threshold: float = 0.65
    max_seeds: int = 5
    related_per_seed: int = 8
    max_depth: int = 2
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "related_chain"

    def create_backfill_strategy(self) -> DiscoveryStrategy | None:
        if self.score_threshold <= 0.58:
            return None
        return replace(
            self,
            score_threshold=max(0.58, round(self.score_threshold - 0.07, 2)),
            related_per_seed=max(self.related_per_seed, 10),
            last_intermediates={},
        )

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        """Start from known good content and explore related chains.

        Args:
            profile: User soul profile.
            limit: Maximum results.

        Returns:
            Discovered content list.
        """
        evaluator = ContentDiscoveryEngine(
            llm_service=self.llm_service,
            concurrency=self.concurrency,
        )
        seed_descriptors = await self._select_seed_descriptors(profile)
        self.last_intermediates = {
            "seeds": [(bvid, topic) for bvid, topic in seed_descriptors],
        }
        if not seed_descriptors:
            return []

        results: list[DiscoveredContent] = []
        seen_bvids = {seed_bvid for seed_bvid, _ in seed_descriptors}
        visited_source_bvids: set[str] = set()

        # Layer-parallel BFS: process each depth level concurrently
        current_layer: list[tuple[str, int, int, str]] = [
            (seed_bvid, 1, seed_index, topic_key)
            for seed_index, (seed_bvid, topic_key) in enumerate(seed_descriptors)
        ]

        runner = self.concurrency.run_bilibili if self.concurrency is not None else None

        for _depth_round in range(self.max_depth):
            if not current_layer or len(results) >= limit:
                break

            # Dedupe and filter visited within layer
            layer_items: list[tuple[str, int, int, str]] = []
            for item in current_layer:
                if item[0] not in visited_source_bvids:
                    visited_source_bvids.add(item[0])
                    layer_items.append(item)

            if not layer_items:
                break

            # Fetch related videos for entire layer concurrently
            related_outcomes = await _gather_bounded(
                [self.bilibili_client.get_related_videos(bvid) for bvid, _, _, _ in layer_items],
                runner=runner,
            )

            # Collect candidates from all results
            batch_candidates: list[tuple[DiscoveredContent, int, int, str]] = []
            for (seed_bvid, depth, seed_index, seed_topic_key), outcome in zip(
                layer_items, related_outcomes, strict=True,
            ):
                if isinstance(outcome, BaseException):
                    logger.exception("Related videos request failed: %s", seed_bvid, exc_info=outcome)
                    continue
                if not isinstance(outcome, list):
                    continue
                for item in outcome[: self.related_per_seed]:
                    content = self._map_related_item(item, seed_topic_key=seed_topic_key)
                    if content is None or content.bvid in seen_bvids:
                        continue
                    seen_bvids.add(content.bvid)
                    batch_candidates.append((content, depth, seed_index, seed_topic_key))

            # Evaluate all candidates concurrently
            contents = [c for c, _, _, _ in batch_candidates]
            scores = await asyncio.gather(
                *(evaluator.evaluate_content(content, profile) for content in contents)
            )

            next_layer: list[tuple[str, int, int, str]] = []
            for (content, depth, seed_index, seed_topic_key), score in zip(
                batch_candidates, scores, strict=True,
            ):
                bonus = self._seed_bonus(seed_index) + self._depth_bonus(depth)
                content.relevance_score = min(1.0, round(score + bonus, 4))
                if content.relevance_score < self.score_threshold:
                    continue
                results.append(content)
                if depth < self.max_depth:
                    next_layer.append((content.bvid, depth + 1, seed_index, seed_topic_key))
                if len(results) >= limit:
                    break

            current_layer = next_layer

        results.sort(key=lambda item: item.relevance_score, reverse=True)
        return results

    async def _select_seed_descriptors(self, profile: SoulProfile) -> list[tuple[str, str]]:
        seeds: list[tuple[str, str]] = []
        seen: set[str] = set()

        for bvid in self._event_seed_bvids():
            if bvid in seen:
                continue
            seen.add(bvid)
            seeds.append((bvid, self._topic_key_from_seed_bvid(bvid)))
            if len(seeds) >= self.max_seeds:
                return seeds

        for bvid in await self._preference_seed_bvids(profile):
            if bvid in seen:
                continue
            seen.add(bvid)
            seeds.append((bvid, self._topic_key_from_seed_bvid(bvid)))
            if len(seeds) >= self.max_seeds:
                return seeds

        for strategy in (self.search_strategy, self.trending_strategy):
            if strategy is None:
                continue
            remaining = self.max_seeds - len(seeds)
            if remaining <= 0:
                break
            try:
                items = await strategy.discover(profile, limit=remaining)
            except Exception:
                logger.exception(
                    "Fallback seed strategy failed: %s",
                    getattr(strategy, "name", "unknown"),
                )
                continue
            for item in items:
                if item.bvid in seen or not item.bvid:
                    continue
                seen.add(item.bvid)
                seeds.append((item.bvid, self._topic_key_from_seed_bvid(item.bvid)))
                if len(seeds) >= self.max_seeds:
                    return seeds

        return seeds

    def _event_seed_bvids(self) -> list[str]:
        events = self.memory_manager.query_events(
            event_types=["view", "favorite", "like"],
            limit=max(self.max_seeds * 3, 12),
        )
        seed_bvids: list[str] = []
        for event in events:
            bvid = self._extract_bvid_from_event(event)
            if bvid:
                seed_bvids.append(bvid)
        return seed_bvids

    async def _preference_seed_bvids(self, profile: SoulProfile) -> list[str]:
        queries: list[str] = []
        queries.extend(
            interest_item.name.strip()
            for interest_item in profile.preferences.interests[:2]
            if interest_item.name.strip()
        )
        queries.extend(
            up_name.strip()
            for up_name in profile.preferences.favorite_up_users[:1]
            if up_name.strip()
        )

        seeds: list[str] = []
        seen: set[str] = set()
        for query in queries:
            try:
                items = await self.bilibili_client.search(query, page=1, page_size=2)
            except Exception:
                logger.exception("Preference seed search failed: %s", query)
                continue
            for item in items:
                bvid = str(item.get("bvid", "")).strip()
                if not bvid or bvid in seen:
                    continue
                seen.add(bvid)
                seeds.append(bvid)
                if len(seeds) >= self.max_seeds:
                    return seeds
        return seeds

    @staticmethod
    def _extract_bvid_from_event(event: dict[str, object]) -> str:
        metadata = event.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if isinstance(metadata, dict):
            bvid = str(metadata.get("bvid", "")).strip()
            if bvid:
                return bvid

        url = str(event.get("url", "")).strip()
        match = re.search(r"/video/(BV[\w]+)", url)
        return match.group(1) if match else ""

    def _map_related_item(
        self,
        item: dict[str, object],
        *,
        seed_topic_key: str,
    ) -> DiscoveredContent | None:
        bvid = str(item.get("bvid", "")).strip()
        if not bvid:
            return None
        owner = item.get("owner")
        up_name = ""
        up_mid = 0
        if isinstance(owner, dict):
            up_name = clean_text(str(owner.get("name", "")))
            up_mid = to_int(owner.get("mid", 0))

        stat = item.get("stat")
        view_count = 0
        like_count = 0
        if isinstance(stat, dict):
            view_count = to_int(stat.get("view", 0))
            like_count = to_int(stat.get("like", 0))

        title_text = clean_text(str(item.get("title", "")))
        return DiscoveredContent(
            bvid=bvid,
            title=title_text,
            up_name=up_name,
            up_mid=up_mid,
            cover_url=str(item.get("pic", "")),
            duration=parse_duration(item.get("duration", 0)),
            view_count=view_count,
            like_count=like_count,
            topic_key=seed_topic_key,
            topic_group=self._topic_group_from_title(title_text),
            description=clean_text(
                str(item.get("desc", item.get("description", "")))
            ),
            style_key=ContentDiscoveryEngine.infer_style_key(
                title=title_text,
                description=clean_text(
                    str(item.get("desc", item.get("description", "")))
                ),
                source_strategy=self.name,
            ),
            source_strategy=self.name,
        )

    @staticmethod
    def _topic_key_from_seed_bvid(seed_bvid: str) -> str:
        return f"related:{seed_bvid.strip().lower()}"

    @staticmethod
    def _topic_group_from_title(title: str) -> str:
        """Extract a coarse topic group from title for diversity bucketing."""
        cleaned = re.sub(r"[【】\[\]《》「」\s]+", " ", title).strip()
        parts = cleaned.split()
        if parts:
            return re.sub(r"\s+", "", parts[0]).lower()[:8]
        return ""

    @staticmethod
    def _seed_bonus(seed_index: int) -> float:
        return max(0.0, 0.03 - seed_index * 0.01)

    @staticmethod
    def _depth_bonus(depth: int) -> float:
        return max(0.0, 0.02 - max(0, depth - 1) * 0.01)
