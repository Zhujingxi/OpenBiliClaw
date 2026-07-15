"""Runtime model bundle composition and atomic publication contracts."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from openbiliclaw.config import Config
from openbiliclaw.llm.base import LLMProvider, LLMResponse
from openbiliclaw.model_config import ChatConnection, ChatRouteConfig, ModelConfig


class _RecordingEventHub:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def publish(self, event: dict[str, object]) -> bool:
        self.events.append(dict(event))
        return True


class _BlockingAdapter(LLMProvider):
    def __init__(self, connection: ChatConnection, *, blocked: bool = False) -> None:
        self._connection = connection
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()

    @property
    def name(self) -> str:
        return self._connection.id

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
        del messages, temperature, max_tokens, json_mode, reasoning_effort, model
        self.entered.set()
        await self.release.wait()
        return LLMResponse(
            content='{"ok": true}',
            provider=self._connection.id,
            model=self._connection.model,
        )


def _connection(connection_id: str, model: str) -> ChatConnection:
    return ChatConnection(
        id=connection_id,
        name=connection_id.title(),
        type="ollama",
        model=model,
        base_url="http://127.0.0.1:11434/v1",
    )


def _config(tmp_path: Any, connection: ChatConnection, *, concurrency: int = 2) -> Config:
    config = Config(data_dir=str(tmp_path / "data"))
    # Keep maintenance-class calls admissible; these tests exercise bundle
    # publication, not the empty-inventory refill policy.
    config.scheduler.pool_target_count = 0
    config.models = ModelConfig(
        chat=ChatRouteConfig(
            connections=(connection,),
            concurrency=concurrency,
            timeout_seconds=30,
        )
    )
    # The pre-Task-8 runtime still reads this legacy section. Keeping it
    # buildable makes RED fail on the missing native bundle, not test setup.
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "legacy-only"
    return config


def _consumer_identities(context: Any) -> tuple[object, ...]:
    return (
        context.llm_service,
        context.soul_engine,
        context.dialogue,
        context.discovery_engine,
        context.recommendation_engine,
        context.runtime_controller,
        context.account_sync_service,
    )


def test_runtime_bundle_is_immutable_and_all_callers_share_one_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from openbiliclaw.api import runtime_context as runtime_module
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.llm import connection_factory

    connection = _connection("primary", "model-primary")
    monkeypatch.setattr(
        connection_factory,
        "build_chat_adapter",
        lambda item, _options: _BlockingAdapter(item),
    )

    context = build_runtime_context(_config(tmp_path, connection))

    assert hasattr(runtime_module, "RuntimeModelBundle")
    bundle = context.model_bundle
    route = bundle.chat_route
    assert context.current_model_candidate is bundle
    assert context.llm_service is bundle.llm_service
    assert context.llm_service.registry is route
    assert context.soul_engine._llm_service.registry is route
    assert context.dialogue._llm_service.registry is route
    assert context.discovery_engine._llm_service.registry is route
    assert context.recommendation_engine._llm.registry is route
    with pytest.raises(FrozenInstanceError):
        bundle.revision = "mutated"


async def test_in_flight_call_finishes_on_old_bundle_and_next_call_uses_new(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.llm import connection_factory
    from openbiliclaw.model_config import compute_model_revision

    old_connection = _connection("old", "model-old")
    new_connection = _connection("new", "model-new")
    adapters: dict[str, _BlockingAdapter] = {}

    def build_adapter(item: ChatConnection, _options: object) -> _BlockingAdapter:
        adapter = _BlockingAdapter(item, blocked=item.id == "old")
        adapters[item.id] = adapter
        return adapter

    monkeypatch.setattr(connection_factory, "build_chat_adapter", build_adapter)
    context = build_runtime_context(_config(tmp_path, old_connection))
    old_bundle = context.model_bundle
    old_service = context.llm_service

    old_call = asyncio.create_task(
        old_service.complete_structured_task(
            system_instruction="Return json.",
            user_input="old route call",
            caller="soul.preference",
        )
    )
    await adapters["old"].entered.wait()

    new_models = replace(
        context.config.models,
        chat=replace(context.config.models.chat, connections=(new_connection,)),
    )
    new_bundle = await context.build_model_candidate(
        new_models,
        compute_model_revision(new_models),
    )
    previous = await context.swap_model_candidate(new_bundle)

    assert previous is old_bundle
    assert context.model_bundle is new_bundle
    adapters["old"].release.set()
    assert (await old_call).connection_id == "old"
    next_response = await context.llm_service.complete_structured_task(
        system_instruction="Return json.",
        user_input="new route call",
        caller="recommendation.write_expression",
    )
    assert next_response.connection_id == "new"


async def test_swap_and_rollback_publish_exact_consumers_with_stable_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.llm import connection_factory
    from openbiliclaw.model_config import compute_model_revision

    monkeypatch.setattr(
        connection_factory,
        "build_chat_adapter",
        lambda item, _options: _BlockingAdapter(item),
    )
    event_hub = _RecordingEventHub()
    old_connection = _connection("old", "model-old")
    context = build_runtime_context(
        _config(tmp_path, old_connection, concurrency=2),
        event_hub=event_hub,
    )
    old_bundle = context.model_bundle
    old_consumers = _consumer_identities(context)
    stable_gate = context.llm_concurrency_gate
    assert stable_gate.total_concurrency == 2

    new_connection = _connection("new", "model-new")
    new_models = replace(
        context.config.models,
        chat=replace(
            context.config.models.chat,
            connections=(new_connection,),
            concurrency=7,
        ),
    )
    revision = compute_model_revision(new_models)
    candidate = await context.build_model_candidate(new_models, revision)

    assert context.model_bundle is old_bundle
    assert _consumer_identities(context) == old_consumers
    assert context.llm_concurrency_gate is stable_gate
    assert stable_gate.total_concurrency == 2
    assert event_hub.events == []

    previous = await context.swap_model_candidate(candidate)

    new_consumers = _consumer_identities(context)
    assert previous is old_bundle
    assert context.model_bundle is candidate
    assert all(new is not old for new, old in zip(new_consumers, old_consumers, strict=True))
    assert context.llm_concurrency_gate is stable_gate
    assert stable_gate.total_concurrency == 7
    assert event_hub.events == [{"type": "config_reloaded", "revision": revision}]

    await context.restore_model_candidate(previous)

    assert context.model_bundle is old_bundle
    assert _consumer_identities(context) == old_consumers
    assert context.llm_concurrency_gate is stable_gate
    assert stable_gate.total_concurrency == 2
    assert event_hub.events == [{"type": "config_reloaded", "revision": revision}]


async def test_gate_configuration_failure_does_not_publish_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.llm import connection_factory
    from openbiliclaw.model_config import compute_model_revision

    monkeypatch.setattr(
        connection_factory,
        "build_chat_adapter",
        lambda item, _options: _BlockingAdapter(item),
    )
    context = build_runtime_context(_config(tmp_path, _connection("old", "model-old")))
    old_bundle = context.model_bundle
    old_consumers = _consumer_identities(context)
    gate = context.llm_concurrency_gate
    old_capacity = gate.total_concurrency
    new_models = replace(
        context.config.models,
        chat=replace(
            context.config.models.chat,
            connections=(_connection("new", "model-new"),),
            concurrency=7,
        ),
    )
    candidate = await context.build_model_candidate(
        new_models,
        compute_model_revision(new_models),
    )

    def fail_configuration(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("gate configuration failed")

    monkeypatch.setattr(gate, "configure_runtime", fail_configuration)

    with pytest.raises(RuntimeError, match="gate configuration failed"):
        await context.swap_model_candidate(candidate)

    assert context.model_bundle is old_bundle
    assert _consumer_identities(context) == old_consumers
    assert context.llm_concurrency_gate is gate
    assert gate.total_concurrency == old_capacity
