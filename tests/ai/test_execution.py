from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ImageUrl, ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, FunctionModel
from pydantic_ai.models.test import TestModel

from openbiliclaw.ai.runtime.budgets import PolicyBook, RunPolicy
from openbiliclaw.ai.runtime.capabilities import AgentId, ModelCapabilities, ModelRequirements
from openbiliclaw.ai.runtime.errors import (
    AIRuntimeError,
    BudgetExhaustedError,
    InvalidOutputError,
    RateLimitedError,
    RunTimedOutError,
)
from openbiliclaw.ai.runtime.execution import (
    AgentRunRequest,
    AIRuntime,
    RuntimeRunFinished,
    RuntimeTextDelta,
    RuntimeToolFinished,
    RuntimeToolStarted,
)
from openbiliclaw.ai.runtime.history import (
    ContextProjection,
    MessageAuditError,
    ToolResultTooLargeError,
)
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.core.resources import ResourceBudget

if TYPE_CHECKING:
    from openbiliclaw.ai.runtime.usage import UsageRecord


@dataclass(frozen=True)
class Answer:
    value: int


@dataclass(frozen=True)
class Deps:
    multiplier: int


class UsageMemory:
    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    async def record(self, record: UsageRecord) -> None:
        self.records.append(record)


def _runtime(model: TestModel | FunctionModel, sink: UsageMemory | None = None) -> AIRuntime:
    configured = ConfiguredModel(
        "test-model", "test", model, ModelCapabilities(structured_output=True)
    )
    route = ModelRoute(
        AgentId("test.answer"), ModelRequirements(structured_output=True), (configured,)
    )
    return AIRuntime(RouteTable((route,)), ResourceBudget("model", 1), usage_sink=sink)


async def test_typed_run_returns_output_messages_route_and_usage() -> None:
    sink = UsageMemory()
    runtime = _runtime(TestModel(custom_output_args={"value": 42}), sink)
    agent: Agent[Deps, Answer] = Agent(deps_type=Deps, output_type=Answer)
    request = AgentRunRequest(
        agent_id=AgentId("test.answer"),
        agent=agent,
        deps=Deps(2),
        user_input="answer",
        history=(),
        context=(ContextProjection("profile", "likes typed systems", 100),),
        requirements=ModelRequirements(structured_output=True),
        policy=RunPolicy(),
        workflow="test",
    )
    result = await runtime.run(request)
    assert result.output == Answer(value=42)
    assert result.model_instance == "test-model"
    assert result.messages
    assert result.usage.attribution.agent_id == AgentId("test.answer")
    assert result.diagnostics.attempted_models == ("test-model",)
    assert result.diagnostics.attempts == 1
    assert sink.records == [result.usage]


async def test_stream_normalizes_text_tools_and_validated_result() -> None:
    agent: Agent[None, str] = Agent(output_type=str)

    @agent.tool_plain
    def lookup() -> str:
        return "private payload"

    runtime = _runtime(TestModel(call_tools="all"))
    request = AgentRunRequest(
        AgentId("test.answer"),
        agent,
        None,
        "go",
        (),
        (),
        ModelRequirements(structured_output=True),
        RunPolicy(),
        "test",
    )
    events = [event async for event in runtime.stream(request)]

    assert any(isinstance(event, RuntimeTextDelta) for event in events)
    assert any(isinstance(event, RuntimeToolStarted) for event in events)
    assert any(isinstance(event, RuntimeToolFinished) for event in events)
    assert isinstance(events[-1], RuntimeRunFinished)
    safe_tool_events = [
        event for event in events if isinstance(event, RuntimeToolStarted | RuntimeToolFinished)
    ]
    assert "private payload" not in repr(safe_tool_events)


