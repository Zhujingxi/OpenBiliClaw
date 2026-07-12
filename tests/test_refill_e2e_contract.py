from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, ModuleLLMConfig
from openbiliclaw.llm.base import LLMResponse, LLMTimeoutError
from openbiliclaw.llm.concurrency import LLMConcurrencyGate
from openbiliclaw.llm.service import LLMService, module_overrides_from_config

from . import test_refill_real_provider_integration as live_refill
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


def test_live_rejection_failure_reports_sanitized_score_and_admission_counts() -> None:
    source = Path("tests/test_refill_real_provider_integration.py").read_text(encoding="utf-8")
    assert "passing_scores=" in source
    assert "rejected={result['rejected']}" in source
    assert "parser_unresolved=" in source


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


def test_live_refill_uses_explicit_config_and_provider_without_mutating_loaded_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="unit-test-model"),
            openai_compatible=LLMProviderConfig(
                api_key="test-compatible-key",
                base_url="https://compatible.example/v1",
                model="test-compatible-model",
            ),
        )
    )
    paths: list[object] = []

    def _load(path: object = None) -> Config:
        paths.append(path)
        return loaded

    monkeypatch.setattr(live_refill, "load_config", _load)
    monkeypatch.setenv("OPENBILICLAW_REFILL_CONFIG", "/tmp/live-refill.toml")
    monkeypatch.setenv("OPENBILICLAW_REFILL_PROVIDER", " OPENAI_COMPATIBLE ")

    config, registry = live_refill._load_live_config_and_registry()

    assert paths == ["/tmp/live-refill.toml"]
    assert loaded.llm.default_provider == "ollama"
    assert config.llm.default_provider == "openai_compatible"
    assert registry.default_provider == "openai_compatible"


def test_live_refill_provider_override_wins_over_evaluation_module_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = Config(
        llm=LLMConfig(
            default_provider="openai_compatible",
            openai_compatible=LLMProviderConfig(
                api_key="test-compatible-key",
                base_url="https://compatible.example/v1",
                model="test-compatible-model",
            ),
            ollama=LLMProviderConfig(model="old-local-model"),
            soul=ModuleLLMConfig(provider="ollama", model="old-soul-model"),
            discovery=ModuleLLMConfig(provider="ollama", model="old-discovery-model"),
            recommendation=ModuleLLMConfig(provider="ollama", model="old-recommendation-model"),
            evaluation=ModuleLLMConfig(provider="ollama", model="old-evaluation-model"),
        )
    )
    monkeypatch.delenv("OPENBILICLAW_REFILL_CONFIG", raising=False)
    monkeypatch.setattr(live_refill, "load_config", lambda: loaded)
    monkeypatch.setenv("OPENBILICLAW_REFILL_PROVIDER", "openai_compatible")

    config, registry = live_refill._load_live_config_and_registry()
    overrides = module_overrides_from_config(config)
    service = LLMService(registry=registry, memory=None, module_overrides=overrides)  # type: ignore[arg-type]

    assert loaded.llm.evaluation.provider == "ollama"
    assert set(overrides) == {"soul", "discovery", "recommendation", "evaluation"}
    assert {name: override.provider for name, override in overrides.items()} == {
        "soul": "openai_compatible",
        "discovery": "openai_compatible",
        "recommendation": "openai_compatible",
        "evaluation": "openai_compatible",
    }
    assert all(override.model == "" for override in overrides.values())
    assert service._resolve_module_override("discovery.evaluate_batch") == (
        "openai_compatible",
        None,
    )


def test_live_refill_fails_when_explicit_provider_is_not_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="unit-test-model"),
        )
    )
    monkeypatch.delenv("OPENBILICLAW_REFILL_CONFIG", raising=False)
    monkeypatch.setattr(live_refill, "load_config", lambda: loaded)
    monkeypatch.setenv("OPENBILICLAW_REFILL_PROVIDER", "openai_compatible")

    with pytest.raises(RuntimeError, match="Requested live refill provider is unavailable"):
        live_refill._load_live_config_and_registry()


def test_live_refill_without_controls_keeps_zero_argument_config_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="unit-test-model"),
        )
    )
    calls: list[tuple[object, ...]] = []

    def _load(*args: object) -> Config:
        calls.append(args)
        return loaded

    monkeypatch.delenv("OPENBILICLAW_REFILL_CONFIG", raising=False)
    monkeypatch.delenv("OPENBILICLAW_REFILL_PROVIDER", raising=False)
    monkeypatch.setattr(live_refill, "load_config", _load)

    config, registry = live_refill._load_live_config_and_registry()

    assert calls == [()]
    assert config is loaded
    assert registry.default_provider == "ollama"


def test_live_refill_integration_remains_explicitly_opt_in() -> None:
    marks = [mark for mark in live_refill.pytestmark if mark.name == "skipif"]
    assert len(marks) == 1
    assert marks[0].kwargs["reason"] == "set OPENBILICLAW_REFILL_E2E=1 for live refill E2E"
