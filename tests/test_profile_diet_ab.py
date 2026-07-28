"""Unit tests for the profile diet A/B replay helpers."""

from __future__ import annotations

import pytest
from scripts.run_profile_diet_ab import (
    ReplayCandidate,
    ReplayMetrics,
    ReplayPair,
    _build_engine,
    _DeterministicLLMService,
    _print_report,
    _score_contents,
    admission_flip_summary,
    cap_body_text,
    relative_gate,
    score_delta_summary,
    select_replay_rows,
    spearman_rank_correlation,
)

from openbiliclaw.discovery.engine import ContentDiscoveryEngine, compact_evaluation_profile_summary
from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import InterestTag, SoulProfile


def test_score_delta_summary_reports_mean_and_nearest_rank_p95() -> None:
    summary = score_delta_summary([0.20, 0.60, 0.90, 0.40], [0.10, 0.65, 0.70, 0.40])

    assert summary.mean_abs_delta == pytest.approx(0.0875)
    assert summary.p95_abs_delta == pytest.approx(0.20)


def test_spearman_rank_correlation_handles_ordering_and_ties() -> None:
    assert spearman_rank_correlation([0.1, 0.2, 0.3, 0.4], [1.0, 2.0, 3.0, 4.0]) == pytest.approx(
        1.0
    )
    assert spearman_rank_correlation([0.1, 0.2, 0.3, 0.4], [4.0, 3.0, 2.0, 1.0]) == pytest.approx(
        -1.0
    )
    assert spearman_rank_correlation([0.5, 0.5, 0.9], [0.4, 0.4, 0.8]) == pytest.approx(1.0)


def test_admission_flip_summary_uses_default_strategy_thresholds() -> None:
    candidates = [
        ReplayCandidate(candidate_id=1, title="search drops", source_strategy="search"),
        ReplayCandidate(candidate_id=2, title="explore rises", source_strategy="explore"),
        ReplayCandidate(candidate_id=3, title="unknown rises", source_strategy="custom"),
        ReplayCandidate(candidate_id=4, title="stable admitted", source_strategy="hot"),
    ]

    summary = admission_flip_summary(
        candidates,
        [0.61, 0.57, 0.59, 0.70],
        [0.59, 0.59, 0.61, 0.68],
    )

    assert summary.flip_count == 3
    assert summary.flip_rate == pytest.approx(0.75)
    assert summary.per_strategy == {"custom": 1, "explore": 1, "search": 1}


def test_select_replay_rows_filters_status_platform_and_orders_deterministically() -> None:
    rows = [
        {
            "id": 1,
            "status": "evaluated",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-04 10:00:00",
            "last_seen_at": "2026-07-04 10:00:00",
        },
        {
            "id": 2,
            "status": "cached",
            "source_platform": "xiaohongshu",
            "evaluated_at": "2026-07-04 11:00:00",
            "last_seen_at": "2026-07-04 11:00:00",
        },
        {
            "id": 3,
            "status": "evaluated",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-04 12:00:00",
            "last_seen_at": "2026-07-04 12:00:00",
        },
        {
            "id": 4,
            "status": "evaluated",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-04 12:00:00",
            "last_seen_at": "2026-07-04 12:00:00",
        },
        {
            "id": 5,
            "status": "pending_eval",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-05 12:00:00",
            "last_seen_at": "2026-07-05 12:00:00",
        },
        {
            "id": 6,
            "status": "rejected_low_score",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-04 13:00:00",
            "last_seen_at": "2026-07-04 13:00:00",
        },
    ]

    selected = select_replay_rows(rows, sample=4, platform="bilibili")

    assert [row["id"] for row in selected] == [6, 4, 3, 1]


def test_admission_flip_summary_uses_runtime_and_row_thresholds() -> None:
    candidates = [
        ReplayCandidate(
            candidate_id=1,
            title="custom floor",
            source_strategy="search",
            score_threshold=0.75,
        ),
        ReplayCandidate(candidate_id=2, title="global floor", source_strategy="search"),
    ]

    summary = admission_flip_summary(
        candidates,
        [0.74, 0.64],
        [0.76, 0.66],
        admission_min_score=0.65,
    )

    assert summary.flip_count == 2


