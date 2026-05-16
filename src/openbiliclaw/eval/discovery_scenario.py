"""Discovery evaluation scenario — mock Bilibili universe for offline eval.

Generates a simulated content universe per persona, including mock search results,
ranking pools, related video graphs, and behavioral event history.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryScenario:
    """A simulated Bilibili content universe for one persona."""

    persona_id: str = ""
    content_pool: list[dict[str, object]] = field(default_factory=list)
    relevance_labels: dict[str, float] = field(default_factory=dict)
    mock_search_index: dict[str, list[str]] = field(default_factory=dict)
    mock_ranking_pools: dict[int, list[str]] = field(default_factory=dict)
    mock_related_graph: dict[str, list[str]] = field(default_factory=dict)
    mock_event_history: list[dict[str, object]] = field(default_factory=list)

    def get_content_by_bvid(self, bvid: str) -> dict[str, object] | None:
        for item in self.content_pool:
            if item.get("bvid") == bvid:
                return item
        return None


# ---------------------------------------------------------------------------
# Mock clients implementing strategy protocols
# ---------------------------------------------------------------------------


class MockBilibiliClient:
    """Mock Bilibili API client backed by a DiscoveryScenario.

    Satisfies SupportsSearchClient, SupportsRankingClient, and
    SupportsRelatedClient protocols from strategies.py.
    """

    def __init__(self, scenario: DiscoveryScenario) -> None:
        self._scenario = scenario
        self._bvid_to_content: dict[str, dict[str, object]] = {
            str(item.get("bvid", "")): item for item in scenario.content_pool if item.get("bvid")
        }

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]:
        """Fuzzy keyword match against the content pool."""
        tokens = _tokenize(keyword)
        if not tokens:
            return []

        # Use pre-built index first
        indexed_bvids: set[str] = set()
        for token in tokens:
            for index_key, bvids in self._scenario.mock_search_index.items():
                if token in _tokenize(index_key):
                    indexed_bvids.update(bvids)

        # Also do fuzzy matching on titles/tags/description
        # Use both token-set matching AND substring matching for Chinese text
        scored: list[tuple[float, dict[str, object]]] = []
        for item in self._scenario.content_pool:
            bvid = str(item.get("bvid", ""))
            title = str(item.get("title", "")).lower()
            desc = str(item.get("description", "")).lower()
            tags = _object_list(item.get("tags"))
            tag_str = " ".join(str(t) for t in tags).lower()
            haystack = f"{title} {desc} {tag_str}"

            title_tokens = _tokenize(title)
            tag_tokens: set[str] = set()
            for tag in tags:
                tag_tokens.update(_tokenize(str(tag)))
            desc_tokens = _tokenize(desc)
            all_tokens = title_tokens | tag_tokens | desc_tokens

            # Token-level match (exact token equality)
            token_hits = sum(1 for t in tokens if t in all_tokens)
            # Substring match (handles Chinese where tokenization is imprecise)
            substr_hits = sum(1 for t in tokens if len(t) >= 2 and t in haystack)
            hit_count = max(token_hits, substr_hits)

            index_bonus = 0.5 if bvid in indexed_bvids else 0.0
            score = hit_count / len(tokens) + index_bonus if tokens else 0.0

            if score > 0.0:
                scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        start = (page - 1) * page_size
        end = start + page_size
        return [item for _, item in scored[start:end]]

    async def get_ranking(self, rid: int = 0) -> list[dict[str, object]]:
        """Return mock ranking pool for the given rid."""
        bvids = self._scenario.mock_ranking_pools.get(rid, [])
        results: list[dict[str, object]] = []
        for bvid in bvids:
            content = self._bvid_to_content.get(bvid)
            if content is not None:
                results.append(content)
        return results

    async def get_related_videos(self, bvid: str) -> list[dict[str, object]]:
        """Return mock related videos for a given bvid."""
        related_bvids = self._scenario.mock_related_graph.get(bvid, [])
        results: list[dict[str, object]] = []
        for related_bvid in related_bvids:
            content = self._bvid_to_content.get(related_bvid)
            if content is not None:
                results.append(content)
        return results


class MockMemoryManager:
    """Mock MemoryManager providing synthetic event history for seed selection."""

    def __init__(self, scenario: DiscoveryScenario) -> None:
        self._scenario = scenario

    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
        keyword: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        events = list(self._scenario.mock_event_history)
        if event_types:
            events = [e for e in events if str(e.get("event_type", "")) in event_types]
        return events[:limit]


# ---------------------------------------------------------------------------
# LLM protocol for scenario generation
# ---------------------------------------------------------------------------


class SupportsStructuredTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> object: ...


# ---------------------------------------------------------------------------
# Scenario generator
# ---------------------------------------------------------------------------


SCENARIO_GENERATION_PROMPT = """\
你要为一个模拟用户生成 B 站内容宇宙，用于评估内容发现系统的质量。