async def test_stream_emits_only_textual_provider_reasoning() -> None:
    async def stream_response(messages: list[ModelMessage], info: AgentInfo):
        del messages, info
        yield {0: DeltaThinkingPart(content="provider reasoning")}
        yield "answer"

    runtime = _runtime(FunctionModel(stream_function=stream_response))
    request = AgentRunRequest(
        AgentId("test.answer"),
        Agent(output_type=str),
        None,
        "go",
        (),
        (),
        ModelRequirements(structured_output=True),
        RunPolicy(),
        "test",
    )
    events = [event async for event in runtime.stream(request)]

    reasoning = [
        event
        for event in events
        if isinstance(event, RuntimeTextDelta) and event.kind == "reasoning_delta"
    ]
    assert [event.text for event in reasoning] == ["provider reasoning"]


async def test_stream_never_retries_after_visible_output() -> None:
    attempts = 0

    async def broken_stream(messages: list[ModelMessage], info: AgentInfo):
        nonlocal attempts
        del messages, info
        attempts += 1
        yield "visible"
        raise ConnectionError("offline after output")

    runtime = _runtime(FunctionModel(stream_function=broken_stream))
    request = AgentRunRequest(
        AgentId("test.answer"),
        Agent(output_type=str),
        None,
        "go",
        (),
        (),
        ModelRequirements(structured_output=True),
        RunPolicy(retries=2),
        "test",
    )
    with pytest.raises(AIRuntimeError):
        _ = [event async for event in runtime.stream(request)]
    assert attempts == 1


async def test_stream_cancellation_closes_native_stream_and_releases_resource_slot() -> None:
    entered = asyncio.Event()
    closed = asyncio.Event()
    blocker = asyncio.Event()

    async def stream_response(messages: list[ModelMessage], info: AgentInfo):
        del messages, info
        try:
            entered.set()
            await blocker.wait()
            yield "never"
        finally:
            closed.set()

    model = FunctionModel(stream_function=stream_response)
    configured = ConfiguredModel("m", "test", model, ModelCapabilities())
    agent_id = AgentId("test.stream-cancel")
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(), (configured,)),)),
        ResourceBudget("model", 1),
    )
    request = AgentRunRequest(
        agent_id,
        Agent(output_type=str),
        None,
        "go",
        (),
        (),
        ModelRequirements(),
        RunPolicy(timeout_seconds=30),
        "test",
    )

    async def consume() -> None:
        async for _ in runtime.stream(request):
            pass

    task = asyncio.create_task(consume())
    await entered.wait()
    assert runtime.active_runs == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert runtime.active_runs == 0


async def test_resource_budget_serializes_runs_without_leaking_slots() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        entered.set()
        await release.wait()
        return ModelResponse(parts=[TextPart("done")])

    model = FunctionModel(respond)
    configured = ConfiguredModel("m", "test", model, ModelCapabilities())
    agent_id = AgentId("test.text")
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(), (configured,)),)),
        ResourceBudget("model", 1),
    )
    agent: Agent[None, str] = Agent(output_type=str)
    request = AgentRunRequest(
        agent_id, agent, None, "go", (), (), ModelRequirements(), RunPolicy(), "test"
    )
    task = asyncio.create_task(runtime.run(request))
    await entered.wait()
    assert runtime.active_runs == 1
    release.set()
    await task
    assert runtime.active_runs == 0


