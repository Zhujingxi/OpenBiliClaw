"""Supervised model-free recommendation replenishment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random
from typing import TYPE_CHECKING

from openbiliclaw.content.integration.capabilities import (
    FeedCapability,
    FeedQuery,
    PageRequest,
    SearchCapability,
)
from openbiliclaw.core.jobs import IntervalSchedule, JobSpec, MissedRunPolicy, OverlapPolicy
from openbiliclaw.recommendation.allocation import (
    AllocationDecision,
    HypothesisCounts,
    ThompsonAllocator,
)
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
    ExplorationAttribution,
    candidate_identity,
    record_identity,
)
from openbiliclaw.recommendation.policy_journal import JournalBrief, PolicyJournal
from openbiliclaw.recommendation.selection.service import SelectionService
from openbiliclaw.understanding.projections import discovery_projection, recommendation_projection

DEFAULT_PROFILE_ID = "default"

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.access.models import AccessHandle
    from openbiliclaw.access.service import AccessService
    from openbiliclaw.composition.providers import ProviderGraph
    from openbiliclaw.composition.repositories import RepositoryGraph
    from openbiliclaw.content.integration.identity import ProviderId
    from openbiliclaw.content.integration.projections import ContentPreview
    from openbiliclaw.recommendation.brief import BriefService
    from openbiliclaw.recommendation.hypotheses import HypothesisRegistry
    from openbiliclaw.understanding.service import UnderstandingService


@dataclass(frozen=True, slots=True)
class ReplenishmentResult:
    discovered: int
    added: int
    selected: int


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
        hypotheses: HypothesisRegistry,
        policy_journal: PolicyJournal,
        briefs: BriefService | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._providers = providers
        self._access = access
        self._repositories = repositories
        self._understanding = understanding
        self._target_count = target_count
        self._hypotheses = hypotheses
        self._policy_journal = policy_journal
        self._briefs = briefs
        self._clock = clock
        self._planner = DiscoveryPlanner()
        self._discovery = DiscoveryService(self._resolve)
        self._evaluation = EvaluationService(self._evaluate_model_free, self._clock)
        self._selection = SelectionService(threshold=0.5)
        self._expression = ExpressionService(None, self._clock)

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

    async def _ensure_hypothesis(self, arm: str, now: datetime) -> str:
        standing = await self._hypotheses.ensure_active(
            arm=arm,
            statement={
                "source-novel": "Cross-provider public feeds may expose relevant source novelty",
                "weak-signal": "Public feed signals may reveal an emerging interest",
                "exploit": "The familiar exploit strategy may satisfy current intent",
            }[arm],
            evidence_refs=("system:replenishment",),
            falsification="resolved failures exceed resolved successes",
            expires_at=now + timedelta(days=365),
            now=now,
        )
        return standing.hypothesis_id

    async def _allocate(self, seed: int, now: datetime) -> AllocationDecision:
        await self._ensure_hypothesis("source-novel", now)
        await self._ensure_hypothesis("weak-signal", now)
        await self._ensure_hypothesis("exploit", now)
        active = await self._hypotheses.active(now)
        counts: list[HypothesisCounts] = []
        for hypothesis in active:
            attempts, successes, failures = await self._hypotheses.posterior(
                hypothesis.hypothesis_id
            )
            counts.append(
                (
                    hypothesis.hypothesis_id,
                    hypothesis.arm,
                    attempts,
                    successes,
                    failures,
                )
            )
        decision = ThompsonAllocator(Random(seed)).decide(
            intent="uncertain",
            hypotheses=tuple(counts),
        )
        await self._journal_decision(seed, now, decision)
        return decision

    async def _exploit_only(self, seed: int, now: datetime) -> AllocationDecision:
        """Honor the explicit user statement without training policy from disengagement."""

        await self._ensure_hypothesis("exploit", now)
        decision = AllocationDecision(
            intent="uncertain",
            explore=False,
            arm=None,
            hypothesis_id=None,
            samples=(),
        )
        await self._journal_decision(seed, now, decision, explicit_zero=True)
        return decision

    async def _journal_decision(
        self,
        seed: int,
        now: datetime,
        decision: AllocationDecision,
        *,
        explicit_zero: bool = False,
    ) -> None:
        payload = {
            "kind": "allocation",
            "intent": decision.intent,
            "explore": decision.explore,
            "arm": decision.arm,
            "hypothesis_id": decision.hypothesis_id,
            "explicit_exploration_zero": explicit_zero,
            "samples": [
                {
                    "arm": sample.arm,
                    "hypothesis_id": sample.hypothesis_id,
                    "alpha": sample.alpha,
                    "beta": sample.beta,
                    "value": sample.value,
                }
                for sample in decision.samples
            ],
        }
        await self._policy_journal.append_brief(
            JournalBrief(
                brief_id=record_identity("brief", f"allocation:{seed}"),
                episode_id=f"replenishment:{seed}",
                status="active",
                payload=payload,
                created_at=now,
            )
        )

    async def _feed_supply(
        self,
        *,
        limit: int,
    ) -> tuple[tuple[ContentPreview, str], ...]:
        supply: list[tuple[ContentPreview, str]] = []
        manifests = self._providers.registry.manifests()
        public_channels = tuple(
            (manifest, channel)
            for manifest in manifests
            for channel in manifest.channels
            if not channel.auth_required
        )
        per_channel = max(1, limit // max(1, len(public_channels)))
        for manifest, channel in public_channels:
            provider = self._providers.registry.provider(manifest.provider_id)
            handle = self._access.connected_handle(manifest.provider_id.value, None)
            if not isinstance(provider, FeedCapability) or handle is None:
                continue
            try:
                page = await provider.feed(
                    FeedQuery(feed_id=channel.feed_id, page=PageRequest(limit=per_channel)),
                    handle,
                )
            except Exception:
                continue
            supply.extend(
                (preview, f"{manifest.provider_id.value}:{channel.feed_id}")
                for preview in page.items
            )
        return tuple(supply[:limit])

    async def _compile_shadow_brief(self, episode_id: str) -> None:
        if self._briefs is None:
            return
        try:
            await self._briefs.compile_shadow(episode_id)
        except Exception:
            # Shadow policy is observational: compiler or journal failures must not
            # alter the live replenishment path.
            return

    async def replenish(self, maximum_items: int | None = None) -> ReplenishmentResult:
        now = self._clock()
        seed = int(now.timestamp() * 1_000_000)
        limit = min(maximum_items or self._target_count, self._target_count, 20)
        profile = await self._understanding.profile(DEFAULT_PROFILE_ID)
        discovery_profile = discovery_projection(profile)
        plans = await self._planner.plan(
            discovery_profile,
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
        discovered = await self._discovery.discover(connected, limit=limit)
        await self._compile_shadow_brief(f"replenishment:{seed}")
        decision = (
            await self._exploit_only(seed, now)
            if profile.exploration_disabled()
            else await self._allocate(seed, now)
        )
        attribution = (
            ExplorationAttribution(
                hypothesis_id=decision.hypothesis_id,
                arm=decision.arm,
            )
            if decision.explore and decision.hypothesis_id is not None and decision.arm is not None
            else None
        )
        feed_supply = await self._feed_supply(limit=limit)
        search_candidates = tuple(
            Candidate(
                candidate_id=candidate_identity(
                    item.preview.ref,
                    "scheduled.search",
                    item.preview.ref.provider_content_id,
                ),
                preview=item.preview,
                provenance=DiscoveryProvenance(
                    strategy_id="scheduled.search",
                    query_key=item.preview.ref.provider_content_id,
                    provider=item.preview.ref.provider_id.value,
                    channel=None,
                    discovered_at=now,
                ),
                topics=(item.topic,),
                expires_at=now + timedelta(days=7),
            )
            for item in discovered
        )
        feed_candidates = tuple(
            Candidate(
                candidate_id=candidate_identity(
                    preview.ref,
                    "provider.feed",
                    f"{channel}:{preview.ref.provider_content_id}",
                ),
                preview=preview,
                provenance=DiscoveryProvenance(
                    strategy_id="provider.feed",
                    query_key=f"{channel}:{preview.ref.provider_content_id}",
                    provider=preview.ref.provider_id.value,
                    channel=channel,
                    exploration=(
                        attribution.model_copy(update={"channel": channel})
                        if attribution is not None
                        else None
                    ),
                    discovered_at=now,
                ),
                topics=(channel,),
                expires_at=now + timedelta(days=7),
            )
            for preview, channel in feed_supply
        )
        candidates = (*search_candidates, *feed_candidates)
        added_list: list[Candidate] = []
        for candidate in candidates:
            if await self._repositories.recommendations.add_candidate(candidate):
                added_list.append(candidate)
        added = tuple(added_list)
        accepted, _rejected = normalize_and_prefilter(
            added,
            seen_ids=frozenset(),
            blocked_urls=frozenset(),
            avoidances=discovery_profile.avoidances,
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
            seed=seed,
            now=now,
            negative_preferences=recommendation_projection(profile).negative_topics,
            exploration=(attribution,) if attribution is not None else (),
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
        return ReplenishmentResult(len(candidates), len(added), len(selections))

    async def expire(self) -> None:
        await self._repositories.recommendations.expire_due(now=datetime.now(UTC).isoformat())


def build_recommendation_jobs(pipeline: RecommendationPipeline) -> tuple[JobSpec, ...]:
    async def replenish() -> None:
        await pipeline.replenish()

    return recommendation_jobs(replenishment=replenish, expiry=pipeline.expire)


def build_understanding_job(understanding: UnderstandingService) -> JobSpec:
    async def process() -> None:
        await understanding.process(DEFAULT_PROFILE_ID)

    return JobSpec(
        "understanding.analysis",
        IntervalSchedule(60),
        55,
        "model",
        OverlapPolicy.REJECT,
        MissedRunPolicy.SKIP,
        process,
    )
