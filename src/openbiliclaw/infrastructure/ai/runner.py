"""PydanticAI runner whose only production model endpoint is LiteLLM."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, cast

from openai import AsyncOpenAI
from pydantic import TypeAdapter
from pydantic_ai import UnexpectedModelBehavior, UsageLimits
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from openbiliclaw.infrastructure.ai.spec import CachePolicy, InputT, OutputT

AwaitedT = TypeVar("AwaitedT")

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from uuid import UUID

    import httpx
    from pydantic_ai import AgentRunResult
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models import Model
    from pydantic_ai.result import StreamedRunResult
    from pydantic_ai.settings import ModelSettings

    from openbiliclaw.features.system.domain import UserSettings
    from openbiliclaw.infrastructure.ai.spec import GenerativeAlias, TaskSpec


class ProductSettingsProvider(Protocol):
    def get(self) -> UserSettings: ...


class AIRunRecorder(Protocol):
    """Secret-safe persistence port for AI run lifecycle metadata."""

    def start(self, *, task_name: str, model_alias: str) -> UUID: ...

    def succeed(
        self,
        run_id: UUID,
        *,
        usage: dict[str, int],
    ) -> None: ...

    def fail(
        self, run_id: UUID, *, error_kind: str, usage: dict[str, int] | None = None
    ) -> None: ...


class ModelResolver(Protocol):
    """Resolve an application alias to one PydanticAI model."""

    def __call__(self, alias: GenerativeAlias) -> Model: ...


@dataclass(frozen=True, slots=True)
class TaskStreamOutput(Generic[OutputT]):
    """One approved typed snapshot tied to its persisted AI run."""

    run_id: UUID
    output: OutputT


@dataclass(frozen=True, slots=True)
class _ExecutionSettings:
    model_alias: GenerativeAlias
    semantic_retry_limit: int
    timeout_seconds: float
    usage_limits: UsageLimits


class _SuccessRecordedCancellation(asyncio.CancelledError):
    """Cancellation observed only after the success transaction completed."""


@dataclass(frozen=True, slots=True)
class _StreamFailure:
    """One producer failure relayed across the task-neutral stream boundary."""

    error: BaseException


_STREAM_FINISHED = object()


class LiteLLMModelResolver:
    """Resolve stable aliases against one OpenAI-compatible LiteLLM endpoint.

    The OpenAI transport retry count is explicitly zero. LiteLLM is the sole owner
    of network retry, routing, provider fallback, cooldown, rate limits, and cache.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_base = base_url.rstrip("/")
        endpoint = normalized_base if normalized_base.endswith("/v1") else f"{normalized_base}/v1"
        client = AsyncOpenAI(
            base_url=endpoint,
            api_key=api_key,
            max_retries=0,
            http_client=http_client,
        )
        provider = OpenAIProvider(openai_client=client)
        self._client = client
        aliases: tuple[GenerativeAlias, ...] = ("obc-interactive", "obc-analysis")
        self._models: dict[GenerativeAlias, Model] = {
            alias: OpenAIChatModel(alias, provider=provider) for alias in aliases
        }

    def __call__(self, alias: GenerativeAlias) -> Model:
        """Return the cached model for an already-validated stable alias."""

        return self._models[alias]

    async def aclose(self) -> None:
        """Close the owned OpenAI-compatible client during application shutdown."""

        await self._client.close()