async def test_timeout_is_typed_and_cancellation_propagates() -> None:
    blocker = asyncio.Event()

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await blocker.wait()
        return ModelResponse(parts=[TextPart("never")])

    model = FunctionModel(respond)
    configured = ConfiguredModel("m", "test", model, ModelCapabilities())
    agent_id = AgentId("test.text")
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(), (configured,)),)),
        ResourceBudget("model", 1),
    )
    agent: Agent[None, str] = Agent(output_type=str)
    request = AgentRunRequest(
        agent_id,
        agent,
        None,
        "go",
        (),
        (),
        ModelRequirements(),
        RunPolicy(timeout_seconds=0.001),
        "test",
    )
    with pytest.raises(RunTimedOutError):
        await runtime.run(request)

    request2 = AgentRunRequest(
        agent_id,
        agent,
        None,
        "go",
        (),
        (),
        ModelRequirements(),
        RunPolicy(timeout_seconds=30),
        "test",
    )
    task = asyncio.create_task(runtime.run(request2))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_invalid_structured_output_and_secret_input_are_rejected() -> None:
    runtime = _runtime(TestModel(custom_output_args={"wrong": 42}))
    agent: Agent[Deps, Answer] = Agent(deps_type=Deps, output_type=Answer, output_retries=0)
    request = AgentRunRequest(
        AgentId("test.answer"),
        agent,
        Deps(1),
        "answer",
        (),
        (),
        ModelRequirements(structured_output=True),
        RunPolicy(),
        "test",
    )
    with pytest.raises(InvalidOutputError):
        await runtime.run(request)
    secret = AgentRunRequest(
        AgentId("test.answer"),
        agent,
        Deps(1),
        "vault:credential",
        (),
        (),
        ModelRequirements(structured_output=True),
        RunPolicy(),
        "test",
    )
    with pytest.raises(MessageAuditError):
        await runtime.run(secret)
    secret_image = AgentRunRequest(
        AgentId("test.answer"),
        agent,
        Deps(1),
        "answer",
        (),
        (),
        ModelRequirements(structured_output=True),
        RunPolicy(),
        "test",
        attachments=(ImageUrl("https://example.com/?api_key=secret"),),
    )
    with pytest.raises(MessageAuditError):
        await runtime.run(secret_image)


async def test_retry_limit_and_compatible_fallback_are_enforced() -> None:
    attempts = 0

    async def flaky(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("offline")
        return ModelResponse(parts=[TextPart("recovered")])

    agent_id = AgentId("test.retry")
    retry_model = ConfiguredModel("retry", "test", FunctionModel(flaky), ModelCapabilities())
    agent: Agent[None, str] = Agent(output_type=str)
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(), (retry_model,)),)),
        ResourceBudget("model", 1),
    )
    request = AgentRunRequest(
        agent_id,
        agent,
        None,
        "go",
        (),
        (),
        ModelRequirements(),
        RunPolicy(retries=1),
        "test",
    )
    result = await runtime.run(request)
    assert result.output == "recovered"
    assert attempts == 2

    async def offline(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError("offline")

    fallback = ConfiguredModel(
        "fallback",
        "test",
        FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("fallback")])),
        ModelCapabilities(),
    )
    primary = ConfiguredModel("primary", "test", FunctionModel(offline), ModelCapabilities())
    fallback_runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(), (primary, fallback)),)),
        ResourceBudget("model", 1),
    )
    fallback_result = await fallback_runtime.run(
        AgentRunRequest(
            agent_id,
            agent,
            None,
            "go",
            (),
            (),
            ModelRequirements(),
            RunPolicy(retries=0),
            "test",
        )
    )
    assert fallback_result.output == "fallback"
    assert fallback_result.model_instance == "fallback"
    assert fallback_result.diagnostics.attempted_models == ("primary", "fallback")


async def test_function_model_usage_limit_is_a_typed_budget_error() -> None:
    requests = 0

    async def request_tool(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal requests
        requests += 1
        return ModelResponse(parts=[ToolCallPart("lookup", {})])

    agent: Agent[None, str] = Agent(output_type=str)

    @agent.tool_plain
    def lookup() -> str:
        return "bounded result"

    agent_id = AgentId("test.budget")
    configured = ConfiguredModel(
        "budget-model", "test", FunctionModel(request_tool), ModelCapabilities(tools=True)
    )
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(tools=True), (configured,)),)),
        ResourceBudget("model", 1),
    )
    request = AgentRunRequest(
        agent_id,
        agent,
        None,
        "use lookup",
        (),
        (),
        ModelRequirements(tools=True),
        RunPolicy(request_limit=1, retries=0),
        "test",
    )
    with pytest.raises(BudgetExhaustedError) as caught:
        await runtime.run(request)
    assert caught.value.model_instance == "budget-model"
    assert requests == 1


