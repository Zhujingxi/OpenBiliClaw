"""Tests for discovery inspiration probes and lateral expansion helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.discovery import inspiration as inspiration_module
from openbiliclaw.discovery.inspiration import (
    AllocationTarget,
    ExaPreviewItem,
    InspirationSeed,
    MaterializeCandidate,
    SecondaryInterest,
    build_grounding_probes,
    build_like_secondary_interest_window,
    derive_inspiration_seeds,
    materialize_platform_keywords,
    platform_style_score,
)
from openbiliclaw.soul.profile import (
    InterestDomain,
    InterestLayer,
    InterestSpecific,
    InterestTag,
    OnionProfile,
    PreferenceLayer,
    SoulProfile,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


_BILI = "bilibili"
_DIGEST = "digest-rich-profile"
_AXIS_NOW = "2026-07-05T12:00:00Z"
_AXIS_LATER = "2026-07-06T12:00:00Z"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    d.initialize()
    return d


def _axis_row(
    axis_label: str,
    *,
    interest_label: str = "游戏评价",
    axis_kind: str = "subgenre",
    source: str = "external_search",
    axis_id: str = "",
    interest_id: str = "",
    example_terms: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    time_sensitive: bool = False,
    freshness_ttl_days: int | None = None,
    yield_score: float = 0.0,
    admissions: int = 0,
    use_count: int = 0,
    status: str = "active",
    created_at: str = _AXIS_NOW,
    last_used_at: str | None = None,
    last_refreshed_at: str = _AXIS_NOW,
) -> object:
    return inspiration_module.AxisRow(
        interest_label=interest_label,
        interest_id=interest_id,
        axis_label=axis_label,
        axis_kind=axis_kind,
        example_terms=example_terms,
        evidence_refs=evidence_refs,
        source=source,
        time_sensitive=time_sensitive,
        freshness_ttl_days=freshness_ttl_days,
        yield_score=yield_score,
        admissions=admissions,
        use_count=use_count,
        status=status,
        created_at=created_at,
        last_used_at=last_used_at,
        last_refreshed_at=last_refreshed_at,
        axis_id=axis_id,
    )


def test_build_grounding_probes_orders_axis_and_example_terms() -> None:
    probes = build_grounding_probes(
        [
            SecondaryInterest(
                interest_id="interest:game-review",
                label="游戏评价",
                parent="游戏",
                weight=0.9,
            )
        ],
        [
            inspiration_module.AxisRow(
                interest_label="游戏评价",
                axis_label="机制拆解",
                axis_kind="method",
                source="test",
                example_terms=("设计理念", "耐玩"),
            ),
            inspiration_module.AxisRow(
                interest_label="游戏评价",
                axis_label="冷门佳作",
                axis_kind="anchor",
                source="test",
                example_terms=("宝藏",),
            ),
        ],
        {},
        limit=5,
    )

    assert [probe.probe_queries[0] for probe in probes] == [
        "游戏评价",
        "游戏评价 机制拆解",
        "游戏评价 设计理念",
        "游戏评价 耐玩",
        "游戏评价 冷门佳作",
    ]


def test_build_grounding_probes_dedupes_and_caps_terms() -> None:
    probes = build_grounding_probes(
        [
            SecondaryInterest(
                interest_id="interest:game-review",
                label="游戏评价",
                parent="游戏",
                weight=0.9,
            )
        ],
        [
            inspiration_module.AxisRow(
                interest_label="游戏评价",
                axis_label="机制拆解",
                axis_kind="method",
                source="test",
                example_terms=("机制拆解", "设计理念"),
            )
        ],
        {"游戏评价": ("设计理念", "社区黑话")},
        limit=4,
    )

    assert [probe.probe_queries[0] for probe in probes] == [
        "游戏评价",
        "游戏评价 机制拆解",
        "游戏评价 设计理念",
        "游戏评价 社区黑话",
    ]


def test_build_grounding_probes_cold_start_keeps_plain_interest_labels() -> None:
    probes = build_grounding_probes(
        [
            SecondaryInterest(interest_id="interest:a", label="兴趣A", weight=0.9),
            SecondaryInterest(interest_id="interest:b", label="兴趣B", weight=0.8),
        ],
        [],
        {},
        limit=3,
    )

    assert [probe.probe_queries[0] for probe in probes] == ["兴趣A", "兴趣B"]


def test_like_secondary_interest_window_prefers_positive_and_downweights_covered() -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(
                    name="Switch 独立游戏",
                    category="游戏",
                    weight=0.95,
                    source="like",
                ),
                InterestTag(
                    name="王者荣耀匹配机制",
                    category="游戏",
                    weight=0.9,
                    source="accepted",
                ),
                InterestTag(name="AI 工具实测", category="科技", weight=0.82, source="profile"),
            ],
            disliked_topics=["AI 焦虑贩卖"],
        )
    )
    snapshot = {
        "Switch 独立游戏": {"generated_keyword_count": 30, "admitted_count": 20},
        "王者荣耀匹配机制": {"generated_keyword_count": 0, "admitted_count": 0},
    }

    interests = build_like_secondary_interest_window(
        profile,
        coverage_snapshot=snapshot,
        max_interests=2,
    )

    assert [item.label for item in interests] == ["王者荣耀匹配机制", "AI 工具实测"]
    assert all("AI 焦虑贩卖" not in item.label for item in interests)


def test_like_secondary_interest_window_uses_onion_like_specifics_before_domains() -> None:
    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="游戏",
                    weight=0.95,
                    source="watch history",
                    specifics=[
                        InterestSpecific(name="王者荣耀", weight=0.75),
                        InterestSpecific(name="任天堂Switch", weight=0.65),
                    ],
                ),
                InterestDomain(domain="萌宠", weight=0.6, source="watch history"),
            ],
        )
    )

    interests = build_like_secondary_interest_window(profile, max_interests=3)

    labels = [item.label for item in interests]
    assert "游戏" not in labels
    assert set(labels) == {"王者荣耀", "任天堂Switch", "萌宠"}
    assert {item.label: item.parent for item in interests} == {
        "王者荣耀": "游戏",
        "任天堂Switch": "游戏",
        "萌宠": "萌宠",
    }
    assert next(item for item in interests if item.label == "萌宠").low_specificity is True


def test_like_secondary_interest_window_spreads_initial_window_across_parents() -> None:
    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="游戏",
                    weight=0.95,
                    specifics=[
                        InterestSpecific(name="王者荣耀", weight=0.8),
                        InterestSpecific(name="任天堂Switch", weight=0.78),
                    ],
                ),
                InterestDomain(
                    domain="动漫",
                    weight=0.9,
                    specifics=[InterestSpecific(name="动漫新番", weight=0.4)],
                ),
                InterestDomain(
                    domain="科技",
                    weight=0.85,
                    specifics=[InterestSpecific(name="AI工具", weight=0.7)],
                ),
                InterestDomain(
                    domain="美食",
                    weight=0.8,
                    specifics=[InterestSpecific(name="美食探店", weight=0.75)],
                ),
            ],
        )
    )

    interests = build_like_secondary_interest_window(profile, max_interests=4)

    assert [item.parent for item in interests] == ["游戏", "科技", "美食", "动漫"]
    assert [item.label for item in interests] == ["王者荣耀", "AI工具", "美食探店", "动漫新番"]


def test_like_secondary_interest_window_penalizes_overcovered_candidate_pool() -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="A 高覆盖兴趣", category="测试", weight=0.8, source="like"),
                InterestTag(name="B 空白兴趣", category="测试", weight=0.8, source="like"),
            ],
        )
    )
    snapshot = {
        "A 高覆盖兴趣": {
            "candidate_count": 120,
            "candidate_share": 0.45,
            "dominant_candidate_content_type_share": 0.95,
        }
    }

    interests = build_like_secondary_interest_window(
        profile,
        coverage_snapshot=snapshot,
        max_interests=1,
    )

    assert [item.label for item in interests] == ["B 空白兴趣"]


def test_inspiration_cache_tables_are_created(db: Database) -> None:
    names = {
        str(row["name"])
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    assert "discovery_inspiration_probe_cache" in names
    assert "discovery_inspiration_expansion_cache" in names


def test_inspiration_axis_table_schema_and_interest_index_created(db: Database) -> None:
    columns = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA table_info(discovery_inspiration_axis)").fetchall()
    }
    indexes = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA index_list(discovery_inspiration_axis)").fetchall()
    }

    assert columns == {
        "axis_id",
        "interest_label",
        "interest_id",
        "axis_label",
        "axis_kind",
        "example_terms",
        "evidence_refs",
        "source",
        "time_sensitive",
        "freshness_ttl_days",
        "yield_score",
        "admissions",
        "use_count",
        "status",
        "created_at",
        "last_used_at",
        "last_refreshed_at",
    }
    assert "idx_discovery_inspiration_axis_interest" in indexes


def test_axis_row_derives_stable_axis_id_from_normalized_axis_label() -> None:
    first = _axis_row("  Designer／Lens!! ")
    second = _axis_row("designer lens")
    different_interest = _axis_row("designer lens", interest_label="科技新闻")

    assert first.axis_id.startswith("axis:")
    assert first.axis_id == second.axis_id
    assert first.axis_id != different_interest.axis_id


def test_upsert_inspiration_axes_merges_evidence_and_respects_preview_usage(
    db: Database,
) -> None:
    inserted = _axis_row(
        "设计师视角",
        axis_kind="creator_lens",
        interest_id="interest:game-review",
        example_terms=("拆解",),
        evidence_refs=("https://example.test/a",),
        last_used_at=_AXIS_NOW,
    )

    db.upsert_inspiration_axes([inserted])
    row = db.conn.execute(
        "SELECT * FROM discovery_inspiration_axis WHERE axis_id = ?",
        (inserted.axis_id,),
    ).fetchone()
    assert row is not None
    assert int(row["use_count"]) == 1
    assert str(row["last_used_at"]) == _AXIS_NOW

    preview_refresh = _axis_row(
        "设计师视角",
        axis_id=inserted.axis_id,
        axis_kind="creator_lens",
        example_terms=("设计理念",),
        evidence_refs=("https://example.test/b",),
        created_at=_AXIS_LATER,
        last_used_at=_AXIS_LATER,
        last_refreshed_at=_AXIS_LATER,
    )
    db.upsert_inspiration_axes([preview_refresh], bump_usage=False)

    row = db.conn.execute(
        "SELECT * FROM discovery_inspiration_axis WHERE axis_id = ?",
        (inserted.axis_id,),
    ).fetchone()
    assert row is not None
    assert int(row["use_count"]) == 1
    assert str(row["last_used_at"]) == _AXIS_NOW
    assert str(row["last_refreshed_at"]) == _AXIS_LATER
    assert json.loads(str(row["evidence_refs"])) == [
        "https://example.test/a",
        "https://example.test/b",
    ]

    production_refresh = _axis_row(
        "设计师视角",
        axis_id=inserted.axis_id,
        axis_kind="creator_lens",
        evidence_refs=("https://example.test/c",),
        created_at="2026-07-07T12:00:00Z",
        last_used_at="2026-07-07T12:00:00Z",
        last_refreshed_at="2026-07-07T12:00:00Z",
    )
    db.upsert_inspiration_axes([production_refresh])

    row = db.conn.execute(
        "SELECT * FROM discovery_inspiration_axis WHERE axis_id = ?",
        (inserted.axis_id,),
    ).fetchone()
    assert row is not None
    assert int(row["use_count"]) == 2
    assert str(row["last_used_at"]) == "2026-07-07T12:00:00Z"
    assert json.loads(str(row["evidence_refs"])) == [
        "https://example.test/a",
        "https://example.test/b",
        "https://example.test/c",
    ]


def test_upsert_inspiration_axes_marks_lowest_ranked_overflow_stale(db: Database) -> None:
    axes = [
        _axis_row(
            f"轴 {index:02d}",
            created_at=f"2026-07-{index + 1:02d}T00:00:00Z",
            last_refreshed_at=f"2026-07-{index + 1:02d}T00:00:00Z",
        )
        for index in range(17)
    ]

    db.upsert_inspiration_axes(axes, bump_usage=False)

    rows = db.conn.execute(
        """
        SELECT axis_label, status
        FROM discovery_inspiration_axis
        WHERE interest_label = ?
        ORDER BY axis_label ASC
        """,
        ("游戏评价",),
    ).fetchall()
    active_labels = [str(row["axis_label"]) for row in rows if str(row["status"]) == "active"]
    stale_labels = [str(row["axis_label"]) for row in rows if str(row["status"]) == "stale"]

    assert len(active_labels) == 16
    assert stale_labels == ["轴 00"]


def test_list_inspiration_axes_filters_and_orders_with_zero_yield_prior(
    db: Database,
) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    db.upsert_inspiration_axes(
        [
            _axis_row(
                "新鲜-低使用-subgenre",
                axis_kind="subgenre",
                last_refreshed_at="2026-07-04T12:00:00Z",
                use_count=1,
            ),
            _axis_row(
                "新鲜-低使用-event",
                axis_kind="event",
                last_refreshed_at="2026-07-04T12:00:00Z",
                use_count=1,
            ),
            _axis_row(
                "新鲜-高使用",
                axis_kind="method",
                last_refreshed_at="2026-07-04T12:00:00Z",
                use_count=10,
            ),
            _axis_row(
                "较旧-零收益",
                axis_kind="subgenre",
                last_refreshed_at="2026-07-01T12:00:00Z",
            ),
            _axis_row(
                "过期时效轴",
                axis_kind="event",
                time_sensitive=True,
                freshness_ttl_days=7,
                last_refreshed_at="2026-06-01T12:00:00Z",
            ),
            _axis_row(
                "手动陈旧轴",
                axis_kind="anchor",
                status="stale",
                last_refreshed_at="2026-07-05T12:00:00Z",
            ),
        ],
        bump_usage=False,
    )

    axes = db.list_inspiration_axes(["游戏评价"], limit=10, now=now)

    assert [axis.axis_label for axis in axes] == [
        "新鲜-低使用-subgenre",
        "新鲜-低使用-event",
        "新鲜-高使用",
        "较旧-零收益",
    ]
    assert all(axis.yield_score == 0.0 for axis in axes)


def test_upsert_and_list_inspiration_seeds_round_trips_json_fields(db: Database) -> None:
    db.upsert_discovery_inspiration_seed(
        platform=_BILI,
        profile_kw_digest=_DIGEST,
        aspect_id="interest:indie-games",
        query_kind="regular",
        seed_query="独立游戏 叙事设计",
        inspiration_id="isaac-like-roguelite",
        source_terms=["以撒like", "房间构筑"],
        evidence_titles=["为什么肉鸽游戏会上瘾"],
        evidence_urls=["https://example.test/roguelite"],
        reason="从叙事设计横向扩展到关卡循环。",
        risk_flags=["niche"],
        probe_backend="exa",
        freshness_digest="2026-W27",
        domain_filters=["bilibili.com"],
        source_domains=["game"],
    )

    rows = db.list_discovery_inspiration_seeds(
        _BILI,
        _DIGEST,
        aspect_id="interest:indie-games",
        query_kind="regular",
    )

    assert rows == [
        {
            "platform": _BILI,
            "profile_kw_digest": _DIGEST,
            "aspect_id": "interest:indie-games",
            "query_kind": "regular",
            "probe_backend": "exa",
            "freshness_digest": "2026-W27",
            "seed_query": "独立游戏 叙事设计",
            "domain_filters": ["bilibili.com"],
            "inspiration_id": "isaac-like-roguelite",
            "source_domains": ["game"],
            "source_terms": ["以撒like", "房间构筑"],
            "evidence_titles": ["为什么肉鸽游戏会上瘾"],
            "evidence_urls": ["https://example.test/roguelite"],
            "reason": "从叙事设计横向扩展到关卡循环。",
            "risk_flags": ["niche"],
            "selected_count": 0,
            "yielded_count": 0,
        }
    ]


def test_inspiration_seed_upsert_merges_and_yield_counter_is_incremental(db: Database) -> None:
    kwargs = {
        "platform": _BILI,
        "profile_kw_digest": _DIGEST,
        "aspect_id": "interest:finance",
        "query_kind": "explore",
        "seed_query": "供应链 瓶颈",
        "inspiration_id": "photolithography",
        "source_terms": ["光刻胶"],
        "probe_backend": "exa",
        "freshness_digest": "2026-W27",
    }
    db.upsert_discovery_inspiration_seed(**kwargs)
    assert db.increment_discovery_inspiration_yield(**kwargs) is True

    updated_kwargs = {
        **kwargs,
        "source_terms": ["光刻胶", "EUV"],
        "evidence_titles": ["EUV 光刻瓶颈"],
        "reason": "更新后的证据。",
    }
    db.upsert_discovery_inspiration_seed(**updated_kwargs)

    rows = db.list_discovery_inspiration_seeds(_BILI, _DIGEST)
    assert len(rows) == 1
    assert rows[0]["source_terms"] == ["光刻胶", "EUV"]
    assert rows[0]["evidence_titles"] == ["EUV 光刻瓶颈"]
    assert rows[0]["reason"] == "更新后的证据。"
    assert rows[0]["yielded_count"] == 1


def test_upsert_expansion_and_increment_yield(db: Database) -> None:
    db.upsert_discovery_inspiration_expansion(
        platform=_BILI,
        profile_kw_digest=_DIGEST,
        aspect_id="interest:game-design",
        query_kind="regular",
        inspiration_id="deckbuilder",
        expansion_id="drafting-economy",
        relation="mechanic",
        text="卡牌构筑中的经济系统",
        detail_axes=["机制", "复盘"],
        source_terms=["杀戮尖塔", "牌组曲线"],
        curator_decision="keep",
        curator_score=0.86,
        curator_reason="和用户的系统设计兴趣强相关。",
        curator_feedback="补充更具体的卡组术语。",
        risk_flags=["low_popularity"],
        status="ready",
    )

    assert (
        db.increment_discovery_inspiration_expansion_yield(
            _BILI,
            _DIGEST,
            aspect_id="interest:game-design",
            query_kind="regular",
            inspiration_id="deckbuilder",
            expansion_id="drafting-economy",
        )
        is True
    )

    rows = db.list_discovery_inspiration_expansions(
        _BILI,
        _DIGEST,
        aspect_id="interest:game-design",
        inspiration_id="deckbuilder",
    )
    assert rows == [
        {
            "platform": _BILI,
            "profile_kw_digest": _DIGEST,
            "aspect_id": "interest:game-design",
            "query_kind": "regular",
            "inspiration_id": "deckbuilder",
            "parent_expansion_id": "",
            "expansion_id": "drafting-economy",
            "hop": 1,
            "relation": "mechanic",
            "text": "卡牌构筑中的经济系统",
            "detail_axes": ["机制", "复盘"],
            "source_terms": ["杀戮尖塔", "牌组曲线"],
            "curator_decision": "keep",
            "curator_score": 0.86,
            "curator_reason": "和用户的系统设计兴趣强相关。",
            "curator_feedback": "补充更具体的卡组术语。",
            "risk_flags": ["low_popularity"],
            "status": "ready",
            "selected_count": 0,
            "realized_count": 0,
            "yielded_count": 1,
            "failed_count": 0,
        }
    ]


def test_keyword_metadata_can_be_attached_without_changing_dedupe_key(db: Database) -> None:
    inserted = db.insert_pending_keywords(
        _BILI,
        ["EUV 光刻胶 产业链", "EUV 光刻胶 产业链"],
        _DIGEST,
        keyword_kind="explore",
        metadata_by_keyword={
            "EUV 光刻胶 产业链": {
                "aspect_id": "interest:semiconductor",
                "inspiration_backend": "exa",
                "inspiration_id": "photolithography",
                "inspiration_terms": ["EUV", "光刻胶"],
                "expansion_id": "materials-bottleneck",
                "expansion_label": "材料瓶颈",
                "angle_id": "supply-chain",
                "angle_label": "供应链",
                "query_kind": "explore",
                "source_domain": "semiconductor",
                "source_interest": "产业链瓶颈",
                "generation_reason": "从搜索证据横向扩展。",
            }
        },
    )

    assert inserted == 1
    assert (
        db.insert_pending_keywords(
            _BILI,
            ["EUV 光刻胶 产业链"],
            _DIGEST,
            keyword_kind="explore",
            metadata_by_keyword={"EUV 光刻胶 产业链": {"aspect_id": "changed"}},
        )
        == 0
    )
    row = db.conn.execute(
        """
        SELECT aspect_id, inspiration_backend, inspiration_id, inspiration_terms,
               expansion_id, expansion_label, angle_id, angle_label, query_kind,
               source_domain, source_interest, generation_reason, normalized_keyword
        FROM discovery_keywords
        WHERE keyword = ?
        """,
        ("EUV 光刻胶 产业链",),
    ).fetchone()

    assert row is not None
    assert dict(row) == {
        "aspect_id": "interest:semiconductor",
        "inspiration_backend": "exa",
        "inspiration_id": "photolithography",
        "inspiration_terms": "EUV,光刻胶",
        "expansion_id": "materials-bottleneck",
        "expansion_label": "材料瓶颈",
        "angle_id": "supply-chain",
        "angle_label": "供应链",
        "query_kind": "explore",
        "source_domain": "semiconductor",
        "source_interest": "产业链瓶颈",
        "generation_reason": "从搜索证据横向扩展。",
        "normalized_keyword": "euv 光刻胶 产业链",
    }


def test_keyword_interest_coverage_snapshot_counts_keywords_and_admitted_pool(
    db: Database,
) -> None:
    inserted = db.insert_pending_keywords(
        _BILI,
        ["Switch 冷门佳作", "Switch 双人游戏"],
        _DIGEST,
        metadata_by_keyword={
            "Switch 冷门佳作": {
                "source_interest": "Switch 独立游戏",
                "inspiration_id": "switch-hidden-gems",
                "expansion_id": "hidden-gems",
            },
            "Switch 双人游戏": {
                "source_interest": "Switch 独立游戏",
                "inspiration_id": "switch-party",
                "expansion_id": "party-games",
            },
        },
    )
    assert inserted == 2
    claimed = db.claim_keywords(_BILI, 1)
    assert len(claimed) == 1
    keyword_id = int(claimed[0]["id"])
    db.mark_keyword_used(keyword_id)
    assert db.increment_keyword_yield(keyword_id, "BV_SWITCH") is True
    db.cache_content(
        "BV_SWITCH",
        title="Switch 独立游戏盘点",
        topic_group="Switch 独立游戏",
        pool_topic_label="Switch 独立游戏",
        source_platform=_BILI,
        relevance_score=0.9,
    )
    db.cache_content(
        "BV_OTHER",
        title="AI 工具",
        topic_group="AI 工具实测",
        pool_topic_label="AI 工具实测",
        source_platform=_BILI,
        relevance_score=0.9,
    )

    snapshot = db.get_keyword_interest_coverage_snapshot()

    assert snapshot["Switch 独立游戏"]["generated_keyword_count"] == 2
    assert snapshot["Switch 独立游戏"]["selected_keyword_count"] == 1
    assert snapshot["Switch 独立游戏"]["yield_count"] == 1
    assert snapshot["Switch 独立游戏"]["admitted_count"] == 1
    assert snapshot["Switch 独立游戏"]["admitted_share"] == pytest.approx(0.5)
    assert snapshot["AI 工具实测"]["admitted_count"] == 1


def test_keyword_interest_coverage_snapshot_counts_candidate_distribution(
    db: Database,
) -> None:
    for idx, (platform, content_type, source_interest) in enumerate(
        [
            ("bilibili", "video", "Switch 独立游戏"),
            ("bilibili", "video", "Switch 独立游戏"),
            ("reddit", "thread", "Switch 独立游戏"),
            ("bilibili", "video", "AI 工具实测"),
        ],
        start=1,
    ):
        db.conn.execute(
            """
            INSERT INTO discovery_candidates (
                candidate_key,
                status,
                source_platform,
                source_strategy,
                content_type,
                content_id,
                title,
                raw_payload
            )
            VALUES (?, 'pending_eval', ?, 'search', ?, ?, ?, ?)
            """,
            (
                f"candidate:{idx}",
                platform,
                content_type,
                f"id-{idx}",
                f"title-{idx}",
                json.dumps({"source_interest": source_interest}, ensure_ascii=False),
            ),
        )
    db.conn.commit()

    snapshot = db.get_keyword_interest_coverage_snapshot()

    switch = snapshot["Switch 独立游戏"]
    assert switch["candidate_count"] == 3
    assert switch["candidate_share"] == pytest.approx(0.75)
    assert switch["dominant_candidate_platform"] == "bilibili"
    assert switch["dominant_candidate_platform_share"] == pytest.approx(2 / 3)
    assert switch["dominant_candidate_content_type"] == "video"
    assert switch["dominant_candidate_content_type_share"] == pytest.approx(2 / 3)
    assert snapshot["AI 工具实测"]["candidate_count"] == 1


def test_keyword_interest_coverage_snapshot_normalizes_interest_labels(
    db: Database,
) -> None:
    db.insert_pending_keywords(
        _BILI,
        ["AI Agent 工具", "ai agent 实战"],
        _DIGEST,
        metadata_by_keyword={
            "AI Agent 工具": {"source_interest": " AI Agent  工具 "},
            "ai agent 实战": {"source_interest": "ai agent 工具"},
        },
    )

    snapshot = db.get_keyword_interest_coverage_snapshot()

    assert "AI Agent 工具" in snapshot
    assert "ai agent 工具" not in snapshot
    assert snapshot["AI Agent 工具"]["generated_keyword_count"] == 2


def test_migrate_keyword_interest_labels_rewrites_and_merges_snapshot_buckets(
    db: Database,
) -> None:
    db.insert_pending_keywords(
        _BILI,
        ["旧标签 玩法", "新标签 案例"],
        _DIGEST,
        metadata_by_keyword={
            "旧标签 玩法": {"source_interest": "旧标签"},
            "新标签 案例": {"source_interest": "新标签"},
        },
    )

    updated = db.migrate_keyword_interest_labels({"旧标签": "新标签"})
    snapshot = db.get_keyword_interest_coverage_snapshot()

    assert updated == 1
    assert "旧标签" not in snapshot
    assert snapshot["新标签"]["generated_keyword_count"] == 2


def test_migrate_keyword_interest_labels_rewrites_selection_ledger(
    db: Database,
) -> None:
    db.record_keyword_interest_selection(["旧标签"], query_kind="regular")

    updated = db.migrate_keyword_interest_labels({"旧标签": "新标签"})
    snapshot = db.get_keyword_interest_coverage_snapshot()

    assert updated == 1
    assert "旧标签" not in snapshot
    assert snapshot["新标签"]["interest_selection_count"] == 1


def test_keyword_cohort_stats_compare_inspiration_and_merged_yield(
    db: Database,
) -> None:
    db.insert_pending_keywords(
        _BILI,
        ["灵感关键词", "旧流程关键词"],
        _DIGEST,
        metadata_by_keyword={
            "灵感关键词": {
                "inspiration_id": "seed-1",
                "source_interest": "Switch 独立游戏",
            }
        },
    )
    rows = db.conn.execute("SELECT id, keyword FROM discovery_keywords ORDER BY id ASC").fetchall()
    ids = {str(row["keyword"]): int(row["id"]) for row in rows}
    for keyword_id in ids.values():
        db.conn.execute(
            """
            UPDATE discovery_keywords
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (keyword_id,),
        )
    db.conn.execute(
        """
        INSERT INTO content_cache (
            bvid, title, relevance_score, pool_status, pool_topic_label,
            topic_group, source_platform, delight_score, source_keyword_id
        )
        VALUES
            ('BV_INSPIRE_1', 'inspire one', 0.9, 'fresh', '独立游戏', '游戏', 'bilibili', 0.92, ?),
            ('BV_INSPIRE_2', 'inspire two', 0.9, 'fresh', '独立游戏', '游戏', 'bilibili', 0.88, ?),
            ('BV_MERGED_1', 'merged one', 0.9, 'fresh', 'AI 工具', '科技', 'bilibili', 0.80, ?)
        """,
        (ids["灵感关键词"], ids["灵感关键词"], ids["旧流程关键词"]),
    )
    assert db.increment_keyword_yield(ids["灵感关键词"], "BV_INSPIRE_1")
    assert db.increment_keyword_yield(ids["灵感关键词"], "BV_INSPIRE_2")
    assert db.increment_keyword_yield(ids["旧流程关键词"], "BV_MERGED_1")

    stats = db.get_keyword_cohort_stats(window_days=14)

    assert stats["thresholds"] == {
        "min_window_days": 14,
        "min_inspiration_claimed_keywords": 200,
        "min_admissions_per_claimed_ratio": 0.8,
        "min_mean_delight_ratio": 0.95,
    }
    assert stats["cohorts"]["inspiration"]["generated_keywords"] == 1
    assert stats["cohorts"]["inspiration"]["claimed_keywords"] == 1
    assert stats["cohorts"]["inspiration"]["admissions_per_claimed_keyword"] == 2.0
    assert stats["cohorts"]["inspiration"]["mean_delight"] == pytest.approx(0.9)
    assert stats["cohorts"]["merged"]["admissions_per_claimed_keyword"] == 1.0
    for cohort in ("inspiration", "merged"):
        assert stats["cohorts"][cohort]["claim_counts_by_day"] == {}
        assert stats["cohorts"][cohort]["claim_counts_by_platform"] == {}
        assert stats["cohorts"][cohort]["claim_counts_by_source_interest"] == {}
        assert stats["cohorts"][cohort]["grounding_mix"] == {}
        assert stats["cohorts"][cohort]["duplicate_rate_by_grounding_source"] == {}
    assert stats["gate"]["verdict"] == "insufficient_sample"