要求:
1. 生成 30 条模拟 B 站视频，每条包含: bvid, title, description, up_name, tags (list), \
duration (秒), view_count, like_count, rid (分区号)
2. bvid 格式: BV + 10位随机字母数字
3. 内容分布（重要！必须严格遵守）:
   - ~25% 高度相关 (贴合用户核心兴趣, relevance >= 0.8)
   - ~25% 中等相关 (相邻领域或部分匹配, relevance 0.4-0.7)
   - ~20% 跨域但合理 (与用户兴趣无直接关联，但心理需求/认知风格上说得通, relevance 0.3-0.6)
     例如：深度用户可能喜欢"建筑结构分析"即使从没看过建筑类内容
     这部分内容的 tags 和 title 不能包含用户已有兴趣的关键词
   - ~15% 低相关 (泛热门但不匹配, relevance 0.1-0.3)
   - ~15% 完全不相关 (噪音, relevance < 0.1)
4. 为每条视频标注 relevance (0.0-1.0)
5. 每条视频关联 3-5 条相关视频的 bvid（相关视频应来自不同 topic）
6. 生成 5-8 条模拟行为事件 (view/like/favorite)，每条包含 event_type, title, \
metadata: {bvid, up_name, duration, progress}
7. 常见 B 站分区 rid: 0(全站), 1(动画), 3(音乐), 4(游戏), 5(娱乐), \
17(知识), 36(科技), 119(鬼畜), 155(时尚), 160(生活), 181(影视), 188(数码)
8. ranking_pools 规则（重要！）:
   - rid=0 全站榜: 放 8-10 条来自至少 4 个不同 topic 的视频
   - 每个分区榜: 放 3-5 条，同一 topic 最多 2 条
   - 至少包含 3 个不同 rid 的分区榜
   - 分区榜内容要和该分区主题匹配（rid=36 科技放科技内容，rid=181 影视放影视内容）

