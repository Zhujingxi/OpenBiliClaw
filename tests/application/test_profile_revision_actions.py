from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.ai.providers.embeddings import EmbeddingModelInfo, EmbeddingResult, EmbeddingUsage
from openbiliclaw.ai.providers.embeddings.index import EmbeddingIndex
from openbiliclaw.application.content_actions import (
    ConfirmProfileRevision,
    ConfirmProfileRevisionCommand,
    ProposeProfileRevision,
    ProposeProfileRevisionCommand,
    RejectPendingAction,
    RejectPendingActionCommand,
)
from openbiliclaw.application.edit_profile import EditProfile, EditProfileCommand
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.application.pending_actions import SqlitePendingActionRepository
from openbiliclaw.application.unit_of_work import ProfileEditUnitOfWork
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.observations.repository import SqliteObservationRepository
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.profile import CanonicalProfile, StableInterestClaim, claim_id
from openbiliclaw.understanding.repository import SqliteUnderstandingRepository
from openbiliclaw.understanding.service import UnderstandingService

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.ai.providers.embeddings import Vector

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class FakeEmbeddings:
    def __init__(self) -> None:
        self.info = EmbeddingModelInfo(
            provider="test", model="corrections", dimensions=2, normalized=True, version="1"
        )

    async def embed_documents(self, texts: tuple[str, ...]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple((1.0, 0.0) for _ in texts),
            usage=EmbeddingUsage(requests=1, input_tokens=len(texts)),
            model=self.info,
        )

    async def embed_query(self, text: str) -> Vector:
        del text
        return (1.0, 0.0)