def test_keyword_cohort_stats_reports_interest_selection_ledger(
    db: Database,
) -> None:
    db.record_keyword_interest_selection(
        ["兴趣A", "兴趣B"],
        query_kind="regular",
        selection_scope="production",
    )
    db.record_keyword_interest_selection(
        ["兴趣A"],
        query_kind="explore",
        selection_scope="production",
    )
    db.record_keyword_interest_selection(
        ["预览兴趣"],
        query_kind="regular",
        selection_scope="preview",
    )

    stats = db.get_keyword_cohort_stats(window_days=14)

    production = stats["interest_selection"]["production"]
    preview = stats["interest_selection"]["preview"]
    assert production["total_selected_interests"] == 3
    assert production["distinct_interests"] == 2
    assert production["by_source_interest"] == {"兴趣A": 2, "兴趣B": 1}
    assert production["by_query_kind"] == {"explore": 1, "regular": 2}
    assert isinstance(production["last_selected_at"], str)
    assert preview["total_selected_interests"] == 1
    assert preview["by_source_interest"] == {"预览兴趣": 1}


def test_record_keyword_interest_selection_prunes_old_rows(db: Database) -> None:
    db.conn.execute(
        """
        INSERT INTO discovery_interest_selection_ledger (
            source_interest, normalized_interest, query_kind, selection_scope, selected_at
        )
        VALUES ('过期兴趣', '过期兴趣', 'regular', 'production', datetime('now', '-45 days'))
        """
    )
    db.conn.commit()

    db.record_keyword_interest_selection(["新兴趣"], query_kind="regular")

    rows = db.conn.execute(
        "SELECT source_interest FROM discovery_interest_selection_ledger ORDER BY id ASC"
    ).fetchall()
    assert [str(row["source_interest"]) for row in rows] == ["新兴趣"]


