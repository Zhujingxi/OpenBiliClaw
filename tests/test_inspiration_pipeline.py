"""Thin direct unit tests for :class:`InspirationKeywordPipeline`.

The deep behavioural coverage lives in ``tests/test_keyword_planner.py`` and
``tests/test_discovery_inspiration.py`` (exercised through the planner's
compatibility delegates). These tests drive the extracted pipeline directly
with fakes to pin the seam: happy path, the ④-LLM-failure deterministic
fallback, and preview isolation (persist / bump semantics).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import DiscoveryConfig, derive_inspiration_breadth_params
from openbiliclaw.discovery.inspiration import ExaPreviewItem
from openbiliclaw.runtime.inspiration_pipeline import InspirationKeywordPipeline
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

_BILI = "bilibili"
_CLOCK = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


@dataclass
class _FakeLLM:
    payload: dict[str, object] | None
    raises: bool = False
    calls: list[str] = field(default_factory=list)

    async def complete_structured_task(self, *, caller: str = "", **_: object) -> Any:
        self.calls.append(caller)
        if self.raises:
            raise RuntimeError("llm down")
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider="test",
            model="test-model",
        )


@dataclass
class _FakeProvider:
    previews_by_query: dict[str, list[ExaPreviewItem]]

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        return list(self.previews_by_query.get(query, []))


@dataclass
class _FakeHost:
    """Stand-in for the shared planner infra the pipeline calls back on."""

    profile: SoulProfile
    inserted: list[tuple[str, list[str]]] = field(default_factory=list)

    def _history(self, platform: str) -> list[str]:
        return []

    def _insert(
        self,
        platform: str,
        words: list[str],
        digest: str,
        *,
        keyword_kind: str = "regular",
        metadata_by_keyword: dict[str, dict[str, object]] | None = None,
    ) -> int:
        self.inserted.append((platform, list(words)))
        return len(words)

    def _avoid_hints(self, profile: SoulProfile | None = None) -> dict[str, dict[str, object]]:
        return {}

    def _supply_hints(
        self, hints_by_platform: dict[str, dict[str, object]]
    ) -> dict[str, list[str]]:
        return {}

    async def _load_profile(self) -> SoulProfile | None:
        return self.profile


def _profile() -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(name="Switch 独立游戏", category="游戏", weight=0.95)]
        )
    )


def _axis_payload() -> dict[str, object]:
    return {
        "axes": [
            {
                "interest": "Switch 独立游戏",
                "axis_label": "冷门佳作",
                "axis_kind": "community_vocab",
                "example_terms": ["冷门佳作"],
            }
        ],
        "keywords": [
            {
                "interest": "Switch 独立游戏",
                "axis_id_or_label": "冷门佳作",
                "platform": _BILI,
                "core_concept": "Switch 独立游戏 冷门佳作",
                "decoration": "盘点",
            }
        ],
    }


def _make_pipeline(
    db: Database,
    *,
    llm: _FakeLLM,
    host: _FakeHost,
    provider: _FakeProvider,
) -> InspirationKeywordPipeline:
    discovery = DiscoveryConfig(inspiration_search_enabled=True)  # type: ignore[call-arg]
    # Cap to one keyword/platform so the coverage-first fill does not add a
    # second deterministic keyword — keeps these seam tests single-output.
    params = dataclasses.replace(
        derive_inspiration_breadth_params("medium"), max_keywords_per_platform=1
    )
    return InspirationKeywordPipeline(
        host=host,
        db=db,
        llm_service=llm,
        inspiration_provider=provider,
        discovery=lambda: discovery,
        inspiration_params=lambda: params,
        clock=lambda: _CLOCK,
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "pipeline.db")
    d.initialize()
    return d


async def test_pipeline_happy_path_persists_keywords_and_upserts_axis(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=_axis_payload())
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="hidden gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    ledger = await pipeline._run_inspiration_stage([_BILI], profile=profile, digest="d1")

    assert llm.calls == ["discovery.keyword_inspiration"]
    assert ledger == {_BILI: 1}
    assert host.inserted == [(_BILI, ["Switch 独立游戏 冷门佳作 盘点"])]
    # Axis was upserted with production bump semantics.
    row = db.conn.execute(
        "SELECT use_count, status FROM discovery_inspiration_axis WHERE interest_label = ?",
        ("Switch 独立游戏",),
    ).fetchone()
    assert row is not None
    assert int(row["use_count"]) == 1
    assert str(row["status"]) == "active"


async def test_pipeline_llm_failure_falls_back_deterministically(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=None, raises=True)
    provider = _FakeProvider(previews_by_query={})
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    ledger = await pipeline._run_inspiration_stage([_BILI], profile=profile, digest="d1")

    # ④ failed but the stage still produced a deterministic interest-only keyword.
    assert llm.calls == ["discovery.keyword_inspiration"]
    assert ledger == {_BILI: 1}
    assert host.inserted == [(_BILI, ["Switch 独立游戏"])]


async def test_pipeline_preview_does_not_persist_keywords_or_run_backfill(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=_axis_payload())
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="hidden gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    report = await pipeline.preview_inspiration_keywords(
        [_BILI], profile=profile, persist_axes=False
    )

    # Preview never writes keyword rows, never persists axes, never ticks backfill.
    assert host.inserted == []
    assert report["platform_keywords"] == {_BILI: ["Switch 独立游戏 冷门佳作 盘点"]}
    assert pipeline.last_axis_backfill == {"ran": False, "staled": 0, "retired": 0, "purged": 0}
    axis_count = db.conn.execute("SELECT COUNT(*) AS n FROM discovery_inspiration_axis").fetchone()
    assert int(axis_count["n"]) == 0


async def test_pipeline_preview_persist_axes_writes_axis_without_bumping_usage(
    db: Database,
) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=_axis_payload())
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="hidden gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)

    await pipeline.preview_inspiration_keywords([_BILI], profile=profile, persist_axes=True)

    assert host.inserted == []  # still no keyword rows
    row = db.conn.execute(
        "SELECT use_count FROM discovery_inspiration_axis WHERE interest_label = ?",
        ("Switch 独立游戏",),
    ).fetchone()
    assert row is not None
    # persist_axes=True writes the row, but preview keeps bump_axis_usage=False.
    assert int(row["use_count"]) == 0


# ── Part E: embedding near-duplicate axis merge ─────────────────────────


@dataclass
class _FakeEmbeddingService:
    """Deterministic fake: maps text → vector; can raise / time out / go blank."""

    vectors: dict[str, list[float]]
    mode: str = "ok"  # "ok" | "raise" | "timeout" | "empty"
    calls: list[str] = field(default_factory=list)

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.mode == "raise":
            raise RuntimeError("embed provider down")
        if self.mode == "timeout":
            import asyncio

            await asyncio.sleep(3600)
        if self.mode == "empty":
            return []
        return list(self.vectors.get(text, []))


def _axis(label: str, *, interest: str = "游戏评价", terms: tuple[str, ...] = ()) -> Any:
    from openbiliclaw.discovery.inspiration import AxisRow

    return AxisRow(
        interest_label=interest,
        axis_label=label,
        axis_kind="subgenre",
        source="external_search",
        example_terms=terms,
    )


async def _pipeline_with_embeddings(
    db: Database, service: _FakeEmbeddingService
) -> InspirationKeywordPipeline:
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(payload=None)
    provider = _FakeProvider(previews_by_query={})
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)
    pipeline._embedding_service = service
    return pipeline


async def test_axis_merge_folds_near_duplicate_into_existing_row(db: Database) -> None:
    existing = _axis("维修与DIY", terms=("拆机",))
    new = _axis("故障自修", terms=("自己修",))
    # Near-identical vectors → cosine ~1.0 >= 0.92.
    service = _FakeEmbeddingService(
        vectors={"维修与DIY": [1.0, 0.0, 0.02], "故障自修": [1.0, 0.0, 0.0]}
    )
    pipeline = await _pipeline_with_embeddings(db, service)

    resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])

    assert degraded is False
    assert len(resolved) == 1
    # Folded onto the existing axis identity (same id/label), evidence unioned.
    assert resolved[0].axis_id == existing.axis_id
    assert resolved[0].axis_label == "维修与DIY"
    assert set(resolved[0].example_terms) == {"拆机", "自己修"}


async def test_axis_merge_below_threshold_keeps_new_row(db: Database) -> None:
    existing = _axis("维修与DIY")
    new = _axis("剧情解析")
    # Orthogonal vectors → cosine 0.0 < 0.92.
    service = _FakeEmbeddingService(vectors={"维修与DIY": [1.0, 0.0], "剧情解析": [0.0, 1.0]})
    pipeline = await _pipeline_with_embeddings(db, service)

    resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])

    assert degraded is False
    assert len(resolved) == 1
    assert resolved[0].axis_id == new.axis_id
    assert resolved[0].axis_label == "剧情解析"


async def test_axis_merge_only_matches_same_interest(db: Database) -> None:
    existing = _axis("维修与DIY", interest="数码")
    new = _axis("故障自修", interest="游戏评价")
    service = _FakeEmbeddingService(vectors={"维修与DIY": [1.0, 0.0], "故障自修": [1.0, 0.0]})
    pipeline = await _pipeline_with_embeddings(db, service)

    resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])

    assert degraded is False
    # Different interest → never merged even though the labels embed identically.
    assert resolved[0].axis_id == new.axis_id


async def test_axis_merge_timeout_degrades_to_string_behaviour(db: Database) -> None:
    existing = _axis("维修与DIY")
    new = _axis("故障自修")
    service = _FakeEmbeddingService(
        vectors={"维修与DIY": [1.0, 0.0], "故障自修": [1.0, 0.0]}, mode="timeout"
    )
    pipeline = await _pipeline_with_embeddings(db, service)
    # Shrink the timeout so the test is fast.
    import openbiliclaw.runtime.inspiration_pipeline as pipeline_module

    original = pipeline_module._AXIS_EMBEDDING_TIMEOUT_SECONDS
    pipeline_module._AXIS_EMBEDDING_TIMEOUT_SECONDS = 0.05
    try:
        resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])
    finally:
        pipeline_module._AXIS_EMBEDDING_TIMEOUT_SECONDS = original

    # Hard requirement (AC9): timeout → axes pass through unchanged + flag.
    assert degraded is True
    assert [a.axis_id for a in resolved] == [new.axis_id]


async def test_axis_merge_provider_error_degrades_and_never_blocks(db: Database) -> None:
    existing = _axis("维修与DIY")
    new = _axis("故障自修")
    service = _FakeEmbeddingService(vectors={}, mode="raise")
    pipeline = await _pipeline_with_embeddings(db, service)

    resolved, degraded = await pipeline._resolve_axis_merges([new], [existing])

    assert degraded is True
    assert [a.axis_id for a in resolved] == [new.axis_id]


async def test_axis_merge_no_service_passes_through_without_degrade(db: Database) -> None:
    profile = _profile()
    host = _FakeHost(profile=profile)
    pipeline = _make_pipeline(
        db, llm=_FakeLLM(payload=None), host=host, provider=_FakeProvider(previews_by_query={})
    )
    # Default: no embedding service injected.
    resolved, degraded = await pipeline._resolve_axis_merges(
        [_axis("故障自修")], [_axis("维修与DIY")]
    )

    assert degraded is False  # absence is not degradation
    assert len(resolved) == 1


async def test_stage_embedding_merge_writes_single_row_and_flags_clean(db: Database) -> None:
    # End-to-end through the production stage: seed an existing axis, then let
    # the LLM propose a near-duplicate; embedding merge folds it into one row.
    from openbiliclaw.discovery.inspiration import AxisRow

    db.upsert_inspiration_axes(
        [
            AxisRow(
                interest_label="Switch 独立游戏",
                axis_label="冷门佳作",
                axis_kind="community_vocab",
                source="external_search",
            )
        ],
        bump_usage=False,
    )
    profile = _profile()
    host = _FakeHost(profile=profile)
    llm = _FakeLLM(
        payload={
            "axes": [
                {
                    "interest": "Switch 独立游戏",
                    "axis_label": "小众神作",
                    "axis_kind": "community_vocab",
                    "example_terms": ["小众神作"],
                }
            ],
            "keywords": [],
        }
    )
    provider = _FakeProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(title="gem", url="https://x.test/a", highlights=("Balatro",))
            ]
        }
    )
    pipeline = _make_pipeline(db, llm=llm, host=host, provider=provider)
    pipeline._embedding_service = _FakeEmbeddingService(
        vectors={"冷门佳作": [1.0, 0.0, 0.01], "小众神作": [1.0, 0.0, 0.0]}
    )

    report = (
        await pipeline._run_inspiration_stage(["bilibili"], profile=profile, digest="d1")
        is not None
    )
    assert report

    rows = db.conn.execute(
        "SELECT axis_label FROM discovery_inspiration_axis WHERE interest_label = ?",
        ("Switch 独立游戏",),
    ).fetchall()
    # The near-duplicate "小众神作" was folded into "冷门佳作" — still one row.
    assert [str(r["axis_label"]) for r in rows] == ["冷门佳作"]
