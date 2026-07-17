"""Use-case tests for the vNext evidence, feed, library, and chat slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Thread
from typing import Any
from uuid import UUID

import anyio
import pytest

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.activity.service import ActivityService, project_activity_event
from openbiliclaw.features.chat.service import ChatChunkKind, ChatResponseDelta, ChatService
from openbiliclaw.features.feed.domain import (
    CandidateAssessment,
    ContentItem,
    FeedEntry,
    Interaction,
    InteractionKind,
)
from openbiliclaw.features.feed.service import (
    FeedbackService,
    FeedPolicy,
    FeedService,
    StaleFeedProfileRevisionError,
    allocate_source_limits,
)
from openbiliclaw.features.library.domain import CollectionKind
from openbiliclaw.features.library.service import LibraryService
from openbiliclaw.features.profile.domain import ProfileDelta, ProfileFacet, ProfileSnapshot
from openbiliclaw.features.profile.service import (
    InvalidProfileDeltaError,
    ProfileService,
    StaleProfileRevisionError,
)
from openbiliclaw.features.sources.domain import (
    SourceCapability,
    SourceId,
    SourceManifest,
    SourceOperation,
    SourceOperationSpec,
    SourceResultKind,
    SourceTransportKind,
)

PROFILE_ID = UUID("00000000-0000-0000-0000-000000000020")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000021")
CONTENT_A = UUID("00000000-0000-0000-0000-000000000022")
CONTENT_B = UUID("00000000-0000-0000-0000-000000000023")
CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000024")


@dataclass
class MemoryState:
    events: list[ActivityEvent] = field(default_factory=list)
    profiles: list[ProfileSnapshot] = field(default_factory=list)
    content: list[ContentItem] = field(default_factory=list)
    assessments: list[CandidateAssessment] = field(default_factory=list)
    feed: list[Any] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    collections: list[Any] = field(default_factory=list)
    turns: list[Any] = field(default_factory=list)
    consumed_evidence: set[UUID] = field(default_factory=set)


class MemoryRepository:
    def __init__(self, state: MemoryState) -> None:
        self.state = state

    def add(self, value: Any) -> Any:
        name = type(value).__name__
        target = {
            "ActivityEvent": self.state.events,
            "ProfileSnapshot": self.state.profiles,
            "ContentItem": self.state.content,
            "CandidateAssessment": self.state.assessments,
            "FeedEntry": self.state.feed,
            "Interaction": self.state.interactions,
            "CollectionItem": self.state.collections,
            "ChatTurn": self.state.turns,
        }[name]
        target.append(value)
        return value

    def add_if_absent(self, value: ActivityEvent) -> bool:
        if any(event.id == value.id for event in self.state.events):
            return False
        self.state.events.append(value)
        return True

    def get_activity(self, event_id: UUID) -> ActivityEvent | None:
        return next((event for event in self.state.events if event.id == event_id), None)

    def list_recent_by_conversation(self, conversation_id: UUID, *, limit: int) -> tuple[Any, ...]:
        matches = [turn for turn in self.state.turns if turn.conversation_id == conversation_id]
        return tuple(matches[-limit:])

    def latest(self) -> ProfileSnapshot | None:
        return self.state.profiles[-1] if self.state.profiles else None

    def append(self, snapshot: ProfileSnapshot, expected_revision: int | None) -> None:
        actual = None if not self.state.profiles else self.state.profiles[-1].revision
        if actual != expected_revision:
            raise RuntimeError("revision conflict")
        self.state.profiles.append(snapshot)

    def consumed_evidence_ids(self) -> frozenset[UUID]:
        return frozenset(self.state.consumed_evidence)

    def mark_evidence_consumed(
        self, evidence_ids: frozenset[UUID], *, profile_revision: int
    ) -> None:
        del profile_revision
        self.state.consumed_evidence.update(evidence_ids)

    def get_by_identity(self, source_id: str, external_id: str) -> ContentItem | None:
        return next(
            (
                item
                for item in self.state.content
                if item.source_id == source_id and item.external_id == external_id
            ),
            None,
        )

    def get(self, content_id: UUID) -> ContentItem | None:
        return next((item for item in self.state.content if item.id == content_id), None)

    def flush(self) -> None:
        return None

    def unseen_count(self) -> int:
        seen = {item.content_id for item in self.state.interactions}
        return sum(entry.content_id not in seen for entry in self.state.feed)

    def unseen_diversity_keys(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        seen = {item.content_id for item in self.state.interactions}
        content_by_id = {item.id: item for item in self.state.content}
        assessment_by_id = {item.id: item for item in self.state.assessments}
        result: list[tuple[str, tuple[str, ...]]] = []
        for entry in self.state.feed:
            if entry.content_id in seen:
                continue
            content = content_by_id[entry.content_id]
            assessment = assessment_by_id.get(entry.assessment_id) if entry.assessment_id else None
            result.append((content.source_id, assessment.topics if assessment else ()))
        return tuple(result)

    def next_position(self) -> int:
        return len(self.state.feed)

    def adjustment(self, content_id: UUID) -> float:
        adjustment = 0.0
        for interaction in self.state.interactions:
            if interaction.content_id == content_id:
                adjustment += 0.2 if interaction.kind is InteractionKind.POSITIVE else 0.0
                adjustment -= 0.5 if interaction.kind is InteractionKind.NEGATIVE else 0.0
        return adjustment

    def excluded_content_ids(self, profile_revision: int) -> frozenset[UUID]:
        assessed = {
            assessment.content_id
            for assessment in self.state.assessments
            if assessment.profile_revision == profile_revision
        }
        admitted = {entry.content_id for entry in self.state.feed}
        interacted = {interaction.content_id for interaction in self.state.interactions}
        return frozenset(assessed | admitted | interacted)

    def excluded_content_identities(self, profile_revision: int) -> frozenset[tuple[str, str]]:
        excluded = self.excluded_content_ids(profile_revision)
        return frozenset(
            (item.source_id, item.external_id) for item in self.state.content if item.id in excluded
        )

    def remove(self, collection: CollectionKind, content_id: UUID) -> bool:
        before = len(self.state.collections)
        self.state.collections[:] = [
            item
            for item in self.state.collections
            if not (item.collection is collection and item.content_id == content_id)
        ]
        return len(self.state.collections) != before


class MemoryUow:
    def __init__(self, state: MemoryState) -> None:
        repo = MemoryRepository(state)
        self.activities = repo
        self.profiles = repo
        self.content = repo
        self.assessments = repo
        self.feed = repo
        self.interactions = repo
        self.collections = repo
        self.chat = repo
        self.commits = 0

    def __enter__(self) -> MemoryUow:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1


class BatchAssessor:
    def __init__(self) -> None:
        self.calls: list[tuple[ContentItem, ...]] = []

    async def assess_batch(
        self, profile: ProfileSnapshot, content: tuple[ContentItem, ...]
    ) -> tuple[CandidateAssessment, ...]:
        self.calls.append(content)
        return tuple(
            CandidateAssessment(
                content_id=item.id,
                profile_revision=profile.revision,
                relevance=0.9,
                quality=0.9,
                novelty=0.9,
                risk=0,
                topics=("python" if index < 2 else "architecture",),
            )
            for index, item in enumerate(content)
        )


class TopicAssessor:
    async def assess_batch(
        self, profile: ProfileSnapshot, content: tuple[ContentItem, ...]
    ) -> tuple[CandidateAssessment, ...]:
        topics = (("python",), ("python", "new"), ("other",))
        return tuple(
            CandidateAssessment(
                content_id=item.id,
                profile_revision=profile.revision,
                relevance=0.9,
                quality=0.9,
                novelty=0.9,
                risk=0,
                topics=topics[index],
            )
            for index, item in enumerate(content)
        )


class Connector:
    def __init__(self, source: SourceId, items: tuple[ContentItem, ...]) -> None:
        self._items = items
        self.calls: list[tuple[SourceOperation, str | None, int]] = []
        self.manifest = SourceManifest(
            source_id=source,
            display_name=source.value,
            capabilities=frozenset({SourceCapability.TRENDING_FEED}),
            operations=(
                SourceOperationSpec(
                    operation=SourceOperation.TRENDING,
                    capability=SourceCapability.TRENDING_FEED,
                    result_kind=SourceResultKind.CONTENT,
                    requires_auth=False,
                    transport_kind=SourceTransportKind.DIRECT,
                ),
            ),
        )

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> tuple[ContentItem, ...]:
        self.calls.append((operation, query, limit))
        return self._items[:limit]


def test_ingestion_persists_event_and_projects_deterministic_evidence() -> None:
    state = MemoryState()
    event = ActivityEvent(
        id=EVENT_ID,
        source_id="bilibili",
        kind=ActivityKind.FAVORITE,
        title="Typed Python architecture",
    )

    signals = ActivityService(lambda: MemoryUow(state)).ingest(event)

    assert state.events == [event]
    assert signals == project_activity_event(event)
    assert signals[0].evidence_ids == (EVENT_ID,)


def test_activity_projection_bounds_long_valid_event_values_without_rejecting_event() -> None:
    event = ActivityEvent(
        source_id="bilibili",
        kind=ActivityKind.VIEW,
        title="界" * 1_000,
    )

    signal = project_activity_event(event)[0]

    assert signal.value == "界" * 500


@pytest.mark.asyncio
async def test_profile_delta_is_validated_and_appended_atomically() -> None:
    state = MemoryState(events=[ActivityEvent(id=EVENT_ID, source_id="local", kind="feedback")])
    service = ProfileService(lambda: MemoryUow(state))
    delta = ProfileDelta(
        upserts=(
            ProfileFacet(
                name="interests",
                value="Python",
                weight=0.8,
                confidence=0.9,
                evidence_ids=(EVENT_ID,),
            ),
        )
    )

    snapshot = service.apply_delta(delta, evidence_ids=frozenset({EVENT_ID}))

    assert snapshot.revision == 0
    assert state.profiles == [snapshot]
    bad = delta.model_copy(update={"upserts": (delta.upserts[0], delta.upserts[0].model_copy())})
    with pytest.raises(InvalidProfileDeltaError, match="duplicate"):
        service.apply_delta(bad, evidence_ids=frozenset({EVENT_ID}))


@pytest.mark.asyncio
async def test_profile_projection_marks_narrative_only_evidence_consumed() -> None:
    state = MemoryState()

    class NarrativeAI:
        async def propose(self, profile: ProfileSnapshot, signals: tuple[Any, ...]) -> ProfileDelta:
            del profile, signals
            return ProfileDelta(narrative="A stable narrative without facets")

    signal = project_activity_event(
        ActivityEvent(
            id=EVENT_ID,
            source_id="local",
            kind=ActivityKind.CHAT_LEARNING,
            text="architecture",
        )
    )[0]

    await ProfileService(lambda: MemoryUow(state), ai=NarrativeAI()).project((signal,))

    assert state.consumed_evidence == {EVENT_ID}


@pytest.mark.asyncio
async def test_profile_projection_rejects_delta_when_base_revision_changes_during_ai() -> None:
    original = ProfileSnapshot(id=PROFILE_ID, revision=0)
    state = MemoryState(profiles=[original])

    class RacingAI:
        async def propose(self, profile: ProfileSnapshot, signals: tuple[Any, ...]) -> ProfileDelta:
            del profile, signals
            state.profiles.append(original.model_copy(update={"revision": 1}))
            return ProfileDelta(narrative="stale proposal")

    signal = project_activity_event(
        ActivityEvent(
            id=EVENT_ID,
            source_id="local",
            kind=ActivityKind.CHAT_LEARNING,
            text="architecture",
        )
    )[0]

    with pytest.raises(StaleProfileRevisionError):
        await ProfileService(lambda: MemoryUow(state), ai=RacingAI()).project((signal,))
    assert [profile.revision for profile in state.profiles] == [0, 1]


def test_source_allocation_is_deterministic_and_exact() -> None:
    assert allocate_source_limits(5, ("youtube", "bilibili", "reddit")) == {
        "bilibili": 2,
        "reddit": 2,
        "youtube": 1,
    }
    assert allocate_source_limits(
        7,
        ("youtube", "bilibili", "reddit"),
        weights={"bilibili": 3.0, "reddit": 1.0, "youtube": 0.0},
    ) == {"bilibili": 5, "reddit": 2}
    assert (
        sum(
            allocate_source_limits(
                11,
                ("youtube", "bilibili", "reddit"),
                weights={"bilibili": 0.5, "reddit": 1.5, "youtube": 2.0},
            ).values()
        )
        == 11
    )
    with pytest.raises(ValueError, match="finite and non-negative"):
        allocate_source_limits(3, ("bilibili",), weights={"bilibili": -1.0})


@pytest.mark.asyncio
async def test_feed_replenishment_dedupes_batches_and_enforces_diversity() -> None:
    state = MemoryState(profiles=[ProfileSnapshot(id=PROFILE_ID, revision=0, narrative="Python")])
    duplicate = ContentItem(
        id=CONTENT_A,
        source_id="bilibili",
        external_id="same",
        url="https://example.com/a",
        title="A",
    )
    second = ContentItem(
        id=CONTENT_B,
        source_id="bilibili",
        external_id="second",
        url="https://example.com/b",
        title="B",
    )
    third = ContentItem(
        source_id="youtube",
        external_id="third",
        url="https://example.com/c",
        title="C",
    )
    assessor = BatchAssessor()
    service = FeedService(
        lambda: MemoryUow(state),
        connectors=(
            Connector(SourceId.BILIBILI, (duplicate, second)),
            Connector(SourceId.YOUTUBE, (duplicate.model_copy(), third)),
        ),
        assessor=assessor,
        policy=FeedPolicy(low_watermark=1, high_watermark=2, max_per_topic=1),
    )

    entries = await service.replenish()

    assert len(entries) == 2
    assert len(assessor.calls) == 1  # typed batch, never candidate-by-candidate N+1
    assert len({(item.source_id, item.external_id) for item in state.content}) == len(state.content)
    assert [entry.position for entry in entries] == [0, 1]


@pytest.mark.asyncio
async def test_feed_uses_planned_search_query_semantic_novelty_and_generated_explanation() -> None:
    profile = ProfileSnapshot(id=PROFILE_ID, revision=1, narrative="fallback must not escape")
    item = ContentItem(
        source_id="bilibili",
        external_id="planned",
        url="https://example.com/planned",
        title="Planned content",
    )

    class SearchConnector(Connector):
        def __init__(self) -> None:
            super().__init__(SourceId.BILIBILI, (item,))
            self.manifest = SourceManifest(
                source_id=SourceId.BILIBILI,
                display_name="Bilibili",
                capabilities=frozenset({SourceCapability.SEARCH}),
                operations=(
                    SourceOperationSpec(
                        operation=SourceOperation.SEARCH,
                        capability=SourceCapability.SEARCH,
                        result_kind=SourceResultKind.CONTENT,
                        requires_auth=False,
                        transport_kind=SourceTransportKind.DIRECT,
                    ),
                ),
            )
            self.queries: list[str | None] = []

        async def execute(
            self, operation: SourceOperation, query: str | None = None, limit: int = 20
        ) -> tuple[ContentItem, ...]:
            assert operation is SourceOperation.SEARCH
            self.queries.append(query)
            return (item,)

    class Planner:
        async def plan(self, current: ProfileSnapshot, *, limit: int) -> tuple[str, ...]:
            assert current.revision == 1
            assert limit == 1
            return ("AI planned query",)

    class Explainer:
        async def explain(
            self,
            current: ProfileSnapshot,
            content: ContentItem,
            assessment: CandidateAssessment,
        ) -> str:
            assert current.revision == assessment.profile_revision
            return f"Because {content.title} matches your profile"

    class Novelty:
        async def score(self, content: tuple[ContentItem, ...]) -> dict[UUID, float]:
            return {candidate.id: 1.0 for candidate in content}

    connector = SearchConnector()
    state = MemoryState(profiles=[profile])
    entries = await FeedService(
        lambda: MemoryUow(state),
        connectors=(connector,),
        assessor=BatchAssessor(),
        query_planner=Planner(),
        explainer=Explainer(),
        novelty_scorer=Novelty(),
        policy=FeedPolicy(low_watermark=1, high_watermark=1),
    ).replenish()

    assert connector.queries == ["AI planned query"]
    assert entries[0].explanation == "Because Planned content matches your profile"


@pytest.mark.asyncio
async def test_feed_checks_cancellation_between_admitted_explanations() -> None:
    profile = ProfileSnapshot(id=PROFILE_ID, revision=0, narrative="Python")
    items = tuple(
        ContentItem(
            source_id="bilibili",
            external_id=f"explain-{index}",
            url=f"https://example.com/explain/{index}",
            title=f"Explanation {index}",
        )
        for index in range(2)
    )
    calls: list[UUID] = []

    class Explainer:
        async def explain(
            self,
            current: ProfileSnapshot,
            content: ContentItem,
            assessment: CandidateAssessment,
        ) -> str:
            assert current.revision == assessment.profile_revision
            calls.append(content.id)
            return content.title

    def cancel_after_first(progress: float) -> None:
        if progress >= 0.78:
            raise RuntimeError("cancelled between explanations")

    state = MemoryState(profiles=[profile])
    service = FeedService(
        lambda: MemoryUow(state),
        connectors=(Connector(SourceId.BILIBILI, items),),
        assessor=BatchAssessor(),
        explainer=Explainer(),
        policy=FeedPolicy(low_watermark=1, high_watermark=2),
    )

    with pytest.raises(RuntimeError, match="cancelled between explanations"):
        await service.replenish(checkpoint=cancel_after_first)

    assert calls == [items[0].id]
    assert state.feed == []


@pytest.mark.asyncio
async def test_feed_rejects_multi_topic_candidate_when_any_topic_is_saturated() -> None:
    state = MemoryState(profiles=[ProfileSnapshot(id=PROFILE_ID, revision=0)])
    items = tuple(
        ContentItem(
            source_id="bilibili",
            external_id=f"topic-{index}",
            url=f"https://example.com/topic/{index}",
            title=f"Topic {index}",
        )
        for index in range(3)
    )
    service = FeedService(
        lambda: MemoryUow(state),
        connectors=(Connector(SourceId.BILIBILI, items),),
        assessor=TopicAssessor(),
        policy=FeedPolicy(
            low_watermark=1,
            high_watermark=2,
            max_per_source=3,
            max_per_topic=1,
        ),
    )

    entries = await service.replenish()

    assert [entry.content_id for entry in entries] == [items[0].id, items[2].id]


@pytest.mark.asyncio
async def test_feed_diversity_includes_existing_unseen_entries() -> None:
    profile = ProfileSnapshot(id=PROFILE_ID, revision=0)
    existing = ContentItem(
        source_id="bilibili",
        external_id="existing",
        url="https://example.com/existing",
        title="Existing",
    )
    existing_assessment = CandidateAssessment(
        content_id=existing.id,
        profile_revision=0,
        relevance=1,
        quality=1,
        novelty=1,
        risk=0,
        topics=("saturated",),
    )
    existing_entry = FeedEntry(
        content_id=existing.id,
        assessment_id=existing_assessment.id,
        position=0,
    )
    blocked_by_source = ContentItem(
        source_id="bilibili",
        external_id="same-source",
        url="https://example.com/same-source",
        title="Same source",
    )
    blocked_by_topic = ContentItem(
        source_id="youtube",
        external_id="same-topic",
        url="https://example.com/same-topic",
        title="Same topic",
    )
    admitted = ContentItem(
        source_id="youtube",
        external_id="fresh",
        url="https://example.com/fresh",
        title="Fresh",
    )
    state = MemoryState(
        profiles=[profile],
        content=[existing],
        assessments=[existing_assessment],
        feed=[existing_entry],
    )

    class DiversityAssessor:
        async def assess_batch(
            self, candidate_profile: ProfileSnapshot, content: tuple[ContentItem, ...]
        ) -> tuple[CandidateAssessment, ...]:
            return tuple(
                CandidateAssessment(
                    content_id=item.id,
                    profile_revision=candidate_profile.revision,
                    relevance=1,
                    quality=1,
                    novelty=1,
                    risk=0,
                    topics=("saturated",) if item.external_id == "same-topic" else ("fresh",),
                )
                for item in content
            )

    service = FeedService(
        lambda: MemoryUow(state),
        connectors=(
            Connector(SourceId.BILIBILI, (blocked_by_source,)),
            Connector(SourceId.YOUTUBE, (blocked_by_topic, admitted)),
        ),
        assessor=DiversityAssessor(),
        policy=FeedPolicy(
            low_watermark=2,
            high_watermark=3,
            max_per_source=1,
            max_per_topic=1,
        ),
    )

    entries = await service.replenish()

    assert [entry.content_id for entry in entries] == [admitted.id]


@pytest.mark.asyncio
async def test_feed_retries_once_when_profile_changes_before_admission() -> None:
    original = ProfileSnapshot(id=PROFILE_ID, revision=0)
    state = MemoryState(profiles=[original])
    item = ContentItem(
        source_id="bilibili",
        external_id="retry",
        url="https://example.com/retry",
        title="Retry",
    )

    class RacingAssessor(BatchAssessor):
        async def assess_batch(
            self, profile: ProfileSnapshot, content: tuple[ContentItem, ...]
        ) -> tuple[CandidateAssessment, ...]:
            result = await super().assess_batch(profile, content)
            if len(self.calls) == 1:
                state.profiles.append(original.model_copy(update={"revision": 1}))
            return result

    assessor = RacingAssessor()
    service = FeedService(
        lambda: MemoryUow(state),
        connectors=(Connector(SourceId.BILIBILI, (item,)),),
        assessor=assessor,
        policy=FeedPolicy(low_watermark=1, high_watermark=1),
    )

    entries = await service.replenish()

    assert len(assessor.calls) == 2
    assert len(entries) == 1
    assert len(state.assessments) == 1
    assert state.assessments[0].profile_revision == 1


@pytest.mark.asyncio
async def test_feed_reports_conflict_after_bounded_profile_revision_retries() -> None:
    original = ProfileSnapshot(id=PROFILE_ID, revision=0)
    state = MemoryState(profiles=[original])
    item = ContentItem(
        source_id="bilibili",
        external_id="always-racing",
        url="https://example.com/always-racing",
        title="Always racing",
    )

    class AlwaysRacingAssessor(BatchAssessor):
        async def assess_batch(
            self, profile: ProfileSnapshot, content: tuple[ContentItem, ...]
        ) -> tuple[CandidateAssessment, ...]:
            result = await super().assess_batch(profile, content)
            state.profiles.append(
                original.model_copy(update={"revision": state.profiles[-1].revision + 1})
            )
            return result

    assessor = AlwaysRacingAssessor()
    service = FeedService(
        lambda: MemoryUow(state),
        connectors=(Connector(SourceId.BILIBILI, (item,)),),
        assessor=assessor,
        policy=FeedPolicy(low_watermark=1, high_watermark=1),
    )

    with pytest.raises(StaleFeedProfileRevisionError):
        await service.replenish()

    assert len(assessor.calls) == 2
    assert state.assessments == []
    assert state.feed == []


def test_feedback_persists_interaction_and_activity_that_changes_later_rank() -> None:
    content = ContentItem(
        id=CONTENT_A,
        source_id="bilibili",
        external_id="feedback-content",
        url="https://example.com/feedback-content",
        title="Graph database internals",
        summary="Storage engines and traversal",
    )
    state = MemoryState(content=[content])
    interaction = Interaction(content_id=CONTENT_A, kind=InteractionKind.NEGATIVE)

    signal = FeedbackService(lambda: MemoryUow(state)).record(interaction)

    assert state.interactions == [interaction]
    assert len(state.events) == 1
    assert signal.evidence_ids == (state.events[0].id,)
    assert signal.value == "Graph database internals | Storage engines and traversal"
    assert MemoryRepository(state).adjustment(CONTENT_A) < 0


@pytest.mark.parametrize(
    "kind",
    (
        InteractionKind.IMPRESSION,
        InteractionKind.OPEN,
        InteractionKind.SAVE_FAVORITE,
        InteractionKind.SAVE_WATCH_LATER,
    ),
)
def test_non_feedback_interaction_persists_without_profile_evidence(
    kind: InteractionKind,
) -> None:
    content = ContentItem(
        id=CONTENT_A,
        source_id="bilibili",
        external_id="passive-content",
        url="https://example.com/passive-content",
        title="A viewed item is not a preference",
    )
    state = MemoryState(content=[content])
    interaction = Interaction(content_id=CONTENT_A, kind=kind)

    signal = FeedbackService(lambda: MemoryUow(state)).record(interaction)

    assert signal is None
    assert state.interactions == [interaction]
    assert state.events == []


@pytest.mark.parametrize(
    "kind",
    (InteractionKind.POSITIVE, InteractionKind.NEGATIVE, InteractionKind.DISMISS),
)
def test_explicit_feedback_persists_profile_evidence(kind: InteractionKind) -> None:
    content = ContentItem(
        id=CONTENT_A,
        source_id="bilibili",
        external_id="explicit-feedback",
        url="https://example.com/explicit-feedback",
        title="Explicit preference",
    )
    state = MemoryState(content=[content])

    signal = FeedbackService(lambda: MemoryUow(state)).record(
        Interaction(content_id=CONTENT_A, kind=kind)
    )

    assert signal is not None
    assert signal.evidence_ids == (state.events[0].id,)
    assert len(state.interactions) == len(state.events) == 1


def test_library_mutation_is_local_and_limited_to_two_collections() -> None:
    state = MemoryState()
    service = LibraryService(lambda: MemoryUow(state))

    item = service.save(CollectionKind.FAVORITES, CONTENT_A, note="keep")

    assert item.collection is CollectionKind.FAVORITES
    assert service.remove(CollectionKind.FAVORITES, CONTENT_A) is True
    assert state.collections == []


class ChatResponder:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(
        self,
        *,
        conversation_id: UUID,
        message: str,
        history: tuple[Any, ...],
    ):
        del conversation_id, history
        self.calls += 1
        yield ChatResponseDelta(content=f"Echo: {message}", ai_run_id=CONVERSATION_ID)


@pytest.mark.asyncio
async def test_chat_persists_both_turns_and_emits_sse_compatible_chunks() -> None:
    state = MemoryState()
    responder = ChatResponder()
    service = ChatService(lambda: MemoryUow(state), responder=responder)

    chunks = [
        chunk
        async for chunk in service.stream(
            conversation_id=CONVERSATION_ID,
            message="I enjoy maintainable systems",
            learn=True,
        )
    ]

    assert [turn.role.value for turn in state.turns] == ["user", "assistant"]
    assert responder.calls == 1
    assert [chunk.kind for chunk in chunks] == [ChatChunkKind.DELTA, ChatChunkKind.DONE]
    assert all(chunk.to_sse().endswith("\n\n") for chunk in chunks)
    assert state.events[-1].kind is ActivityKind.CHAT_LEARNING


@pytest.mark.asyncio
async def test_chat_sync_persistence_does_not_block_the_event_loop() -> None:
    state = MemoryState()
    responder = ChatResponder()
    started = Event()
    progressed = Event()
    release = Event()
    observed: list[bool] = []
    first_enter = True

    class BlockingMemoryUow(MemoryUow):
        def __enter__(self) -> BlockingMemoryUow:
            nonlocal first_enter
            if first_enter:
                first_enter = False
                started.set()
                assert release.wait(2)
            return super().__enter__()

    service = ChatService(lambda: BlockingMemoryUow(state), responder=responder)

    def watch() -> None:
        assert started.wait(1)
        observed.append(progressed.wait(0.5))
        release.set()

    watcher = Thread(target=watch, daemon=True)
    watcher.start()

    async def consume() -> None:
        chunks = [
            chunk
            async for chunk in service.stream(
                conversation_id=CONVERSATION_ID,
                message="keep the loop responsive",
            )
        ]
        assert chunks

    async def mark_progress() -> None:
        while not started.is_set():
            await anyio.sleep(0)
        progressed.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume)
        tasks.start_soon(mark_progress)

    watcher.join(timeout=1)
    assert observed == [True]