def test_keyword_yield_backfills_inspiration_and_expansion_once(db: Database) -> None:
    db.upsert_discovery_inspiration_seed(
        platform=_BILI,
        profile_kw_digest=_DIGEST,
        aspect_id="interest:game-design",
        query_kind="regular",
        seed_query="独立游戏叙事",
        inspiration_id="environmental-narrative",
        source_terms=["环境叙事"],
        probe_backend="exa",
    )
    db.upsert_discovery_inspiration_expansion(
        platform=_BILI,
        profile_kw_digest=_DIGEST,
        aspect_id="interest:game-design",
        query_kind="regular",
        inspiration_id="environmental-narrative",
        expansion_id="fragmented-clues",
        relation="mechanic",
        text="碎片化线索",
        status="ready",
    )
    db.insert_pending_keywords(
        _BILI,
        ["环境叙事 碎片化线索 复盘"],
        _DIGEST,
        metadata_by_keyword={
            "环境叙事 碎片化线索 复盘": {
                "aspect_id": "interest:game-design",
                "inspiration_backend": "exa",
                "inspiration_id": "environmental-narrative",
                "expansion_id": "fragmented-clues",
                "query_kind": "regular",
            }
        },
    )
    row = db.conn.execute(
        "SELECT id FROM discovery_keywords WHERE keyword = ?",
        ("环境叙事 碎片化线索 复盘",),
    ).fetchone()
    assert row is not None
    keyword_id = int(row["id"])

    assert db.increment_keyword_yield(keyword_id, "BV1") is True
    assert db.increment_keyword_yield(keyword_id, "BV1") is False

    assert db.keyword_yield_count(keyword_id) == 1
    seed = db.list_discovery_inspiration_seeds(_BILI, _DIGEST)[0]
    expansion = db.list_discovery_inspiration_expansions(_BILI, _DIGEST)[0]
    assert seed["yielded_count"] == 1
    assert expansion["yielded_count"] == 1


