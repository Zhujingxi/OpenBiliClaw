"""Supervised model-free recommendation replenishment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from openbiliclaw.content.integration.capabilities import SearchCapability
from openbiliclaw.core.jobs import IntervalSchedule, JobSpec, MissedRunPolicy, OverlapPolicy
from openbiliclaw.recommendation.discovery.planner import DiscoveryPlanner
from openbiliclaw.recommendation.discovery.service import DiscoveryService
from openbiliclaw.recommendation.evaluation.agent import CandidateScore, EvaluationBatch
from openbiliclaw.recommendation.evaluation.prefilter import normalize_and_prefilter
from openbiliclaw.recommendation.evaluation.service import EvaluationService
from openbiliclaw.recommendation.expression.service import ExpressionService
from openbiliclaw.recommendation.jobs import recommendation_jobs
from openbiliclaw.recommendation.models import (
    Candidate,
    CandidateState,
    DiscoveryProvenance,
    candidate_identity,
)
from openbiliclaw.recommendation.selection.service import SelectionService
from openbiliclaw.understanding.projections import discovery_projection

if TYPE_CHECKING:
    from openbiliclaw.access.models import AccessHandle
    from openbiliclaw.access.service import AccessService
    from openbiliclaw.composition.providers import ProviderGraph
    from openbiliclaw.composition.repositories import RepositoryGraph
    from openbiliclaw.content.integration.identity import ProviderId
    from openbiliclaw.understanding.service import UnderstandingService


class RecommendationPipeline:
    """One bounded discovery→prefilter→evaluation→selection pipeline."""

    def __init__(
        self,
        providers: ProviderGraph,
        access: AccessService,
        repositories: RepositoryGraph,
        understanding: UnderstandingService,
        *,
        target_count: int,
    ) -> None:
        self._providers = providers
        self._access = access
        self._repositories = repositories
        self._understanding = understanding
        self._target_count = target_count
        self._planner = DiscoveryPlanner()
        self._discovery = DiscoveryService(self._resolve)
        self._evaluation = EvaluationService(self._evaluate_model_free, lambda: datetime.now(UTC))
        self._selection = SelectionService(threshold=0.5)
        self._expression = ExpressionService(None, lambda: datetime.now(UTC))

    def _resolve(self, provider_id: ProviderId) -> tuple[SearchCapability, AccessHandle]:
        provider = self._providers.registry.provider(provider_id)
        if not isinstance(provider, SearchCapability):
            raise RuntimeError("provider search is unavailable")
        handle = self._access.connected_handle(provider_id.value, None)
        if handle is None:
            raise RuntimeError("provider is not connected")
        return provider, handle

    @staticmethod
    async def _evaluate_model_free(
        candidates: tuple[Candidate, ...],
    ) -> tuple[EvaluationBatch, str, int, int]:
        return (
            EvaluationBatch(
                results=tuple(
                    CandidateScore(
                        candidate_id=item.candidate_id,
                        score=0.65,
                        rationale="deterministic accessible content baseline",
                        uncertainty=0.35,
                    )
                    for item in candidates
                )
            ),
            "deterministic-baseline-v1",
            0,
            0,
        )

    async def replenish(self) -> None:
        now = datetime.now(UTC)
        profile = await self._understanding.profile("default")
        plans = await self._planner.plan(
            discovery_projection(profile),
            self._providers.registry.manifests(),
            inventory_count=0,
            target_inventory=self._target_count,
            provider_quota=1,
        )
        connected = tuple(
            plan
            for plan in plans
            if self._access.connected_handle(plan.provider_id.value, None) is not None
        )
        previews = await self._discovery.discover(connected, limit=min(self._target_count, 20))
        candidates = tuple(
            Candidate(
                candidate_id=candidate_identity(
                    preview.ref, "scheduled.search", preview.ref.provider_content_id
                ),
                preview=preview,
                provenance=DiscoveryProvenance(
                    strategy_id="scheduled.search",
                    query_key=preview.ref.provider_content_id,
                    discovered_at=now,
                ),
                expires_at=now + timedelta(days=7),
            )
            for preview in previews
        )
        for candidate in candidates:
            await self._repositories.recommendations.add_candidate(candidate)
        accepted, _rejected = normalize_and_prefilter(
            candidates,
            seen_ids=frozenset(),
            blocked_urls=frozenset(),
            avoidances=(),
            now=now,
        )
        for candidate in accepted:
            await self._repositories.recommendations.transition(
                candidate.candidate_id, CandidateState.DISCOVERED, CandidateState.NORMALIZED
            )
            await self._repositories.recommendations.transition(
                candidate.candidate_id, CandidateState.NORMALIZED, CandidateState.PREFILTERED
            )
        evaluated, records = await self._evaluation.evaluate(accepted)
        for candidate, record in zip(evaluated, records, strict=True):
            await self._repositories.recommendations.transition(
                candidate.candidate_id, CandidateState.PREFILTERED, CandidateState.EVALUATED
            )
            await self._repositories.recommendations.save_evaluation(record)
        selected, admissions, selections = self._selection.select(
            evaluated,
            records,
            limit=min(self._target_count, 100),
            seed=int(now.timestamp()),
            now=now,
        )
        by_id = {item.candidate_id: item for item in evaluated}
        await self._selection.persist_selection(
            self._repositories.recommendations,
            tuple(by_id[item.candidate_id] for item in selected),
            admissions,
            selections,
        )
        for expression in await self._expression.express(selections):
            await self._repositories.recommendations.save_expression(expression)

    async def expire(self) -> None:
        await self._repositories.recommendations.expire_due(now=datetime.now(UTC).isoformat())


def build_recommendation_jobs(pipeline: RecommendationPipeline) -> tuple[JobSpec, ...]:
    return recommendation_jobs(replenishment=pipeline.replenish, expiry=pipeline.expire)


def build_understanding_job(understanding: UnderstandingService) -> JobSpec:
    async def process() -> None:
        await understanding.process("default")

    return JobSpec(
        "understanding.analysis",
        IntervalSchedule(60),
        55,
        "model",
        OverlapPolicy.REJECT,
        MissedRunPolicy.SKIP,
        process,
    )