输出严格 JSON:
{
  "content_pool": [...],
  "relevance_labels": {"BVxxxx": 0.85, ...},
  "related_graph": {"BVxxxx": ["BVyyy", ...], ...},
  "ranking_pools": {"0": ["BVxxxx", ...], "36": [...], "181": [...], ...},
  "events": [...]
}"""


class ScenarioGenerator:
    """Generates mock content universes for discovery evaluation."""

    def __init__(self, llm_service: SupportsStructuredTask) -> None:
        self._llm = llm_service

    async def generate(self, persona: OnionProfile) -> DiscoveryScenario:
        """Generate a complete DiscoveryScenario for the given persona."""
        persona_text = _persona_summary(persona)

        try:
            response = await self._llm.complete_structured_task(
                system_instruction=SCENARIO_GENERATION_PROMPT,
                user_input=f"用户画像:\n{persona_text}",
                temperature=0.8,
                max_tokens=16384,
                caller="eval.scenario_gen",
            )
            raw = str(getattr(response, "content", "")).strip()
            logger.info("Scenario LLM response length: %d chars", len(raw))
            parsed = _extract_json(raw)
        except Exception:
            logger.exception("Scenario generation failed")
            return DiscoveryScenario()

        if not isinstance(parsed, dict):
            logger.error("Scenario generation returned non-dict: %s", type(parsed).__name__)
            logger.error("Raw response (first 500 chars): %s", raw[:500] if raw else "(empty)")
            return DiscoveryScenario()

        return self._build_scenario(parsed, persona)

    def _build_scenario(
        self,
        data: dict[str, Any],
        persona: OnionProfile,
    ) -> DiscoveryScenario:
        content_pool = data.get("content_pool", [])
        if not isinstance(content_pool, list):
            content_pool = []

        # Normalize content pool
        normalized_pool: list[dict[str, object]] = []
        for item in content_pool:
            if not isinstance(item, dict) or not item.get("bvid"):
                continue
            normalized_pool.append(
                {
                    "bvid": str(item.get("bvid", "")),
                    "title": str(item.get("title", "")),
                    "description": str(item.get("description", item.get("desc", ""))),
                    "author": str(item.get("up_name", item.get("author", ""))),
                    "up_name": str(item.get("up_name", item.get("author", ""))),
                    "mid": int(item.get("mid", 0) or 0),
                    "tags": _object_list(item.get("tags")),
                    "duration": int(item.get("duration", 300) or 300),
                    "play": int(item.get("view_count", item.get("play", 1000)) or 1000),
                    "view_count": int(item.get("view_count", item.get("play", 1000)) or 1000),
                    "like": int(item.get("like_count", item.get("like", 100)) or 100),
                    "pic": str(item.get("pic", item.get("cover_url", ""))),
                    "rid": int(item.get("rid", 0) or 0),
                    "owner": {
                        "name": str(item.get("up_name", item.get("author", ""))),
                        "mid": int(item.get("mid", 0) or 0),
                    },
                    "stat": {
                        "view": int(item.get("view_count", item.get("play", 1000)) or 1000),
                        "like": int(item.get("like_count", item.get("like", 100)) or 100),
                    },
                    "desc": str(item.get("description", item.get("desc", ""))),
                }
            )

        # Relevance labels
        raw_labels = data.get("relevance_labels", {})
        labels: dict[str, float] = {}
        if isinstance(raw_labels, dict):
            for bvid, score in raw_labels.items():
                with suppress(ValueError, TypeError):
                    labels[str(bvid)] = max(0.0, min(1.0, float(score)))

        # Related graph
        raw_graph = data.get("related_graph", {})
        related_graph: dict[str, list[str]] = {}
        if isinstance(raw_graph, dict):
            for bvid, related in raw_graph.items():
                if isinstance(related, list):
                    related_graph[str(bvid)] = [str(r) for r in related]

        # Ranking pools — parse then enforce topic diversity
        raw_rankings = data.get("ranking_pools", {})
        ranking_pools: dict[int, list[str]] = {}
        if isinstance(raw_rankings, dict):
            for rid_str, bvids in raw_rankings.items():
                try:
                    rid = int(rid_str)
                except (ValueError, TypeError):
                    continue
                if isinstance(bvids, list):
                    ranking_pools[rid] = [str(b) for b in bvids]

        # Post-process: diversify ranking pools so no single topic dominates
        bvid_to_item = {str(item.get("bvid", "")): item for item in normalized_pool}
        ranking_pools = self._diversify_ranking_pools(
            ranking_pools,
            bvid_to_item,
            normalized_pool,
        )

        # Build search index from titles and tags
        search_index: dict[str, list[str]] = {}
        for item in normalized_pool:
            bvid = str(item["bvid"])
            index_keys: set[str] = set()
            title = str(item.get("title", ""))
            index_keys.add(title)
            for tag in _object_list(item.get("tags")):
                if tag:
                    index_keys.add(str(tag))
            for key in index_keys:
                search_index.setdefault(key, []).append(bvid)

        # Events
        raw_events = data.get("events", [])
        events: list[dict[str, object]] = []
        if isinstance(raw_events, list):
            for event in raw_events:
                if isinstance(event, dict):
                    events.append(event)

        persona_id = _persona_signature(persona)

        return DiscoveryScenario(
            persona_id=persona_id,
            content_pool=normalized_pool,
            relevance_labels=labels,
            mock_search_index=search_index,
            mock_ranking_pools=ranking_pools,
            mock_related_graph=related_graph,
            mock_event_history=events,
        )

    @staticmethod
    def _diversify_ranking_pools(
        ranking_pools: dict[int, list[str]],
        bvid_to_item: dict[str, dict[str, object]],
        all_items: list[dict[str, object]],
    ) -> dict[int, list[str]]:
        """Post-process ranking pools to ensure topic diversity.

        For each rid, limits any single topic to at most 2 entries,
        then backfills from the full content pool to reach target size.
        For rid=0 (global), ensures at least 4 distinct topic groups.
        """

        def _topic_of(bvid: str) -> str:
            item = bvid_to_item.get(bvid, {})
            tags = _object_list(item.get("tags"))
            if tags:
                return str(tags[0]).strip().lower()
            title = str(item.get("title", "")).strip().lower()
            return title[:8] if title else "unknown"

        result: dict[int, list[str]] = {}
        for rid, bvids in ranking_pools.items():
            target_size = max(len(bvids), 8 if rid == 0 else 4)
            max_per_topic = 2

            # Pass 1: select diverse subset
            selected: list[str] = []
            topic_counts: dict[str, int] = {}
            deferred: list[str] = []
            for bvid in bvids:
                topic = _topic_of(bvid)
                if topic_counts.get(topic, 0) >= max_per_topic:
                    deferred.append(bvid)
                    continue
                selected.append(bvid)
                topic_counts[topic] = topic_counts.get(topic, 0) + 1

            # Pass 2: backfill from full pool if under target
            if len(selected) < target_size:
                local_used = set(selected) | set(deferred)
                # Prefer items matching this rid
                candidates: list[str] = []
                for item in all_items:
                    bvid = str(item.get("bvid", ""))
                    if bvid in local_used:
                        continue
                    if rid != 0:
                        item_rid = _int_value(item.get("rid"), 0)
                        if item_rid != rid:
                            continue
                    candidates.append(bvid)
                for bvid in candidates:
                    topic = _topic_of(bvid)
                    if topic_counts.get(topic, 0) >= max_per_topic:
                        continue
                    selected.append(bvid)
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
                    if len(selected) >= target_size:
                        break

            # Pass 3: if still short, add from deferred
            for bvid in deferred:
                if len(selected) >= target_size:
                    break
                if bvid not in selected:
                    selected.append(bvid)

            # For rid=0, verify minimum topic diversity
            if rid == 0 and len(set(topic_counts.keys())) < 4:
                # Force-add items from under-represented topics
                selected_bvids = set(selected)
                for item in all_items:
                    bvid = str(item.get("bvid", ""))
                    if bvid in selected_bvids:
                        continue
                    topic = _topic_of(bvid)
                    if topic not in topic_counts:
                        selected.append(bvid)
                        topic_counts[topic] = 1
                        selected_bvids.add(bvid)
                    if len(set(topic_counts.keys())) >= 4:
                        break

            result[rid] = selected

        # Ensure rid=0 exists with broad coverage
        if 0 not in result:
            # Build from scratch: round-robin across all rids
            global_pool: list[str] = []
            used: set[str] = set()
            for rid_bvids in ranking_pools.values():
                for bvid in rid_bvids[:2]:
                    if bvid not in used:
                        global_pool.append(bvid)
                        used.add(bvid)
            # Fill from all items
            for item in all_items:
                bvid = str(item.get("bvid", ""))
                if bvid not in used:
                    global_pool.append(bvid)
                    used.add(bvid)
                if len(global_pool) >= 10:
                    break
            result[0] = global_pool

        return result


# ---------------------------------------------------------------------------
# Scenario pool (disk cache)
# ---------------------------------------------------------------------------


class ScenarioPool:
    """Cache generated scenarios to disk to avoid expensive regeneration."""

    def __init__(self, cache_dir: str | Path = "data/eval/scenario_pool") -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, scenario: DiscoveryScenario) -> Path:
        filename = f"{scenario.persona_id}.json"
        path = self._dir / filename
        data = {
            "persona_id": scenario.persona_id,
            "content_pool": scenario.content_pool,
            "relevance_labels": scenario.relevance_labels,
            "mock_search_index": scenario.mock_search_index,
            "mock_ranking_pools": {str(k): v for k, v in scenario.mock_ranking_pools.items()},
            "mock_related_graph": scenario.mock_related_graph,
            "mock_event_history": scenario.mock_event_history,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, persona_id: str) -> DiscoveryScenario | None:
        path = self._dir / f"{persona_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ranking_pools: dict[int, list[str]] = {}
            for k, v in data.get("mock_ranking_pools", {}).items():
                with suppress(ValueError, TypeError):
                    ranking_pools[int(k)] = v
            return DiscoveryScenario(
                persona_id=str(data.get("persona_id", persona_id)),
                content_pool=_dict_list(data.get("content_pool")),
                relevance_labels=_float_dict(data.get("relevance_labels")),
                mock_search_index=_list_dict(data.get("mock_search_index")),
                mock_ranking_pools=ranking_pools,
                mock_related_graph=_list_dict(data.get("mock_related_graph")),
                mock_event_history=_dict_list(data.get("mock_event_history")),
            )
        except Exception:
            logger.exception("Failed to load scenario: %s", path)
            return None

    def count(self) -> int:
        return len(list(self._dir.glob("*.json")))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Simple tokenization: split on whitespace and punctuation, lowercase."""
    cleaned = re.sub(
        r"[，。！？、：；\u201c\u201d\u2018\u2019（）【】\s]+", " ", text.lower().strip()
    )
    return {t for t in cleaned.split() if len(t) >= 1}


