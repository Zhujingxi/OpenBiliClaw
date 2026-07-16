"""Contract tests for the typed vNext generative-AI boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import Agent, ModelRetry, UsageLimitExceeded, UsageLimits, models
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_evals import Dataset
from sqlalchemy import select

from openbiliclaw.infrastructure.ai.runner import LiteLLMModelResolver, TaskRunner
from openbiliclaw.infrastructure.ai.spec import CachePolicy, TaskLane, TaskSpec
from openbiliclaw.infrastructure.ai.tasks import (
    CANDIDATE_ASSESSMENT_TASK,
    KEYWORD_GENERATION_TASK,
    PROFILE_DELTA_TASK,
    RECOMMENDATION_EXPLANATION_TASK,
)
from openbiliclaw.infrastructure.database.base import (
    Base,
    DatabaseSettings,
    create_engine_and_session,
)
from openbiliclaw.infrastructure.database.models import AIRunModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork

models.ALLOW_MODEL_REQUESTS = False
RUN_ID = UUID("00000000-0000-0000-0000-000000000901")
REPOSITORY_ROOT = Path(__file__).parents[2]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)


class CredentialBearingAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    provider_api_key: str = Field(min_length=1)
    nested: dict[str, str]


@dataclass
class RecordingSpy:
    started: list[tuple[str, str]] = field(default_factory=list)
    succeeded: list[tuple[UUID, dict[str, object], dict[str, int]]] = field(default_factory=list)
    failed: list[tuple[UUID, str]] = field(default_factory=list)

    def start(self, *, task_name: str, model_alias: str) -> UUID:
        self.started.append((task_name, model_alias))
        return RUN_ID

    def succeed(
        self,
        run_id: UUID,
        *,
        output_payload: dict[str, object],
        usage: dict[str, int],
    ) -> None:
        self.succeeded.append((run_id, output_payload, usage))

    def fail(self, run_id: UUID, *, error_kind: str) -> None:
        self.failed.append((run_id, error_kind))


def make_spec(
    agent: Agent[None, Answer],
    *,
    alias: str = "obc-interactive",
    lane: TaskLane = TaskLane.INTERACTIVE,
    semantic_retry_limit: int = 1,
    timeout_seconds: float = 1,
    usage_limits: UsageLimits | None = None,
) -> TaskSpec[Question, Answer]:
    return TaskSpec(
        name="answer-question",
        input_type=Question,
        output_type=Answer,
        agent=agent,
        model_alias=alias,
        semantic_retry_limit=semantic_retry_limit,
        timeout_seconds=timeout_seconds,
        usage_limits=usage_limits or UsageLimits(request_limit=3),
        cache_policy=CachePolicy.DEFAULT,
        lane=lane,
    )


async def test_runner_validates_input_before_starting_or_calling_model() -> None:
    model = TestModel(custom_output_args={"answer": "ok"})
    recorder = RecordingSpy()
    runner = TaskRunner(model_resolver=lambda alias: model, recorder=recorder)
    spec = make_spec(Agent(output_type=Answer))

    with pytest.raises(ValidationError):
        await runner.run(spec, {"text": "", "provider_api_key": "must-not-survive"})

    assert model.last_model_request_parameters is None
    assert recorder.started == []


async def test_runner_returns_typed_output_and_records_only_safe_outcome_fields() -> None:
    model = TestModel(custom_output_args={"answer": "typed"})
    recorder = RecordingSpy()
    resolved_aliases: list[str] = []

    def resolve(alias: str) -> TestModel:
        resolved_aliases.append(alias)
        return model

    runner = TaskRunner(model_resolver=resolve, recorder=recorder)
    spec = make_spec(Agent(output_type=Answer))
    raw_input = {"text": "raw private prompt that must not be recorded"}

    output = await runner.run(spec, raw_input)

    assert output == Answer(answer="typed")
    assert resolved_aliases == ["obc-interactive"]
    assert recorder.started == [("answer-question", "obc-interactive")]
    assert recorder.succeeded[0][0:2] == (RUN_ID, {"answer": "typed"})
    assert recorder.failed == []
    assert "private prompt" not in repr(recorder)


async def test_runner_redacts_credential_shaped_output_fields_before_recording() -> None:
    synthetic_key = "synthetic-provider-key-never-persist"
    model = TestModel(
        custom_output_args={
            "answer": "typed",
            "provider_api_key": synthetic_key,
            "nested": {"access_token": synthetic_key},
        }
    )
    recorder = RecordingSpy()
    runner = TaskRunner(model_resolver=lambda alias: model, recorder=recorder)
    spec = TaskSpec(
        name="credential-bearing-answer",
        input_type=Question,
        output_type=CredentialBearingAnswer,
        agent=Agent(output_type=CredentialBearingAnswer),
        model_alias="obc-interactive",
        semantic_retry_limit=0,
        timeout_seconds=1,
        usage_limits=UsageLimits(request_limit=1),
        cache_policy=CachePolicy.DEFAULT,
        lane=TaskLane.INTERACTIVE,
    )

    output = await runner.run(spec, {"text": "question"})

    assert output.provider_api_key == synthetic_key
    recorded = recorder.succeeded[0][1]
    assert recorded["provider_api_key"] == "[REDACTED]"
    assert recorded["nested"] == {"access_token": "[REDACTED]"}
    assert synthetic_key not in repr(recorder)


async def test_sqlalchemy_ai_run_record_never_persists_raw_input_or_credentials(
    tmp_path: Any,
) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{tmp_path / 'ai-run.db'}")
    )
    Base.metadata.create_all(engine)
    raw_secret = "synthetic-input-secret-must-not-be-stored"

    with UnitOfWork(session_factory) as uow:
        output = await TaskRunner(
            model_resolver=lambda alias: TestModel(custom_output_args={"answer": "safe"}),
            recorder=uow.ai_runs,
        ).run(make_spec(Agent(output_type=Answer)), {"text": raw_secret})
        uow.commit()

    with session_factory() as session:
        stored = session.scalar(select(AIRunModel))
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.output_payload == output.model_dump(mode="json")
        assert stored.usage is not None
        assert raw_secret not in repr(stored.__dict__)
        assert set(stored.__dict__) >= {
            "task_name",
            "model_alias",
            "status",
            "output_payload",
            "usage",
            "error",
        }
    engine.dispose()


async def test_semantic_output_retry_is_bounded_by_task_spec() -> None:
    attempts = 0
    agent: Agent[None, Answer] = Agent(output_type=Answer)

    @agent.output_validator
    def accept_second_attempt(output: Answer) -> Answer:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ModelRetry("semantic result was not admissible")
        return output

    recorder = RecordingSpy()
    runner = TaskRunner(
        model_resolver=lambda alias: TestModel(custom_output_args={"answer": "valid"}),
        recorder=recorder,
    )

    output = await runner.run(make_spec(agent, semantic_retry_limit=1), {"text": "question"})

    assert output.answer == "valid"
    assert attempts == 2
    assert recorder.failed == []


async def test_zero_semantic_retry_does_not_add_json_repair_or_fallback() -> None:
    attempts = 0
    agent: Agent[None, Answer] = Agent(output_type=Answer)

    @agent.output_validator
    def always_reject(output: Answer) -> Answer:
        nonlocal attempts
        attempts += 1
        raise ModelRetry("reject")

    recorder = RecordingSpy()
    runner = TaskRunner(
        model_resolver=lambda alias: TestModel(custom_output_args={"answer": "invalid"}),
        recorder=recorder,
    )

    with pytest.raises(Exception, match="maximum output retries"):
        await runner.run(
            make_spec(agent, semantic_retry_limit=0),
            {"text": "question"},
        )

    assert attempts == 1
    assert recorder.failed == [(RUN_ID, "UnexpectedModelBehavior")]


async def test_runner_enforces_timeout_and_records_only_error_classification() -> None:
    async def slow_model(messages: list[Any], info: Any) -> Any:
        await asyncio.sleep(1)
        raise AssertionError("timeout should cancel the test model")

    recorder = RecordingSpy()
    runner = TaskRunner(model_resolver=lambda alias: FunctionModel(slow_model), recorder=recorder)

    with pytest.raises(TimeoutError):
        await runner.run(
            make_spec(Agent(output_type=Answer), timeout_seconds=0.01),
            {"text": "secret prompt"},
        )

    assert recorder.failed == [(RUN_ID, "TimeoutError")]
    assert "secret prompt" not in repr(recorder)


async def test_runner_propagates_pydantic_ai_usage_limit_failure() -> None:
    recorder = RecordingSpy()
    runner = TaskRunner(
        model_resolver=lambda alias: TestModel(custom_output_args={"answer": "unused"}),
        recorder=recorder,
    )

    with pytest.raises(UsageLimitExceeded):
        await runner.run(
            make_spec(Agent(output_type=Answer), usage_limits=UsageLimits(request_limit=0)),
            {"text": "question"},
        )

    assert recorder.failed == [(RUN_ID, "UsageLimitExceeded")]


@pytest.mark.parametrize(
    ("lane", "alias"),
    [
        (TaskLane.INTERACTIVE, "obc-interactive"),
        (TaskLane.ANALYSIS, "obc-analysis"),
    ],
)
def test_task_spec_accepts_only_the_stable_alias_for_its_lane(lane: TaskLane, alias: str) -> None:
    spec = make_spec(Agent(output_type=Answer), lane=lane, alias=alias)
    assert spec.model_alias == alias

    other = "obc-analysis" if alias == "obc-interactive" else "obc-interactive"
    with pytest.raises(ValueError, match="requires model alias"):
        make_spec(Agent(output_type=Answer), lane=lane, alias=other)


def test_task_spec_rejects_unknown_or_embedding_aliases() -> None:
    with pytest.raises(ValueError, match="model alias"):
        make_spec(Agent(output_type=Answer), alias="provider-specific-model")
    with pytest.raises(ValueError, match="model alias"):
        make_spec(Agent(output_type=Answer), alias="obc-embedding")


async def test_litellm_resolver_normalizes_v1_and_disables_sdk_retries() -> None:
    resolver = LiteLLMModelResolver(
        base_url="http://litellm.test/v1/",
        api_key="synthetic-proxy-key",
    )

    try:
        assert str(resolver._client.base_url) == "http://litellm.test/v1/"
        assert resolver._client.max_retries == 0
        assert resolver("obc-interactive") is resolver("obc-interactive")
        assert resolver("obc-analysis") is resolver("obc-analysis")
    finally:
        await resolver.aclose()


@pytest.mark.parametrize(
    ("dataset_name", "spec"),
    [
        ("profile_delta", PROFILE_DELTA_TASK),
        ("keyword_generation", KEYWORD_GENERATION_TASK),
        ("candidate_assessment", CANDIDATE_ASSESSMENT_TASK),
        ("recommendation_explanation", RECOMMENDATION_EXPLANATION_TASK),
    ],
)
def test_eval_dataset_cases_match_their_typed_task_contracts(
    dataset_name: str, spec: TaskSpec[Any, Any]
) -> None:
    dataset = Dataset[dict[str, object], dict[str, object], dict[str, str]].from_file(
        REPOSITORY_ROOT / "evals" / "datasets" / f"{dataset_name}.yaml"
    )

    assert dataset.cases
    for case in dataset.cases:
        spec.input_type.model_validate(case.inputs)
        spec.output_type.model_validate(case.expected_output)
