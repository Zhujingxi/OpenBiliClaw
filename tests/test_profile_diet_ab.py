"""Unit tests for the profile diet A/B replay helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import scripts.run_profile_diet_ab as replay_script
from scripts.run_profile_diet_ab import (
    _REPLAY_SOURCE_CONTEXT,
    ModelOverride,
    ReplayCandidate,
    ReplayEmbeddingAudit,
    ReplayEmbeddingValidationError,
    ReplayMetrics,
    ReplayPair,
    ReplayProfileSnapshot,
    ReplayRecallAudit,
    _build_engine,
    _DeterministicLLMService,
    _load_profile_snapshot,
    _print_report,
    _rows_to_contents,
    _score_contents,
    _write_artifact,
    admission_flip_summary,
    body_cap_affected_count,
    cap_body_text,
    configured_topic_lifecycle_serialization,
    legacy_body_text_prompt_caps,
    relative_gate,
    replay_blocking_reasons,
    replay_call_attribution,
    run_scoped_embedding_audit,
    score_delta_summary,
    select_replay_rows,
    spearman_rank_correlation,
    validate_replay_prefilter_compatibility,
    validate_replay_routes,
)

from openbiliclaw.discovery.engine import ContentDiscoveryEngine, compact_evaluation_profile_summary
from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.llm.base import LLMRateLimitError, LLMResponse
from openbiliclaw.llm.service import LLMProviderExecutionError
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.overrides import ListEdit, ProfileOverrides
from openbiliclaw.soul.profile import InterestDomain, InterestTag, OnionProfile, SoulProfile
from openbiliclaw.soul.speculator import (
    SpeculativeInterest,
    SpeculativeState,
    save_speculative_state,
)


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


def _passing_replay_gate_inputs() -> dict[str, object]:
    return {
        "quality_passed": True,
        "route_audit": {"passed": True, "blocking_reasons": []},
        "embedding_audit": {"passed": True, "blocking_reasons": []},
        "recall_audit": {"passed": True, "blocking_reasons": []},
        "body_cap": False,
        "body_cap_affected": 0,
        "body_cap_contract_matches": True,
        "profile_snapshot_stable": True,
        "candidate_snapshot_stable": True,
    }


def test_replay_final_gate_accepts_only_complete_evidence() -> None:
    assert replay_blocking_reasons(**_passing_replay_gate_inputs()) == []  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        ("quality", "relative quality gate failed"),
        ("route", "route audit failed"),
        ("embedding", "embedding audit failed"),
        ("recall", "recall audit failed"),
        ("body_zero", "zero candidates affected"),
        ("body_contract", "body caps no longer match"),
        ("profile", "profile snapshot drifted"),
        ("candidate", "candidate snapshot drifted"),
    ],
)
def test_replay_final_gate_blocks_each_independent_failure(
    failure: str,
    expected_reason: str,
) -> None:
    inputs = _passing_replay_gate_inputs()
    if failure == "quality":
        inputs["quality_passed"] = False
    elif failure in {"route", "embedding", "recall"}:
        inputs[f"{failure}_audit"] = {"passed": False, "blocking_reasons": []}
    elif failure == "body_zero":
        inputs.update(body_cap=True, body_cap_affected=0)
    elif failure == "body_contract":
        inputs.update(body_cap=True, body_cap_affected=1, body_cap_contract_matches=False)
    elif failure == "profile":
        inputs["profile_snapshot_stable"] = False
    elif failure == "candidate":
        inputs["candidate_snapshot_stable"] = False

    reasons = replay_blocking_reasons(**inputs)  # type: ignore[arg-type]

    assert expected_reason in " ".join(reasons)


def test_artifact_keeps_raw_scores_digests_usage_routes_without_private_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.run_profile_diet_ab._git_metadata",
        lambda: {"commit": "abc123", "dirty": False},
    )
    candidate = ReplayCandidate(
        candidate_id=1,
        title="PRIVATE TITLE",
        source_strategy="feed",
        source_platform="twitter",
        content_id="item-1",
    )
    pair = _pair(
        kind="control",
        repeat=1,
        flip_rate=0.0,
        spearman=1.0,
        admission_delta=0.0,
    )
    output = tmp_path / "artifact.json"

    _write_artifact(
        output,
        args=SimpleNamespace(arm_b="compact", repeats=3, platform=None),
        db_path=tmp_path / "production.db",
        config_path=tmp_path / "config.toml",
        rows=[
            {
                "id": 1,
                "status": "evaluated",
                "body_text": "PRIVATE BODY",
                "title": "PRIVATE TITLE",
            }
        ],
        profile_snapshot=ReplayProfileSnapshot(
            raw_profile=object(),
            effective_profile=object(),
            raw_digest="raw-digest",
            effective_digest="effective-digest",
            overrides_present=True,
            active_speculation_count=2,
        ),
        negative_examples=None,
        candidates=[candidate],
        control_pairs=[pair],
        treatment_pairs=[pair],
        gate_passed=True,
        gate={"blocking_reasons": []},
        admission_min_score=0.6,
        calls=[{"provider": "openai", "usage": {"output_tokens": 7}}],
        route_audit={"passed": True, "logical_runs": []},
        embedding_audit={"passed": True, "namespace": "embed-v1"},
        recall_audit={"passed": True, "injected_label_count": 0},
        body_cap_affected=1,
        production_prefilter_mode="shadow",
        topic_lifecycle_serialization=True,
    )

    raw_artifact = output.read_text(encoding="utf-8")
    artifact = json.loads(raw_artifact)
    assert artifact["schema_version"] == 2
    assert artifact["snapshot"]["raw_profile_digest"] == "raw-digest"
    assert artifact["control_pairs"][0]["scores_a"] == [0.6]
    assert artifact["control_pairs"][0]["scores_a_digest"]
    assert artifact["llm_calls"][0]["usage"] == {"output_tokens": 7}
    assert artifact["routes"]["passed"] is True
    assert artifact["gate_constants"]["llm_max_tokens"] == 4096
    assert artifact["production_context"] == {
        "eval_prefilter_mode": "shadow",
        "topic_lifecycle_serialization": "on",
    }
    assert artifact["replay_context"] == {"eval_prefilter_mode": "off"}
    assert "PRIVATE TITLE" not in raw_artifact
    assert "PRIVATE BODY" not in raw_artifact


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


def test_body_cap_arm_changes_prompt_cap_without_mutating_candidate_body() -> None:
    from openbiliclaw.discovery import engine as engine_module

    body = "description prefix" + ("x" * 500)
    rows = [
        {
            "id": 1,
            "content_id": "tweet-1",
            "source_platform": "twitter",
            "source_strategy": "feed",
            "title": "long post",
            "description": "description prefix",
            "body_text": body,
        }
    ]

    content = _rows_to_contents(rows)[0]

    assert content.body_text == body
    assert body_cap_affected_count(rows) == 1
    assert engine_module._prompt_description_for_content(content, limit=400) == ""
    assert engine_module._prompt_body_text(
        content.body_text,
        head=engine_module._EVALUATION_BODY_TEXT_HEAD_CAP,
        tail=engine_module._EVALUATION_BODY_TEXT_TAIL_CAP,
    ) == cap_body_text(body)
    assert engine_module._batch_evaluation_content_item(
        content,
        source_context="replay",
    )["body_text"] == cap_body_text(body)
    with legacy_body_text_prompt_caps():
        assert (
            engine_module._prompt_body_text(
                content.body_text,
                head=engine_module._EVALUATION_BODY_TEXT_HEAD_CAP,
                tail=engine_module._EVALUATION_BODY_TEXT_TAIL_CAP,
            )
            == body
        )
        # Description dedup still sees the original body in both arms.
        assert engine_module._prompt_description_for_content(content, limit=400) == ""
        assert (
            engine_module._batch_evaluation_content_item(
                content,
                source_context="replay",
            )["body_text"]
            == body
        )

    assert engine_module._EVALUATION_BODY_TEXT_HEAD_CAP == 200
    assert engine_module._EVALUATION_BODY_TEXT_TAIL_CAP == 100


def test_profile_snapshot_matches_effective_soul_profile_contract(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    raw_profile = OnionProfile()
    raw_profile.core.core_traits = ["raw trait"]
    soul_layer = memory.get_layer("soul")
    soul_layer.data.update(raw_profile.to_dict())
    soul_layer.save()
    memory.save_profile_overrides(
        ProfileOverrides(
            list_edits={"core.core_traits": ListEdit(add=["user pinned"], remove=["raw trait"])}
        )
    )
    save_speculative_state(
        tmp_path,
        SpeculativeState(
            active=[
                SpeculativeInterest(domain="active guess", reason="evidence", status="active"),
                SpeculativeInterest(domain="confirmed", reason="done", status="confirmed"),
            ]
        ),
    )

    snapshot = _load_profile_snapshot(tmp_path)

    assert snapshot.raw_profile.core.core_traits == ["raw trait"]
    assert snapshot.effective_profile.core.core_traits == ["user pinned"]
    assert snapshot.overrides_present is True
    assert snapshot.active_speculation_count == 1
    assert [
        item.domain
        for item in snapshot.effective_profile._active_speculations  # type: ignore[attr-defined]
    ] == ["active guess"]
    assert snapshot.raw_digest != snapshot.effective_digest


def test_replay_mirrors_and_restores_topic_lifecycle_serialization_config() -> None:
    from openbiliclaw.soul.profile_views import (
        set_topic_lifecycle_serialization,
        topic_lifecycle_serialization_enabled,
    )

    profile = OnionProfile()
    profile.interest.likes = [
        InterestDomain(domain="active topic", weight=0.8, state="active"),
        InterestDomain(domain="archived topic", weight=0.9, state="archived"),
    ]
    enabled_config = SimpleNamespace(soul=SimpleNamespace(topic_lifecycle_serialization="on"))
    disabled_config = SimpleNamespace(soul=SimpleNamespace(topic_lifecycle_serialization="off"))

    set_topic_lifecycle_serialization(False)
    try:
        with configured_topic_lifecycle_serialization(enabled_config) as enabled:
            assert enabled is True
            assert topic_lifecycle_serialization_enabled() is True
            assert [
                item["domain"] for item in build_profile_summary(profile)["interest_domains"]
            ] == ["active topic"]
        assert topic_lifecycle_serialization_enabled() is False

        set_topic_lifecycle_serialization(True)
        with configured_topic_lifecycle_serialization(disabled_config) as enabled:
            assert enabled is False
            assert topic_lifecycle_serialization_enabled() is False
        assert topic_lifecycle_serialization_enabled() is True
    finally:
        set_topic_lifecycle_serialization(False)


@pytest.mark.parametrize("mode", ["off", "shadow", "invalid"])
def test_replay_accepts_non_enforcing_production_prefilter(mode: str) -> None:
    config = SimpleNamespace(discovery=SimpleNamespace(eval_prefilter_mode=mode))

    assert validate_replay_prefilter_compatibility(config) == (
        mode if mode in {"off", "shadow"} else "shadow"
    )


def test_replay_rejects_enforcing_production_prefilter() -> None:
    config = SimpleNamespace(discovery=SimpleNamespace(eval_prefilter_mode="enforce"))

    with pytest.raises(RuntimeError, match="production config is enforce"):
        validate_replay_prefilter_compatibility(config)


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


class _SequenceEmbedding:
    cache_model_namespace = "provider:model#namespace=test"
    similarity_threshold = 0.82

    def __init__(self, results: list[object]) -> None:
        self.results = list(results)

    async def embed(self, text: str) -> object:
        del text
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "message"),
    [
        ([], "empty or non-list"),
        ([1.0, float("nan")], "NaN or infinity"),
        ([1.0, "bad"], "non-numeric"),
        (RuntimeError("provider down"), "raised RuntimeError"),
    ],
)
async def test_embedding_audit_fails_closed_on_invalid_results(
    result: object,
    message: str,
) -> None:
    audit = ReplayEmbeddingAudit(_SequenceEmbedding([result]))

    with pytest.raises(ReplayEmbeddingValidationError, match=message):
        await audit.embed("interest")

    assert audit.calls[0]["status"] == "error"
    assert audit.errors


@pytest.mark.asyncio
async def test_embedding_audit_rejects_dimension_drift() -> None:
    audit = ReplayEmbeddingAudit(_SequenceEmbedding([[1.0, 0.0], [1.0, 0.0, 0.0]]))

    await audit.embed("interest")
    with pytest.raises(ReplayEmbeddingValidationError, match="dimension drift"):
        await audit.embed("content")


@pytest.mark.asyncio
async def test_embedding_audit_accepts_complete_vectors_with_zero_injection() -> None:
    audit = ReplayEmbeddingAudit(_SequenceEmbedding([[1.0, 0.0], [0.0, 1.0]]))
    recall = ReplayRecallAudit()
    with replay_call_attribution(
        pair_kind="treatment",
        repeat=1,
        logical_run="B",
        arm="B",
    ):
        await audit.embed("tail interest")
        await audit.embed("unrelated content")
        recall.record_batch({}, candidate_count=1)

    summary = audit.summary(eligible_tail_count=1, recall_audit=recall)

    assert summary["passed"] is True
    assert summary["call_count"] == 2
    assert recall.payload()["injected_label_count"] == 0


def test_embedding_audit_accepts_zero_tail_without_requests() -> None:
    audit = ReplayEmbeddingAudit(_SequenceEmbedding([]))

    summary = audit.summary(eligible_tail_count=0, recall_audit=ReplayRecallAudit())

    assert summary["passed"] is True
    assert summary["call_count"] == 0


def test_run_scoped_embedding_cache_lives_through_context_and_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cache:
        closed = False

        def close(self) -> None:
            self.closed = True

    class _Service:
        cache_model_namespace = "test:model"

        def __init__(self) -> None:
            self._l2_cache = _Cache()

    config = SimpleNamespace(
        data_dir=str(tmp_path / "production"),
        llm=SimpleNamespace(embedding=SimpleNamespace(provider="test")),
    )
    service = _Service()
    monkeypatch.setattr(
        "scripts.run_profile_diet_ab._build_embedding_service",
        lambda _config: service,
    )

    with run_scoped_embedding_audit(config, allow_no_embedding=False) as audit:
        cache_dir = Path(config.data_dir)
        assert cache_dir.exists()
        assert audit is not None
        assert service._l2_cache.closed is False

    assert not cache_dir.exists()
    assert service._l2_cache.closed is True
    assert config.data_dir == str(tmp_path / "production")


def test_no_embedding_requires_explicit_degraded_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        data_dir=str(tmp_path / "production"),
        llm=SimpleNamespace(embedding=SimpleNamespace(provider="", fallback_provider="")),
    )
    monkeypatch.setattr(
        "scripts.run_profile_diet_ab._build_embedding_service",
        lambda _config: None,
    )

    with (
        pytest.raises(RuntimeError, match="--allow-no-embedding"),
        run_scoped_embedding_audit(config, allow_no_embedding=False),
    ):
        pass
    with run_scoped_embedding_audit(config, allow_no_embedding=True) as audit:
        assert audit is None


def test_degraded_flag_cannot_mask_configured_embedding_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        data_dir=str(tmp_path / "production"),
        llm=SimpleNamespace(embedding=SimpleNamespace(provider="ollama", fallback_provider="")),
    )
    monkeypatch.setattr(
        "scripts.run_profile_diet_ab._build_embedding_service",
        lambda _config: None,
    )

    with (
        pytest.raises(RuntimeError, match="could not be constructed"),
        run_scoped_embedding_audit(config, allow_no_embedding=True),
    ):
        pass


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
    service = _DeterministicLLMService(inner, service="arm_a")

    with replay_call_attribution(
        pair_kind="control",
        repeat=2,
        logical_run="A1",
        arm="A",
    ):
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
            "service": "arm_a",
            "pair_kind": "control",
            "repeat": 2,
            "logical_run": "A1",
            "arm": "A",
            "method": "complete_multimodal_structured_task",
            "caller": "",
            "provider": "sensenova",
            "instance_id": "gateway",
            "model": "reasoning-model",
            "temperature": 0.0,
            "max_tokens": 4096,
            "usage": {"output_tokens": 12},
            "status": "ok",
        }
    ]


@pytest.mark.asyncio
async def test_deterministic_wrapper_labels_transient_rate_limit_for_route_audit() -> None:
    class _RateLimitedService:
        async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
            del kwargs
            try:
                raise LLMRateLimitError("openai_compatible rate limit exceeded")
            except LLMRateLimitError as exc:
                raise LLMProviderExecutionError("All providers failed") from exc

    service = _DeterministicLLMService(_RateLimitedService(), service="arm_a")

    with (
        replay_call_attribution(
            pair_kind="control",
            repeat=1,
            logical_run="A1",
            arm="A",
        ),
        pytest.raises(LLMProviderExecutionError),
    ):
        await service.complete_structured_task(system_instruction="system", user_input="user")

    assert service.calls[0]["status"] == "error"
    assert service.calls[0]["error_kind"] == "transient_rate_limit"


def _attributed_route_calls(
    *,
    treatment_b_route: tuple[str, str, str] = ("openai", "primary", "model-a"),
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    baseline_route = ("openai", "primary", "model-a")
    for repeat in range(1, 4):
        for pair_kind, logical_run, arm in (
            ("control", "A1", "A"),
            ("control", "A2", "A"),
            ("treatment", "A", "A"),
            ("treatment", "B", "B"),
        ):
            route = treatment_b_route if pair_kind == "treatment" and arm == "B" else baseline_route
            calls.append(
                {
                    "pair_kind": pair_kind,
                    "repeat": repeat,
                    "logical_run": logical_run,
                    "arm": arm,
                    "provider": route[0],
                    "instance_id": route[1],
                    "model": route[2],
                    "status": "ok",
                }
            )
    return calls


def test_route_audit_enforces_non_model_arm_equivalence() -> None:
    passed = validate_replay_routes(
        _attributed_route_calls(),
        repeats=3,
        model_override=None,
    )
    drifted = validate_replay_routes(
        _attributed_route_calls(treatment_b_route=("openai_compatible", "fallback", "model-b")),
        repeats=3,
        model_override=None,
    )

    assert passed["passed"] is True
    assert drifted["passed"] is False
    assert "drifted route" in " ".join(drifted["blocking_reasons"])


def test_route_audit_allows_only_requested_model_treatment_route() -> None:
    audit = validate_replay_routes(
        _attributed_route_calls(
            treatment_b_route=("openai_compatible", "diet-instance", "diet-model")
        ),
        repeats=3,
        model_override=ModelOverride(provider="diet-instance", model=""),
    )

    assert audit["passed"] is True
    assert len(audit["logical_runs"]) == 12


def test_route_audit_rejects_consistent_unexpected_failover() -> None:
    audit = validate_replay_routes(
        _attributed_route_calls(),
        repeats=3,
        model_override=None,
        expected_control_instance="configured-primary",
        expected_treatment_instance="configured-primary",
    )

    assert audit["passed"] is False
    assert "unexpectedly failed over" in " ".join(audit["blocking_reasons"])


def test_route_audit_rejects_empty_and_mixed_routes_within_logical_run() -> None:
    calls = _attributed_route_calls()
    calls[0]["model"] = ""
    calls.append(
        {
            **calls[1],
            "pair_kind": "control",
            "repeat": 1,
            "logical_run": "A2",
            "instance_id": "unexpected",
        }
    )

    audit = validate_replay_routes(calls, repeats=3, model_override=None)
    reasons = " ".join(audit["blocking_reasons"])

    assert audit["passed"] is False
    assert "empty actual route" in reasons
    assert "mixed 2 actual routes" in reasons


def test_route_audit_allows_a_recovered_transient_rate_limit() -> None:
    calls = _attributed_route_calls()
    calls.append(
        {
            **calls[0],
            "provider": "",
            "instance_id": "",
            "model": "",
            "status": "error",
            "error_kind": "transient_rate_limit",
        }
    )

    audit = validate_replay_routes(calls, repeats=3, model_override=None)

    assert audit["passed"] is True
    assert audit["recovered_rate_limit_call_count"] == 1
    recovered_run = next(
        run
        for run in audit["logical_runs"]
        if run["pair_kind"] == "control" and run["repeat"] == 1 and run["logical_run"] == "A1"
    )
    assert recovered_run["call_count"] == 2
    assert recovered_run["successful_call_count"] == 1
    assert recovered_run["recovered_rate_limit_call_count"] == 1


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


@pytest.mark.asyncio
async def test_score_contents_matches_production_claim_grouping_and_context() -> None:
    class _RecordingEngine:
        _EVALUATE_BATCH_HARD_CAP = 90

        def __init__(self) -> None:
            self.calls: list[tuple[int, str, int]] = []

        async def evaluate_content_batch(
            self,
            contents: list[object],
            profile: object,
            *,
            source_context: str,
            batch_size: int,
        ) -> list[float]:
            del profile
            self.calls.append((len(contents), source_context, batch_size))
            return [0.7] * len(contents)

    engine = _RecordingEngine()
    contents = [
        SimpleNamespace(content_id=f"candidate-{index}", relevance_reason="")
        for index in range(100)
    ]

    scores = await _score_contents(
        engine,
        contents,
        object(),
        source_context=_REPLAY_SOURCE_CONTEXT,
    )

    assert _REPLAY_SOURCE_CONTEXT == "mixed"
    assert engine.calls == [
        (30, "mixed", 30),
        (30, "mixed", 30),
        (30, "mixed", 30),
        (10, "mixed", 30),
    ]
    assert scores == [0.7] * 100


@pytest.mark.asyncio
async def test_score_contents_retries_transient_rate_limit_and_restores_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    class _RateLimitedOnceEngine:
        _EVALUATE_BATCH_HARD_CAP = 90

        def __init__(self) -> None:
            self.states: list[tuple[float, str]] = []

        async def evaluate_content_batch(
            self,
            contents: list[object],
            profile: object,
            *,
            source_context: str,
            batch_size: int,
        ) -> list[float]:
            del profile, source_context, batch_size
            content = contents[0]
            self.states.append((content.relevance_score, content.relevance_reason))
            if len(self.states) == 1:
                content.relevance_score = 0.99
                content.relevance_reason = "partial failed attempt"
                try:
                    raise LLMRateLimitError("openai_compatible rate limit exceeded")
                except LLMRateLimitError as exc:
                    raise LLMProviderExecutionError("All providers failed") from exc
            return [0.7]

    monkeypatch.setattr(replay_script.asyncio, "sleep", fake_sleep)
    engine = _RateLimitedOnceEngine()
    content = SimpleNamespace(
        content_id="candidate-1",
        title="candidate",
        relevance_score=0.1,
        relevance_reason="original",
    )

    scores = await _score_contents(engine, [content], object(), source_context="mixed")

    assert scores == [0.7]
    assert sleeps == [65.0]
    assert engine.states == [(0.1, "original"), (0.1, "original")]


@pytest.mark.asyncio
async def test_score_contents_does_not_retry_non_transient_quota_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    class _BillingLimitedEngine:
        _EVALUATE_BATCH_HARD_CAP = 90

        async def evaluate_content_batch(
            self,
            contents: list[object],
            profile: object,
            *,
            source_context: str,
            batch_size: int,
        ) -> list[float]:
            del contents, profile, source_context, batch_size
            try:
                raise LLMRateLimitError("provider backoff: HTTP 402 insufficient balance")
            except LLMRateLimitError as exc:
                raise LLMProviderExecutionError("All providers failed") from exc

    monkeypatch.setattr(replay_script.asyncio, "sleep", fake_sleep)
    content = SimpleNamespace(
        content_id="candidate-1",
        title="candidate",
        relevance_score=0.1,
        relevance_reason="original",
    )

    with pytest.raises(LLMProviderExecutionError, match="All providers failed"):
        await _score_contents(_BillingLimitedEngine(), [content], object(), source_context="mixed")

    assert sleeps == []


def _many_interest_profile() -> SoulProfile:
    profile = SoulProfile()
    profile.preferences.interests = [
        InterestTag(name=f"兴趣{index}", category="测试", weight=1.0 - index / 1000)
        for index in range(100)
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
    assert len(interests) == 100


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


def test_compact_arm_b_uses_exact_production_profile_view() -> None:
    profile = _many_interest_profile()
    engine = _build_engine(
        object(),
        _ReplayConfig(),
        compact_profile=True,
        negative_examples=None,
        legacy_profile=False,
        embedding_service=_ReplayEmbedding(),
    )

    assert engine._evaluation_profile_summary(  # noqa: SLF001
        profile
    ) == ContentDiscoveryEngine._evaluation_profile_summary(profile)  # noqa: SLF001


@pytest.mark.asyncio
async def test_replay_engine_audits_current_batch_recall_result_path() -> None:
    embedding = _ReplayEmbedding()
    recall = ReplayRecallAudit()
    engine = _build_engine(
        object(),
        _ReplayConfig(),
        compact_profile=True,
        negative_examples=None,
        legacy_profile=False,
        embedding_service=embedding,
        recall_audit=recall,
    )
    content = _rows_to_contents(
        [
            {
                "content_id": "item-1",
                "source_platform": "twitter",
                "source_strategy": "feed",
                "title": "matching content",
                "body_text": "matching body",
            }
        ]
    )[0]

    with replay_call_attribution(
        pair_kind="treatment",
        repeat=1,
        logical_run="B",
        arm="B",
    ):
        result = await engine._related_interests_for_batch_result(  # noqa: SLF001
            [content],
            _many_interest_profile(),
        )

    assert result.complete_indices == frozenset({0})
    assert recall.events[0]["complete_candidate_count"] == 1
    assert recall.events[0]["logical_run"] == "B"


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
