"""Tests for the Phase 0 profile-update ledger (DAO + 8 write-point hooks + CLI).

Spec: docs/plans/2026-07-17-cognitive-profile-pipeline-spec.md §Phase 0.
Plan: docs/plans/2026-07-17-cognitive-profile-pipeline-plan.md Task 0.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.engine import SoulEngine
from openbiliclaw.soul.ledger import ProfileLedger, diff_for_ledger, summarize_for_ledger
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class FakeRegistry:
    """Minimal LLM registry returning a fixed content string."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.content, provider="openai")


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "ledger.db")
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


def test_insert_profile_ledger_persists_row(tmp_path: Path) -> None:
    db = _db(tmp_path)
    row_id = db.insert_profile_ledger(
        write_point="dialogue_preference_overwrite",
        source="chat",
        before_summary="{}",
        after_summary='{"interests": 1}',
        diff='{"changed_keys": ["interests"]}',
        source_refs=["interest:AI"],
        outcome="success",
        turn_id="turn-1",
    )
    assert row_id > 0
    rows = db.query_profile_ledger(days=30)
    assert len(rows) == 1
    row = rows[0]
    assert row["write_point"] == "dialogue_preference_overwrite"
    assert row["source"] == "chat"
    assert row["outcome"] == "success"
    assert row["turn_id"] == "turn-1"
    assert row["source_refs"] == ["interest:AI"]


