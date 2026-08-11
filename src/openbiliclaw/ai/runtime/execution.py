"""Single typed PydanticAI execution boundary."""

from __future__ import annotations

import asyncio
from copy import copy
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Generic, TypeAlias, TypeVar

from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage

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

if TYPE_CHECKING:
    from collections.abc import Sequence

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


class AIRuntime:
    """The only production entrypoint for typed domain-agent execution."""

    def __init__(
        self,
        routes: RouteTable,
        resource_budget: ResourceBudget,
        *,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self._routes = routes
        self._resource_budget = resource_budget
        self._usage_sink = usage_sink

    @property
    def active_runs(self) -> int:
        return self._resource_budget.active

    async def run(self, request: AgentRunRequest[DepsT, OutputT]) -> AgentRunResult[OutputT]:
        """Execute within capability, concurrency, usage, and time bounds."""

        audit_text(request.user_input)
        audit_model_messages(request.history)
        for projection in request.context:
            audit_text(projection.text)
        route = self._routes.resolve(request.agent_id, request.requirements)
        prompt = _build_prompt(request.user_input, request.context)
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

    async def _run_route(
        self,
        request: AgentRunRequest[DepsT, OutputT],
        route: ModelRoute,
        prompt: str,
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


def _build_prompt(user_input: str, context: Sequence[ContextProjection]) -> str:
    if not context:
        return user_input
    projected = "\n".join(f"<context name={item.label!r}>{item.text}</context>" for item in context)
    return f"{projected}\n{user_input}"


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
