from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.observations.models import PreferenceStatementObservation
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.observations.repository import SqliteObservationRepository
from openbiliclaw.understanding.evidence import EvidenceLink
from openbiliclaw.understanding.ledger import LedgerStatus
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.profile import (
    CanonicalProfile,
    EmergingInterestClaim,
    StableInterestClaim,
    claim_id,
)
from openbiliclaw.understanding.proposals import ClaimProposal, ProposalBatch, ProposalOwner
from openbiliclaw.understanding.repository import SqliteUnderstandingRepository
from openbiliclaw.understanding.service import AnalyzerContract, AnalyzerInput, UnderstandingService

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def observation(index: int = 1) -> PreferenceStatementObservation:
    return PreferenceStatementObservation(
        observation_id="obs_" + f"{index:032x}",
        idempotency_key=f"preference-{index}",
        occurred_at=NOW,
        received_at=NOW,
        account_id="account-1",
        content_ref=None,
        provenance=ObservationProvenance(
            producer_id="builtin.assistant",
            source=ObservationSource.ASSISTANT,
            authenticated=True,
            trust_level=TrustLevel.HIGH,
        ),
        payload={"statement": "I enjoy practical science explanations"},
    )


class PreferenceAnalyzer:
    contract = AnalyzerContract.preference()

    def __init__(self) -> None:
        self.inputs: list[AnalyzerInput] = []

    async def analyze(self, data: AnalyzerInput) -> ProposalBatch:
        self.inputs.append(data)
        evidence = data.evidence[0]
        claim = StableInterestClaim(
            claim_id=claim_id("stable_interest", "science"),
            value="science",
            confidence=0.9,
            fresh_at=NOW,
            evidence_ids=(evidence.evidence_id,),
        )
        return ProposalBatch(
            proposals=(
                ClaimProposal(
                    proposal_id="prop_" + "a" * 32,
                    analyzer_id=self.contract.agent_id.value,
                    owner=ProposalOwner.PREFERENCE,
                    claim=claim,
                    evidence=(evidence,),
                    proposed_at=NOW,
                ),
            )
        )


async def setup(
    path: Path,
) -> tuple[SqliteDatabase, SqliteObservationRepository, SqliteUnderstandingRepository]:
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    return database, SqliteObservationRepository(database), SqliteUnderstandingRepository(database)


async def test_atomic_apply_persists_proposal_before_decision_and_checkpoint(
    tmp_path: Path,
) -> None:
    database, observations, repository = await setup(tmp_path / "state.db")
    event = observation()
    await observations.insert_batch((event,))
    analyzer = PreferenceAnalyzer()
    service = UnderstandingService(
        observations, repository, analyzers=(analyzer,), clock=lambda: NOW
    )

    result = await service.process("default", batch_size=10)
    assert result.accepted == 1
    profile = await repository.load_profile("default", now=NOW)
    assert profile.revision == 1
    assert profile.claims[0].value == "science"
    assert await repository.checkpoint(analyzer.contract.agent_id.value) is not None
    ledger = await repository.ledger("default")
    assert ledger[0].status is LedgerStatus.ACCEPTED
    assert await repository.proposal_exists("prop_" + "a" * 32)
    assert (
        analyzer.inputs[0].evidence[0].summary
        == "Preference statement: I enjoy practical science explanations"
    )
    assert len(analyzer.inputs[0].model_dump_json()) < 4_000
    await database.close()


async def test_replay_and_restart_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database, observations, repository = await setup(path)
    await observations.insert_batch((observation(),))
    analyzer = PreferenceAnalyzer()
    service = UnderstandingService(
        observations, repository, analyzers=(analyzer,), clock=lambda: NOW
    )
    first = await service.process("default", batch_size=10)
    profile = await repository.load_profile("default", now=NOW)
    ledger = await repository.ledger("default")
    second = await service.process("default", batch_size=10)
    assert first.accepted == 1 and second.accepted == 0
    assert await repository.load_profile("default", now=NOW) == profile
    assert await repository.ledger("default") == ledger
    await database.close()

    database2 = SqliteDatabase(path)
    await database2.open()
    restarted = SqliteUnderstandingRepository(database2)
    assert await restarted.load_profile("default", now=NOW) == profile
    assert await restarted.ledger("default") == ledger
    await database2.close()