class TaskRunner:
    """Validate, execute, and record typed semantic tasks without provider logic."""

    def __init__(
        self,
        *,
        model_resolver: ModelResolver,
        recorder: AIRunRecorder,
        settings: ProductSettingsProvider | None = None,
    ) -> None:
        self._model_resolver = model_resolver
        self._recorder = recorder
        self._settings = settings

    async def run(
        self,
        spec: TaskSpec[InputT, OutputT],
        raw_input: InputT | dict[str, object],
    ) -> OutputT:
        """Run one typed task with bounded semantic retries and wall-clock time."""

        validated_input = TypeAdapter(spec.input_type).validate_python(raw_input)
        execution = await self._load_execution_settings(spec)
        model = self._model_resolver(execution.model_alias)
        usage = RunUsage()
        run_id = await self._start_ai_run(
            task_name=spec.name,
            model_alias=execution.model_alias,
            usage=usage,
        )
        try:
            async with asyncio.timeout(execution.timeout_seconds):
                result = await spec.agent.run(
                    validated_input.model_dump_json(),
                    model=model,
                    model_settings=_model_settings(spec.cache_policy),
                    usage_limits=execution.usage_limits,
                    usage=usage,
                    retries={"output": execution.semantic_retry_limit},
                )
            output = TypeAdapter(spec.output_type).validate_python(result.output)
            await _record_success(
                self._recorder,
                run_id,
                _safe_usage(result),
            )
            return output
        except _SuccessRecordedCancellation:
            raise asyncio.CancelledError from None
        except asyncio.CancelledError:
            await _record_failure(
                self._recorder,
                run_id,
                "CancelledError",
                _usage_dict(usage),
            )
            raise
        except Exception as exc:
            await _record_failure(self._recorder, run_id, type(exc).__name__, _usage_dict(usage))
            raise

    async def stream(
        self,
        spec: TaskSpec[InputT, OutputT],
        raw_input: InputT | dict[str, object],
    ) -> AsyncGenerator[TaskStreamOutput[OutputT]]:
        """Relay validated snapshots without leaking task-affine provider state.

        Async generators may be advanced by different tasks (for example when
        a caller wraps the first ``anext()`` in ``asyncio.wait_for``). PydanticAI
        and AnyIO streaming contexts must be entered and exited by one task, so
        a dedicated producer owns the complete provider lifecycle while this
        public generator only crosses a queue boundary.
        """

        queue: asyncio.Queue[TaskStreamOutput[OutputT] | _StreamFailure | object] = asyncio.Queue(
            maxsize=1
        )
        producer = asyncio.create_task(self._produce_stream(queue, spec, raw_input))
        try:
            while True:
                event = await queue.get()
                if event is _STREAM_FINISHED:
                    return
                if isinstance(event, _StreamFailure):
                    raise event.error
                yield cast("TaskStreamOutput[OutputT]", event)
        finally:
            await _stop_stream_producer(producer)

    async def _produce_stream(
        self,
        queue: asyncio.Queue[TaskStreamOutput[OutputT] | _StreamFailure | object],
        spec: TaskSpec[InputT, OutputT],
        raw_input: InputT | dict[str, object],
    ) -> None:
        """Own the complete provider stream lifecycle in this single task."""

        try:
            async for output in self._stream_owned(spec, raw_input):
                await queue.put(output)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(_StreamFailure(exc))
        else:
            await queue.put(_STREAM_FINISHED)

    async def _stream_owned(
        self,
        spec: TaskSpec[InputT, OutputT],
        raw_input: InputT | dict[str, object],
    ) -> AsyncGenerator[TaskStreamOutput[OutputT]]:
        """Stream validated typed output snapshots while recording one AI run.

        PydanticAI remains responsible for structured-output validation and
        semantic retries. Consumers receive only snapshots that validate as the
        declared output type; provider text or tool-call fragments never escape
        this boundary.
        """

        validated_input = TypeAdapter(spec.input_type).validate_python(raw_input)
        execution = await self._load_execution_settings(spec)
        model = self._model_resolver(execution.model_alias)
        usage = RunUsage()
        run_id = await self._start_ai_run(
            task_name=spec.name,
            model_alias=execution.model_alias,
            usage=usage,
        )
        active_result: StreamedRunResult[Any, Any] | None = None

        def set_active(value: StreamedRunResult[Any, Any] | None) -> None:
            nonlocal active_result
            active_result = value

        try:
            async with asyncio.timeout(execution.timeout_seconds):
                prompt = validated_input.model_dump_json()
                if execution.semantic_retry_limit == 0:
                    async with spec.agent.run_stream(
                        prompt,
                        model=model,
                        model_settings=_model_settings(spec.cache_policy),
                        usage_limits=execution.usage_limits,
                        usage=usage,
                        retries={"output": 0},
                    ) as result:
                        set_active(result)
                        try:
                            async with aclosing(
                                _validated_stream_outputs(result, spec.output_type)
                            ) as outputs:
                                async for output in outputs:
                                    yield TaskStreamOutput(run_id=run_id, output=output)
                        except GeneratorExit:
                            await _cancel_stream_result(result)
                            set_active(None)
                            await _record_failure(
                                self._recorder,
                                run_id,
                                "CancelledError",
                                _usage_dict(usage),
                            )
                            return
                    set_active(None)
                else:
                    approved = await self._run_stream_attempts(
                        spec,
                        prompt,
                        model=model,
                        semantic_retry_limit=execution.semantic_retry_limit,
                        usage_limits=execution.usage_limits,
                        usage=usage,
                        active=set_active,
                    )
                    active_result = None
                    for output in approved:
                        yield TaskStreamOutput(run_id=run_id, output=output)
            await _record_success(
                self._recorder,
                run_id,
                _usage_dict(usage),
            )
        except _SuccessRecordedCancellation:
            raise asyncio.CancelledError from None
        except GeneratorExit:
            await _cancel_stream_result(active_result)
            await _record_failure(self._recorder, run_id, "CancelledError", _usage_dict(usage))
            return
        except asyncio.CancelledError:
            await _cancel_stream_result(active_result)
            await _record_failure(
                self._recorder,
                run_id,
                "CancelledError",
                _usage_dict(usage),
            )
            raise
        except Exception as exc:
            await _cancel_stream_result(active_result)
            await _record_failure(self._recorder, run_id, type(exc).__name__, _usage_dict(usage))
            raise

    def _execution_settings(self, spec: TaskSpec[InputT, OutputT]) -> _ExecutionSettings:
        configured = (
            None
            if self._settings is None
            else cast("dict[str, Any]", self._settings.get().tasks).get(spec.name)
        )
        if configured is None:
            return _ExecutionSettings(
                model_alias=spec.model_alias,
                semantic_retry_limit=spec.semantic_retry_limit,
                timeout_seconds=spec.timeout_seconds,
                usage_limits=spec.usage_limits,
            )
        if configured.model_alias != spec.lane.model_alias:
            raise ValueError(f"{spec.lane.value} lane requires model alias {spec.lane.model_alias}")
        return _ExecutionSettings(
            model_alias=cast("GenerativeAlias", configured.model_alias),
            semantic_retry_limit=configured.semantic_retry_limit,
            timeout_seconds=configured.timeout_seconds,
            usage_limits=UsageLimits(
                request_limit=configured.request_limit,
                total_tokens_limit=configured.total_tokens_limit,
            ),
        )

    async def _load_execution_settings(
        self,
        spec: TaskSpec[InputT, OutputT],
    ) -> _ExecutionSettings:
        """Resolve SQLite-backed mutable settings off-loop within a hard bound."""

        if self._settings is None:
            return self._execution_settings(spec)
        async with asyncio.timeout(spec.timeout_seconds):
            return await asyncio.to_thread(self._execution_settings, spec)

    async def _start_ai_run(
        self,
        *,
        task_name: str,
        model_alias: GenerativeAlias,
        usage: RunUsage,
    ) -> UUID:
        start_task = asyncio.create_task(
            asyncio.to_thread(
                partial(
                    self._recorder.start,
                    task_name=task_name,
                    model_alias=model_alias,
                )
            )
        )
        try:
            return await asyncio.shield(start_task)
        except asyncio.CancelledError:
            run_id = await _await_uninterruptibly(start_task)
            await _record_failure(
                self._recorder,
                run_id,
                "CancelledError",
                _usage_dict(usage),
            )
            raise

    async def _run_stream_attempts(
        self,
        spec: TaskSpec[InputT, OutputT],
        prompt: str,
        *,
        model: Model,
        semantic_retry_limit: int,
        usage_limits: UsageLimits,
        usage: RunUsage,
        active: Callable[[StreamedRunResult[Any, Any] | None], None],
    ) -> tuple[OutputT, ...]:
        """Buffer an attempt until final validators approve it, then expose it."""

        last_error: UnexpectedModelBehavior | None = None
        for attempt in range(semantic_retry_limit + 1):
            buffered: list[OutputT] = []
            try:
                async with spec.agent.run_stream(
                    prompt,
                    model=model,
                    model_settings=_model_settings(spec.cache_policy),
                    usage_limits=usage_limits,
                    usage=usage,
                    retries={"output": 0},
                ) as result:
                    active(result)
                    final_response: ModelResponse | None = None
                    async for response in result.stream_response(debounce_by=None):
                        final_response = response
                        partial_output = _partial_output(spec.output_type, response)
                        if partial_output is not None and (
                            not buffered or partial_output != buffered[-1]
                        ):
                            buffered.append(partial_output)
                    if final_response is None:
                        raise UnexpectedModelBehavior("stream produced no response")
                    final = TypeAdapter(spec.output_type).validate_python(await result.get_output())
                active(None)
                if not buffered or buffered[-1] != final:
                    buffered.append(final)
                return tuple(buffered)
            except UnexpectedModelBehavior as error:
                active(None)
                last_error = error
                if attempt >= semantic_retry_limit:
                    raise
        assert last_error is not None
        raise last_error