def test_derive_inspiration_seeds_filters_generic_preview_terms() -> None:
    seeds = derive_inspiration_seeds(
        "独立游戏 叙事设计",
        [
            ExaPreviewItem(
                title="叙事游戏如何设计碎片化线索",
                url="https://example.test/a",
                highlights=["环境叙事", "碎片化线索", "游戏"],
            ),
            ExaPreviewItem(
                title="游戏推荐榜单",
                url="https://example.test/b",
                highlights=["游戏", "推荐", "好玩"],
            ),
        ],
        max_seeds=4,
    )

    assert seeds == [
        InspirationSeed(
            inspiration_id="environmental-narrative",
            source_terms=("环境叙事",),
            evidence_titles=("叙事游戏如何设计碎片化线索",),
            evidence_urls=("https://example.test/a",),
            reason="Search preview surfaced a specific adjacent term.",
        ),
        InspirationSeed(
            inspiration_id="fragmented-clues",
            source_terms=("碎片化线索",),
            evidence_titles=("叙事游戏如何设计碎片化线索",),
            evidence_urls=("https://example.test/a",),
            reason="Search preview surfaced a specific adjacent term.",
        ),
    ]


def test_derive_inspiration_seeds_uses_title_when_highlight_is_too_long() -> None:
    seeds = derive_inspiration_seeds(
        "游戏 具体案例 机制 方法 争议 深度分析",
        [
            ExaPreviewItem(
                title="游戏玩法和策略是否属于《著作权法》保护对象？-36氪",
                url="https://example.test/copyright",
                highlights=[
                    "但是，是否所有的游戏玩法都属于作品范畴并受到《著作权法》保护？"
                    "答案当然是否定的，本案中涉诉玩法共计121个，一审法院仅对其中79个进行了认定。"
                ],
            )
        ],
        max_seeds=2,
    )

    assert seeds == [
        InspirationSeed(
            inspiration_id="term-9a07486e67",
            source_terms=("游戏玩法和策略是否属于《著作权法》保护对象",),
            evidence_titles=("游戏玩法和策略是否属于《著作权法》保护对象？-36氪",),
            evidence_urls=("https://example.test/copyright",),
            reason="Search preview surfaced a specific adjacent term.",
        )
    ]


