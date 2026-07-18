"""Feedback evidence must influence admission of later, distinct candidates."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config

from openbiliclaw.features.feed.domain import (
    CandidateAssessment,
    ContentItem,
    FeedEntry,
    Interaction,
    InteractionKind,
)
from openbiliclaw.features.feed.service import FeedbackService, FeedPolicy, FeedService
from openbiliclaw.features.profile.domain import ProfileSnapshot
from openbiliclaw.features.profile.service import ProfileService
from openbiliclaw.features.sources.domain import (
    SourceCapability,
    SourceId,
    SourceManifest,
    SourceOperation,
    SourceOperationSpec,
    SourceResultKind,
    SourceTransportKind,
)
from openbiliclaw.features.system.domain import DatabaseSettings
from openbiliclaw.infrastructure.database.base import create_engine_and_session
from openbiliclaw.infrastructure.database.uow import UnitOfWork

ROOT = Path(__file__).parents[2]
PROFILE_ID = UUID("00000000-0000-0000-0000-00000000e301")
SEED_ID = UUID("00000000-0000-0000-0000-00000000e302")
SEED_ASSESSMENT_ID = UUID("00000000-0000-0000-0000-00000000e303")
RELATED_ID = UUID("00000000-0000-0000-0000-00000000e304")
ALTERNATIVE_ID = UUID("00000000-0000-0000-0000-00000000e305")


class _Connector:
    manifest = SourceManifest(
        source_id=SourceId.ZHIHU,
        display_name="Zhihu",
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

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> tuple[ContentItem, ...]:
        assert operation is SourceOperation.SEARCH
        assert query
        return (
            ContentItem(
                id=RELATED_ID,
                source_id="zhihu",
                external_id="related-new",
                url="https://www.zhihu.com/question/related-new",
                title="Graph database scaling patterns",
            ),
            ContentItem(
                id=ALTERNATIVE_ID,
                source_id="zhihu",
                external_id="alternative-new",
                url="https://www.zhihu.com/question/alternative-new",
                title="Typed API boundary design",
            ),
        )[:limit]


class _AvoidanceAwareAssessor:
    async def assess_batch(
        self, profile: ProfileSnapshot, content: tuple[ContentItem, ...]
    ) -> tuple[CandidateAssessment, ...]:
        avoidances = " ".join(
            facet.value.casefold() for facet in profile.facets if facet.name == "avoidances"
        )
        assert "graph database internals" in avoidances
        return tuple(
            CandidateAssessment(
                content_id=item.id,
                profile_revision=profile.revision,
                relevance=0.95 if item.id == RELATED_ID else 0.8,
                quality=0.9,
                novelty=0.8,
                risk=0.5 if "graph database" in item.title.casefold() else 0.0,
                topics=("databases",) if item.id == RELATED_ID else ("apis",),
            )
            for item in content
        )


async def test_negative_feedback_projects_content_semantics_and_changes_later_ranking(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'later-ranking.db'}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=database_url))

    def uow_factory() -> UnitOfWork:
        return UnitOfWork(session_factory)

    seed = ContentItem(
        id=SEED_ID,
        source_id="zhihu",
        external_id="seed",
        url="https://www.zhihu.com/question/seed",
        title="Graph database internals",
        summary="Storage engines and graph traversal",
    )
    with uow_factory() as uow:
        uow.profiles.append(
            ProfileSnapshot(id=PROFILE_ID, revision=0, narrative="Backend systems"),
            expected_revision=None,
        )
        uow.content.add(seed)
        uow.content.flush()
        uow.assessments.add(
            CandidateAssessment(
                id=SEED_ASSESSMENT_ID,
                content_id=seed.id,
                profile_revision=0,
                relevance=0.95,
                quality=0.9,
                novelty=0.8,
                risk=0,
            )
        )
        uow.feed.add(FeedEntry(content_id=seed.id, assessment_id=SEED_ASSESSMENT_ID, position=0))
        uow.commit()

    feedback = FeedbackService(uow_factory)  # type: ignore[arg-type]
    signal = feedback.record(Interaction(content_id=seed.id, kind=InteractionKind.DISMISS))
    assert signal.facet == "avoidances"
    assert signal.value == "Graph database internals | Storage engines and graph traversal"
    profile = await ProfileService(uow_factory).project((signal,))  # type: ignore[arg-type]
    assert profile.revision == 1

    entries = await FeedService(
        uow_factory,  # type: ignore[arg-type]
        connectors=(_Connector(),),
        assessor=_AvoidanceAwareAssessor(),
        policy=FeedPolicy(low_watermark=1, high_watermark=2, max_per_source=2),
    ).replenish()

    assert [entry.content_id for entry in entries] == [ALTERNATIVE_ID, RELATED_ID]
    engine.dispose()