def _model_settings(cache_policy: CachePolicy) -> ModelSettings | None:
    if cache_policy is CachePolicy.BYPASS:
        return {"extra_body": {"cache": {"no-cache": True}}}
    return None


def _partial_output(output_type: type[OutputT], response: ModelResponse) -> OutputT | None:
    adapter = TypeAdapter(output_type)
    for tool_call in reversed(response.tool_calls):
        try:
            return adapter.validate_json(
                tool_call.args_as_json_str(),
                experimental_allow_partial="trailing-strings",
            )
        except ValueError:
            continue
    return None


async def _validated_stream_outputs(
    result: StreamedRunResult[Any, Any],
    output_type: type[OutputT],
) -> AsyncGenerator[OutputT]:
    """Yield deduplicated partial snapshots followed by the validated final output."""

    previous: OutputT | None = None
    final_response: ModelResponse | None = None
    async for response in result.stream_response(debounce_by=None):
        final_response = response
        output = _partial_output(output_type, response)
        if output is not None and output != previous:
            previous = output
            yield output
    if final_response is None:
        raise UnexpectedModelBehavior("stream produced no response")
    final = TypeAdapter(output_type).validate_python(await result.get_output())
    if final != previous:
        yield final


async def _record_failure(
    recorder: AIRunRecorder,
    run_id: UUID,
    error_kind: str,
    usage: dict[str, int],
) -> None:
    task = asyncio.create_task(
        asyncio.to_thread(partial(recorder.fail, run_id, error_kind=error_kind, usage=usage))
    )
    await _await_uninterruptibly(task)


async def _record_success(
    recorder: AIRunRecorder,
    run_id: UUID,
    usage: dict[str, int],
) -> None:
    """Finish one success transaction without racing a cancellation failure write."""

    task = asyncio.create_task(asyncio.to_thread(partial(recorder.succeed, run_id, usage=usage)))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await _await_uninterruptibly(task)
        raise _SuccessRecordedCancellation from None


async def _await_uninterruptibly(task: asyncio.Task[AwaitedT]) -> AwaitedT:
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            continue


async def _cancel_stream_result(
    result: StreamedRunResult[Any, Any] | None,
) -> None:
    if result is not None and not result.is_complete:
        await result.cancel()


async def _stop_stream_producer(task: asyncio.Task[None]) -> None:
    """Cancel and join a producer without abandoning its durable cleanup."""

    if not task.done():
        task.cancel()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    if not task.cancelled():
        task.result()


def _safe_usage(result: AgentRunResult[object]) -> dict[str, int]:
    return _usage_dict(result.usage)


def _usage_dict(usage: RunUsage) -> dict[str, int]:
    return {
        "requests": usage.requests,
        "tool_calls": usage.tool_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
    }