@pytest.mark.asyncio
async def test_profile_revision_requires_approval_then_dual_writes_statement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision.db"
    database = SqliteDatabase(path)
    await SchemaMigrator(path).migrate()
    await database.open()
    try:
        repository = SqliteUnderstandingRepository(database)
        observations = SqliteObservationRepository(database)
        embeddings = FakeEmbeddings()
        index = EmbeddingIndex(database, embeddings, embeddings.info, clock=lambda: NOW)
        original = StableInterestClaim(
            claim_id=claim_id("stable_interest", "science"),
            value="science",
            confidence=0.6,
            fresh_at=NOW,
            evidence_ids=("ev_" + "1" * 32,),
        )
        profile = CanonicalProfile(
            profile_id="default", revision=1, updated_at=NOW, claims=(original,)
        )
        await repository.commit_analysis(
            profile=profile,
            proposals=(),
            decisions=(),
            evidence=(),
            analyzer_id="test.seed",
            checkpoint="0",
        )
        understanding = UnderstandingService(
            observations, repository, analyzers=(), clock=lambda: NOW, embedding_index=index
        )
        edit = EditProfile(
            ProfileEditUnitOfWork(repository, observations, embedding_index=index),
            clock=lambda: NOW,
        )
        pending = SqlitePendingActionRepository(database)
        current_time = [NOW]
        proposal = await ProposeProfileRevision(
            pending, understanding, clock=lambda: current_time[0]
        )(
            ProposeProfileRevisionCommand(
                idempotency_key="assistant:revision:science",
                profile_id="default",
                account_id="local",
                user_id="local",
                field=original.claim_id,
                operation=OverrideOperation.SET,
                value="robotics",
                rationale="I care about robotics now",
            )
        )
        assert proposal.kind == "profile_revision"
        assert (await understanding.profile("default")).claims == (original,)

        confirmation = ConfirmProfileRevision(
            pending, edit, understanding, clock=lambda: current_time[0]
        )
        result = await confirmation(
            ConfirmProfileRevisionCommand(
                pending_action_id=proposal.pending_action_id, user_id="local"
            )
        )
        updated = await understanding.profile("default")
        replacement = next(item for item in updated.claims if item.value == "robotics")
        assert replacement.confidence == 1.0
        assert replacement.evidence_ids == ("ev_" + result.observation_id.removeprefix("obs_"),)
        history = await repository.proposals_for_claims("default", (replacement.claim_id,))
        assert history[-1].evidence[0].trust == 1.0
        assert await index.vector("claim", replacement.claim_id) == pytest.approx((1.0, 0.0))
        assert await index.vector("evidence", replacement.evidence_ids[0]) == pytest.approx(
            (1.0, 0.0)
        )
        ledger = await repository.ledger("default")
        assert {item.status.value for item in ledger} >= {"override", "accepted"}
        replay = await edit(
            EditProfileCommand(
                idempotency_key=proposal.idempotency_key + ":approve",
                profile_id="default",
                account_id="local",
                claim_id=original.claim_id,
                operation=OverrideOperation.SET,
                value="robotics",
            )
        )
        assert replay.profile == updated
        assert replay.observation_id == result.observation_id
        assert await repository.ledger("default") == ledger
        current_time[0] = NOW + timedelta(hours=1)
        assert (
            await confirmation(
                ConfirmProfileRevisionCommand(
                    pending_action_id=proposal.pending_action_id, user_id="local"
                )
            )
            == result
        )
        assert await repository.ledger("default") == ledger
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_rejected_revision_never_changes_profile(tmp_path: Path) -> None:
    path = tmp_path / "reject.db"
    database = SqliteDatabase(path)
    await SchemaMigrator(path).migrate()
    await database.open()
    try:
        pending = SqlitePendingActionRepository(database)
        understanding = SqliteUnderstandingRepository(database)
        before = await understanding.load_profile("default", now=NOW)
        profiles = UnderstandingService(
            SqliteObservationRepository(database),
            understanding,
            analyzers=(),
            clock=lambda: NOW,
        )
        proposal = await ProposeProfileRevision(pending, profiles, clock=lambda: NOW)(
            ProposeProfileRevisionCommand(
                idempotency_key="assistant:revision:reject",
                profile_id="default",
                account_id="local",
                user_id="local",
                field="exploration.disabled",
                operation=OverrideOperation.SET,
                value="true",
                rationale="stop exploring",
            )
        )
        rejected = await RejectPendingAction(pending, clock=lambda: NOW)(
            RejectPendingActionCommand(
                pending_action_id=proposal.pending_action_id, user_id="local"
            )
        )
        assert rejected.decision == "rejected"
        stored = await pending.get(proposal.pending_action_id)
        assert stored is not None and stored.decision == "rejected"
        assert await understanding.load_profile("default", now=NOW) == before
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_exploration_approval_scope_expiry_replay_and_rejection_rules(
    tmp_path: Path,
) -> None:
    path = tmp_path / "exploration-actions.db"
    database = SqliteDatabase(path)
    await SchemaMigrator(path).migrate()
    await database.open()
    try:
        repository = SqliteUnderstandingRepository(database)
        observations = SqliteObservationRepository(database)
        understanding = UnderstandingService(
            observations, repository, analyzers=(), clock=lambda: NOW
        )
        edit = EditProfile(ProfileEditUnitOfWork(repository, observations), clock=lambda: NOW)
        pending = SqlitePendingActionRepository(database)
        current_time = [NOW]
        proposer = ProposeProfileRevision(pending, understanding, clock=lambda: current_time[0])
        proposal = await proposer(
            ProposeProfileRevisionCommand(
                idempotency_key="x" * 200,
                profile_id="default",
                account_id="local",
                user_id="local",
                field="exploration.disabled",
                operation=OverrideOperation.SET,
                value="true",
                rationale="stop exploring",
            )
        )
        confirmation = ConfirmProfileRevision(
            pending, edit, understanding, clock=lambda: current_time[0]
        )
        with pytest.raises(ApplicationError, match="scope mismatch") as wrong_user:
            await confirmation(
                ConfirmProfileRevisionCommand(
                    pending_action_id=proposal.pending_action_id, user_id="other"
                )
            )
        assert wrong_user.value.code is ApplicationErrorCode.FORBIDDEN
        approved = await confirmation(
            ConfirmProfileRevisionCommand(
                pending_action_id=proposal.pending_action_id, user_id="local"
            )
        )
        assert (await understanding.profile("default")).exploration_disabled()
        assert len(approved.idempotency_key) == 200
        current_time[0] = NOW + timedelta(hours=1)
        assert (
            await confirmation(
                ConfirmProfileRevisionCommand(
                    pending_action_id=proposal.pending_action_id, user_id="local"
                )
            )
            == approved
        )
        with pytest.raises(ApplicationError, match="was approved") as approved_reject:
            await RejectPendingAction(pending, clock=lambda: current_time[0])(
                RejectPendingActionCommand(
                    pending_action_id=proposal.pending_action_id, user_id="local"
                )
            )
        assert approved_reject.value.code is ApplicationErrorCode.CONFLICT

        current_time[0] = NOW
        expiring = await proposer(
            ProposeProfileRevisionCommand(
                idempotency_key="assistant:revision:expired",
                profile_id="default",
                account_id="local",
                user_id="local",
                field="exploration.disabled",
                operation=OverrideOperation.REMOVE,
                value=None,
                rationale="keep exploring",
                expires_in_seconds=1,
            )
        )
        current_time[0] = NOW + timedelta(seconds=2)
        with pytest.raises(ApplicationError, match="expired") as expired_confirm:
            await confirmation(
                ConfirmProfileRevisionCommand(
                    pending_action_id=expiring.pending_action_id, user_id="local"
                )
            )
        assert expired_confirm.value.code is ApplicationErrorCode.EXPIRED
        rejected = await RejectPendingAction(pending, clock=lambda: current_time[0])(
            RejectPendingActionCommand(
                pending_action_id=expiring.pending_action_id, user_id="local"
            )
        )
        assert rejected.decision == "rejected"
        assert (await understanding.profile("default")).exploration_disabled()
    finally:
        await database.close()
