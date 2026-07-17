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
from openbiliclaw.soul.posture_gate import (
    ACCEPT,
    DOWNGRADE,
    REJECT,
    PostureGate,
)


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