async def test_exploration_like_proposal_stays_pending_until_corroborated(
    tmp_path: Path,
) -> None:
    database, observations, repository = await setup(tmp_path / "explore-like.db")
    event = observation(7)
    await observations.insert_batch((event,))
    evidence = EvidenceLink(
        evidence_id="ev_" + event.observation_id.removeprefix("obs_"),
        observation_id=event.observation_id,
        summary="Exploration liked: adjacent science; arm=source-novel",
        occurred_at=NOW,
        trust=0.2,
    )
    claim = EmergingInterestClaim(
        claim_id=claim_id("emerging_interest", "adjacent science"),
        value="adjacent science",
        confidence=0.2,
        fresh_at=NOW,
        evidence_ids=(evidence.evidence_id,),
    )
    service = UnderstandingService(observations, repository, analyzers=(), clock=lambda: NOW)

    decision = await service.consider(
        "default",
        ClaimProposal(
            proposal_id="prop_" + "7" * 32,
            analyzer_id="understanding.exploration.v1",
            owner=ProposalOwner.TOPIC_LIFECYCLE,
            claim=claim,
            evidence=(evidence,),
            proposed_at=NOW,
        ),
        evidence,
    )

    assert decision.status is LedgerStatus.PENDING
    assert decision.reason == "low_confidence"
    assert await repository.proposal_exists("prop_" + "7" * 32)
    assert (await service.profile("default")).claims == ()
    await database.close()


async def test_models_optional_profile_reads_and_empty_processing(tmp_path: Path) -> None:
    database, observations, repository = await setup(tmp_path / "state.db")
    service = UnderstandingService(observations, repository, analyzers=(), clock=lambda: NOW)
    assert await service.profile("default") == CanonicalProfile.empty("default", NOW)
    result = await service.process("default", batch_size=10)
    assert result.accepted == 0 and result.rejected == 0
    for size in (0, 51):
        with pytest.raises(ValueError, match="batch size"):
            await service.process("default", batch_size=size)
    await database.close()


async def test_deterministic_override_persists_profile_and_audit_entry(tmp_path: Path) -> None:
    database, observations, repository = await setup(tmp_path / "state.db")
    service = UnderstandingService(observations, repository, analyzers=(), clock=lambda: NOW)
    identity = claim_id("stable_interest", "science")
    updated = await service.apply_override(
        "default", claim_id=identity, operation=OverrideOperation.REMOVE, value=None
    )
    assert updated.overrides[0].claim_id == identity
    loaded = await repository.load_profile("default", now=NOW)
    assert loaded == updated
    ledger = await repository.ledger("default")
    assert ledger[0].status is LedgerStatus.OVERRIDE
    assert ledger[0].override_id == updated.overrides[0].override_id
    await database.close()


async def test_repository_atomic_failure_rolls_back_all_owned_state(tmp_path: Path) -> None:
    database, _observations, repository = await setup(tmp_path / "state.db")
    profile = CanonicalProfile.empty("default", NOW)
    evidence = EvidenceLink(
        evidence_id="ev_" + "1" * 32,
        observation_id="obs_" + "9" * 32,
        summary="missing observation",
        occurred_at=NOW,
        trust=1,
    )
    with pytest.raises(sqlite3.IntegrityError):
        await repository.commit_analysis(
            profile=profile,
            proposals=(),
            decisions=(),
            evidence=(evidence,),
            analyzer_id="understanding.preference.v1",
            checkpoint="1",
        )
    assert await repository.load_profile("default", now=NOW) == CanonicalProfile.empty(
        "default", NOW
    )
    assert await repository.checkpoint("understanding.preference.v1") is None
    await database.close()