def test_derive_inspiration_seeds_strips_leading_conjunctions() -> None:
    seeds = derive_inspiration_seeds(
        "游戏 具体案例 机制 方法 争议 深度分析",
        [
            ExaPreviewItem(
                title="游戏玩法和策略是否属于《著作权法》保护对象？-36氪",
                url="https://example.test/copyright",
                highlights=["以及道具搭配"],
            )
        ],
        max_seeds=1,
    )

    assert seeds[0].source_terms == ("道具搭配",)


def test_derive_inspiration_seeds_filters_markdown_table_noise_and_fragments() -> None:
    seeds = derive_inspiration_seeds(
        "独立游戏叙事 具体案例 机制 方法 争议 深度分析",
        [
            ExaPreviewItem(
                title="独立游戏叙事设计实战",
                url="https://example.test/story",
                highlights=["| --- | --- | --- |", "---", "故事是", "环境叙事"],
            )
        ],
        max_seeds=3,
    )

    assert seeds == [
        InspirationSeed(
            inspiration_id="environmental-narrative",
            source_terms=("环境叙事",),
            evidence_titles=("独立游戏叙事设计实战",),
            evidence_urls=("https://example.test/story",),
            reason="Search preview surfaced a specific adjacent term.",
        )
    ]


def test_materialize_platform_keywords_applies_only_hard_gates_and_keeps_style_mismatch() -> None:
    candidates = [
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform=_BILI,
            core_concept="独立游戏 机制复盘",
            decoration="案例",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform=_BILI,
            core_concept="独立游戏 机制复盘",
            decoration="案例",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform=_BILI,
            core_concept="https://example.test/story",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform=_BILI,
            core_concept="独立游戏 " + "很长" * 30,
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform="youtube",
            core_concept="独立游戏机制复盘",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="game design",
            axis_label="community language",
            platform="reddit",
            core_concept="game design practical workflow",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
    ]

    keywords, telemetry = materialize_platform_keywords(
        candidates,
        {"独立游戏叙事": AllocationTarget(platforms=(_BILI, "youtube"), min_axes=1)},
        max_keywords_per_platform=4,
    )

    assert [item.keyword for item in keywords] == ["独立游戏 机制复盘 案例"]
    assert telemetry["hard_gate_rejects"] == [
        {"keyword": "独立游戏 机制复盘 案例", "platform": _BILI, "reason": "duplicate_keyword"},
        {"keyword": "https://example.test/story", "platform": _BILI, "reason": "url_keyword"},
        {
            "keyword": "独立游戏 " + "很长" * 30,
            "platform": _BILI,
            "reason": "keyword_too_long",
        },
        {"keyword": "独立游戏机制复盘", "platform": "youtube", "reason": "script_mismatch"},
    ]
    assert all(
        item["reason"] != "platform_style_mismatch" for item in telemetry["hard_gate_rejects"]
    )
    assert platform_style_score("game design practical workflow", "reddit") > 0.0