def _pair(
    *,
    kind: str,
    repeat: int,
    flip_rate: float,
    spearman: float,
    admission_delta: float,
) -> ReplayPair:
    return ReplayPair(
        repeat=repeat,
        kind=kind,
        first_arm="A",
        scores_a=(0.6,),
        scores_b=(0.6,),
        metrics=ReplayMetrics(
            mean_abs_delta=0.0,
            p95_abs_delta=0.0,
            spearman=spearman,
            flip_rate=flip_rate,
            flip_count=round(flip_rate * 100),
            admitted_a=50,
            admitted_b=round(50 + admission_delta * 100),
            admission_rate_delta=admission_delta,
        ),
    )


def test_relative_gate_uses_repeated_control_envelope() -> None:
    controls = [
        _pair(kind="control", repeat=1, flip_rate=0.18, spearman=0.83, admission_delta=0.01),
        _pair(kind="control", repeat=2, flip_rate=0.21, spearman=0.80, admission_delta=-0.01),
        _pair(kind="control", repeat=3, flip_rate=0.19, spearman=0.82, admission_delta=0.00),
    ]
    treatments = [
        _pair(kind="treatment", repeat=1, flip_rate=0.17, spearman=0.84, admission_delta=0.01),
        _pair(kind="treatment", repeat=2, flip_rate=0.16, spearman=0.81, admission_delta=0.00),
        _pair(kind="treatment", repeat=3, flip_rate=0.18, spearman=0.82, admission_delta=0.02),
    ]

    passed, gate = relative_gate(controls, treatments)

    assert passed is True
    assert gate["control_flip_ceiling"] == pytest.approx(0.21)
    assert gate["control_spearman_floor"] == pytest.approx(0.80)


def test_relative_gate_rejects_admission_shrink() -> None:
    controls = [
        _pair(kind="control", repeat=index, flip_rate=0.02, spearman=0.98, admission_delta=0.0)
        for index in range(1, 4)
    ]
    treatments = [
        _pair(
            kind="treatment",
            repeat=index,
            flip_rate=0.02,
            spearman=0.98,
            admission_delta=-0.05,
        )
        for index in range(1, 4)
    ]

    passed, _gate = relative_gate(controls, treatments)

    assert passed is False


def test_select_replay_rows_preserves_recent_production_mix() -> None:
    """The gate must not reweight platform/strategy groups."""
    rows = []
    for index in range(6):
        rows.append(
            {
                "id": 100 + index,
                "status": "cached",
                "source_platform": "reddit",
                "source_strategy": "subreddit",
                "evaluated_at": f"2026-07-05 12:0{index}:00",
            }
        )
    rows.append(
        {
            "id": 200,
            "status": "cached",
            "source_platform": "bilibili",
            "source_strategy": "search",
            "evaluated_at": "2026-07-01 08:00:00",
        }
    )

    selected = select_replay_rows(rows, sample=4)

    assert [row["id"] for row in selected] == [105, 104, 103, 102]
    # Deterministic: same input -> same output.
    assert [row["id"] for row in select_replay_rows(rows, sample=4)] == [
        row["id"] for row in selected
    ]


def test_cap_body_text_keeps_short_text_and_caps_long_text() -> None:
    short = "short body"
    long = "h" * 300 + "m" * 100 + "t" * 200

    assert cap_body_text(short) == short
    assert cap_body_text(long) == ("h" * 200) + "\u2026" + ("t" * 100)


class _ReplayDiscoveryConfig:
    multimodal_evaluation_enabled = False
    multimodal_batch_size = 8
    multimodal_image_max_px = 384
    multimodal_image_quality = 72
    multimodal_image_timeout_seconds = 6


class _ReplayConfig:
    discovery = _ReplayDiscoveryConfig()


class _ReplayEmbedding:
    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if text else []


class _RecordingMultimodalService:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def complete_multimodal_structured_task(self, **kwargs: object) -> LLMResponse:
        self.kwargs = dict(kwargs)
        return LLMResponse(
            content="{}",
            provider="sensenova",
            instance_id="gateway",
            model="reasoning-model",
            usage={"output_tokens": 12},
        )