def _object_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _list_dict(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, raw_items in value.items():
        if isinstance(raw_items, list):
            result[str(key)] = [str(item) for item in raw_items]
    return result


def _float_dict(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw_score in value.items():
        try:
            result[str(key)] = float(raw_score)
        except (TypeError, ValueError):
            continue
    return result


def _int_value(value: object, default: int = 0) -> int:
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _persona_summary(persona: Any) -> str:
    """Extract compact text from persona for scenario generation prompt."""
    parts: list[str] = []
    portrait = getattr(persona, "personality_portrait", "")
    if portrait:
        parts.append(f"人格画像: {portrait[:500]}")

    traits = getattr(persona, "core_traits", [])
    if traits:
        parts.append(f"核心特质: {', '.join(str(t) for t in traits[:5])}")

    needs = getattr(persona, "deep_needs", [])
    if needs:
        parts.append(f"深层需求: {', '.join(str(n) for n in needs[:5])}")

    prefs = getattr(persona, "preferences", None)
    if prefs:
        interests = getattr(prefs, "interests", [])
        if interests:
            parts.append(
                "兴趣: "
                + ", ".join(
                    f"{getattr(i, 'name', '')}({getattr(i, 'weight', 0):.1f})"
                    for i in interests[:10]
                )
            )
        ups = getattr(prefs, "favorite_up_users", [])
        if ups:
            parts.append(f"喜欢的UP主: {', '.join(str(u) for u in ups[:5])}")
        openness = getattr(prefs, "exploration_openness", 0.5)
        parts.append(f"探索开放度: {openness}")
    else:
        # OnionProfile path
        interest = getattr(persona, "interest", None)
        if interest is not None:
            likes = getattr(interest, "likes", [])
            if likes:
                parts.append("兴趣: " + ", ".join(str(getattr(d, "name", "")) for d in likes[:10]))

    return "\n".join(parts) or "No profile available"


def _persona_signature(persona: Any) -> str:
    """Generate a stable hash-based signature for the persona."""
    key_parts: list[str] = []
    traits = getattr(persona, "core_traits", [])
    key_parts.extend(str(t) for t in traits[:3])
    prefs = getattr(persona, "preferences", None)
    if prefs:
        for i in getattr(prefs, "interests", [])[:3]:
            key_parts.append(str(getattr(i, "name", "")))

    raw = "|".join(key_parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract JSON from LLM response text with multiple fallback strategies."""
    # Strategy 1: code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    # Strategy 2: full text
    try:
        parsed = json.loads(text.strip())
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    # Strategy 3: find first { to last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        try:
            parsed = json.loads(text[first_brace : last_brace + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    return None