def test_materialize_platform_keywords_selects_coverage_before_soft_score() -> None:
    candidates = [
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="机制",
            platform=_BILI,
            core_concept="游戏评价 机制",
            decoration="复盘",
            recency_sensitivity="low",
            origin="llm-low",
        ),
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="机制",
            platform=_BILI,
            core_concept="游戏评价 机制 解析",
            decoration="",
            recency_sensitivity="low",
            origin="llm-high",
        ),
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="社区语言",
            platform=_BILI,
            core_concept="游戏评价 社区黑话",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="机制",
            platform="reddit",
            core_concept="game review practical analysis",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="社区语言",
            platform="reddit",
            core_concept="game review discussion terms",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
    ]

    keywords, telemetry = materialize_platform_keywords(
        candidates,
        {"游戏评价": AllocationTarget(platforms=(_BILI, "reddit"), min_axes=2)},
        max_keywords_per_platform=2,
    )

    by_platform = {(item.metadata["source_domain"], item.keyword) for item in keywords}
    assert by_platform == {
        (_BILI, "游戏评价 机制 解析"),
        (_BILI, "游戏评价 社区黑话"),
        ("reddit", "game review practical analysis"),
        ("reddit", "game review discussion terms"),
    }
    assert telemetry["axis_coverage"]["游戏评价"] == {
        "count": 2,
        "axes": ["机制", "社区语言"],
        "platforms": [_BILI, "reddit"],
    }
    assert telemetry["soft_score_distribution"]["count"] == 5