@pytest.mark.asyncio
async def test_deterministic_wrapper_keeps_production_budget_for_multimodal() -> None:
    inner = _RecordingMultimodalService()
    service = _DeterministicLLMService(inner)

    await service.complete_multimodal_structured_task(
        system_instruction="system",
        user_input="user",
        image_inputs=[],
        max_tokens=4096,
    )

    assert inner.kwargs["temperature"] == 0.0
    assert inner.kwargs["max_tokens"] == 4096
    assert service.calls == [
        {
            "method": "complete_multimodal_structured_task",
            "caller": "",
            "provider": "sensenova",
            "instance_id": "gateway",
            "model": "reasoning-model",
            "max_tokens": 4096,
            "usage": {"output_tokens": 12},
        }
    ]


class _MissingResponseEngine:
    _EVALUATE_BATCH_HARD_CAP = 90

    async def evaluate_content_batch(
        self,
        contents: list[object],
        profile: object,
        *,
        source_context: str,
        batch_size: int,
    ) -> list[float]:
        del profile, source_context, batch_size
        contents[0].relevance_reason = "evaluation_response_missing"
        return [0.0 for _content in contents]


@pytest.mark.asyncio
async def test_score_contents_rejects_missing_evaluation_responses() -> None:
    class _Content:
        content_id = "failed-item"
        title = "failed"
        relevance_reason = ""

    with pytest.raises(RuntimeError, match="cannot be counted as zero-score"):
        await _score_contents(
            _MissingResponseEngine(),
            [_Content()],
            object(),
            source_context="replay",
        )


def _many_interest_profile() -> SoulProfile:
    profile = SoulProfile()
    profile.preferences.interests = [
        InterestTag(name=f"兴趣{index}", category="测试", weight=1.0 - index / 1000)
        for index in range(80)
    ]
    return profile


def test_compact_replay_arm_a_forces_legacy_full_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After production becomes compact, compact-arm A must still be full-profile legacy."""

    def production_summary(profile: SoulProfile) -> dict[str, object]:
        return compact_evaluation_profile_summary(build_profile_summary(profile))

    monkeypatch.setattr(
        ContentDiscoveryEngine,
        "_evaluation_profile_summary",
        staticmethod(production_summary),
    )

    engine = _build_engine(
        object(),
        _ReplayConfig(),
        compact_profile=False,
        negative_examples=None,
        legacy_profile=True,
        embedding_service=None,
    )

    summary = engine._evaluation_profile_summary(_many_interest_profile())
    interests = summary["interests"]
    assert isinstance(interests, list)
    assert len(interests) == 80


def test_replay_engine_receives_embedding_service_for_production_recall() -> None:
    embedding = _ReplayEmbedding()

    engine = _build_engine(
        object(),
        _ReplayConfig(),
        compact_profile=True,
        negative_examples=None,
        legacy_profile=False,
        embedding_service=embedding,
    )

    assert engine._embedding_service is embedding  # noqa: SLF001


def test_replay_report_mentions_when_compact_recall_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_report(
        arm_b="compact",
        candidates=[ReplayCandidate(candidate_id=1, title="item", source_strategy="search")],
        scores_a=[0.7],
        scores_b=[0.7],
        platform=None,
        recall_note="related_interests recall disabled: embedding service unavailable",
    )

    output = capsys.readouterr().out
    assert "related_interests recall disabled: embedding service unavailable" in output


def test_legacy_reason_prompts_swaps_and_restores() -> None:
    """reason-diet arm A must really restore the legacy prompts, then undo."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_profile_diet_ab as script

    from openbiliclaw.llm import prompts as prompts_module

    before_single = prompts_module._SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT
    before_batch = prompts_module._BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
    with script.legacy_reason_prompts():
        assert "只写一句中文" in prompts_module._SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT
        assert "3a. reason" not in prompts_module._BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
        assert "reason(一句中文)" in prompts_module._BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
    assert before_single == prompts_module._SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT
    assert before_batch == prompts_module._BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
