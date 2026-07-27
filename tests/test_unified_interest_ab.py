"""Gate logic for the unified interest line A/B script (canned inputs, no LLM).

The runner half of ``scripts/run_unified_interest_ab.py`` needs a real database
and a real provider; the comparison half is pure and is where every wrong verdict
would come from, so it is pinned here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from scripts.run_unified_interest_ab import (
    DEFAULT_JACCARD_FLOOR,
    MIN_FEEDBACK_SAMPLE,
    SYNTHETIC_PREFIX,
    _append_synthetic_feedback,
    _clone_root,
    _feedback_rows,
    _open_root,
    build_summary,
    evaluate_gates,
    gate1_new_dislike_superset,
    gate2_top_interest_jaccard,
    gate3_retraction_not_amplified,
    interest_weights,
    jaccard,
    new_dislikes,
    render_table,
    top_interest_names,
)

if TYPE_CHECKING:
    from pathlib import Path


def _pref(
    interests: list[dict[str, Any]] | None = None,
    dislikes: list[str] | None = None,
) -> dict[str, Any]:
    return {"interests": interests or [], "disliked_topics": dislikes or []}


def _interest(name: str, weight: float, state: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "weight": weight, "category": "测试"}
    if state:
        item["state"] = state
    return item


class TestPrimitives:
    def test_new_dislikes_ignores_blanks_and_preexisting(self) -> None:
        before = _pref(dislikes=["标题党", "  "])
        after = _pref(dislikes=["标题党", "无脑鬼畜", ""])

        assert new_dislikes(before, after) == {"无脑鬼畜"}

    def test_new_dislikes_tolerates_a_non_list_field(self) -> None:
        assert new_dislikes({"disliked_topics": None}, {"disliked_topics": "标题党"}) == set()

    def test_interest_weights_skips_archived_and_coerces_bad_weights(self) -> None:
        preference = _pref(
            [
                _interest("城市建筑", 0.9),
                _interest("标题党", 0.7, state="archived"),
                {"name": "结构力学", "weight": "not-a-number"},
                {"name": "  ", "weight": 0.5},
                "junk",
            ]
        )

        assert interest_weights(preference) == {"城市建筑": 0.9, "结构力学": 0.0}

    def test_top_interest_names_orders_by_weight_then_name(self) -> None:
        preference = _pref(
            [
                _interest("b", 0.5),
                _interest("a", 0.5),
                _interest("c", 0.9),
            ]
        )

        assert top_interest_names(preference, top_n=2) == ["c", "a"]

    def test_jaccard_treats_two_empty_sets_as_identical(self) -> None:
        assert jaccard(set(), set()) == 1.0
        assert jaccard({"a"}, set()) == 0.0
        assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


class TestGate1NewDislikeSuperset:
    def test_passes_when_unified_covers_and_extends_legacy(self) -> None:
        gate = gate1_new_dislike_superset(
            legacy_before=_pref(dislikes=["旧避雷"]),
            legacy_after=_pref(dislikes=["旧避雷", "标题党"]),
            unified_before=_pref(dislikes=["旧避雷"]),
            unified_after=_pref(dislikes=["旧避雷", "标题党", "无脑鬼畜"]),
        )

        assert gate.passed is True
        assert gate.detail["missing_from_unified"] == []
        assert set(gate.detail["unified_new_dislikes"]) == {"标题党", "无脑鬼畜"}
        assert gate.detail["legacy_new_dislikes"] == ["标题党"]

    def test_fails_when_unified_drops_a_legacy_dislike(self) -> None:
        gate = gate1_new_dislike_superset(
            legacy_before=_pref(),
            legacy_after=_pref(dislikes=["标题党", "无脑鬼畜"]),
            unified_before=_pref(),
            unified_after=_pref(dislikes=["标题党"]),
        )

        assert gate.passed is False
        assert gate.detail["missing_from_unified"] == ["无脑鬼畜"]
        assert "无脑鬼畜" in gate.observed

    def test_passes_when_neither_path_added_a_dislike(self) -> None:
        gate = gate1_new_dislike_superset(
            legacy_before=_pref(dislikes=["旧避雷"]),
            legacy_after=_pref(dislikes=["旧避雷"]),
            unified_before=_pref(dislikes=["旧避雷"]),
            unified_after=_pref(dislikes=["旧避雷"]),
        )

        assert gate.passed is True


class TestGate2TopInterestJaccard:
    def test_identical_top_lists_score_one(self) -> None:
        preference = _pref([_interest("城市建筑", 0.9), _interest("结构力学", 0.8)])

        gate = gate2_top_interest_jaccard(legacy_after=preference, unified_after=preference)

        assert gate.passed is True
        assert gate.observed == "1.000"

    def test_one_swap_in_ten_still_clears_the_floor(self) -> None:
        legacy = _pref([_interest(f"i{n}", 1.0 - n / 100) for n in range(10)])
        unified = _pref(
            [_interest(f"i{n}", 1.0 - n / 100) for n in range(9)] + [_interest("新词", 0.90)]
        )

        gate = gate2_top_interest_jaccard(legacy_after=legacy, unified_after=unified)

        # 9 shared of 11 in the union = 0.818 >= 0.8
        assert gate.passed is True
        assert gate.detail["only_in_legacy"] == ["i9"]
        assert gate.detail["only_in_unified"] == ["新词"]

    def test_two_swaps_in_ten_fail_the_floor(self) -> None:
        legacy = _pref([_interest(f"i{n}", 1.0 - n / 100) for n in range(10)])
        unified = _pref(
            [_interest(f"i{n}", 1.0 - n / 100) for n in range(8)]
            + [_interest("新词甲", 0.90), _interest("新词乙", 0.89)]
        )

        gate = gate2_top_interest_jaccard(legacy_after=legacy, unified_after=unified)

        # 8 shared of 12 in the union = 0.667 < 0.8
        assert gate.passed is False
        assert float(gate.observed) < DEFAULT_JACCARD_FLOOR

    def test_floor_and_top_n_are_configurable(self) -> None:
        legacy = _pref([_interest("a", 0.9), _interest("b", 0.8)])
        unified = _pref([_interest("a", 0.9), _interest("c", 0.8)])

        assert (
            gate2_top_interest_jaccard(legacy_after=legacy, unified_after=unified, top_n=1).passed
            is True
        )
        assert (
            gate2_top_interest_jaccard(
                legacy_after=legacy, unified_after=unified, top_n=2, floor=0.3
            ).passed
            is True
        )
        assert (
            gate2_top_interest_jaccard(legacy_after=legacy, unified_after=unified, top_n=2).passed
            is False
        )


class TestGate3RetractionNotAmplified:
    def test_passes_when_the_retraction_changes_nothing_upward(self) -> None:
        baseline = _pref([_interest("城市建筑", 0.9)], dislikes=["标题党"])
        after = _pref([_interest("城市建筑", 0.7)], dislikes=["标题党"])

        gate = gate3_retraction_not_amplified(baseline_after=baseline, retraction_after=after)

        assert gate.passed is True
        assert gate.detail == {
            "added_dislikes": [],
            "raised_weights": {},
            "introduced_interests": [],
        }

    def test_fails_when_the_retraction_produces_a_new_dislike(self) -> None:
        baseline = _pref([_interest("城市建筑", 0.9)])
        after = _pref([_interest("城市建筑", 0.9)], dislikes=["城市建筑"])

        gate = gate3_retraction_not_amplified(baseline_after=baseline, retraction_after=after)

        assert gate.passed is False
        assert gate.detail["added_dislikes"] == ["城市建筑"]

    def test_fails_when_the_retraction_raises_an_interest_weight(self) -> None:
        baseline = _pref([_interest("城市建筑", 0.6)])
        after = _pref([_interest("城市建筑", 0.8)])

        gate = gate3_retraction_not_amplified(baseline_after=baseline, retraction_after=after)

        assert gate.passed is False
        assert gate.detail["raised_weights"] == {"城市建筑": [0.6, 0.8]}

    def test_fails_when_the_retraction_introduces_a_brand_new_interest(self) -> None:
        baseline = _pref([_interest("城市建筑", 0.6)])
        after = _pref([_interest("城市建筑", 0.6), _interest("凭空冒出", 0.5)])

        gate = gate3_retraction_not_amplified(baseline_after=baseline, retraction_after=after)

        assert gate.passed is False
        assert gate.detail["introduced_interests"] == ["凭空冒出"]

    def test_an_archived_interest_is_not_read_as_a_weight_change(self) -> None:
        baseline = _pref([_interest("标题党", 0.6)])
        after = _pref([_interest("标题党", 0.6, state="archived")])

        gate = gate3_retraction_not_amplified(baseline_after=baseline, retraction_after=after)

        assert gate.passed is True


class TestReportAssembly:
    def _gates(self) -> list[Any]:
        legacy_after = _pref([_interest("城市建筑", 0.9)], dislikes=["标题党"])
        unified_after = _pref([_interest("城市建筑", 0.9)], dislikes=["标题党", "无脑鬼畜"])
        return evaluate_gates(
            legacy_before=_pref(),
            legacy_after=legacy_after,
            unified_before=_pref(),
            unified_after=unified_after,
            unified_retraction_after=unified_after,
        )

    def test_evaluate_gates_returns_the_three_gates_in_spec_order(self) -> None:
        names = [gate.name for gate in self._gates()]

        assert names == [
            "gate1_new_dislike_superset",
            "gate2_top_interest_jaccard",
            "gate3_retraction_not_amplified",
        ]

    def test_summary_is_json_serialisable_and_reports_the_overall_verdict(self) -> None:
        gates = self._gates()

        summary = build_summary(
            gates=gates,
            sample_size=MIN_FEEDBACK_SAMPLE,
            synthetic_added=0,
            source_root="/tmp/isolated-root",
            baseline_commit="deadbee",
        )
        round_tripped = json.loads(json.dumps(summary, ensure_ascii=False))

        assert round_tripped["all_gates_passed"] is True
        assert round_tripped["sample_size"] == MIN_FEEDBACK_SAMPLE
        assert round_tripped["baseline_commit"] == "deadbee"
        assert [gate["name"] for gate in round_tripped["gates"]] == [gate.name for gate in gates]

    def test_summary_reports_failure_when_any_gate_fails(self) -> None:
        gates = evaluate_gates(
            legacy_before=_pref(),
            legacy_after=_pref(dislikes=["标题党"]),
            unified_before=_pref(),
            unified_after=_pref(),
            unified_retraction_after=_pref(),
        )

        summary = build_summary(
            gates=gates,
            sample_size=8,
            synthetic_added=2,
            source_root="/tmp/isolated-root",
            baseline_commit="deadbee",
        )

        assert summary["all_gates_passed"] is False
        assert summary["synthetic_feedback_added"] == 2

    def test_render_table_has_a_row_per_gate_and_a_verdict_column(self) -> None:
        table = render_table(self._gates())
        lines = table.splitlines()

        # header + separator + one row per gate
        assert len(lines) == 2 + 3
        assert "PASS" in table
        assert "gate2_top_interest_jaccard" in table


class TestSourceIsolation:
    """The synthetic top-up must never reach the source root (no LLM involved)."""

    @pytest.mark.asyncio
    async def test_synthetic_feedback_lands_on_the_copy_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(source))
        _, source_memory, _ = _open_root(source)
        await source_memory.propagate_event(
            {
                "event_type": "feedback",
                "title": "真实反馈",
                "metadata": {"feedback_type": "dislike", "feedback_note": ""},
            }
        )
        assert len(_feedback_rows(source_memory, after_event_id=0)) == 1

        sample = _clone_root(source, tmp_path / "sample")
        _, sample_memory, _ = _open_root(sample)
        await _append_synthetic_feedback(sample_memory, 3)

        sample_rows = _feedback_rows(sample_memory, after_event_id=0)
        assert len(sample_rows) == 4
        assert sum(SYNTHETIC_PREFIX in str(row.get("title", "")) for row in sample_rows) == 3

        # Reopen the source from scratch: it must still hold exactly one row.
        _, reopened_source, _ = _open_root(source)
        source_rows = _feedback_rows(reopened_source, after_event_id=0)
        assert len(source_rows) == 1
        assert SYNTHETIC_PREFIX not in str(source_rows[0].get("title", ""))
