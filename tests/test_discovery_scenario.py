"""Tests for discovery scenario and mock clients."""

from __future__ import annotations

import tempfile

import pytest

from openbiliclaw.eval.discovery_scenario import (
    DiscoveryScenario,
    MockBilibiliClient,
    MockMemoryManager,
    ScenarioPool,
    _tokenize,
)


def _build_scenario() -> DiscoveryScenario:
    """Build a minimal scenario for testing."""
    return DiscoveryScenario(
        persona_id="test_persona_abc",
        content_pool=[
            {
                "bvid": "BV001",
                "title": "纪录片原理讲解",
                "description": "深度讲解纪录片制作原理",
                "author": "知识UP",
                "up_name": "知识UP",
                "mid": 1,
                "tags": ["纪录片", "知识"],
                "duration": 720,
                "play": 50000,
                "view_count": 50000,
                "like": 3000,
                "pic": "",
                "rid": 36,
                "owner": {"name": "知识UP", "mid": 1},
                "stat": {"view": 50000, "like": 3000},
                "desc": "深度讲解纪录片制作原理",
            },
            {
                "bvid": "BV002",
                "title": "摄影构图技巧入门",
                "description": "摄影入门教程",
                "author": "摄影UP",
                "up_name": "摄影UP",
                "mid": 2,
                "tags": ["摄影", "教程"],
                "duration": 480,
                "play": 30000,
                "view_count": 30000,
                "like": 2000,
                "pic": "",
                "rid": 160,
                "owner": {"name": "摄影UP", "mid": 2},
                "stat": {"view": 30000, "like": 2000},
                "desc": "摄影入门教程",
            },
            {
                "bvid": "BV003",
                "title": "今日热门游戏速报",
                "description": "游戏新闻汇总",
                "author": "游戏UP",
                "up_name": "游戏UP",
                "mid": 3,
                "tags": ["游戏", "速报"],
                "duration": 300,
                "play": 100000,
                "view_count": 100000,
                "like": 8000,
                "pic": "",
                "rid": 4,
                "owner": {"name": "游戏UP", "mid": 3},
                "stat": {"view": 100000, "like": 8000},
                "desc": "游戏新闻汇总",
            },
        ],
        relevance_labels={"BV001": 0.9, "BV002": 0.7, "BV003": 0.1},
        mock_search_index={
            "纪录片原理讲解": ["BV001"],
            "纪录片": ["BV001"],
            "摄影构图技巧入门": ["BV002"],
            "摄影": ["BV002"],
            "游戏": ["BV003"],
        },
        mock_ranking_pools={
            0: ["BV001", "BV002", "BV003"],
            36: ["BV001"],
            160: ["BV002"],
            4: ["BV003"],
        },
        mock_related_graph={
            "BV001": ["BV002"],
            "BV002": ["BV001", "BV003"],
            "BV003": [],
        },
        mock_event_history=[
            {
                "event_type": "view",
                "title": "纪录片原理讲解",
                "metadata": {
                    "bvid": "BV001",
                    "up_name": "知识UP",
                    "duration": 720,
                    "progress": 0.9,
                },
            },
            {
                "event_type": "like",
                "title": "摄影构图技巧入门",
                "metadata": {"bvid": "BV002", "up_name": "摄影UP"},
            },
        ],
    )


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def test_tokenize_basic() -> None:
    tokens = _tokenize("纪录片 原理")
    assert "纪录片" in tokens
    assert "原理" in tokens


def test_tokenize_chinese_punctuation() -> None:
    tokens = _tokenize("纪录片，原理！讲解")
    assert len(tokens) >= 2


def test_tokenize_empty() -> None:
    assert _tokenize("") == set()


# ---------------------------------------------------------------------------
# MockBilibiliClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_search_exact_match() -> None:
    scenario = _build_scenario()
    client = MockBilibiliClient(scenario)

    results = await client.search("纪录片")

    assert len(results) >= 1
    bvids = [str(r.get("bvid", "")) for r in results]
    assert "BV001" in bvids


