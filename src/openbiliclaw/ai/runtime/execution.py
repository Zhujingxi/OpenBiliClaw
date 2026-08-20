"""Single typed PydanticAI execution boundary."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from copy import copy
from dataclasses import dataclass, replace
from time import monotonic
from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar, cast

from pydantic_ai.messages import (
    BinaryContent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ImageUrl,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    UserContent,
)
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.usage import RunUsage

from openbiliclaw.ai.runtime.budgets import PolicyBook
from openbiliclaw.ai.runtime.errors import AIRuntimeError, normalize_error
from openbiliclaw.ai.runtime.history import (
    ContextProjection,
    MessageAuditError,
    ToolResultTooLargeError,
    audit_model_messages,
    audit_text,
    audit_tool_results,
)
from openbiliclaw.ai.runtime.usage import UsageAttribution, UsageRecord, UsageSink

DepsT = TypeVar("DepsT")
OutputT = TypeVar("OutputT")
ModelMessageList: TypeAlias = list[ModelMessage]
VisualContent: TypeAlias = ImageUrl | BinaryContent

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Sequence

    from pydantic_ai import Agent

    from openbiliclaw.ai.runtime.budgets import RunPolicy
    from openbiliclaw.ai.runtime.capabilities import AgentId, ModelRequirements
    from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
    from openbiliclaw.core.resources import ResourceBudget


@dataclass(frozen=True, slots=True)
class AgentRunRequest(Generic[DepsT, OutputT]):
    """Typed inputs and limits for one domain-owned agent invocation."""

    agent_id: AgentId
    agent: Agent[DepsT, OutputT]
    deps: DepsT
    user_input: str
    history: tuple[ModelMessage, ...]
    context: tuple[ContextProjection, ...]
    requirements: ModelRequirements
    policy: RunPolicy
    workflow: str
    recommendation_batch: str | None = None
    attachments: tuple[VisualContent, ...] = ()

    def __post_init__(self) -> None:
        if not self.workflow:
            raise ValueError("workflow must not be empty")


@dataclass(frozen=True, slots=True)
class RunDiagnostics:
    """Content-free execution diagnostics safe for logs and transports."""

    attempted_models: tuple[str, ...]
    attempts: int


@dataclass(frozen=True, slots=True)
class AgentRunResult(Generic[OutputT]):
    """Validated output plus inspectable messages, route, and usage."""

    output: OutputT
    messages: tuple[ModelMessage, ...]
    model_instance: str
    provider: str
    elapsed_seconds: float
    usage: UsageRecord
    diagnostics: RunDiagnostics


@dataclass(frozen=True, slots=True)
class RuntimeTextDelta:
    """Provider-supplied visible text or textual reasoning."""

    kind: Literal["response_delta", "reasoning_delta"]
    text: str


@dataclass(frozen=True, slots=True)
class RuntimeToolStarted:
    """Safe function-tool lifecycle start without model arguments."""

    kind: Literal["tool_started"] = "tool_started"
    tool_name: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeToolFinished:
    """Safe function-tool completion without result payloads."""

    kind: Literal["tool_finished"] = "tool_finished"
    tool_name: str = ""
    status: Literal["succeeded", "failed"] = "succeeded"
    summary: str = "Completed"


@dataclass(frozen=True, slots=True)
class RuntimeRunFinished(Generic[OutputT]):
    """Validated terminal result from PydanticAI."""

    result: AgentRunResult[OutputT]
    kind: Literal["run_finished"] = "run_finished"


RuntimeDelta: TypeAlias = RuntimeTextDelta | RuntimeToolStarted | RuntimeToolFinished
RuntimeStreamEvent: TypeAlias = RuntimeDelta | RuntimeRunFinished[OutputT]


class AIRuntime:
    """The only production entrypoint for typed domain-agent execution."""

    def __init__(
        self,
        routes: RouteTable,
        resource_budget: ResourceBudget,
        *,
        usage_sink: UsageSink | None = None,
        policies: PolicyBook | None = None,
    ) -> None:
        self._routes = routes
        self._resource_budget = resource_budget
        self._usage_sink = usage_sink
        self._policies = policies if policies is not None else PolicyBook({})

    @property
    def active_runs(self) -> int:
        return self._resource_budget.active

    def context_window(self, agent_id: AgentId, requirements: ModelRequirements) -> int:
        """Return the smallest configured route window, safe for every fallback."""

        route = self._routes.resolve(agent_id, requirements)
        return min(model.capabilities.context_tokens for model in route.models)

    def policy(self, agent_id: AgentId, default: RunPolicy) -> RunPolicy:
        """Resolve the configured run policy for preflight context selection."""

        return self._policies.resolve(agent_id, default)

    async def run(self, request: AgentRunRequest[DepsT, OutputT]) -> AgentRunResult[OutputT]:
        """Execute within capability, concurrency, usage, and time bounds."""

        audit_text(request.user_input)
        for attachment in request.attachments:
            if isinstance(attachment, ImageUrl):
                audit_text(attachment.url)
        audit_model_messages(request.history)
        for projection in request.context:
            audit_text(projection.text)
        request = replace(request, policy=self._policies.resolve(request.agent_id, request.policy))
        route = self._routes.resolve(request.agent_id, request.requirements)
        prompt = _build_prompt(request.user_input, request.context, request.attachments)
        started = monotonic()
        attempted_models: list[ConfiguredModel] = []
        async with self._resource_budget.acquire():
            try:
                async with asyncio.timeout(request.policy.timeout_seconds):
                    return await self._run_route(request, route, prompt, started, attempted_models)
            except asyncio.CancelledError:
                raise
            except (AIRuntimeError, MessageAuditError, ToolResultTooLargeError):
                raise
            except Exception as exc:
                current = attempted_models[-1] if attempted_models else route.models[0]
                raise normalize_error(exc, model_instance=current.instance_id) from exc

    async def stream(
        self, request: AgentRunRequest[DepsT, OutputT]
    ) -> AsyncIterator[RuntimeStreamEvent[OutputT]]:
        """Stream native PydanticAI events through the same bounded execution path."""

        audit_text(request.user_input)
        for attachment in request.attachments:
            if isinstance(attachment, ImageUrl):
                audit_text(attachment.url)
        audit_model_messages(request.history)
        for projection in request.context:
            audit_text(projection.text)
        request = replace(request, policy=self._policies.resolve(request.agent_id, request.policy))
        route = self._routes.resolve(request.agent_id, request.requirements)
        prompt = _build_prompt(request.user_input, request.context, request.attachments)
        started = monotonic()
        attempted_models: list[ConfiguredModel] = []
        async with self._resource_budget.acquire():
            try:
                async with asyncio.timeout(request.policy.timeout_seconds):
                    events = cast(
                        "AsyncGenerator[RuntimeStreamEvent[OutputT], None]",
                        self._stream_route(request, route, prompt, started, attempted_models),
                    )
                    async with aclosing(events):
                        async for event in events:
                            yield event
            except asyncio.CancelledError:
                raise
            except (AIRuntimeError, MessageAuditError, ToolResultTooLargeError):
                raise
            except Exception as exc:
                current = attempted_models[-1] if attempted_models else route.models[0]
                raise normalize_error(exc, model_instance=current.instance_id) from exc

    async def _run_route(
        self,
        request: AgentRunRequest[DepsT, OutputT],
        route: ModelRoute,
        prompt: str | Sequence[UserContent],
        started: float,
        attempted_models: list[ConfiguredModel],
    ) -> AgentRunResult[OutputT]:
        def audit_history(messages: ModelMessageList) -> ModelMessageList:
            audit_tool_results(messages, request.policy.tool_result_bytes_limit)
            audit_model_messages(messages)
            return messages

        agent = copy(request.agent)
        agent.history_processors = [*request.agent.history_processors, audit_history]
        last_error: AIRuntimeError | None = None
        aggregate_usage = RunUsage()
        for configured in route.models:
            for _attempt in range(request.policy.retries + 1):
                attempted_models.append(configured)
                try:
                    native_result = await agent.run(
                        prompt,
                        deps=request.deps,
                        model=configured.model,
                        message_history=request.history,
                        usage_limits=request.policy.to_usage_limits(),
                        usage=aggregate_usage,
                    )
                except asyncio.CancelledError:
                    raise
                except (MessageAuditError, ToolResultTooLargeError):
                    raise
                except Exception as exc:
                    error = normalize_error(exc, model_instance=configured.instance_id)
                    if not error.retryable:
                        raise error from exc
                    last_error = error
                    continue
                messages = tuple(native_result.all_messages())
                audit_model_messages(messages)
                elapsed = monotonic() - started
                usage = UsageRecord.from_run_usage(
                    _attribution(request, configured), native_result.usage(), elapsed
                )
                if self._usage_sink is not None:
                    await self._usage_sink.record(usage)
                return AgentRunResult(
                    output=native_result.output,
                    messages=messages,
                    model_instance=configured.instance_id,
                    provider=configured.provider,
                    elapsed_seconds=elapsed,
                    usage=usage,
                    diagnostics=RunDiagnostics(
                        attempted_models=tuple(model.instance_id for model in attempted_models),
                        attempts=len(attempted_models),
                    ),
                )
        if last_error is None:  # ModelRoute validation makes this unreachable.
            raise RuntimeError("validated route unexpectedly contains no models")
        raise last_error

    async def _stream_route(
        self,
        request: AgentRunRequest[DepsT, OutputT],
        route: ModelRoute,
        prompt: str | Sequence[UserContent],
        started: float,
        attempted_models: list[ConfiguredModel],
    ) -> AsyncIterator[RuntimeStreamEvent[OutputT]]:
        def audit_history(messages: ModelMessageList) -> ModelMessageList:
            audit_tool_results(messages, request.policy.tool_result_bytes_limit)
            audit_model_messages(messages)
            return messages

        agent = copy(request.agent)
        agent.history_processors = [*request.agent.history_processors, audit_history]
        last_error: AIRuntimeError | None = None
        aggregate_usage = RunUsage()
        visible_output = False
        for configured in route.models:
            for _attempt in range(request.policy.retries + 1):
                attempted_models.append(configured)
                try:
                    native_result = None
                    native_events = cast(
                        "AsyncGenerator[object, None]",
                        agent.run_stream_events(
                            prompt,
                            deps=request.deps,
                            model=configured.model,
                            message_history=request.history,
                            usage_limits=request.policy.to_usage_limits(),
                            usage=aggregate_usage,
                        ),
                    )
                    async with aclosing(native_events):
                        async for native_event in native_events:
                            event = _normalize_stream_event(native_event)
                            if event is not None:
                                visible_output = True
                                yield event
                            if isinstance(native_event, AgentRunResultEvent):
                                native_result = native_event.result
                except asyncio.CancelledError:
                    raise
                except (MessageAuditError, ToolResultTooLargeError):
                    raise
                except Exception as exc:
                    error = normalize_error(exc, model_instance=configured.instance_id)
                    if visible_output or not error.retryable:
                        raise error from exc
                    last_error = error
                    continue
                if native_result is None:
                    raise RuntimeError("PydanticAI stream ended without a validated result")
                messages = tuple(native_result.all_messages())
                audit_model_messages(messages)
                elapsed = monotonic() - started
                usage = UsageRecord.from_run_usage(
                    _attribution(request, configured), native_result.usage(), elapsed
                )
                if self._usage_sink is not None:
                    await self._usage_sink.record(usage)
                yield RuntimeRunFinished(
                    AgentRunResult(
                        output=native_result.output,
                        messages=messages,
                        model_instance=configured.instance_id,
                        provider=configured.provider,
                        elapsed_seconds=elapsed,
                        usage=usage,
                        diagnostics=RunDiagnostics(
                            attempted_models=tuple(model.instance_id for model in attempted_models),
                            attempts=len(attempted_models),
                        ),
                    )
                )
                return
        if last_error is None:
            raise RuntimeError("validated route unexpectedly contains no models")
        raise last_error


def _normalize_stream_event(native_event: object) -> RuntimeDelta | None:
    if isinstance(native_event, PartStartEvent):
        if isinstance(native_event.part, TextPart) and native_event.part.content:
            return RuntimeTextDelta("response_delta", native_event.part.content)
        if isinstance(native_event.part, ThinkingPart) and native_event.part.content:
            return RuntimeTextDelta("reasoning_delta", native_event.part.content)
    if isinstance(native_event, PartDeltaEvent):
        if isinstance(native_event.delta, TextPartDelta) and native_event.delta.content_delta:
            return RuntimeTextDelta("response_delta", native_event.delta.content_delta)
        if isinstance(native_event.delta, ThinkingPartDelta) and native_event.delta.content_delta:
            return RuntimeTextDelta("reasoning_delta", native_event.delta.content_delta)
    if isinstance(native_event, FunctionToolCallEvent):
        return RuntimeToolStarted(tool_name=native_event.part.tool_name)
    if isinstance(native_event, FunctionToolResultEvent):
        failed = native_event.result.part_kind == "retry-prompt"
        return RuntimeToolFinished(
            tool_name=native_event.result.tool_name or "tool",
            status="failed" if failed else "succeeded",
            summary="Failed" if failed else "Completed",
        )
    return None


def _build_prompt(
    user_input: str,
    context: Sequence[ContextProjection],
    attachments: tuple[VisualContent, ...],
) -> str | Sequence[UserContent]:
    projected = "\n".join(f"<context name={item.label!r}>{item.text}</context>" for item in context)
    text = f"{projected}\n{user_input}" if projected else user_input
    return (text, *attachments) if attachments else text


def _attribution(
    request: AgentRunRequest[DepsT, OutputT], configured: ConfiguredModel
) -> UsageAttribution:
    return UsageAttribution(
        agent_id=request.agent_id,
        workflow=request.workflow,
        model_instance=configured.instance_id,
        provider=configured.provider,
        recommendation_batch=request.recommendation_batch,
    )
