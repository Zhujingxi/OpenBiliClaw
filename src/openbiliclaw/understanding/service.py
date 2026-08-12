"""Bounded observation consumption and atomic deterministic commits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.ai.runtime.budgets import RunPolicy
from openbiliclaw.ai.runtime.capabilities import AgentId, ModelRequirements
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.observations.models import Observation, PreferenceStatementObservation

from .evidence import EvidenceLink
from .ledger import LedgerEntry, LedgerStatus
from .overrides import OverrideOperation, UserOverride
from .policy import DecisionReason, ProposalPolicy
from .repository import UnderstandingRepository, ledger_identity

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from openbiliclaw.observations.repository import ObservationRepository

    from .profile import CanonicalProfile
    from .proposals import ClaimProposal, ProposalBatch


class AnalyzerContract(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    agent_id: AgentId
    requirements: ModelRequirements
    policy: RunPolicy
    context_version: int = Field(ge=1)

    @classmethod
    def preference(cls) -> AnalyzerContract:
        return cls(
            agent_id=AgentId("understanding.preference.v1"),
            requirements=ModelRequirements(structured_output=True, context_tokens=4_096),
            policy=RunPolicy(retries=0, timeout_seconds=30),
            context_version=1,
        )


class AnalyzerInput(StrictBaseModel):
    """Compact model-visible observation projection, capped at 50 evidence links."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_revision: int = Field(ge=0)
    evidence: tuple[EvidenceLink, ...] = Field(max_length=50)
    context_version: int = Field(ge=1)


class UnderstandingAnalyzer(Protocol):
    contract: AnalyzerContract

    async def analyze(self, data: AnalyzerInput) -> ProposalBatch: ...


@dataclass(frozen=True, slots=True)
class ProcessResult:
    accepted: int
    rejected: int


class UnderstandingService:
    def __init__(
        self,
        observations: ObservationRepository,
        repository: UnderstandingRepository,
        *,
        analyzers: tuple[UnderstandingAnalyzer, ...],
        clock: Callable[[], datetime],
        policy: ProposalPolicy | None = None,
    ) -> None:
        self._observations = observations
        self._repository = repository
        self._analyzers = analyzers
        self._clock = clock
        self._policy = policy or ProposalPolicy()

    async def profile(self, profile_id: str) -> CanonicalProfile:
        return await self._repository.load_profile(profile_id, now=self._clock())

    async def apply_override(
        self,
        profile_id: str,
        *,
        claim_id: str,
        operation: OverrideOperation,
        value: str | None,
    ) -> CanonicalProfile:
        now = self._clock()
        profile = await self._repository.load_profile(profile_id, now=now)
        override = UserOverride.create(
            claim_id=claim_id, operation=operation, value=value, created_at=now
        )
        updated = override.apply(profile)
        entry = LedgerEntry(
            ledger_id=ledger_identity(override.override_id, LedgerStatus.OVERRIDE.value),
            profile_id=profile_id,
            override_id=override.override_id,
            claim_id=claim_id,
            status=LedgerStatus.OVERRIDE,
            reason=f"user_{operation.value}",
            decided_at=now,
        )
        await self._repository.commit_override(updated, entry)
        return updated

    async def process(self, profile_id: str, *, batch_size: int = 50) -> ProcessResult:
        if not 1 <= batch_size <= 50:
            raise ValueError("batch size must be between 1 and 50")
        accepted = rejected = 0
        for analyzer in self._analyzers:
            cursor = await self._repository.checkpoint(analyzer.contract.agent_id.value)
            page = await self._observations.read(after_cursor=cursor, limit=batch_size)
            if not page.items:
                continue
            now = self._clock()
            profile = await self._repository.load_profile(profile_id, now=now)
            evidence = tuple(_project_observation(item) for item in page.items)
            batch = await analyzer.analyze(
                AnalyzerInput(
                    profile_revision=profile.revision,
                    evidence=evidence,
                    context_version=analyzer.contract.context_version,
                )
            )
            profile, entries, added, denied = self._apply(profile, batch.proposals, now)
            accepted += added
            rejected += denied
            await self._repository.commit_analysis(
                profile=profile,
                proposals=batch.proposals,
                decisions=entries,
                evidence=evidence,
                analyzer_id=analyzer.contract.agent_id.value,
                checkpoint=page.next_cursor or cursor or "0",
            )
        return ProcessResult(accepted, rejected)

    def _apply(
        self, profile: CanonicalProfile, proposals: tuple[ClaimProposal, ...], now: datetime
    ) -> tuple[CanonicalProfile, tuple[LedgerEntry, ...], int, int]:
        claims = list(profile.claims)
        entries: list[LedgerEntry] = []
        accepted = rejected = 0
        for proposal in proposals:
            decision = self._policy.decide(
                profile.model_copy(update={"claims": tuple(claims)}), proposal, now=now
            )
            status = LedgerStatus.REJECTED
            if decision.accepted:
                claims = [item for item in claims if item.claim_id != proposal.claim.claim_id]
                claims.append(proposal.claim)
                status = (
                    LedgerStatus.SUPERSEDED
                    if decision.reason is DecisionReason.SUPERSEDED
                    else LedgerStatus.ACCEPTED
                )
                accepted += 1
            else:
                rejected += 1
            entries.append(
                LedgerEntry(
                    ledger_id=ledger_identity(proposal.proposal_id, status.value),
                    profile_id=profile.profile_id,
                    proposal_id=proposal.proposal_id,
                    claim_id=proposal.claim.claim_id,
                    status=status,
                    reason=decision.reason.value,
                    decided_at=now,
                )
            )
        if proposals:
            profile = profile.model_copy(
                update={
                    "claims": tuple(claims),
                    "revision": profile.revision + 1,
                    "updated_at": now,
                }
            )
        return profile, tuple(entries), accepted, rejected


def _project_observation(observation: Observation) -> EvidenceLink:
    summary = _observation_summary(observation)
    suffix = observation.observation_id.removeprefix("obs_")
    return EvidenceLink(
        evidence_id="ev_" + suffix,
        observation_id=observation.observation_id,
        summary=summary,
        occurred_at=observation.occurred_at,
        trust={"low": 0.25, "medium": 0.6, "high": 1.0}[observation.provenance.trust_level.value],
    )


def _observation_summary(observation: Observation) -> str:
    if isinstance(observation, PreferenceStatementObservation):
        return f"Preference statement: {observation.payload.statement}"[:500]
    label = observation.event_type.replace("_", " ")
    if observation.content_ref is not None:
        ref = observation.content_ref
        return f"{label}: {ref.provider_id.value}/{ref.provider_content_id}"[:500]
    return label[:500]