async def test_all_failed_attempts_surface_the_last_typed_error() -> None:
    async def unavailable(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError("offline")

    async def rate_limited(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(429, "test", None)

    agent_id = AgentId("test.all-fail")
    primary = ConfiguredModel("primary", "test", FunctionModel(unavailable), ModelCapabilities())
    fallback = ConfiguredModel("fallback", "test", FunctionModel(rate_limited), ModelCapabilities())
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(), (primary, fallback)),)),
        ResourceBudget("model", 1),
    )
    agent: Agent[None, str] = Agent(output_type=str)
    request = AgentRunRequest(
        agent_id,
        agent,
        None,
        "go",
        (),
        (),
        ModelRequirements(),
        RunPolicy(retries=0),
        "test",
    )
    with pytest.raises(RateLimitedError) as caught:
        await runtime.run(request)
    assert caught.value.model_instance == "fallback"


async def test_timeout_during_fallback_is_attributed_to_fallback() -> None:
    fallback_entered = asyncio.Event()
    waiting = asyncio.Event()

    async def unavailable(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError("offline")

    async def blocked_fallback(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        fallback_entered.set()
        await waiting.wait()
        return ModelResponse(parts=[TextPart("never")])

    agent_id = AgentId("test.fallback-timeout")
    primary = ConfiguredModel("primary", "test", FunctionModel(unavailable), ModelCapabilities())
    fallback = ConfiguredModel(
        "fallback", "test", FunctionModel(blocked_fallback), ModelCapabilities()
    )
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(), (primary, fallback)),)),
        ResourceBudget("model", 1),
    )
    agent: Agent[None, str] = Agent(output_type=str)
    request = AgentRunRequest(
        agent_id,
        agent,
        None,
        "go",
        (),
        (),
        ModelRequirements(),
        RunPolicy(timeout_seconds=0.05, retries=0),
        "test",
    )
    task = asyncio.create_task(runtime.run(request))
    await fallback_entered.wait()
    with pytest.raises(RunTimedOutError) as caught:
        await task
    assert caught.value.model_instance == "fallback"


async def test_native_tool_result_is_rejected_before_the_next_model_request() -> None:
    agent: Agent[None, str] = Agent(output_type=str)

    @agent.tool_plain
    def oversized() -> str:
        return "12345"

    agent_id = AgentId("test.tool")
    model = ConfiguredModel("tool-model", "test", TestModel(), ModelCapabilities(tools=True))
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(tools=True), (model,)),)),
        ResourceBudget("model", 1),
    )
    request = AgentRunRequest(
        agent_id,
        agent,
        None,
        "use tool",
        (),
        (),
        ModelRequirements(tools=True),
        RunPolicy(retries=0, tool_result_bytes_limit=4),
        "test",
    )
    with pytest.raises(ToolResultTooLargeError):
        await runtime.run(request)


def test_request_requires_a_workflow() -> None:
    agent: Agent[None, str] = Agent(output_type=str)
    with pytest.raises(ValueError):
        AgentRunRequest(
            AgentId("test.text"),
            agent,
            None,
            "go",
            (),
            (),
            ModelRequirements(),
            RunPolicy(),
            "",
        )


async def test_policy_book_override_applies_at_execution_choke_point() -> None:
    requests = 0

    async def request_tool(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal requests
        requests += 1
        return ModelResponse(parts=[ToolCallPart("lookup", {})])

    agent: Agent[None, str] = Agent(output_type=str)

    @agent.tool_plain
    def lookup() -> str:
        return "bounded result"

    agent_id = AgentId("test.budget")
    configured = ConfiguredModel(
        "budget-model", "test", FunctionModel(request_tool), ModelCapabilities(tools=True)
    )
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, ModelRequirements(tools=True), (configured,)),)),
        ResourceBudget("model", 1),
        policies=PolicyBook.from_overrides({"test.budget": {"request_limit": 1, "retries": 0}}),
    )
    request = AgentRunRequest(
        agent_id,
        agent,
        None,
        "use lookup",
        (),
        (),
        ModelRequirements(tools=True),
        RunPolicy(),  # code default allows 3 requests; the config override tightens to 1
        "test",
    )
    with pytest.raises(BudgetExhaustedError) as caught:
        await runtime.run(request)
    assert caught.value.model_instance == "budget-model"
    assert requests == 1
