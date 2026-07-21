"""Posture gate tests (Phase 3 — Wave C).

Covers the builder contract, the three verdicts, conservative downgrade on
failure, shadow async zero-delay + snapshot isolation, ledger recording,
enforce interception, the off full-bypass, and the enforce save-time readiness
guard (five states).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from openbiliclaw.config import (
    POSTURE_GATE_ENFORCE_MIN_OBSERVATION_DAYS,
    Config,
    posture_gate_enforce_readiness_issue,
)
from openbiliclaw.llm.prompts import build_posture_gate_prompt
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.engine import SoulEngine
from openbiliclaw.soul.posture_gate import (
    ACCEPT,
    DOWNGRADE,
    REJECT,
    GateDecision,
    PostureGate,
)
from openbiliclaw.soul.profile import OnionProfile


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model = "fake"
        self.provider = "fake"


class _FakeRegistry:
    """Records calls; returns a fixed content, optionally after an event."""

    def __init__(self, content: str, *, gate: asyncio.Event | None = None) -> None:
        self._content = content
        self._gate = gate
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(dict(kwargs))
        if self._gate is not None:
            await self._gate.wait()
        return _FakeResponse(self._content)


class _FakeLedger:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.rows.append(dict(kwargs))


# --------------------------------------------------------------------------
# Builder contract
# --------------------------------------------------------------------------


def test_build_posture_gate_prompt_carries_three_user_sections() -> None:
    messages = build_posture_gate_prompt(
        change={"kind": "value", "content": "追求效率"},
        core_memory={"name": "白"},
        ledger_digest=[{"write_point": "values"}],
    )
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "<proposed_change>" in user
    assert "<core_memory>" in user
    assert "<ledger_digest>" in user
    # Static system: the three-way judgement rubric + conflict-is-a-hypothesis.
    assert "accept" in messages[0]["content"]
    assert "downgrade" in messages[0]["content"]
    assert "冲突不是错误" in messages[0]["content"]


# --------------------------------------------------------------------------
# enforce verdicts
# --------------------------------------------------------------------------


async def test_enforce_three_verdicts() -> None:
    for raw in (ACCEPT, DOWNGRADE, REJECT):
        gate = PostureGate(mode="enforce", registry=_FakeRegistry(f'{{"verdict": "{raw}"}}'))
        decision = await gate.evaluate(write_point="wp", change={"k": 1})
        assert decision.verdict == raw
        assert decision.enforced is True
    # blocks / downgraded helpers
    gate = PostureGate(mode="enforce", registry=_FakeRegistry('{"verdict": "downgrade"}'))
    d = await gate.evaluate(write_point="wp", change={"k": 1})
    assert d.blocks is True
    assert d.downgraded is True


async def test_enforce_parse_failure_downgrades() -> None:
    gate = PostureGate(mode="enforce", registry=_FakeRegistry("not json at all"))
    decision = await gate.evaluate(write_point="wp", change={"k": 1})
    assert decision.verdict == DOWNGRADE


async def test_enforce_bad_verdict_downgrades() -> None:
    gate = PostureGate(mode="enforce", registry=_FakeRegistry('{"verdict": "explode"}'))
    decision = await gate.evaluate(write_point="wp", change={"k": 1})
    assert decision.verdict == DOWNGRADE


async def test_enforce_llm_exception_downgrades() -> None:
    class _Boom:
        async def complete_structured_task(self, **kwargs: Any) -> _FakeResponse:
            raise RuntimeError("provider down")

    gate = PostureGate(mode="enforce", registry=_Boom())
    decision = await gate.evaluate(write_point="wp", change={"k": 1})
    assert decision.verdict == DOWNGRADE


async def test_enforce_llm_exception_is_error_true() -> None:
    """A provider exception downgrades but flags is_error (F7): callers keep pending."""

    class _Boom:
        async def complete_structured_task(self, **kwargs: Any) -> _FakeResponse:
            raise RuntimeError("provider down")

    gate = PostureGate(mode="enforce", registry=_Boom())
    decision = await gate.evaluate(write_point="wp", change={"k": 1})
    assert decision.verdict == DOWNGRADE
    assert decision.is_error is True
    assert decision.blocks is True


async def test_enforce_real_downgrade_is_error_false() -> None:
    """A genuine LLM downgrade verdict is NOT an error (F7): callers clear pending."""
    gate = PostureGate(mode="enforce", registry=_FakeRegistry('{"verdict": "downgrade"}'))
    decision = await gate.evaluate(write_point="wp", change={"k": 1})
    assert decision.verdict == DOWNGRADE
    assert decision.is_error is False


async def test_enforce_accept_is_error_false() -> None:
    gate = PostureGate(mode="enforce", registry=_FakeRegistry('{"verdict": "accept"}'))
    decision = await gate.evaluate(write_point="wp", change={"k": 1})
    assert decision.verdict == ACCEPT
    assert decision.is_error is False


async def test_off_and_shadow_decisions_are_not_errors() -> None:
    off_gate = PostureGate(mode="off", registry=_FakeRegistry('{"verdict": "reject"}'))
    off_decision = await off_gate.evaluate(write_point="wp", change={"k": 1})
    assert off_decision.is_error is False
    shadow_gate = PostureGate(mode="shadow", registry=_FakeRegistry('{"verdict": "reject"}'))
    shadow_decision = await shadow_gate.evaluate(write_point="wp", change={"k": 1})
    assert shadow_decision.is_error is False
    await shadow_gate.drain_shadow()


# --------------------------------------------------------------------------
# shadow: zero-delay + snapshot isolation + ledger rows
# --------------------------------------------------------------------------


async def test_shadow_returns_immediately_before_judgement() -> None:
    release = asyncio.Event()
    registry = _FakeRegistry('{"verdict": "reject"}', gate=release)
    gate = PostureGate(mode="shadow", registry=registry)
    decision = await gate.evaluate(write_point="wp", change={"k": 1})
    # Write proceeds regardless, and the judgement has NOT completed yet.
    assert decision.verdict == ACCEPT
    assert decision.enforced is False
    release.set()
    await gate.drain_shadow()


async def test_shadow_judges_immutable_snapshot_not_live_state() -> None:
    release = asyncio.Event()
    registry = _FakeRegistry('{"verdict": "accept"}', gate=release)
    ledger = _FakeLedger()
    gate = PostureGate(mode="shadow", registry=registry, ledger=ledger)
    live_change = {"content": "original"}
    await gate.evaluate(write_point="wp", change=live_change)
    # Pollute live state AFTER the commit boundary — must not reach the judge.
    live_change["content"] = "polluted"
    release.set()
    await gate.drain_shadow()
    assert registry.calls, "shadow judgement never ran"
    judged_user_input = registry.calls[0]["user_input"]
    assert "original" in judged_user_input
    assert "polluted" not in judged_user_input
    assert ledger.rows[0]["gate_verdict"] == "shadow_accept"


async def test_shadow_records_verdict_and_error_rows() -> None:
    ledger = _FakeLedger()
    gate = PostureGate(
        mode="shadow", registry=_FakeRegistry('{"verdict": "downgrade"}'), ledger=ledger
    )
    await gate.evaluate(write_point="wp", change={"k": 1})
    await gate.drain_shadow()
    assert ledger.rows[0]["gate_verdict"] == "shadow_downgrade"

    class _Boom:
        async def complete_structured_task(self, **kwargs: Any) -> _FakeResponse:
            raise RuntimeError("down")

    ledger2 = _FakeLedger()
    gate2 = PostureGate(mode="shadow", registry=_Boom(), ledger=ledger2)
    await gate2.evaluate(write_point="wp", change={"k": 1})
    await gate2.drain_shadow()
    assert ledger2.rows[0]["gate_verdict"] == "shadow_error"


# --------------------------------------------------------------------------
# off: complete bypass, zero LLM calls
# --------------------------------------------------------------------------


async def test_off_is_a_full_bypass_with_zero_llm_calls() -> None:
    registry = _FakeRegistry('{"verdict": "reject"}')
    gate = PostureGate(mode="off", registry=registry)
    decision = await gate.evaluate(write_point="wp", change={"k": 1})
    assert decision.verdict == ACCEPT
    assert decision.enforced is False
    assert gate.enabled is False
    assert registry.calls == []


# --------------------------------------------------------------------------
# Save-time enforce readiness guard (five states)
# --------------------------------------------------------------------------


def _enforce_config(*, force: bool = False) -> Config:
    cfg = Config()
    cfg.soul.posture_gate_mode = "enforce"
    cfg.soul.posture_gate_force_enforce = force
    return cfg


def test_save_time_all_three_conditions_met_passes() -> None:
    old = (datetime.now() - timedelta(days=20)).isoformat()
    issue = posture_gate_enforce_readiness_issue(
        _enforce_config(), earliest_valid_at=old, valid_count_14d=15, valid_count_7d=3
    )
    assert issue is None


def test_save_time_first_day_10_rows_but_short_observation_rejected() -> None:
    # 10 rows all today → 14d/7d counts fine, but earliest is < 14 days old.
    today = datetime.now().isoformat()
    issue = posture_gate_enforce_readiness_issue(
        _enforce_config(), earliest_valid_at=today, valid_count_14d=10, valid_count_7d=10
    )
    assert issue is not None
    assert issue.severity == "blocking"


def test_save_time_insufficient_valid_count_rejected() -> None:
    old = (datetime.now() - timedelta(days=20)).isoformat()
    issue = posture_gate_enforce_readiness_issue(
        _enforce_config(), earliest_valid_at=old, valid_count_14d=4, valid_count_7d=2
    )
    assert issue is not None


def test_save_time_no_recent_coverage_rejected() -> None:
    old = (datetime.now() - timedelta(days=20)).isoformat()
    issue = posture_gate_enforce_readiness_issue(
        _enforce_config(), earliest_valid_at=old, valid_count_14d=12, valid_count_7d=0
    )
    assert issue is not None


def test_save_time_force_enforce_bypasses_everything() -> None:
    issue = posture_gate_enforce_readiness_issue(
        _enforce_config(force=True), earliest_valid_at="", valid_count_14d=0, valid_count_7d=0
    )
    assert issue is None


def test_save_time_shadow_mode_never_blocks() -> None:
    cfg = Config()  # default shadow
    issue = posture_gate_enforce_readiness_issue(
        cfg, earliest_valid_at="", valid_count_14d=0, valid_count_7d=0
    )
    assert issue is None


def test_observation_days_constant_is_14() -> None:
    assert POSTURE_GATE_ENFORCE_MIN_OBSERVATION_DAYS == 14


# --------------------------------------------------------------------------
# Task 7: three access-point wiring (matrix of mode × access point)
# --------------------------------------------------------------------------


class _StubGate:
    """Deterministic gate stand-in for wiring tests (no LLM)."""

    def __init__(self, mode: str, decision: GateDecision) -> None:
        self._mode = mode
        self._decision = decision
        self.calls: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return self._mode != "off"

    async def evaluate(self, **kwargs: Any) -> GateDecision:
        self.calls.append(kwargs)
        return self._decision


def _engine(tmp_path: Any) -> SoulEngine:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    class _FR:
        async def complete(self, *a: Any, **k: Any) -> _FakeResponse:
            return _FakeResponse("{}")

    return SoulEngine(llm=_FR(), memory=memory)


# --- Access point ①: dialogue candidates ---------------------------------


async def test_ap1_interest_dislike_bypass_gate(tmp_path: Any) -> None:
    engine = _engine(tmp_path)
    engine._posture_gate = _StubGate("enforce", GateDecision(verdict=REJECT, enforced=True))  # type: ignore[assignment]
    cands = [{"kind": "interest", "content": "a"}, {"kind": "dislike", "content": "b"}]
    kept = await engine._gate_dialogue_candidates(cands)
    assert kept == cands
    assert engine._posture_gate.calls == []  # type: ignore[attr-defined]


async def test_ap1_goal_enforce_reject_dropped(tmp_path: Any) -> None:
    engine = _engine(tmp_path)
    engine._posture_gate = _StubGate("enforce", GateDecision(verdict=REJECT, enforced=True))  # type: ignore[assignment]
    kept = await engine._gate_dialogue_candidates([{"kind": "goal", "content": "转行"}])
    assert kept == []


async def test_ap1_value_enforce_downgrade_becomes_insight(tmp_path: Any) -> None:
    engine = _engine(tmp_path)
    engine._posture_gate = _StubGate(  # type: ignore[assignment]
        "enforce", GateDecision(verdict=DOWNGRADE, enforced=True)
    )
    kept = await engine._gate_dialogue_candidates(
        [{"kind": "value", "content": "追求效率", "confidence": 0.9, "evidence": "e"}]
    )
    assert kept == []
    insights = engine._load_insights()
    assert len(insights) == 1
    assert insights[0].confidence == round(0.9 * 0.6, 4)


async def test_ap1_off_is_byte_identical_passthrough(tmp_path: Any) -> None:
    engine = _engine(tmp_path)
    engine._posture_gate = _StubGate("off", GateDecision(verdict=REJECT, enforced=True))  # type: ignore[assignment]
    cands = [{"kind": "goal", "content": "转行"}]
    kept = await engine._gate_dialogue_candidates(cands)
    assert kept is cands  # untouched, no gate call
    assert engine._posture_gate.calls == []  # type: ignore[attr-defined]


async def test_ap1_shadow_keeps_all(tmp_path: Any) -> None:
    engine = _engine(tmp_path)
    engine._posture_gate = _StubGate("shadow", GateDecision(verdict=ACCEPT, enforced=False))  # type: ignore[assignment]
    cands = [{"kind": "goal", "content": "转行", "confidence": 0.8}]
    kept = await engine._gate_dialogue_candidates(cands)
    assert kept == cands
    assert engine._posture_gate.calls  # judgement scheduled  # type: ignore[attr-defined]


# --- Access point ③: soul rebuild ----------------------------------------


async def test_ap3_rebuild_enforce_downgrade_abandons(tmp_path: Any) -> None:
    engine = _engine(tmp_path)
    engine._posture_gate = _StubGate(  # type: ignore[assignment]
        "enforce", GateDecision(verdict=DOWNGRADE, enforced=True)
    )
    proceed = await engine._gate_soul_rebuild({"interests": []}, {"interests": [{"name": "x"}]}, [])
    assert proceed is False


async def test_ap3_rebuild_shadow_proceeds(tmp_path: Any) -> None:
    engine = _engine(tmp_path)
    engine._posture_gate = _StubGate("shadow", GateDecision(verdict=ACCEPT, enforced=False))  # type: ignore[assignment]
    proceed = await engine._gate_soul_rebuild({}, {"interests": [{"name": "x"}]}, [])
    assert proceed is True


async def test_ap3_rebuild_off_proceeds_without_gate_call(tmp_path: Any) -> None:
    engine = _engine(tmp_path)
    engine._posture_gate = _StubGate("off", GateDecision(verdict=REJECT, enforced=True))  # type: ignore[assignment]
    proceed = await engine._gate_soul_rebuild({}, {"interests": [{"name": "x"}]}, [])
    assert proceed is True
    assert engine._posture_gate.calls == []  # type: ignore[attr-defined]


# --- Access point ② RETIRED: VALUES/CORE are sealed in update_layer ------
# Deep-line consolidation (P1) removed the pipeline VALUES/CORE gate. A direct
# update_layer call for either is a defensive no-op + WARNING; the gate is never
# consulted, no layer mutates, and no downgrade insight is created.


class _FakeBuilderRegistry:
    def __init__(self, content: str) -> None:
        self._content = content

    async def complete_structured_task(self, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._content)


class _FakeBuilder:
    def __init__(self, content: str) -> None:
        self.registry = _FakeBuilderRegistry(content)


async def test_ap2_values_layer_is_sealed(tmp_path: Any) -> None:
    """update_layer(VALUES) is a no-op regardless of gate mode (P1 retired)."""
    from openbiliclaw.soul.layer_updaters import update_layer
    from openbiliclaw.soul.pipeline import OnionLayer

    memory = MemoryManager(tmp_path)
    memory.initialize()
    profile = OnionProfile()
    builder = _FakeBuilder('{"changed": true, "values": ["效率"], "reason": "x"}')
    gate = _StubGate("enforce", GateDecision(verdict=DOWNGRADE, enforced=True))
    signals = [{"payload": {"event_type": "view", "title": "t", "content": "深度思考"}}]

    result = await update_layer(
        layer=OnionLayer.VALUES,
        signals=signals,
        profile=profile,
        memory=memory,
        preference_analyzer=None,
        profile_builder=builder,
        posture_gate=gate,
    )
    assert result.changed is False
    assert profile.values_layer.values == []  # never mutated
    assert gate.calls == []  # gate never consulted (access point ② retired)
    hypotheses = memory.get_layer("insight").data.get("hypotheses", [])
    assert hypotheses == []  # no downgrade insight — the updater never ran


async def test_ap2_core_layer_is_sealed(tmp_path: Any) -> None:
    """update_layer(CORE) is a no-op — deep event-driven writes retired (P1)."""
    from openbiliclaw.soul.layer_updaters import update_layer
    from openbiliclaw.soul.pipeline import OnionLayer

    memory = MemoryManager(tmp_path)
    memory.initialize()
    profile = OnionProfile()
    builder = _FakeBuilder('{"changed": true, "core_traits": ["好奇"], "reason": "x"}')
    gate = _StubGate("shadow", GateDecision(verdict=ACCEPT, enforced=False))
    signals = [{"payload": {"event_type": "view", "title": "t", "content": "深度思考"}}]

    result = await update_layer(
        layer=OnionLayer.CORE,
        signals=signals,
        profile=profile,
        memory=memory,
        preference_analyzer=None,
        profile_builder=builder,
        posture_gate=gate,
    )
    assert result.changed is False
    assert profile.core.core_traits == []  # never mutated
    assert gate.calls == []


async def test_ap2_role_layer_is_not_gated(tmp_path: Any) -> None:
    from openbiliclaw.soul.layer_updaters import update_layer
    from openbiliclaw.soul.pipeline import OnionLayer

    memory = MemoryManager(tmp_path)
    memory.initialize()
    profile = OnionProfile()
    builder = _FakeBuilder('{"changed": false}')
    gate = _StubGate("enforce", GateDecision(verdict=REJECT, enforced=True))
    signals = [{"payload": {"event_type": "view", "title": "t"}}]

    await update_layer(
        layer=OnionLayer.ROLE,
        signals=signals,
        profile=profile,
        memory=memory,
        preference_analyzer=None,
        profile_builder=builder,
        posture_gate=gate,
    )
    assert gate.calls == []  # ROLE bypasses the gate


# --------------------------------------------------------------------------
# Reasoning-model output budget (E2E finding: sensenova deepseek-v4-flash)
# --------------------------------------------------------------------------


class _ReasoningExhaustedRegistry:
    """First call raises the reasoning-only length error; retry succeeds.

    Mirrors what openai_provider raises when a reasoning provider burns the
    whole output budget on thinking (finish_reason=length, no final content).
    """

    def __init__(self, content: str, *, fail_first_n: int = 1) -> None:
        self._content = content
        self._fail_first_n = fail_first_n
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(dict(kwargs))
        if len(self.calls) <= self._fail_first_n:
            raise RuntimeError(
                "openai_compatible returned reasoning but no final content "
                "(finish_reason=length); disable thinking/reasoning or increase max_tokens"
            )
        return _FakeResponse(self._content)


async def test_reasoning_length_retries_once_with_raised_budget() -> None:
    """The deterministic reasoning-only length failure gets ONE raised-budget retry."""
    from openbiliclaw.soul.posture_gate import (
        _POSTURE_GATE_MAX_TOKENS,
        _POSTURE_GATE_REASONING_FALLBACK_MAX_TOKENS,
    )

    registry = _ReasoningExhaustedRegistry('{"verdict": "accept", "reason": "ok"}')
    gate = PostureGate(mode="enforce", registry=registry)
    decision = await gate.evaluate(write_point="wp", change={"goal": "转行"})

    assert decision.verdict == "accept"
    assert len(registry.calls) == 2
    assert registry.calls[0]["max_tokens"] == _POSTURE_GATE_MAX_TOKENS
    assert registry.calls[1]["max_tokens"] == _POSTURE_GATE_REASONING_FALLBACK_MAX_TOKENS


async def test_reasoning_length_shadow_records_verdict_not_error() -> None:
    """Shadow path: budget retry succeeds → shadow_accept row, no shadow_error."""
    registry = _ReasoningExhaustedRegistry('{"verdict": "accept", "reason": "ok"}')
    ledger = _FakeLedger()
    gate = PostureGate(mode="shadow", registry=registry, ledger=ledger)  # type: ignore[arg-type]
    await gate.evaluate(write_point="wp", change={"value": "自由"})
    await gate.drain_shadow()

    verdicts = [row["gate_verdict"] for row in ledger.rows]
    assert verdicts == ["shadow_accept"]


async def test_reasoning_length_persistent_failure_still_conservative() -> None:
    """Retry budget is one-shot: a second length failure propagates as before."""
    registry = _ReasoningExhaustedRegistry('{"verdict": "accept"}', fail_first_n=10)
    gate = PostureGate(mode="enforce", registry=registry)
    decision = await gate.evaluate(write_point="wp", change={"goal": "转行"})

    assert decision.verdict == "downgrade"  # enforce fails closed
    assert len(registry.calls) == 2  # exactly one retry, no loop


async def test_non_reasoning_error_does_not_trigger_budget_retry() -> None:
    """Other provider errors keep the existing single-attempt behaviour."""

    class _Boom:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(self, **kwargs: Any) -> _FakeResponse:
            self.calls += 1
            raise RuntimeError("connection reset")

    boom = _Boom()
    gate = PostureGate(mode="enforce", registry=boom)
    decision = await gate.evaluate(write_point="wp", change={"goal": "x"})
    assert decision.verdict == "downgrade"
    assert boom.calls == 1
