"""Regression contracts for worker ownership, retries, and streamed chat."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from threading import Event, Thread, get_ident
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import pytest
from huey.exceptions import CancelExecution
from pydantic_ai import Agent, ModelRetry, UnexpectedModelBehavior, UsageLimitExceeded, UsageLimits
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import select, update

from openbiliclaw.api.routers.chat import ChatRequest, _chat_events
from openbiliclaw.features.chat.domain import ChatRole, ChatTurn
from openbiliclaw.features.chat.service import ChatChunkKind, ChatResponseDelta, ChatService
from openbiliclaw.features.system.domain import DatabaseSettings, UserSettings
from openbiliclaw.infrastructure.ai.runner import TaskRunner, TaskStreamOutput
from openbiliclaw.infrastructure.ai.spec import CachePolicy, TaskLane, TaskSpec
from openbiliclaw.infrastructure.ai.tasks import ChatResponseInput, ChatResponseOutput
from openbiliclaw.infrastructure.ai.use_cases import TaskRunnerChatResponder
from openbiliclaw.infrastructure.database.base import Base, create_engine_and_session
from openbiliclaw.infrastructure.database.models import JobRunModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs import tasks as job_tasks
from openbiliclaw.infrastructure.jobs import worker
from openbiliclaw.infrastructure.jobs.queue import PRIORITY_SCHEDULED, PRIORITY_USER_TRIGGERED
from openbiliclaw.infrastructure.jobs.tasks import (
    JobExecutionContext,
    JobRunStatus,
    JobService,
    PermanentJobError,
    classify_retry,
    feed_replenishment,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai.messages import ModelMessage

CONVERSATION = UUID("00000000-0000-0000-0000-000000002401")
RUN_ID = UUID("00000000-0000-0000-0000-000000002402")


@dataclass
class Recorder:
    started: list[tuple[str, str]] = field(default_factory=list)
    succeeded: list[tuple[UUID, dict[str, int]]] = field(default_factory=list)
    failed: list[tuple[UUID, str]] = field(default_factory=list)
    failed_usage: list[dict[str, int] | None] = field(default_factory=list)

    def start(self, *, task_name: str, model_alias: str) -> UUID:
        self.started.append((task_name, model_alias))
        return RUN_ID

    def succeed(self, run_id: UUID, *, usage: dict[str, int]) -> None:
        self.succeeded.append((run_id, usage))

    def fail(
        self,
        run_id: UUID,
        *,
        error_kind: str,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.failed.append((run_id, error_kind))
        self.failed_usage.append(usage)


class BlockingSuccessRecorder(Recorder):
    def __init__(self) -> None:
        super().__init__()
        self.success_started = Event()
        self.release_success = Event()

    def succeed(self, run_id: UUID, *, usage: dict[str, int]) -> None:
        self.success_started.set()
        assert self.release_success.wait(timeout=2)
        super().succeed(run_id, usage=usage)


def _chat_spec() -> TaskSpec[ChatResponseInput, ChatResponseOutput]:
    return TaskSpec(
        name="stream-chat-test",
        input_type=ChatResponseInput,
        output_type=ChatResponseOutput,
        agent=Agent(output_type=ChatResponseOutput),
        model_alias="obc-interactive",
        semantic_retry_limit=1,
        timeout_seconds=1,
        usage_limits=UsageLimits(request_limit=2, total_tokens_limit=1000),
        cache_policy=CachePolicy.BYPASS,
        lane=TaskLane.INTERACTIVE,
    )


@pytest.mark.asyncio
async def test_streaming_runner_preserves_semantic_retries_and_usage_limits() -> None:
    attempts = 0
    agent: Agent[None, ChatResponseOutput] = Agent(output_type=ChatResponseOutput)

    @agent.output_validator
    def accept_second(output: ChatResponseOutput) -> ChatResponseOutput:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ModelRetry("retry typed chat output")
        return output

    base = _chat_spec()
    spec = replace(base, agent=agent)
    recorder = Recorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: TestModel(custom_output_args={"content": "valid"}),
        recorder=recorder,
    )

    outputs = [
        output
        async for output in runner.stream(
            spec,
            ChatResponseInput(conversation_id=CONVERSATION, message="hello"),
        )
    ]
    assert outputs[-1].output.content == "valid"
    assert attempts == 2
    assert recorder.succeeded

    limited = replace(spec, usage_limits=UsageLimits(request_limit=0))
    with pytest.raises(UsageLimitExceeded):
        _ = [
            output
            async for output in runner.stream(
                limited,
                ChatResponseInput(conversation_id=CONVERSATION, message="hello"),
            )
        ]
    assert recorder.failed[-1] == (RUN_ID, "UsageLimitExceeded")


@pytest.mark.asyncio
async def test_runner_cancellation_waits_for_success_record_without_racing_failure() -> None:
    recorder = BlockingSuccessRecorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: TestModel(custom_output_args={"content": "valid"}),
        recorder=recorder,
    )

    task = asyncio.create_task(
        runner.run(
            _chat_spec(),
            ChatResponseInput(conversation_id=CONVERSATION, message="hello"),
        )
    )
    assert await asyncio.to_thread(recorder.success_started.wait, 1)
    task.cancel()
    recorder.release_success.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(recorder.succeeded) == 1
    assert recorder.failed == []


@pytest.mark.asyncio
async def test_stream_cancellation_waits_for_success_record_without_racing_failure() -> None:
    recorder = BlockingSuccessRecorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: TestModel(custom_output_args={"content": "valid"}),
        recorder=recorder,
    )

    async def consume() -> list[TaskStreamOutput[ChatResponseOutput]]:
        return [
            item
            async for item in runner.stream(
                replace(_chat_spec(), semantic_retry_limit=0),
                ChatResponseInput(conversation_id=CONVERSATION, message="hello"),
            )
        ]

    task = asyncio.create_task(consume())
    assert await asyncio.to_thread(recorder.success_started.wait, 1)
    task.cancel()
    recorder.release_success.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(recorder.succeeded) == 1
    assert recorder.failed == []


@pytest.mark.asyncio
async def test_runner_resolves_database_backed_settings_off_event_loop() -> None:
    caller_thread = get_ident()
    settings_threads: list[int] = []

    class Settings:
        def get(self) -> UserSettings:
            settings_threads.append(get_ident())
            return UserSettings()

    runner = TaskRunner(
        model_resolver=lambda _alias: TestModel(custom_output_args={"content": "valid"}),
        recorder=Recorder(),
        settings=Settings(),
    )

    output = await runner.run(
        _chat_spec(),
        ChatResponseInput(conversation_id=CONVERSATION, message="hello"),
    )

    assert output.content == "valid"
    assert settings_threads and settings_threads[0] != caller_thread


@pytest.mark.asyncio
async def test_streaming_runner_never_exposes_rejected_attempt_snapshots() -> None:
    requests = 0
    agent: Agent[None, ChatResponseOutput] = Agent(output_type=ChatResponseOutput)

    @agent.output_validator
    def reject_bad(output: ChatResponseOutput) -> ChatResponseOutput:
        if output.content.startswith("bad"):
            raise ModelRetry("reject bad attempt")
        return output

    async def model_stream(
        _messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        nonlocal requests
        requests += 1
        content = "bad leaked" if requests == 1 else "approved output"
        yield {
            0: DeltaToolCall(
                name=info.output_tools[0].name,
                json_args=f'{{"content":"{content[:8]}',
                tool_call_id=f"attempt-{requests}",
            )
        }
        yield {0: DeltaToolCall(json_args=f'{content[8:]}"}}')}

    spec = replace(_chat_spec(), agent=agent)
    recorder = Recorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: FunctionModel(stream_function=model_stream),
        recorder=recorder,
    )

    outputs = [
        item.output.content
        async for item in runner.stream(
            spec,
            ChatResponseInput(conversation_id=CONVERSATION, message="validate"),
        )
    ]

    assert requests == 2
    assert outputs
    assert all("bad" not in output for output in outputs)
    assert outputs[-1] == "approved output"


@pytest.mark.asyncio
async def test_streaming_runner_records_cancellation() -> None:
    started = asyncio.Event()
    never = asyncio.Event()

    async def blocked_stream(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        started.set()
        await never.wait()
        yield {0: DeltaToolCall(name="unused", json_args="{}")}

    recorder = Recorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: FunctionModel(stream_function=blocked_stream),
        recorder=recorder,
    )

    async def consume() -> None:
        _ = [
            output
            async for output in runner.stream(
                _chat_spec(),
                ChatResponseInput(conversation_id=CONVERSATION, message="cancel"),
            )
        ]

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert recorder.failed == [(RUN_ID, "CancelledError")]


@pytest.mark.asyncio
async def test_chat_responder_maps_history_into_typed_task_input() -> None:
    captured: list[ChatResponseInput] = []

    class Runner:
        async def stream(self, _spec: object, raw_input: ChatResponseInput):
            captured.append(raw_input)
            yield TaskStreamOutput(run_id=RUN_ID, output=ChatResponseOutput(content="one"))
            yield TaskStreamOutput(run_id=RUN_ID, output=ChatResponseOutput(content="one two"))

    history = (
        ChatTurn(conversation_id=CONVERSATION, role=ChatRole.USER, content="prior"),
        ChatTurn(conversation_id=CONVERSATION, role=ChatRole.ASSISTANT, content="answer"),
    )
    responder = TaskRunnerChatResponder(Runner())  # type: ignore[arg-type]

    deltas = [
        delta
        async for delta in responder.stream(
            conversation_id=CONVERSATION,
            message="current",
            history=history,
        )
    ]

    assert [delta.content for delta in deltas] == ["one", " two"]
    assert {delta.ai_run_id for delta in deltas} == {RUN_ID}
    assert [(turn.role, turn.content) for turn in captured[0].history] == [
        (ChatRole.USER, "prior"),
        (ChatRole.ASSISTANT, "answer"),
    ]


@pytest.mark.asyncio
async def test_task_runner_streams_validated_structured_output_and_records_once() -> None:
    async def model_stream(
        _messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        yield {
            0: DeltaToolCall(
                name=info.output_tools[0].name,
                json_args='{"content":"first',
                tool_call_id="chat-output",
            )
        }
        await asyncio.sleep(0)
        yield {0: DeltaToolCall(json_args=' second"}')}

    recorder = Recorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: FunctionModel(stream_function=model_stream),
        recorder=recorder,
    )
    outputs = [
        output
        async for output in runner.stream(
            _chat_spec(),
            ChatResponseInput(conversation_id=CONVERSATION, message="hello"),
        )
    ]

    assert [output.output.content for output in outputs] == ["first", "first second"]
    assert recorder.started == [("stream-chat-test", "obc-interactive")]
    assert recorder.succeeded and recorder.succeeded[0][0] == RUN_ID
    assert recorder.failed == []


@pytest.mark.asyncio
async def test_zero_retry_stream_exposes_live_typed_snapshots_before_provider_finishes() -> None:
    release_final = asyncio.Event()

    async def gated_stream(
        _messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        yield {
            0: DeltaToolCall(
                name=info.output_tools[0].name,
                json_args='{"content":"live',
                tool_call_id="live-output",
            )
        }
        await release_final.wait()
        yield {0: DeltaToolCall(json_args=' final"}')}

    runner = TaskRunner(
        model_resolver=lambda _alias: FunctionModel(stream_function=gated_stream),
        recorder=Recorder(),
    )
    stream = runner.stream(
        replace(_chat_spec(), semantic_retry_limit=0),
        ChatResponseInput(conversation_id=CONVERSATION, message="hello"),
    )
    first = await asyncio.wait_for(anext(stream), timeout=1)
    assert first.output.content == "live"

    release_final.set()
    remaining = [item async for item in stream]
    assert remaining[-1].output.content == "live final"


@pytest.mark.asyncio
async def test_early_stream_close_cancels_provider_and_records_usage_once() -> None:
    provider_closed = asyncio.Event()

    async def blocked_after_first(
        _messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        try:
            yield {
                0: DeltaToolCall(
                    name=info.output_tools[0].name,
                    json_args='{"content":"first',
                    tool_call_id="cancel-output",
                )
            }
            await asyncio.Event().wait()
        finally:
            provider_closed.set()

    recorder = Recorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: FunctionModel(stream_function=blocked_after_first),
        recorder=recorder,
    )
    stream = runner.stream(
        replace(_chat_spec(), semantic_retry_limit=0),
        ChatResponseInput(conversation_id=CONVERSATION, message="cancel"),
    )

    first = await anext(stream)
    assert first.output.content == "first"
    await stream.aclose()

    await asyncio.wait_for(provider_closed.wait(), timeout=1)
    assert recorder.failed == [(RUN_ID, "CancelledError")]
    assert recorder.succeeded == []
    assert recorder.failed_usage[0] is not None
    assert recorder.failed_usage[0]["requests"] == 1


@pytest.mark.asyncio
async def test_sync_ai_recorder_does_not_block_the_event_loop() -> None:
    recorder_started = Event()
    release_recorder = Event()

    class BlockingRecorder(Recorder):
        def start(self, *, task_name: str, model_alias: str) -> UUID:
            recorder_started.set()
            assert release_recorder.wait(timeout=1)
            return super().start(task_name=task_name, model_alias=model_alias)

    recorder = BlockingRecorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: TestModel(custom_output_args={"content": "done"}),
        recorder=recorder,
    )
    run_task = asyncio.create_task(
        runner.run(
            _chat_spec(),
            ChatResponseInput(conversation_id=CONVERSATION, message="hello"),
        )
    )
    assert await asyncio.to_thread(recorder_started.wait, 1)
    loop_progressed = asyncio.Event()
    asyncio.get_running_loop().call_soon(loop_progressed.set)
    await asyncio.wait_for(loop_progressed.wait(), timeout=0.1)

    release_recorder.set()
    assert (await run_task).content == "done"


@pytest.mark.asyncio
async def test_api_disconnect_closes_real_nested_chat_stream_in_same_context() -> None:
    provider_closed = asyncio.Event()

    async def blocked_after_first(
        _messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        try:
            yield {
                0: DeltaToolCall(
                    name=info.output_tools[0].name,
                    json_args='{"content":"first',
                    tool_call_id="api-disconnect-output",
                )
            }
            await asyncio.Event().wait()
        finally:
            provider_closed.set()

    recorder = Recorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: FunctionModel(stream_function=blocked_after_first),
        recorder=recorder,
    )
    repository = ChatRepository()
    chat = ChatService(
        lambda: ChatUow(repository),  # type: ignore[arg-type]
        responder=TaskRunnerChatResponder(runner),
    )

    class DisconnectAfterChunk:
        calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls >= 2

    class Container:
        pass

    container = Container()
    container.chat = chat  # type: ignore[attr-defined]
    events = [
        event
        async for event in _chat_events(
            ChatRequest(conversation_id=CONVERSATION, message="disconnect"),
            DisconnectAfterChunk(),  # type: ignore[arg-type]
            container,  # type: ignore[arg-type]
        )
    ]

    assert events == []
    assert provider_closed.is_set()
    assert recorder.failed == [(RUN_ID, "CancelledError")]
    assert recorder.succeeded == []


@pytest.mark.asyncio
async def test_cancellation_during_recorder_start_cannot_orphan_running_ai_run() -> None:
    recorder_started = Event()
    release_recorder = Event()
    recorder_completed = Event()

    class BlockingStartRecorder(Recorder):
        def start(self, *, task_name: str, model_alias: str) -> UUID:
            recorder_started.set()
            assert release_recorder.wait(timeout=1)
            try:
                return super().start(task_name=task_name, model_alias=model_alias)
            finally:
                recorder_completed.set()

    recorder = BlockingStartRecorder()
    runner = TaskRunner(
        model_resolver=lambda _alias: TestModel(custom_output_args={"content": "unused"}),
        recorder=recorder,
    )
    task = asyncio.create_task(
        runner.run(
            _chat_spec(),
            ChatResponseInput(conversation_id=CONVERSATION, message="cancel start"),
        )
    )
    assert await asyncio.to_thread(recorder_started.wait, 1)

    task.cancel()
    release_recorder.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(recorder_completed.wait, 1)
    assert recorder.failed == [(RUN_ID, "CancelledError")]
    assert recorder.succeeded == []


class ChatRepository:
    def __init__(self) -> None:
        self.turns: list[ChatTurn] = []

    def add(self, turn: ChatTurn) -> None:
        self.turns.append(turn)

    def list_recent_by_conversation(
        self, conversation_id: UUID, *, limit: int
    ) -> tuple[ChatTurn, ...]:
        matches = [turn for turn in self.turns if turn.conversation_id == conversation_id]
        return tuple(matches[-limit:])

    def list_by_conversation(
        self, conversation_id: UUID, *, limit: int, offset: int
    ) -> tuple[ChatTurn, ...]:
        matches = [turn for turn in self.turns if turn.conversation_id == conversation_id]
        return tuple(matches[offset : offset + limit])


class ChatUow:
    def __init__(self, repository: ChatRepository) -> None:
        self.chat = repository
        self.activities = repository

    def __enter__(self) -> ChatUow:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        return None


class StreamingResponder:
    def __init__(self) -> None:
        self.history: tuple[ChatTurn, ...] = ()

    async def stream(
        self,
        *,
        conversation_id: UUID,
        message: str,
        history: tuple[ChatTurn, ...],
    ) -> AsyncIterator[ChatResponseDelta]:
        del conversation_id, message
        self.history = history
        yield ChatResponseDelta(content="first", ai_run_id=RUN_ID)
        await asyncio.sleep(0)
        yield ChatResponseDelta(content=" second", ai_run_id=RUN_ID)


@pytest.mark.asyncio
async def test_chat_loads_bounded_history_and_emits_multiple_provider_deltas() -> None:
    repository = ChatRepository()
    for index in range(5):
        repository.add(
            ChatTurn(
                conversation_id=CONVERSATION,
                role=ChatRole.USER if index % 2 == 0 else ChatRole.ASSISTANT,
                content=f"old-{index}",
            )
        )
    responder = StreamingResponder()
    service = ChatService(
        lambda: ChatUow(repository),  # type: ignore[arg-type]
        responder=responder,
        history_limit=3,
    )

    chunks = [
        chunk
        async for chunk in service.stream(
            conversation_id=CONVERSATION,
            message="new",
        )
    ]

    assert [turn.content for turn in responder.history] == ["old-2", "old-3", "old-4"]
    assert [chunk.kind for chunk in chunks] == [
        ChatChunkKind.DELTA,
        ChatChunkKind.DELTA,
        ChatChunkKind.DONE,
    ]
    assert [chunk.content for chunk in chunks[:-1]] == ["first", " second"]
    assert repository.turns[-2].content == "new"
    assert repository.turns[-1].content == "first second"
    assert repository.turns[-1].ai_run_id == RUN_ID


def test_worker_recovery_does_not_steal_an_active_run(tmp_path: Any) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{tmp_path / 'leases.db'}")
    )
    Base.metadata.create_all(engine)
    queue: list[tuple[str, UUID, int]] = []

    class Queue:
        def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
            queue.append((job_name, run_id, priority))

    first = JobService(
        lambda: UnitOfWork(session_factory),
        queue=Queue(),
        worker_id="worker-a",
        lease_seconds=60,
    )
    second = JobService(
        lambda: UnitOfWork(session_factory),
        queue=Queue(),
        worker_id="worker-b",
        lease_seconds=60,
    )
    run = first.schedule("source_sync", idempotency_key="owned")
    assert first.claim(run.id) is not None

    assert second.recover_interrupted(now=datetime.now(UTC)) == ()
    assert second.inspect(run.id).status is JobRunStatus.RUNNING

    with session_factory() as session:
        session.execute(
            update(JobRunModel)
            .where(JobRunModel.id == str(run.id))
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()

    assert second.recover_interrupted(now=datetime.now(UTC)) == (run.id,)
    assert second.inspect(run.id).status is JobRunStatus.PENDING
    with session_factory() as session:
        row = session.scalar(select(JobRunModel).where(JobRunModel.id == str(run.id)))
        assert row is not None
        assert row.worker_id is None
        assert row.lease_expires_at is None
    engine.dispose()


def test_reclaimed_same_process_run_fences_the_stale_execution(tmp_path: Any) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{tmp_path / 'fence.db'}")
    )
    Base.metadata.create_all(engine)

    class Queue:
        def enqueue(self, _job_name: str, _run_id: UUID, _priority: int) -> None:
            return None

    service = JobService(
        lambda: UnitOfWork(session_factory),
        queue=Queue(),
        worker_id="same-process",
        lease_seconds=60,
    )
    run = service.schedule("source_sync", idempotency_key="fenced")
    stale_claim = service.claim(run.id)
    assert stale_claim is not None
    stale = JobExecutionContext(service, run.id, stale_claim)
    with session_factory() as session:
        session.execute(
            update(JobRunModel)
            .where(JobRunModel.id == str(run.id))
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
    service.recover_expired_leases()
    current_claim = service.claim(run.id)
    assert current_claim is not None
    current = JobExecutionContext(service, run.id, current_claim)

    with pytest.raises(RuntimeError, match="not running"):
        stale.checkpoint(0.5)
    service.succeed(run.id, claim_token=stale.claim_token)
    assert service.inspect(run.id).status is JobRunStatus.RUNNING
    current.checkpoint(0.5)
    service.succeed(run.id, claim_token=current.claim_token)
    assert service.inspect(run.id).status is JobRunStatus.SUCCEEDED
    engine.dispose()


def test_delayed_retry_is_not_republished_early_and_budget_is_durable(tmp_path: Any) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{tmp_path / 'retry.db'}")
    )
    Base.metadata.create_all(engine)
    enqueued: list[UUID] = []

    class Queue:
        def enqueue(self, _job_name: str, run_id: UUID, _priority: int) -> None:
            enqueued.append(run_id)

    service = JobService(lambda: UnitOfWork(session_factory), queue=Queue())
    run = service.schedule("source_sync", idempotency_key="retry-budget")
    first_claim = service.claim(run.id)
    assert isinstance(first_claim, str)
    assert service.retry(
        run.id,
        TimeoutError("later"),
        delay_seconds=60,
        claim_token=first_claim,
    )
    before = len(enqueued)

    service.recover_interrupted(now=datetime.now(UTC))
    assert len(enqueued) == before
    assert service.claim(run.id) is None

    with session_factory() as session:
        session.execute(
            update(JobRunModel)
            .where(JobRunModel.id == str(run.id))
            .values(retry_not_before=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
    service.recover_expired_leases()
    assert len(enqueued) == before + 1
    second_claim = service.claim(run.id)
    assert isinstance(second_claim, str)
    assert service.retry(
        run.id,
        TimeoutError("last"),
        delay_seconds=0,
        claim_token=second_claim,
    )
    third_claim = service.claim(run.id)
    assert isinstance(third_claim, str)
    assert (
        service.retry(
            run.id,
            TimeoutError("exhausted"),
            delay_seconds=0,
            claim_token=third_claim,
        )
        is False
    )
    service.fail(run.id, TimeoutError("exhausted"), claim_token=third_claim)
    assert service.inspect(run.id).status is JobRunStatus.FAILED
    engine.dispose()


def test_expired_final_attempt_becomes_terminal_instead_of_unclaimable(
    tmp_path: Any,
) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{tmp_path / 'final-expiry.db'}")
    )
    Base.metadata.create_all(engine)

    class Queue:
        def enqueue(self, _job_name: str, _run_id: UUID, _priority: int) -> None:
            return None

    service = JobService(lambda: UnitOfWork(session_factory), queue=Queue())
    run = service.schedule("cleanup", idempotency_key="final-expiry")
    first_claim = service.claim(run.id)
    assert isinstance(first_claim, str)
    with session_factory() as session:
        session.execute(
            update(JobRunModel)
            .where(JobRunModel.id == str(run.id))
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
    assert service.recover_expired_leases() == (run.id,)
    second_claim = service.claim(run.id)
    assert isinstance(second_claim, str)
    with session_factory() as session:
        session.execute(
            update(JobRunModel)
            .where(JobRunModel.id == str(run.id))
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()

    assert service.recover_expired_leases() == ()
    terminal = service.inspect(run.id)
    assert terminal.status is JobRunStatus.FAILED
    assert terminal.error == "WorkerInterrupted"
    engine.dispose()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("offline"), True),
        (ModelHTTPError(429, "obc-analysis"), True),
        (ModelHTTPError(503, "obc-analysis"), True),
        (ModelHTTPError(400, "obc-analysis"), False),
        (UnexpectedModelBehavior("invalid semantic output"), False),
        (ValueError("invalid connector result"), False),
    ],
)
def test_retry_classifier_distinguishes_transport_from_semantic_failures(
    error: Exception, expected: bool
) -> None:
    assert classify_retry(error) is expected


def test_permanent_job_error_stops_retry_even_with_transient_cause() -> None:
    try:
        raise PermanentJobError("invalid work") from httpx.ConnectError("offline")
    except PermanentJobError as error:
        assert classify_retry(error) is False


def test_periodic_feed_replenishment_uses_scheduled_maintenance_priority() -> None:
    assert feed_replenishment.settings["default_priority"] == PRIORITY_SCHEDULED


def test_explicit_feed_job_is_user_triggered_but_periodic_is_scheduled(tmp_path: Any) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{tmp_path / 'priorities.db'}")
    )
    Base.metadata.create_all(engine)

    class Queue:
        def enqueue(self, _job_name: str, _run_id: UUID, _priority: int) -> None:
            return None

    service = JobService(lambda: UnitOfWork(session_factory), queue=Queue())

    explicit = service.schedule("feed_replenishment", idempotency_key="explicit")
    periodic = service.schedule_periodic(
        "feed_replenishment", now=datetime(2026, 7, 17, tzinfo=UTC)
    )

    assert explicit.priority == PRIORITY_USER_TRIGGERED
    assert periodic.priority == PRIORITY_SCHEDULED
    engine.dispose()


def test_worker_lifecycle_keeps_sweeping_for_newly_expired_leases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swept_after_startup = Event()

    class Service:
        recovery_interval_seconds = 0.01

        def __init__(self) -> None:
            self.calls = 0

        def recover_interrupted(self) -> None:
            self.calls += 1

        def recover_expired_leases(self) -> None:
            self.calls += 1
            if self.calls >= 2:
                swept_after_startup.set()

    class Consumer:
        def run(self) -> None:
            assert swept_after_startup.wait(timeout=1)

        def stop(self, *, graceful: bool = False) -> None:
            assert graceful is True

    service = Service()
    monkeypatch.setattr(worker.huey, "create_consumer", lambda **_kwargs: Consumer())

    worker.run_worker(
        lambda: (service, {}),  # type: ignore[arg-type,return-value]
        workers=1,
        settings_loader=UserSettings,
    )

    assert service.calls >= 2


def test_executor_shutdown_leaves_incomplete_job_lease_recoverable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setattr(worker, "ASYNC_SHUTDOWN_TIMEOUT_SECONDS", 0.05)
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{tmp_path / 'shutdown-recovery.db'}")
    )
    Base.metadata.create_all(engine)

    class Queue:
        def __init__(self) -> None:
            self.enqueued: list[UUID] = []

        def enqueue(self, _job_name: str, run_id: UUID, _priority: int) -> None:
            self.enqueued.append(run_id)

    queue = Queue()
    service = JobService(
        lambda: UnitOfWork(session_factory),
        queue=queue,
        worker_id="shutdown-worker",
        lease_seconds=0.1,
    )
    run = service.schedule("cleanup", idempotency_key="shutdown-recovery")
    started = Event()

    async def cancellation_resistant_job(
        _run_id: UUID,
        _context: JobExecutionContext,
    ) -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            stop_resisting_at = asyncio.get_running_loop().time() + 0.3
        else:
            return
        while asyncio.get_running_loop().time() < stop_resisting_at:
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                continue
        raise asyncio.CancelledError

    executor = worker.AsyncJobExecutor()
    job_tasks.configure_job_runtime(
        service,
        {"cleanup": cancellation_resistant_job},
        async_job_runner=executor.run,
    )
    errors: list[BaseException] = []

    def invoke() -> None:
        try:
            job_tasks._run_job("cleanup", str(run.id), None)
        except BaseException as error:
            errors.append(error)

    caller = Thread(target=invoke)
    caller.start()
    try:
        assert started.wait(timeout=1)
        executor.close()
        caller.join(timeout=1)
        assert caller.is_alive() is False
        assert len(errors) == 1
        assert isinstance(errors[0], CancelExecution)

        interrupted = service.inspect(run.id)
        assert interrupted.status is JobRunStatus.RUNNING
        assert interrupted.progress < 0.99

        recovered = service.recover_expired_leases(now=datetime.now(UTC) + timedelta(seconds=1))
        assert recovered == (run.id,)
        pending = service.inspect(run.id)
        assert pending.status is JobRunStatus.PENDING
        assert pending.error == "WorkerInterrupted"
    finally:
        job_tasks.configure_job_runtime(service, {})
        engine.dispose()