@pytest.mark.asyncio
async def test_mock_search_no_match() -> None:
    scenario = _build_scenario()
    client = MockBilibiliClient(scenario)

    results = await client.search("量子力学太空探索")

    assert len(results) == 0


@pytest.mark.asyncio
async def test_mock_search_fuzzy_match() -> None:
    scenario = _build_scenario()
    client = MockBilibiliClient(scenario)

    results = await client.search("纪录片 原理")

    assert len(results) >= 1
    bvids = [str(r.get("bvid", "")) for r in results]
    assert "BV001" in bvids


@pytest.mark.asyncio
async def test_mock_search_pagination() -> None:
    scenario = _build_scenario()
    client = MockBilibiliClient(scenario)

    page1 = await client.search("纪录片", page=1, page_size=1)
    await client.search("纪录片", page=2, page_size=1)

    assert len(page1) <= 1
    # page2 may be empty if only 1 result


@pytest.mark.asyncio
async def test_mock_ranking() -> None:
    scenario = _build_scenario()
    client = MockBilibiliClient(scenario)

    results = await client.get_ranking(0)
    assert len(results) == 3

    results_36 = await client.get_ranking(36)
    assert len(results_36) == 1
    assert results_36[0].get("bvid") == "BV001"


@pytest.mark.asyncio
async def test_mock_ranking_unknown_rid() -> None:
    scenario = _build_scenario()
    client = MockBilibiliClient(scenario)

    results = await client.get_ranking(999)
    assert results == []


@pytest.mark.asyncio
async def test_mock_related_videos() -> None:
    scenario = _build_scenario()
    client = MockBilibiliClient(scenario)

    results = await client.get_related_videos("BV001")
    assert len(results) == 1
    assert results[0].get("bvid") == "BV002"

    results_3 = await client.get_related_videos("BV003")
    assert results_3 == []


@pytest.mark.asyncio
async def test_mock_related_unknown_bvid() -> None:
    scenario = _build_scenario()
    client = MockBilibiliClient(scenario)

    results = await client.get_related_videos("BV_NONEXISTENT")
    assert results == []


# ---------------------------------------------------------------------------
# MockMemoryManager
# ---------------------------------------------------------------------------


def test_mock_memory_query_all() -> None:
    scenario = _build_scenario()
    memory = MockMemoryManager(scenario)

    events = memory.query_events(limit=100)
    assert len(events) == 2


def test_mock_memory_filter_event_types() -> None:
    scenario = _build_scenario()
    memory = MockMemoryManager(scenario)

    events = memory.query_events(event_types=["view"])
    assert len(events) == 1
    assert events[0]["event_type"] == "view"


def test_mock_memory_limit() -> None:
    scenario = _build_scenario()
    memory = MockMemoryManager(scenario)

    events = memory.query_events(limit=1)
    assert len(events) == 1


# ---------------------------------------------------------------------------
# ScenarioPool
# ---------------------------------------------------------------------------


def test_scenario_pool_save_and_load() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pool = ScenarioPool(tmpdir)
        scenario = _build_scenario()

        pool.save(scenario)
        assert pool.count() == 1

        loaded = pool.load("test_persona_abc")
        assert loaded is not None
        assert loaded.persona_id == "test_persona_abc"
        assert len(loaded.content_pool) == 3
        assert loaded.relevance_labels.get("BV001") == 0.9
        assert 0 in loaded.mock_ranking_pools
        assert "BV001" in loaded.mock_related_graph


def test_scenario_pool_load_nonexistent() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pool = ScenarioPool(tmpdir)
        assert pool.load("nonexistent") is None


def test_scenario_pool_count_empty() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pool = ScenarioPool(tmpdir)
        assert pool.count() == 0


# ---------------------------------------------------------------------------
# DiscoveryScenario
# ---------------------------------------------------------------------------


def test_scenario_get_content_by_bvid() -> None:
    scenario = _build_scenario()

    content = scenario.get_content_by_bvid("BV001")
    assert content is not None
    assert content["title"] == "纪录片原理讲解"

    assert scenario.get_content_by_bvid("BV_NONEXISTENT") is None
