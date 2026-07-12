from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.llm.base import LLMResponse, LLMTimeoutError
from openbiliclaw.llm.concurrency import LLMConcurrencyGate

from .test_refill_real_provider_integration import _LiveMetrics, _MonitoredRegistry


def test_live_summary_has_no_fabricated_metric_literals() -> None:
    source = Path("tests/test_refill_real_provider_integration.py").read_text(encoding="utf-8")
    assert '"peak_total=4 peak_background=3 max_copy_batch=8 transient_retries=0"' not in source


def test_every_live_service_receives_normal_module_overrides() -> None:
    source = Path("tests/test_refill_real_provider_integration.py").read_text(encoding="utf-8")
    assert source.count("module_overrides=overrides") == 3


def test_deterministic_uses_real_service_and_isolated_user_scenarios() -> None:
    source = Path("tests/test_refill_end_to_end.py").read_text(encoding="utf-8")
    assert "LLMService(" in source
    assert "class _ControlledLLM" not in source
    assert "test_user_a" in source
    assert "test_user_b" in source


def test_deterministic_proves_exact_two_by_thirty_copy_fanout_and_claim_query() -> None:
    source = Path("tests/test_refill_end_to_end.py").read_text(encoding="utf-8")
    assert "test_sixty_pending_copy_rows_fan_out_as_two_thirty_item_requests" in source
    assert "sorted(registry.expression_batch_sizes) == [30, 30]" in source
    assert "registry.peak_expression == 2" in source
    assert "WHERE claim_token IS NOT NULL" in source


def test_live_summary_reports_measured_retry_attempts() -> None:
    source = Path("tests/test_refill_real_provider_integration.py").read_text(encoding="utf-8")
    assert "transient_retry_count={metrics.transient_retry_count}" in source
    assert "metrics.transient_retry_count <= metrics.provider_round_count" in source


@pytest.mark.asyncio
async def test_retry_observer_counts_only_next_actual_invocation_after_transient() -> None:
    class _SequenceRegistry:
        default_provider = "test"

        def __init__(self) -> None:
            self.calls = 0

        def is_chat_capable(self, name: str) -> bool:
            return name == "test"

        async def complete(self, messages: list[dict[str, str]], **kwargs: object) -> LLMResponse:
            del messages, kwargs
            self.calls += 1
            if self.calls == 1:
                raise LLMTimeoutError("transient")
            return LLMResponse(content="{}", provider="test", model="test")

    metrics = _LiveMetrics(LLMConcurrencyGate(4))
    registry = _MonitoredRegistry(_SequenceRegistry(), metrics)
    messages = [{"role": "system", "content": 'schema: "score"'}]
    with pytest.raises(LLMTimeoutError):
        await registry.complete(messages)
    assert metrics.provider_round_count == 1
    assert metrics.transient_retry_count == 0
    assert metrics.transient_registry_failures == 1

    await registry.complete(messages)
    assert metrics.provider_round_count == 2
    assert metrics.transient_retry_count == 1


@pytest.mark.asyncio
async def test_retry_observer_does_not_count_unrelated_same_caller_request() -> None:
    class _SequenceRegistry:
        default_provider = "test"

        def __init__(self) -> None:
            self.calls = 0

        def is_chat_capable(self, name: str) -> bool:
            return name == "test"

        async def complete(self, messages: list[dict[str, str]], **kwargs: object) -> LLMResponse:
            del messages, kwargs
            self.calls += 1
            if self.calls == 1:
                raise LLMTimeoutError("transient")
            return LLMResponse(content="{}", provider="test", model="test")

    metrics = _LiveMetrics(LLMConcurrencyGate(4))
    registry = _MonitoredRegistry(_SequenceRegistry(), metrics)
    candidate_a = [
        {"role": "system", "content": 'schema: "score"'},
        {"role": "user", "content": "candidate-a"},
    ]
    candidate_b = [
        {"role": "system", "content": 'schema: "score"'},
        {"role": "user", "content": "candidate-b"},
    ]
    with pytest.raises(LLMTimeoutError):
        await registry.complete(candidate_a, max_tokens=32)
    await registry.complete(candidate_b, max_tokens=32)

    assert metrics.provider_round_count == 2
    assert metrics.transient_retry_count == 0
