from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.ai.runtime.capabilities import AgentId
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.observations.models import (
    ExternalContentPayload,
    ExternalSaveObservation,
    PreferenceStatementObservation,
    ReasonPayload,
    RecommendationDislikedObservation,
)
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
    AvoidanceClaim,
    CanonicalProfile,
    EmergingInterestClaim,
    StableInterestClaim,
    claim_id,
)
from openbiliclaw.understanding.proposals import ClaimProposal, ProposalBatch, ProposalOwner
from openbiliclaw.understanding.repository import SqliteUnderstandingRepository
from openbiliclaw.understanding.resynthesis import (
    ResynthesisResult,
    ResynthesisService,
    ResynthesisTrigger,
)
from openbiliclaw.understanding.service import (
    AnalyzerContract,
    AnalyzerInput,
    UnderstandingService,
    _project_observation,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.ai.providers.embeddings.index import EmbeddingKind

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


class IndexSpy:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = fail

    async def upsert(self, kind: EmbeddingKind, ref_id: str, text: str) -> bool:
        self.calls.append((kind, ref_id, text))
        if self.fail:
            raise RuntimeError("embedding outage")
        return True


class ResynthesisSpy:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, tuple[ClaimProposal, ...]]] = []
        self.fail = fail

    async def after_proposals(
        self, profile_id: str, proposals: tuple[ClaimProposal, ...]
    ) -> ResynthesisResult:
        self.calls.append((profile_id, proposals))
        if self.fail:
            raise RuntimeError("resynthesis failed")
        return ResynthesisResult(profile=CanonicalProfile.empty(profile_id, NOW), claim_ids=())


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


def test_only_explicit_observations_project_statement_trust() -> None:
    statement = observation()
    behavior = RecommendationDislikedObservation(
        observation_id="obs_" + "f" * 32,
        idempotency_key="feedback-disliked",
        occurred_at=NOW,
        received_at=NOW,
        account_id="account-1",
        content_ref=None,
        provenance=ObservationProvenance(
            producer_id="application.feedback",
            source=ObservationSource.RECOMMENDATION,
            authenticated=True,
            trust_level=TrustLevel.HIGH,
        ),
        payload=ReasonPayload(reason="not relevant", exposed=True),
    )

    assert _project_observation(statement).trust == 1.0
    assert _project_observation(behavior).trust == 0.6


async def test_external_evidence_uses_canonical_analyzer_pipeline(tmp_path: Path) -> None:
    database, observations, repository = await setup(tmp_path / "external.db")
    event = ExternalSaveObservation(
        observation_id="obs_" + "9" * 32,
        idempotency_key="external_save:bilibili:BVtyped",
        occurred_at=NOW,
        received_at=NOW,
        account_id=None,
        content_ref=ContentRef(
            provider_id=ProviderId(value="bilibili"),
            content_kind=ContentKind(value="video"),
            provider_content_id="BVtyped",
            canonical_url="https://www.bilibili.com/video/BVtyped",
        ),
        provenance=ObservationProvenance(
            producer_id="provider.bilibili.evidence",
            source=ObservationSource.PROVIDER_IMPORT,
            authenticated=True,
            trust_level=TrustLevel.HIGH,
        ),
        payload=ExternalContentPayload(
            provider_event_id="BVtyped", title="Typed systems", creator_label="Creator"
        ),
    )
    await observations.insert_batch((event,))
    analyzer = PreferenceAnalyzer()
    service = UnderstandingService(
        observations, repository, analyzers=(analyzer,), clock=lambda: NOW
    )

    assert (await service.process("default", batch_size=10)).accepted == 1
    evidence = analyzer.inputs[0].evidence[0]
    assert evidence.trust == 0.6
    assert evidence.summary == "external save: Typed systems by Creator (bilibili/BVtyped)"
    assert (await repository.load_profile("default", now=NOW)).revision == 1
    await database.close()


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
    assert (await repository.proposals_for_claims("default", (profile.claims[0].claim_id,)))[
        0
    ].proposal_id == "prop_" + "a" * 32
    assert (
        analyzer.inputs[0].evidence[0].summary
        == "Preference statement: I enjoy practical science explanations"
    )
    assert len(analyzer.inputs[0].model_dump_json()) < 4_000
    await database.close()


async def test_understanding_commit_indexes_evidence_and_accepted_claim_fail_open(
    tmp_path: Path,
) -> None:
    for fail in (False, True):
        database, observations, repository = await setup(tmp_path / f"index-{fail}.db")
        await observations.insert_batch((observation(),))
        index = IndexSpy(fail=fail)
        service = UnderstandingService(
            observations,
            repository,
            analyzers=(PreferenceAnalyzer(),),
            clock=lambda: NOW,
            embedding_index=index,
        )

        assert (await service.process("default", batch_size=1)).accepted == 1
        assert {item[0] for item in index.calls} == {"evidence", "claim"}
        assert (await service.profile("default")).claims[0].value == "science"
        await database.close()


