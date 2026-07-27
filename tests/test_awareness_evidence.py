"""Tests for the Phase 0 awareness evidence chain (note_id + source_event_ids).

Spec: docs/plans/2026-07-17-cognitive-profile-pipeline-spec.md §Phase 0.
Plan: docs/plans/2026-07-17-cognitive-profile-pipeline-plan.md Task 1.
"""

from __future__ import annotations

import json

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
from openbiliclaw.soul.profile import (
    AwarenessNote,
    awareness_note_from_dict,
    awareness_note_to_dict,
)


class FakeStructuredService:
    def __init__(self, content: str) -> None:
        self.content = content

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        return LLMResponse(content=self.content, provider="openai")


_NOTE_JSON = json.dumps(
    [
        {
            "date": "2026-03-08",
            "observation": "最近连续浏览高信息密度内容。",
            "trend": "更偏向深度解释。",
            "emotion_guess": "专注",
        }
    ],
    ensure_ascii=False,
)


@pytest.mark.asyncio
async def test_analyze_attaches_note_id_and_source_event_ids() -> None:
    analyzer = AwarenessAnalyzer(FakeStructuredService(_NOTE_JSON))
    notes = await analyzer.analyze(
        events=[{"event_type": "view", "title": "x", "id": 11}],
        preference={},
        soul_profile={},
        source_event_ids=[11, 12, 13],
    )
    assert len(notes) == 1
    assert notes[0].note_id  # generated, non-empty
    assert notes[0].source_event_ids == [11, 12, 13]
    assert notes[0].source_event_ids_approximate is True


@pytest.mark.asyncio
async def test_analyze_derives_ids_from_events_when_not_passed() -> None:
    analyzer = AwarenessAnalyzer(FakeStructuredService(_NOTE_JSON))
    notes = await analyzer.analyze(
        events=[
            {"event_type": "view", "title": "a", "id": 5},
            {"event_type": "view", "title": "b", "id": 6},
        ],
        preference={},
        soul_profile={},
    )
    assert notes[0].source_event_ids == [5, 6]


@pytest.mark.asyncio
async def test_analyze_no_ids_yields_empty_and_not_approximate() -> None:
    analyzer = AwarenessAnalyzer(FakeStructuredService(_NOTE_JSON))
    notes = await analyzer.analyze(
        events=[{"event_type": "view", "title": "a"}],
        preference={},
        soul_profile={},
    )
    assert notes[0].source_event_ids == []
    assert notes[0].source_event_ids_approximate is False


def test_awareness_note_roundtrip_preserves_evidence() -> None:
    note = AwarenessNote(
        date="2026-03-08",
        observation="obs",
        note_id="abc123",
        source_event_ids=[1, 2, 3],
        source_event_ids_approximate=True,
    )
    restored = awareness_note_from_dict(awareness_note_to_dict(note))
    assert restored == note


def test_awareness_note_from_legacy_dict_defaults_evidence() -> None:
    # Old persisted data lacked note_id / source_event_ids.
    legacy = {
        "date": "2026-03-08",
        "observation": "obs",
        "trend": "t",
        "emotion_guess": "e",
    }
    restored = awareness_note_from_dict(legacy)
    assert restored.note_id == ""
    assert restored.source_event_ids == []
    assert restored.source_event_ids_approximate is False


def test_awareness_note_from_dict_ignores_malformed_ids() -> None:
    restored = awareness_note_from_dict({"observation": "o", "source_event_ids": [1, "x", None, 3]})
    assert restored.source_event_ids == [1, 3]


def test_awareness_prompt_bytes_unchanged_by_evidence_chain() -> None:
    # The evidence chain is a parse-side enhancement; the prompt itself must
    # stay byte-identical (回放不变性: analyze() render path).
    from openbiliclaw.llm.prompts import build_awareness_prompt

    messages = build_awareness_prompt(
        events=[{"event_type": "view", "title": "x", "id": 11}],
        preference_summary={"a": 1},
        soul_profile={"x": 1},
    )
    user = messages[1]["content"]
    assert "note_id" not in user
    assert "source_event_ids" not in user
