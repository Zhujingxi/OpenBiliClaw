"""End-to-end contract for one global ordered Chat route."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.base import LLMProvider, LLMResponse, LLMResponseError
from openbiliclaw.model_config import ChatConnection, ChatRouteConfig, ModelConfig

if TYPE_CHECKING:
    import pytest


@dataclass
class _RouteAdapter(LLMProvider):
    connection: ChatConnection
    fail: bool
    calls: list[tuple[str, str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.connection.id

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
        del temperature, max_tokens, json_mode, reasoning_effort, model
        user_input = messages[-1]["content"]
        self.calls.append((self.connection.id, user_input))
        if self.fail:
            # Invalid responses are prompt-scoped and therefore exercise the
            # full ordered route on every caller without opening a peer circuit.
            raise LLMResponseError("route peer returned invalid content")
        return LLMResponse(
            content='{"ok": true}',
            provider=self.connection.id,
            model=self.connection.model,
        )


async def test_all_callers_use_the_same_order_and_connection_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config
    from openbiliclaw.llm import connection_factory

    primary = ChatConnection(
        id="primary",
        name="Primary",
        type="ollama",
        model="configured-primary-model",
    )
    fallback = ChatConnection(
        id="fallback",
        name="Fallback",
        type="ollama",
        model="configured-fallback-model",
    )
    adapters: dict[str, _RouteAdapter] = {}

    def build_adapter(connection: ChatConnection, _options: object) -> _RouteAdapter:
        adapter = _RouteAdapter(connection=connection, fail=connection.id == "primary")
        adapters[connection.id] = adapter
        return adapter

    monkeypatch.setattr(connection_factory, "build_chat_adapter", build_adapter)
    config = Config(data_dir=str(tmp_path / "data"))
    config.scheduler.pool_target_count = 0
    config.models = ModelConfig(
        chat=ChatRouteConfig(
            connections=(primary, fallback),
            concurrency=3,
            timeout_seconds=30,
        )
    )
    context = build_runtime_context(config)

    callers = (
        "recommendation.write_expression",
        "recommendation.evaluate_batch",
        "discovery.keyword_inspiration",
        "soul.preference",
    )
    responses = []
    for caller in callers:
        responses.append(
            await context.llm_service.complete_structured_task(
                system_instruction="Return json.",
                user_input=caller,
                caller=caller,
            )
        )

    assert [response.connection_id for response in responses] == ["fallback"] * 4
    assert [response.model for response in responses] == ["configured-fallback-model"] * 4
    assert adapters["primary"].calls == [("primary", caller) for caller in callers]
    assert adapters["fallback"].calls == [("fallback", caller) for caller in callers]
    assert context.soul_engine._llm_service.registry is context.model_bundle.chat_route
    assert context.discovery_engine._llm_service.registry is context.model_bundle.chat_route
    assert context.recommendation_engine._llm.registry is context.model_bundle.chat_route
