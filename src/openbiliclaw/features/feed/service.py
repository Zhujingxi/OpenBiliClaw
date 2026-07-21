"""Deterministic feed collection, batch assessment, admission, and feedback."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID, uuid5

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind, ProfileSignal
from openbiliclaw.features.activity.service import project_activity_event
from openbiliclaw.features.feed.domain import (
    CandidateAssessment,
    ContentItem,
    FeedEntry,
    FeedItem,
    Interaction,
    InteractionKind,
    feed_deficit,
)
from openbiliclaw.features.sources.domain import (
    SourceConnector,
    SourceOperation,
    SourceResultKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import TracebackType

    from openbiliclaw.features.profile.domain import ProfileSnapshot
    from openbiliclaw.features.system.domain import UserSettings

_FEEDBACK_EVENT_NAMESPACE = UUID("613c8260-0658-4e94-8730-83b5627f1077")
_PROFILE_FEEDBACK_SENTIMENT = {
    InteractionKind.POSITIVE: "positive",
    InteractionKind.NEGATIVE: "negative",
    InteractionKind.DISMISS: "negative",
}


@dataclass(frozen=True, slots=True)
class FeedPolicy:
    """Application-owned, bounded admission policy."""

    low_watermark: int = 10
    high_watermark: int = 20
    candidate_multiplier: int = 3
    max_batch_candidates: int = 100
    min_score: float = 0.55
    min_novelty: float = 0.2
    max_per_source: int = 4
    max_per_topic: int = 3

    def __post_init__(self) -> None:
        feed_deficit(0, self.low_watermark, self.high_watermark)
        if self.candidate_multiplier < 1:
            raise ValueError("candidate multiplier must be positive")
        if not 1 <= self.max_batch_candidates <= 100:
            raise ValueError("candidate batch bound must be between 1 and 100")
        if not 0 <= self.min_score <= 1 or not 0 <= self.min_novelty <= 1:
            raise ValueError("feed score thresholds must be between zero and one")
        if self.max_per_source < 1 or self.max_per_topic < 1:
            raise ValueError("feed diversity bounds must be positive")


class StaleFeedProfileRevisionError(RuntimeError):
    """Raised when assessed candidates no longer match the latest profile revision."""


class ContentRepository(Protocol):
    def add(self, item: ContentItem) -> None: ...

    def get(self, content_id: UUID) -> ContentItem | None: ...

    def get_by_identity(self, source_id: str, external_id: str) -> ContentItem | None: ...

    def flush(self) -> None: ...


class AssessmentRepository(Protocol):
    def add(self, assessment: CandidateAssessment) -> None: ...

    def excluded_content_ids(self, profile_revision: int) -> frozenset[UUID]: ...

    def excluded_content_identities(self, profile_revision: int) -> frozenset[tuple[str, str]]: ...


class FeedRepository(Protocol):
    def add(self, entry: FeedEntry) -> None: ...

    def unseen_count(self) -> int: ...

    def unseen_diversity_keys(self) -> tuple[tuple[str, tuple[str, ...]], ...]: ...

    def next_position(self) -> int: ...

    def list_entries(self, *, limit: int, offset: int) -> tuple[FeedItem, ...]: ...


class InteractionRepository(Protocol):
    def add(self, interaction: Interaction) -> None: ...

    def adjustment(self, content_id: UUID) -> float: ...


class ActivityRepository(Protocol):
    def add(self, event: ActivityEvent) -> None: ...


class ProfileRepository(Protocol):
    def latest(self) -> ProfileSnapshot | None: ...


class FeedUnitOfWork(Protocol):
    content: ContentRepository
    assessments: AssessmentRepository
    feed: FeedRepository
    interactions: InteractionRepository
    activities: ActivityRepository
    profiles: ProfileRepository

    def __enter__(self) -> FeedUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class CandidateBatchAssessor(Protocol):
    """One typed batch call for a bounded candidate set."""

    async def assess_batch(
        self,
        profile: ProfileSnapshot,
        content: tuple[ContentItem, ...],
    ) -> tuple[CandidateAssessment, ...]: ...


class DiscoveryQueryPlanner(Protocol):
    """Generate source-neutral queries from the current evidence profile."""

    async def plan(self, profile: ProfileSnapshot, *, limit: int) -> tuple[str, ...]: ...


class RecommendationExplainer(Protocol):
    """Explain an already-admitted item without controlling admission or persistence."""

    async def explain(
        self,
        profile: ProfileSnapshot,
        content: ContentItem,
        assessment: CandidateAssessment,
    ) -> str: ...


class CandidateNoveltyScorer(Protocol):
    """Return bounded semantic-diversity scores for one normalized candidate batch."""

    async def score(self, content: tuple[ContentItem, ...]) -> Mapping[UUID, float]: ...


class FeedSettings(Protocol):
    """Typed settings read port supplied by SettingsService."""

    def get(self) -> UserSettings: ...


@dataclass(frozen=True, slots=True)
class _ReplenishmentState:
    profile: ProfileSnapshot | None
    current_unseen: int
    excluded_ids: frozenset[UUID]
    excluded_identities: frozenset[tuple[str, str]]
    diversity_keys: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class _NormalizedCandidates:
    batch: tuple[ContentItem, ...]
    new_content: tuple[ContentItem, ...]


def _checkpoint(callback: Callable[[float], None] | None, progress: float) -> None:
    if callback is not None:
        callback(progress)


def allocate_source_limits(
    deficit: int,
    source_ids: Sequence[str],
    *,
    weights: Mapping[str, float] | None = None,
) -> dict[str, int]:
    """Allocate exactly by normalized weights and stable largest remainder."""

    if deficit < 0:
        raise ValueError("source allocation deficit cannot be negative")
    ordered = sorted(set(source_ids))
    if not ordered:
        return {}
    resolved = {source_id: 1.0 for source_id in ordered}
    if weights is not None:
        resolved = {source_id: float(weights.get(source_id, 0.0)) for source_id in ordered}
    if any(not math.isfinite(weight) or weight < 0 for weight in resolved.values()):
        raise ValueError("source weights must be finite and non-negative")
    positive = {source_id: weight for source_id, weight in resolved.items() if weight > 0}
    if not positive or deficit == 0:
        return {}
    total_weight = sum(positive.values())
    exact = {source_id: deficit * weight / total_weight for source_id, weight in positive.items()}
    allocation = {source_id: int(value) for source_id, value in exact.items()}
    remaining = deficit - sum(allocation.values())
    remainder_order = sorted(
        positive,
        key=lambda source_id: (-(exact[source_id] - allocation[source_id]), source_id),
    )
    for source_id in remainder_order[:remaining]:
        allocation[source_id] += 1
    return {source_id: count for source_id, count in allocation.items() if count > 0}


def _operation(connector: SourceConnector) -> SourceOperation | None:
    """Choose only one concrete operation that the connector advertises."""

    supported = {
        spec.operation
        for spec in connector.manifest.operations
        if spec.result_kind is SourceResultKind.CONTENT
    }
    for operation in (
        SourceOperation.FEED,
        SourceOperation.TRENDING,
        SourceOperation.SEARCH,
        SourceOperation.COMMUNITY,
    ):
        if operation in supported:
            return operation
    return None


def _query(profile: ProfileSnapshot) -> str | None:
    interests = [
        facet.value for facet in profile.facets if facet.name == "interests" and facet.weight > 0
    ]
    return " ".join(interests[:3]) or profile.narrative.strip() or None


def _validate_assessments(
    profile: ProfileSnapshot,
    content: tuple[ContentItem, ...],
    assessments: tuple[CandidateAssessment, ...],
) -> dict[UUID, CandidateAssessment]:
    expected = {item.id for item in content}
    by_content: dict[UUID, CandidateAssessment] = {}
    for assessment in assessments:
        if assessment.content_id not in expected:
            raise ValueError("batch assessment references an unknown candidate")
        if assessment.profile_revision != profile.revision:
            raise ValueError("batch assessment references a stale profile revision")
        if assessment.content_id in by_content:
            raise ValueError("batch assessment contains duplicate candidates")
        by_content[assessment.content_id] = assessment
    if set(by_content) != expected:
        raise ValueError("batch assessment must return exactly one result per candidate")
    return by_content


def _diversity_counts(
    keys: Sequence[tuple[str, tuple[str, ...]]],
) -> tuple[dict[str, int], dict[str, int]]:
    source_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for source_id, raw_topics in keys:
        source_counts[source_id] = source_counts.get(source_id, 0) + 1
        for topic in dict.fromkeys(value.casefold() for value in raw_topics if value):
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    return source_counts, topic_counts


class FeedService:
    """Replenish a bounded local feed from supported read-only source operations."""

    def __init__(
        self,
        uow_factory: Callable[[], FeedUnitOfWork],
        *,
        connectors: Sequence[SourceConnector] | Callable[[], Sequence[SourceConnector]],
        assessor: CandidateBatchAssessor,
        query_planner: DiscoveryQueryPlanner | None = None,
        explainer: RecommendationExplainer | None = None,
        novelty_scorer: CandidateNoveltyScorer | None = None,
        policy: FeedPolicy | None = None,
        settings: FeedSettings | None = None,
        profile_conflict_retries: int = 1,
    ) -> None:
        if not 0 <= profile_conflict_retries <= 3:
            raise ValueError("profile conflict retries must be between zero and three")
        self._uow_factory = uow_factory
        self._connector_provider = connectors if callable(connectors) else lambda: tuple(connectors)
        self._assessor = assessor
        self._query_planner = query_planner
        self._explainer = explainer
        self._novelty_scorer = novelty_scorer
        self._policy = policy or FeedPolicy()
        self._settings = settings
        self._profile_conflict_retries = profile_conflict_retries

    def list_entries(self, *, limit: int = 50, offset: int = 0) -> tuple[FeedItem, ...]:
        """Return a bounded ordered feed projection."""

        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("invalid feed page")
        with self._uow_factory() as uow:
            return uow.feed.list_entries(limit=limit, offset=offset)

    def _load_replenishment_state(self) -> _ReplenishmentState:
        with self._uow_factory() as uow:
            profile = uow.profiles.latest()
            current_unseen = uow.feed.unseen_count()
            diversity_keys = uow.feed.unseen_diversity_keys()
            excluded_ids = (
                frozenset()
                if profile is None
                else uow.assessments.excluded_content_ids(profile.revision)
            )
            excluded_identities = (
                frozenset()
                if profile is None
                else uow.assessments.excluded_content_identities(profile.revision)
            )
        return _ReplenishmentState(
            profile=profile,
            current_unseen=current_unseen,
            excluded_ids=excluded_ids,
            excluded_identities=excluded_identities,
            diversity_keys=diversity_keys,
        )

    def _effective_policy(self, settings: UserSettings | None) -> FeedPolicy:
        if settings is None:
            return self._policy
        return FeedPolicy(
            low_watermark=settings.feed.low_watermark,
            high_watermark=settings.feed.high_watermark,
            candidate_multiplier=settings.feed.candidate_multiplier,
            max_batch_candidates=settings.feed.max_batch_candidates,
            min_score=settings.feed.min_score,
            min_novelty=settings.feed.min_novelty,
            max_per_source=settings.feed.max_per_source,
            max_per_topic=settings.feed.max_per_topic,
        )

    def _eligible_connectors(
        self, settings: UserSettings | None
    ) -> tuple[tuple[SourceConnector, SourceOperation], ...]:
        eligible: list[tuple[SourceConnector, SourceOperation]] = []
        for connector in self._connector_provider():
            source_id = connector.manifest.source_id.value
            enabled = (
                cast("Mapping[str, bool]", settings.sources.enabled)
                if settings is not None
                else None
            )
            if enabled is not None and not enabled.get(source_id, False):
                continue
            operation = _operation(connector)
            if operation is not None:
                eligible.append((connector, operation))
        return tuple(eligible)

    async def _collect_from_connector(
        self,
        connector: SourceConnector,
        operation: SourceOperation,
        *,
        profile: ProfileSnapshot,
        limit: int,
        state: _ReplenishmentState,
        identities: set[tuple[str, str]],
        query: str | None,
        checkpoint: Callable[[float], None] | None,
    ) -> tuple[ContentItem, ...]:
        _checkpoint(checkpoint, 0.15)
        resolved_query = query if operation.requires_input else None
        request_limit = min(100, limit + len(state.excluded_ids))
        result = await connector.execute(operation, query=resolved_query, limit=request_limit)
        _checkpoint(checkpoint, 0.35)
        accepted: list[ContentItem] = []
        for item in result:
            if not isinstance(item, ContentItem):
                raise TypeError("content discovery operation returned activity evidence")
            identity = (item.source_id, item.external_id)
            if (
                identity in identities
                or identity in state.excluded_identities
                or item.id in state.excluded_ids
            ):
                continue
            identities.add(identity)
            accepted.append(item)
            if len(accepted) == limit:
                break
        return tuple(accepted)

    async def _collect_candidates(
        self,
        profile: ProfileSnapshot,
        state: _ReplenishmentState,
        eligible: tuple[tuple[SourceConnector, SourceOperation], ...],
        allocations: Mapping[str, int],
        queries: Mapping[str, str],
        checkpoint: Callable[[float], None] | None,
    ) -> tuple[ContentItem, ...]:
        candidates: list[ContentItem] = []
        identities: set[tuple[str, str]] = set()
        for connector, operation in sorted(
            eligible, key=lambda pair: pair[0].manifest.source_id.value
        ):
            limit = allocations.get(connector.manifest.source_id.value, 0)
            if not limit:
                continue
            candidates.extend(
                await self._collect_from_connector(
                    connector,
                    operation,
                    profile=profile,
                    limit=limit,
                    state=state,
                    identities=identities,
                    query=queries.get(connector.manifest.source_id.value, _query(profile)),
                    checkpoint=checkpoint,
                )
            )
        return tuple(candidates)

    def _normalize_candidates(self, candidates: tuple[ContentItem, ...]) -> _NormalizedCandidates:
        normalized: list[ContentItem] = []
        new_content: list[ContentItem] = []
        with self._uow_factory() as uow:
            for candidate in candidates:
                existing = uow.content.get_by_identity(candidate.source_id, candidate.external_id)
                normalized.append(existing or candidate)
                if existing is None:
                    new_content.append(candidate)
        return _NormalizedCandidates(tuple(normalized), tuple(new_content))

    def _adjustments(self, batch: tuple[ContentItem, ...]) -> dict[UUID, float]:
        with self._uow_factory() as uow:
            return {item.id: uow.interactions.adjustment(item.id) for item in batch}

    @staticmethod
    def _admissible(
        item: ContentItem,
        assessment: CandidateAssessment,
        score: float,
        topics: tuple[str, ...],
        policy: FeedPolicy,
        source_counts: Mapping[str, int],
        topic_counts: Mapping[str, int],
    ) -> bool:
        if score < policy.min_score or assessment.novelty < policy.min_novelty:
            return False
        if source_counts.get(item.source_id, 0) >= policy.max_per_source:
            return False
        return not topics or all(
            topic_counts.get(topic, 0) < policy.max_per_topic for topic in topics
        )

    @staticmethod
    def _admit(
        ranked: list[ContentItem],
        assessments: Mapping[UUID, CandidateAssessment],
        adjustments: Mapping[UUID, float],
        *,
        deficit: int,
        policy: FeedPolicy,
        semantic_novelty: Mapping[UUID, float],
        existing_diversity: Sequence[tuple[str, tuple[str, ...]]] = (),
    ) -> tuple[tuple[ContentItem, CandidateAssessment], ...]:
        admitted: list[tuple[ContentItem, CandidateAssessment]] = []
        source_counts, topic_counts = _diversity_counts(existing_diversity)
        for item in ranked:
            assessment = assessments[item.id]
            topics = tuple(dict.fromkeys(topic.casefold() for topic in assessment.topics if topic))
            score = (
                assessment.score + adjustments[item.id] + 0.1 * semantic_novelty.get(item.id, 0.0)
            )
            if not FeedService._admissible(
                item, assessment, score, topics, policy, source_counts, topic_counts
            ):
                continue
            admitted.append((item, assessment))
            source_counts[item.source_id] = source_counts.get(item.source_id, 0) + 1
            for topic in topics:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
            if len(admitted) == deficit:
                break
        return tuple(admitted)

    def _persist_admission(
        self,
        normalized: _NormalizedCandidates,
        assessments: Mapping[UUID, CandidateAssessment],
        admitted: tuple[tuple[ContentItem, CandidateAssessment], ...],
        explanations: Mapping[UUID, str],
        expected_profile_revision: int,
        transaction_guard: Callable[[object], None] | None,
    ) -> tuple[FeedEntry, ...]:
        entries: list[FeedEntry] = []
        with self._uow_factory() as uow:
            if transaction_guard is not None:
                transaction_guard(uow)
            latest_profile = uow.profiles.latest()
            if latest_profile is None or latest_profile.revision != expected_profile_revision:
                raise StaleFeedProfileRevisionError(
                    "profile changed while feed candidates were being assessed"
                )
            for item in normalized.new_content:
                uow.content.add(item)
            uow.content.flush()
            for assessment in assessments.values():
                uow.assessments.add(assessment)
            position = uow.feed.next_position()
            for index, (item, assessment) in enumerate(admitted):
                entry = FeedEntry(
                    content_id=item.id,
                    assessment_id=assessment.id,
                    position=position + index,
                    explanation=explanations[item.id],
                )
                uow.feed.add(entry)
                entries.append(entry)
            uow.commit()
        return tuple(entries)

    async def replenish(
        self,
        *,
        checkpoint: Callable[[float], None] | None = None,
        transaction_guard: Callable[[object], None] | None = None,
    ) -> tuple[FeedEntry, ...]:
        """Replenish against one profile, retrying one optimistic conflict by default."""

        for attempt in range(self._profile_conflict_retries + 1):
            try:
                return await self._replenish_once(
                    checkpoint=checkpoint,
                    transaction_guard=transaction_guard,
                )
            except StaleFeedProfileRevisionError:
                if attempt == self._profile_conflict_retries:
                    raise
        raise AssertionError("profile conflict retry loop exhausted without an outcome")

    async def _replenish_once(
        self,
        *,
        checkpoint: Callable[[float], None] | None,
        transaction_guard: Callable[[object], None] | None,
    ) -> tuple[FeedEntry, ...]:
        state = self._load_replenishment_state()
        profile = state.profile
        if profile is None:
            raise RuntimeError("feed replenishment requires a profile")
        settings = self._settings.get() if self._settings else None
        policy = self._effective_policy(settings)
        deficit = feed_deficit(state.current_unseen, policy.low_watermark, policy.high_watermark)
        if not deficit:
            return ()

        eligible = self._eligible_connectors(settings)
        queries = await self._plan_queries(profile, eligible)
        collection_bound = min(deficit * policy.candidate_multiplier, policy.max_batch_candidates)
        allocations = allocate_source_limits(
            collection_bound,
            tuple(connector.manifest.source_id.value for connector, _ in eligible),
            weights=(cast("Mapping[str, float]", settings.sources.weights) if settings else None),
        )
        candidates = await self._collect_candidates(
            profile, state, eligible, allocations, queries, checkpoint
        )
        if not candidates:
            return ()

        normalized = self._normalize_candidates(candidates)
        assessment_values = await self._assessor.assess_batch(profile, normalized.batch)
        _checkpoint(checkpoint, 0.7)
        assessments = _validate_assessments(profile, normalized.batch, assessment_values)
        adjustments = self._adjustments(normalized.batch)
        semantic_novelty = (
            await self._novelty_scorer.score(normalized.batch)
            if self._novelty_scorer is not None
            else {}
        )
        expected_semantic_ids = (
            {item.id for item in normalized.batch} if semantic_novelty else set()
        )
        if set(semantic_novelty) != expected_semantic_ids:
            raise ValueError("semantic novelty must cover the complete candidate batch")
        if any(
            not math.isfinite(value) or not 0 <= value <= 1 for value in semantic_novelty.values()
        ):
            raise ValueError("semantic novelty scores must be finite values between zero and one")

        # Threshold seeds were chosen conservatively for the first vNext offline corpus.
        # Re-run feed calibration after a source-normalization or assessment-model swap.
        ranked = sorted(
            normalized.batch,
            key=lambda item: (
                -(
                    assessments[item.id].score
                    + adjustments[item.id]
                    + 0.1 * semantic_novelty.get(item.id, 0.0)
                ),
                item.source_id,
                item.external_id,
            ),
        )
        admitted = self._admit(
            ranked,
            assessments,
            adjustments,
            deficit=deficit,
            policy=policy,
            semantic_novelty=semantic_novelty,
            existing_diversity=state.diversity_keys,
        )
        explanations = await self._explain_admitted(profile, admitted, checkpoint)
        _checkpoint(checkpoint, 0.85)
        return self._persist_admission(
            normalized,
            assessments,
            admitted,
            explanations,
            profile.revision,
            transaction_guard,
        )

    async def _plan_queries(
        self,
        profile: ProfileSnapshot,
        eligible: tuple[tuple[SourceConnector, SourceOperation], ...],
    ) -> dict[str, str]:
        requiring = tuple(
            connector
            for connector, operation in sorted(
                eligible, key=lambda pair: pair[0].manifest.source_id.value
            )
            if operation.requires_input
        )
        if not requiring or self._query_planner is None:
            return {}
        planned = tuple(
            value.strip()
            for value in await self._query_planner.plan(profile, limit=len(requiring))
            if value.strip()
        )
        if not planned:
            raise ValueError("discovery query planner returned no usable queries")
        return {
            connector.manifest.source_id.value: planned[index % len(planned)]
            for index, connector in enumerate(requiring)
        }

    async def _explain_admitted(
        self,
        profile: ProfileSnapshot,
        admitted: tuple[tuple[ContentItem, CandidateAssessment], ...],
        checkpoint: Callable[[float], None] | None,
    ) -> dict[UUID, str]:
        if self._explainer is None:
            return {item.id: assessment.explanation for item, assessment in admitted}
        explanations: dict[UUID, str] = {}
        count = len(admitted)
        for index, (item, assessment) in enumerate(admitted):
            _checkpoint(checkpoint, 0.72 + 0.12 * index / max(1, count))
            explanations[item.id] = await self._explainer.explain(profile, item, assessment)
            _checkpoint(checkpoint, 0.72 + 0.12 * (index + 1) / max(1, count))
        return explanations


class FeedbackService:
    """Persist interactions, projecting only explicit feedback into profile evidence."""

    def __init__(self, uow_factory: Callable[[], FeedUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def record(self, interaction: Interaction) -> ProfileSignal | None:
        sentiment = _PROFILE_FEEDBACK_SENTIMENT.get(interaction.kind)
        with self._uow_factory() as uow:
            content = uow.content.get(interaction.content_id)
            if content is None:
                raise LookupError("interaction content does not exist")
            uow.interactions.add(interaction)
            if sentiment is None:
                uow.commit()
                return None
            event = ActivityEvent(
                id=uuid5(_FEEDBACK_EVENT_NAMESPACE, str(interaction.id)),
                source_id="openbiliclaw",
                kind=ActivityKind.FEEDBACK,
                content_external_id=content.external_id,
                url=content.url,
                title=content.title,
                text=content.summary or None,
                metadata={
                    "interaction_id": str(interaction.id),
                    "sentiment": sentiment,
                    "value": _feedback_evidence_value(content),
                },
            )
            uow.activities.add(event)
            uow.commit()
        signals = project_activity_event(event)
        if not signals:
            raise RuntimeError("feedback event did not produce evidence")
        return signals[0]


def _feedback_evidence_value(content: ContentItem) -> str:
    """Build bounded semantic evidence from normalized content, never an opaque row ID."""

    values = [content.title, content.summary]
    for key in ("topic", "topics", "tag", "tags"):
        value = content.metadata.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, tuple):
            values.extend(item for item in value if isinstance(item, str))
    return " | ".join(value.strip() for value in values if value.strip())[:500]


__all__ = [
    "CandidateBatchAssessor",
    "FeedPolicy",
    "FeedService",
    "FeedbackService",
    "allocate_source_limits",
]