def test_materialize_platform_keywords_deterministically_fills_thin_pool_from_axes() -> None:
    axes = [
        inspiration_module.AxisRow(
            interest_label="game design",
            axis_label="mechanics",
            axis_kind="method",
            source="test",
            example_terms=("combat tuning",),
        ),
        inspiration_module.AxisRow(
            interest_label="game design",
            axis_label="community",
            axis_kind="community_language",
            source="test",
            example_terms=("player discourse",),
        ),
    ]
    candidates = [
        MaterializeCandidate(
            interest="game design",
            axis_label="mechanics",
            platform="youtube",
            core_concept="game design mechanics",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        )
    ]

    keywords, telemetry = materialize_platform_keywords(
        candidates,
        {"game design": AllocationTarget(platforms=("youtube",), min_axes=2)},
        axes=axes,
        max_keywords_per_platform=2,
    )

    assert [item.keyword for item in keywords] == [
        "game design mechanics",
        "game design player discourse",
    ]
    assert keywords[1].metadata["origin"] == "deterministic_fill"
    assert telemetry["deterministic_fill_count"] == 1
    assert telemetry["coverage_shortfall"] == []


def test_materialize_platform_keywords_reports_script_mismatch_instead_of_garbage_fill() -> None:
    axes = [
        inspiration_module.AxisRow(
            interest_label="独立游戏叙事",
            axis_label="机制",
            axis_kind="method",
            source="test",
            example_terms=("碎片化线索",),
        )
    ]

    keywords, telemetry = materialize_platform_keywords(
        [],
        {"独立游戏叙事": AllocationTarget(platforms=("youtube", "reddit"), min_axes=1)},
        axes=axes,
        max_keywords_per_platform=1,
    )

    assert keywords == []
    assert telemetry["coverage_shortfall"] == [
        {
            "interest": "独立游戏叙事",
            "platform": "youtube",
            "reason": "script_mismatch",
            "missing_axes": ["机制"],
            "missing_platforms": ["youtube"],
        },
        {
            "interest": "独立游戏叙事",
            "platform": "reddit",
            "reason": "script_mismatch",
            "missing_axes": ["机制"],
            "missing_platforms": ["reddit"],
        },
    ]


