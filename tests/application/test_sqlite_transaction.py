from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.application.record_feedback import RecordFeedback, RecordFeedbackCommand
from openbiliclaw.application.unit_of_work import FeedbackUnitOfWork, ProfileEditUnitOfWork
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.observations.models import (
    DeterministicProfileEditObservation,
    Observation,
    ProfileEditPayload,
    observation_adapter,
)
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.observations.repository import SqliteObservationRepository
from openbiliclaw.recommendation.models import FeedbackKind, FeedbackRecord
from openbiliclaw.recommendation.repositories import SqliteRecommendationRepository
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.repository import SqliteUnderstandingRepository

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2030, 1, 1, tzinfo=UTC)
REF = ContentRef(
    provider_id=ProviderId(value="demo"),
    content_kind=ContentKind(value="video"),
    provider_content_id="1",
    canonical_url="https://demo.example/1",
)


class SqliteFeedbackUow:
    """Test adapter proving the workflow port fits one real SQLite transaction."""

    def __init__(self, database: SqliteDatabase, *, fail_after_primary: bool = False) -> None:
        self.database = database
        self.fail_after_primary = fail_after_primary

    async def record_feedback(self, feedback: FeedbackRecord, observation: Observation) -> bool:
        async with self.database.transaction() as session:
            existing = await session.fetch_one(
                "SELECT 1 FROM recommendation_feedback WHERE feedback_id=?",
                (feedback.feedback_id,),
            )
            if existing is not None:
                return False
            await session.execute(
                "INSERT INTO recommendation_feedback(feedback_id,record_json,created_at) "
                "VALUES(?,?,?)",
                (
                    feedback.feedback_id,
                    feedback.model_dump_json(),
                    feedback.occurred_at.isoformat(),
                ),
            )
            if self.fail_after_primary:
                raise RuntimeError("injected failure")
            ref = observation.content_ref
            assert ref is not None
            await session.execute(
                "INSERT OR IGNORE INTO content_references(provider,external_id,kind,canonical_url) "
                "VALUES(?,?,?,?)",
                (
                    ref.provider_id.value,
                    ref.provider_content_id,
                    ref.content_kind.value,
                    ref.canonical_url,
                ),
            )
            row = await session.fetch_one(
                "SELECT content_id FROM content_references WHERE provider=? AND external_id=?",
                (ref.provider_id.value, ref.provider_content_id),
            )
            assert row is not None and isinstance(row[0], int)
            payload = observation_adapter.dump_python(observation, mode="json")
            await session.execute(
                "INSERT INTO observations(observation_id,content_id,kind,occurred_at,strength,"
                "producer,idempotency_key,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    observation.observation_id,
                    row[0],
                    observation.event_type,
                    observation.occurred_at.isoformat(),
                    1.0,
                    observation.provenance.producer_id,
                    observation.idempotency_key,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                ),
            )
        return True


async def test_feedback_sequence_rolls_back_together_and_is_restart_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    command = RecordFeedbackCommand(
        idempotency_key="feedback:transaction:1",
        shown_id="shown_" + "1" * 32,
        content_ref=REF,
        kind=FeedbackKind.LIKED,
        account_id="acct",
    )
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            await RecordFeedback(
                SqliteFeedbackUow(database, fail_after_primary=True), clock=lambda: NOW
            )(command)
        assert await database.fetch_value("SELECT COUNT(*) FROM recommendation_feedback") == 0
        assert await database.fetch_value("SELECT COUNT(*) FROM observations") == 0

        first = await RecordFeedback(SqliteFeedbackUow(database), clock=lambda: NOW)(command)
        assert first.inserted
        await database.close()

        restarted = SqliteDatabase(path)
        await restarted.open()
        try:
            duplicate = await RecordFeedback(SqliteFeedbackUow(restarted), clock=lambda: NOW)(
                command
            )
            assert not duplicate.inserted
            assert await restarted.fetch_value("SELECT COUNT(*) FROM recommendation_feedback") == 1
            assert await restarted.fetch_value("SELECT COUNT(*) FROM observations") == 1
        finally:
            await restarted.close()
    finally:
        if not database.closed:
            await database.close()


async def test_production_units_of_work_commit_feedback_and_profile_edit(tmp_path: Path) -> None:
    path = tmp_path / "production-uow.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    observations = SqliteObservationRepository(database)
    recommendations = SqliteRecommendationRepository(database)
    understanding = SqliteUnderstandingRepository(database)
    command = RecordFeedbackCommand(
        idempotency_key="feedback:uow:production",
        shown_id="shown_" + "2" * 32,
        content_ref=REF,
        kind=FeedbackKind.LIKED,
        account_id="acct",
    )
    assert (
        await RecordFeedback(FeedbackUnitOfWork(recommendations, observations), clock=lambda: NOW)(
            command
        )
    ).inserted
    provenance = ObservationProvenance(
        producer_id="application.profile-edit",
        source=ObservationSource.PROFILE_EDITOR,
        authenticated=True,
        trust_level=TrustLevel.HIGH,
    )
    edit = DeterministicProfileEditObservation(
        observation_id="obs_" + "a" * 32,
        idempotency_key="profile:uow:production",
        occurred_at=NOW,
        received_at=NOW,
        account_id="acct",
        provenance=provenance,
        payload=ProfileEditPayload(field="claim", operation="set", value="science"),
    )
    profile = await ProfileEditUnitOfWork(understanding, observations).edit_profile(
        "default",
        claim_id="claim_" + "b" * 32,
        operation=OverrideOperation.SET,
        value="science",
        observation=edit,
    )
    assert profile.revision == 1
    assert len((await observations.read(after_cursor=None, limit=10)).items) == 2
    await database.close()