async def test_resynthesis_caps_claim_evidence_and_profile_reloads(tmp_path: Path) -> None:
    database, observations, repository = await setup(tmp_path / "bounded-evidence.db")
    events = tuple(observation(index) for index in range(1, 71))
    await observations.insert_batch(events)
    links = tuple(
        EvidenceLink(
            evidence_id="ev_" + item.observation_id.removeprefix("obs_"),
            observation_id=item.observation_id,
            summary=f"statement {index}",
            occurred_at=NOW,
            trust=1.0,
        )
        for index, item in enumerate(events, start=1)
    )
    claim = StableInterestClaim(
        claim_id=claim_id("stable_interest", "science"),
        value="science",
        confidence=0.9,
        fresh_at=NOW,
        evidence_ids=(links[0].evidence_id,),
    )
    proposals = tuple(
        ClaimProposal(
            proposal_id="prop_" + marker * 32,
            analyzer_id="understanding.preference.v1",
            owner=ProposalOwner.PREFERENCE,
            claim=claim,
            evidence=batch,
            proposed_at=NOW,
        )
        for marker, batch in (("c", links[:64]), ("d", links[64:]))
    )
    profile = CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=(claim,))
    await repository.commit_analysis(
        profile=profile,
        proposals=proposals,
        decisions=(),
        evidence=links,
        analyzer_id="understanding.preference.v1",
        checkpoint="70",
    )

    await ResynthesisService(repository, clock=lambda: NOW).resynthesize(
        "default", ResynthesisTrigger.CONTRADICTORY_EVIDENCE, (claim.claim_id,)
    )

    loaded = await repository.load_profile("default", now=NOW)
    assert len(loaded.claims[0].evidence_ids) == 64
    assert loaded == CanonicalProfile.model_validate_json(loaded.model_dump_json())
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
    resynthesis = ResynthesisSpy()
    service = UnderstandingService(
        observations,
        repository,
        analyzers=(),
        clock=lambda: NOW,
        resynthesis=resynthesis,
    )

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
    assert len(resynthesis.calls) == 1
    assert resynthesis.calls[0][0] == "default"
    assert resynthesis.calls[0][1][0].proposal_id == "prop_" + "7" * 32
    await database.close()


async def test_resynthesis_failure_does_not_abort_remaining_analyzers(tmp_path: Path) -> None:
    database, observations, repository = await setup(tmp_path / "analyzer-failure.db")
    await observations.insert_batch((observation(),))
    first = PreferenceAnalyzer()
    second = PreferenceAnalyzer()
    second.contract = second.contract.model_copy(
        update={"agent_id": AgentId("understanding.insight.v1")}
    )
    service = UnderstandingService(
        observations,
        repository,
        analyzers=(first, second),
        clock=lambda: NOW,
        resynthesis=ResynthesisSpy(fail=True),
    )

    result = await service.process("default", batch_size=1)

    assert result.accepted == 2
    assert len(first.inputs) == len(second.inputs) == 1
    assert await repository.checkpoint(second.contract.agent_id.value) == "1"
    await database.close()


async def test_resynthesis_failure_does_not_fail_committed_source_operation(
    tmp_path: Path,
) -> None:
    database, observations, repository = await setup(tmp_path / "resynthesis-failure.db")
    event = observation(8)
    await observations.insert_batch((event,))
    link = EvidenceLink(
        evidence_id="ev_" + event.observation_id.removeprefix("obs_"),
        observation_id=event.observation_id,
        summary="new evidence",
        occurred_at=NOW,
        trust=1.0,
    )
    claim = EmergingInterestClaim(
        claim_id=claim_id("emerging_interest", "resilient"),
        value="resilient",
        confidence=0.2,
        fresh_at=NOW,
        evidence_ids=(link.evidence_id,),
    )
    hook = ResynthesisSpy(fail=True)
    service = UnderstandingService(
        observations, repository, analyzers=(), clock=lambda: NOW, resynthesis=hook
    )

    decision = await service.consider(
        "default",
        ClaimProposal(
            proposal_id="prop_" + "8" * 32,
            analyzer_id="understanding.exploration.v1",
            owner=ProposalOwner.TOPIC_LIFECYCLE,
            claim=claim,
            evidence=(link,),
            proposed_at=NOW,
        ),
        link,
    )

    assert decision.status is LedgerStatus.PENDING
    assert await repository.proposal_exists("prop_" + "8" * 32)
    assert len(hook.calls) == 1
    await database.close()


async def test_committed_contradiction_triggers_audited_resynthesis(tmp_path: Path) -> None:
    database, observations, repository = await setup(tmp_path / "contradiction.db")
    medium = ObservationProvenance(
        producer_id="builtin.assistant",
        source=ObservationSource.ASSISTANT,
        authenticated=True,
        trust_level=TrustLevel.MEDIUM,
    )
    first = observation().model_copy(update={"provenance": medium})
    second = observation(2).model_copy(update={"provenance": medium})
    await observations.insert_batch((first, second))
    resynthesis = ResynthesisService(repository, clock=lambda: NOW)
    service = UnderstandingService(
        observations,
        repository,
        analyzers=(PreferenceAnalyzer(),),
        clock=lambda: NOW,
        resynthesis=resynthesis,
    )
    assert (await service.process("default", batch_size=1)).accepted == 1
    link = EvidenceLink(
        evidence_id="ev_" + second.observation_id.removeprefix("obs_"),
        observation_id=second.observation_id,
        summary="User avoids science",
        occurred_at=NOW,
        trust=0.6,
    )
    opposite = AvoidanceClaim(
        claim_id=claim_id("avoidance", "science"),
        value="science",
        confidence=0.8,
        fresh_at=NOW,
        evidence_ids=(link.evidence_id,),
    )

    decision = await service.consider(
        "default",
        ClaimProposal(
            proposal_id="prop_" + "b" * 32,
            analyzer_id="understanding.avoidance.v1",
            owner=ProposalOwner.AVOIDANCE,
            claim=opposite,
            evidence=(link,),
            proposed_at=NOW,
        ),
        link,
    )

    assert decision.reason == "contradiction"
    profile = await service.profile("default")
    assert profile.claims[0].confidence == 0.5
    assert (await repository.ledger("default"))[-1].reason == ("resynthesis_contradictory_evidence")
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
