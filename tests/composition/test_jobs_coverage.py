from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from tests.recommendation.test_prefilter_expression import candidate

from openbiliclaw.composition.jobs import (
    RecommendationPipeline,
    build_recommendation_jobs,
    build_understanding_job,
)
from openbiliclaw.content.integration.identity import ProviderId
from openbiliclaw.recommendation.evaluation.agent import EvaluationBatch
from openbiliclaw.recommendation.models import (
    CandidateState,
    EvaluationRecord,
    record_identity,
)
from openbiliclaw.understanding.profile import CanonicalProfile

if TYPE_CHECKING:
    from openbiliclaw.content.integration.capabilities import SearchCapability

NOW = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_model_free_evaluation_and_empty_pipeline_are_executable() -> None:
    batch, model, input_tokens, output_tokens = await RecommendationPipeline._evaluate_model_free(
        ()
    )
    assert batch == EvaluationBatch(results=())
    assert (model, input_tokens, output_tokens) == ("deterministic-baseline-v1", 0, 0)

    resolver = object.__new__(RecommendationPipeline)
    resolver_dynamic = cast("Any", resolver)
    resolver_dynamic._providers = SimpleNamespace(
        registry=SimpleNamespace(provider=lambda _provider: object())
    )
    resolver_dynamic._access = SimpleNamespace(connected_handle=lambda *_: None)
    with pytest.raises(RuntimeError, match="search"):
        resolver._resolve(ProviderId(value="demo"))
    provider = SimpleNamespace(search=AsyncMock())
    resolver_dynamic._providers.registry.provider = lambda _provider: provider
    resolver_dynamic._access.connected_handle = lambda *_: None
    with pytest.raises(RuntimeError, match="connected"):
        resolver._resolve(ProviderId(value="demo"))
    searchable = cast("SearchCapability", provider)
    handle = object()
    resolver_dynamic._providers.registry.provider = lambda _provider: searchable
    resolver_dynamic._access.connected_handle = lambda *_: handle
    assert resolver._resolve(ProviderId(value="demo")) == (searchable, handle)

    pipeline = object.__new__(RecommendationPipeline)
    dynamic = cast("Any", pipeline)
    dynamic._understanding = SimpleNamespace(
        profile=AsyncMock(
            return_value=CanonicalProfile(profile_id="default", revision=0, updated_at=NOW)
        )
    )
    dynamic._providers = SimpleNamespace(registry=SimpleNamespace(manifests=lambda: ()))
    dynamic._access = SimpleNamespace(connected_handle=lambda *_: None)
    dynamic._target_count = 10
    dynamic._planner = SimpleNamespace(plan=AsyncMock(return_value=()))
    dynamic._discovery = SimpleNamespace(discover=AsyncMock(return_value=()))
    dynamic._evaluation = SimpleNamespace(evaluate=AsyncMock(return_value=((), ())))
    dynamic._selection = SimpleNamespace(
        select=lambda *_args, **_kwargs: ((), (), ()), persist_selection=AsyncMock()
    )
    dynamic._expression = SimpleNamespace(express=AsyncMock(return_value=()))
    dynamic._repositories = SimpleNamespace(recommendations=SimpleNamespace(expire_due=AsyncMock()))

    await pipeline.replenish()

    item = candidate("full")
    normalized = item.transition(CandidateState.NORMALIZED).transition(CandidateState.PREFILTERED)
    evaluated = normalized.transition(CandidateState.EVALUATED)
    record = EvaluationRecord(
        evaluation_id=record_identity("eval", item.candidate_id, "1"),
        candidate_id=item.candidate_id,
        model_instance="baseline",
        rubric_version=1,
        context_version=1,
        score=0.65,
        rationale="baseline",
        uncertainty=0.35,
        input_tokens=0,
        output_tokens=0,
        evaluated_at=NOW,
    )
    preview = item.preview.model_copy(update={"source_timestamp": datetime(2020, 1, 1, tzinfo=UTC)})
    dynamic._discovery.discover.return_value = (preview,)
    dynamic._evaluation.evaluate.return_value = ((evaluated,), (record,))
    dynamic._repositories.recommendations.add_candidate = AsyncMock()
    dynamic._repositories.recommendations.transition = AsyncMock()
    dynamic._repositories.recommendations.save_evaluation = AsyncMock()
    dynamic._repositories.recommendations.save_expression = AsyncMock()
    dynamic._selection.select = lambda *_args, **_kwargs: ((), (), ())
    dynamic._expression.express.return_value = (SimpleNamespace(),)
    await pipeline.replenish()
    await pipeline.expire()
    assert dynamic._selection.persist_selection.await_count == 2
    assert dynamic._repositories.recommendations.expire_due.await_count == 1


def test_recommendation_and_understanding_jobs_have_real_callbacks() -> None:
    pipeline = SimpleNamespace(replenish=AsyncMock(), expire=AsyncMock())
    jobs = build_recommendation_jobs(cast("Any", pipeline))
    assert tuple(job.job_id for job in jobs) == (
        "recommendation.replenishment",
        "recommendation.expiry",
    )
    understanding = SimpleNamespace(process=AsyncMock())
    job = build_understanding_job(cast("Any", understanding))
    assert job.job_id == "understanding.analysis"


@pytest.mark.asyncio
async def test_understanding_job_callback_processes_default_profile() -> None:
    understanding = SimpleNamespace(process=AsyncMock())
    job = build_understanding_job(cast("Any", understanding))
    await job.run()
    understanding.process.assert_awaited_once_with("default")