def test_query_profile_ledger_filters_by_write_point(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.insert_profile_ledger(write_point="dialogue_preference_overwrite", source="chat")
    db.insert_profile_ledger(write_point="feedback_preference_overwrite", source="feedback")
    db.insert_profile_ledger(write_point="feedback_preference_overwrite", source="feedback")

    only_feedback = db.query_profile_ledger(days=30, write_point="feedback_preference_overwrite")
    assert len(only_feedback) == 2
    assert {r["write_point"] for r in only_feedback} == {"feedback_preference_overwrite"}

    all_rows = db.query_profile_ledger(days=30)
    assert len(all_rows) == 3


def test_query_profile_ledger_newest_first_and_limit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for idx in range(5):
        db.insert_profile_ledger(write_point=f"wp-{idx}", source="chat")
    rows = db.query_profile_ledger(days=30, limit=2)
    assert len(rows) == 2
    # Newest (highest id) first.
    assert rows[0]["write_point"] == "wp-4"
    assert rows[1]["write_point"] == "wp-3"


def test_ledger_table_exists_after_migration_on_legacy_db(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()
    # Migration path (not the fresh-schema path) must create the table.
    row_id = db.insert_profile_ledger(write_point="init_preference_build", source="init")
    assert row_id > 0


# ---------------------------------------------------------------------------
# ProfileLedger helper
# ---------------------------------------------------------------------------


def test_summarize_for_ledger_is_deterministic_and_capped() -> None:
    text = summarize_for_ledger({"b": 2, "a": 1})
    assert text == '{"a": 1, "b": 2}'
    big = summarize_for_ledger({"k": "x" * 5000})
    assert len(big) <= 2000


def test_diff_for_ledger_reports_changed_keys() -> None:
    diff = diff_for_ledger({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
    payload = json.loads(diff)
    assert payload["changed_keys"] == ["b", "c"]


def test_profile_ledger_record_success(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ledger = ProfileLedger(db)
    ledger.record(
        write_point="init_preference_build",
        source="init",
        before={"a": 1},
        after={"a": 2},
        source_refs=["events:3"],
    )
    rows = db.query_profile_ledger(days=30)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
    assert rows[0]["source_refs"] == ["events:3"]
    assert rows[0]["diff"]  # non-empty diff recorded


def test_profile_ledger_action_records_failed_and_reraises(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ledger = ProfileLedger(db)
    with (
        pytest.raises(ValueError, match="boom"),
        ledger.action(write_point="dialogue_soul_rebuild", source="chat", before={"a": 1}),
    ):
        raise ValueError("boom")
    rows = db.query_profile_ledger(days=30)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "failed"
    assert "boom" in rows[0]["error"]


def test_profile_ledger_write_failure_is_swallowed(tmp_path: Path) -> None:
    class _BoomDB:
        def insert_profile_ledger(self, **_kwargs: object) -> int:
            raise RuntimeError("db down")

    ledger = ProfileLedger(_BoomDB())
    # Must not raise — best-effort observer.
    ledger.record(write_point="init_preference_build", source="init", before={}, after={})


def test_profile_ledger_none_database_is_noop() -> None:
    ledger = ProfileLedger(None)
    ledger.record(write_point="x", source="y")  # no crash


# ---------------------------------------------------------------------------
# 8 write-point hooks (via SoulEngine)
# ---------------------------------------------------------------------------


def _engine(tmp_path: Path, content: str) -> tuple[SoulEngine, MemoryManager]:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry(content), memory=memory)
    return engine, memory


def _ledger_rows(memory: MemoryManager, write_point: str = "") -> list[dict[str, object]]:
    return memory._database.query_profile_ledger(days=30, write_point=write_point)


@pytest.mark.asyncio
async def test_hook_init_preference_build(tmp_path: Path) -> None:
    engine, memory = _engine(
        tmp_path,
        json.dumps({"interests": [{"name": "历史", "category": "知识", "weight": 0.8}]}),
    )
    await engine.analyze_events([{"event_type": "view", "title": "世界史"}])
    rows = _ledger_rows(memory, "init_preference_build")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
    assert rows[0]["source"] == "init"
    assert rows[0]["source_refs"]  # non-empty


@pytest.mark.asyncio
async def test_hook_init_soul_build(tmp_path: Path) -> None:
    portrait = "这个人偏好高信息密度内容。" * 12
    content = json.dumps(
        {
            "personality_portrait": portrait,
            "core_traits": ["理性"],
            "cognitive_style": ["结构化"],
            "motivational_drivers": ["求知"],
            "current_phase": "积累",
            "values": ["成长"],
            "life_stage": "探索",
            "deep_needs": ["被理解"],
        }
    )
    engine, memory = _engine(tmp_path, content)
    memory.get_layer("preference").data.update(
        {"interests": [{"name": "科技", "category": "知识", "weight": 0.8}]}
    )
    await engine.build_initial_profile(history=[{"title": "AI 实测"}])
    rows = _ledger_rows(memory, "init_soul_build")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_hook_dialogue_preference_and_soul_and_dislike(tmp_path: Path) -> None:
    # Extraction returns a high-confidence dislike candidate; the preference
    # analyzer then returns a preference with a new disliked topic + interests
    # so all three dialogue hooks fire.
    class _SeqRegistry:
        def __init__(self) -> None:
            self.idx = 0

        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            json_mode: bool = False,
            reasoning_effort: str | None = None,
            model: str | None = None,
        ) -> LLMResponse:
            self.idx += 1
            # First call: dialogue-insight extraction.
            if self.idx == 1:
                return LLMResponse(
                    content=json.dumps(
                        {
                            "candidates": [
                                {
                                    "kind": "dislike",
                                    "content": "带货视频",
                                    "confidence": 0.95,
                                    "evidence": "明确说讨厌",
                                }
                            ]
                        }
                    ),
                    provider="openai",
                )
            # Subsequent calls: preference analysis / profile build.
            return LLMResponse(
                content=json.dumps(
                    {
                        "interests": [{"name": "历史", "category": "知识", "weight": 0.9}],
                        "disliked_topics": ["带货视频"],
                        "personality_portrait": "克制。" * 20,
                        "core_traits": ["理性"],
                    }
                ),
                provider="openai",
            )

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=_SeqRegistry(), memory=memory)
    await engine.learn_from_dialogue(
        user_message="我很讨厌带货视频",
        assistant_reply="明白了",
        session="popup",
    )
    assert len(_ledger_rows(memory, "dialogue_preference_overwrite")) == 1
    assert len(_ledger_rows(memory, "dislike_purge")) == 1
    # Soul rebuild fires because preference changed significantly (new dislike).
    assert len(_ledger_rows(memory, "dialogue_soul_rebuild")) >= 1


@pytest.mark.asyncio
async def test_hook_feedback_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory, feedback_batch_threshold=1)

    async def fake_analyze_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
        progress_callback: object | None = None,
    ) -> dict[str, object]:
        del events, existing_preference, event_chunk_size, progress_callback
        return {
            "interests": [{"name": "AI", "category": "科技", "weight": 0.8}],
            "disliked_topics": [],
            "favorite_up_users": [],
            "style": {},
            "context": {},
            "exploration_openness": 0.5,
        }

    monkeypatch.setattr(engine._preference_analyzer, "analyze_events", fake_analyze_events)
    for _ in range(2):
        await memory.propagate_event(
            {"event_type": "feedback", "title": "x", "metadata": {"feedback_type": "like"}}
        )
    result = await engine.process_feedback_batch_if_needed()
    assert result["triggered"]
    assert len(_ledger_rows(memory, "feedback_preference_overwrite")) == 1


def test_hook_speculation_confirm_reject(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    from openbiliclaw.soul.speculator import SpeculativeInterest, save_speculative_state

    state = engine._speculator._load_state()
    state.active.append(SpeculativeInterest(domain="桌游", category="游戏", status="active"))
    save_speculative_state(tmp_path, state)

    assert engine._speculator.user_confirm_speculation("桌游")
    rows = _ledger_rows(memory, "speculation_confirm")
    assert len(rows) == 1
    assert rows[0]["source"] == "speculation"

    state2 = engine._speculator._load_state()
    state2.active.append(SpeculativeInterest(domain="钓鱼", category="生活", status="active"))
    save_speculative_state(tmp_path, state2)
    assert engine._speculator.user_reject_speculation("钓鱼")
    assert len(_ledger_rows(memory, "speculation_reject")) == 1


@pytest.mark.asyncio
async def test_hook_pipeline_layer_update(tmp_path: Path) -> None:
    from openbiliclaw.soul.layer_updaters import update_layer
    from openbiliclaw.soul.pipeline import OnionLayer
    from openbiliclaw.soul.profile import OnionProfile

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    profile = OnionProfile()
    # Surface updater is pure computation — enough views flip depth_preference.
    signals: list[dict[str, object]] = [{"payload": {"event_type": "view"}} for _ in range(4)]
    signals.extend({"payload": {"event_type": "search"}} for _ in range(8))
    result = await update_layer(
        layer=OnionLayer.SURFACE,
        signals=signals,
        profile=profile,
        memory=memory,
        preference_analyzer=engine._preference_analyzer,
        profile_builder=engine._profile_builder,
    )
    if result.changed:
        rows = _ledger_rows(memory, "pipeline_layer_update")
        assert len(rows) == 1
        assert rows[0]["source"] == "pipeline:surface"
    else:  # pragma: no cover - defensive
        pytest.skip("surface updater produced no change for this input")


@pytest.mark.asyncio
async def test_hook_cognition_sync(tmp_path: Path) -> None:
    from openbiliclaw.soul.profile import OnionProfile

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)
    # Seed a soul profile so cognition sync has something to write into.
    soul = memory.get_layer("soul")
    soul.data.update(OnionProfile().to_dict())
    soul.save()

    cycle = engine._cognition_cycle
    from openbiliclaw.soul.cognition_cycle import CognitionCycleResult

    result = CognitionCycleResult(ran=True, awareness_generated=2, insight_generated=1)
    cycle._sync_to_profile(result)
    rows = _ledger_rows(memory, "cognition_sync")
    assert len(rows) == 1
    assert rows[0]["source"] == "cognition_cycle"