def test_materialize_platform_keywords_reports_missing_axes_for_empty_pool() -> None:
    keywords, telemetry = materialize_platform_keywords(
        [],
        {"城市声音采样": AllocationTarget(platforms=(_BILI,), min_axes=2)},
        max_keywords_per_platform=2,
    )

    assert keywords == []
    assert telemetry["coverage_shortfall"] == [
        {
            "interest": "城市声音采样",
            "platform": _BILI,
            "reason": "missing_axes",
            "missing_axes": 2,
            "missing_platforms": [_BILI],
        }
    ]


def test_materialize_platform_keywords_degenerate_single_axis_records_shortfall() -> None:
    candidates = [
        MaterializeCandidate(
            interest="game design",
            axis_label="mechanics",
            platform="youtube",
            core_concept="game design mechanics",
            decoration="analysis",
            recency_sensitivity="high",
            origin="llm",
        )
    ]

    keywords, telemetry = materialize_platform_keywords(
        candidates,
        {"game design": AllocationTarget(platforms=("youtube",), min_axes=2)},
        max_keywords_per_platform=2,
    )

    assert [item.keyword for item in keywords] == ["game design mechanics analysis"]
    assert not any(str(year) in keywords[0].keyword for year in range(2024, 2027))
    assert telemetry["coverage_shortfall"] == [
        {
            "interest": "game design",
            "platform": "youtube",
            "reason": "missing_axes",
            "missing_axes": 1,
            "missing_platforms": ["youtube"],
        }
    ]


def test_materialize_platform_keywords_appends_decoration_only_within_budget() -> None:
    candidates = [
        MaterializeCandidate(
            interest="game design",
            axis_label="mechanics",
            platform="youtube",
            core_concept="game design combat tuning",
            decoration="designer interview breakdown",
            recency_sensitivity="high",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="game design",
            axis_label="community",
            platform="youtube",
            core_concept="game design community discourse near limit",
            decoration="extra detail that would exceed budget",
            recency_sensitivity="low",
            origin="llm",
        ),
    ]

    keywords, _telemetry = materialize_platform_keywords(
        candidates,
        {"game design": AllocationTarget(platforms=("youtube",), min_axes=2)},
        max_keywords_per_platform=2,
        max_keyword_chars=48,
    )

    assert [item.keyword for item in keywords] == [
        "game design combat tuning designer interview",
        "game design community discourse near limit",
    ]
