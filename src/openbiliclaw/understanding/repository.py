"""Domain-owned persistence port and SQLite implementation."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol

from .ledger import LedgerEntry
from .profile import CanonicalProfile
from .proposals import ClaimProposal

if TYPE_CHECKING:
    from datetime import datetime

    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase

    from .evidence import EvidenceLink


class UnderstandingRepository(Protocol):
    async def load_profile(self, profile_id: str, *, now: datetime) -> CanonicalProfile: ...
    async def checkpoint(self, analyzer_id: str) -> str | None: ...
    async def ledger(self, profile_id: str) -> tuple[LedgerEntry, ...]: ...
    async def proposals_for_claims(
        self, profile_id: str, claim_ids: tuple[str, ...]
    ) -> tuple[ClaimProposal, ...]: ...
    async def proposal_exists(self, proposal_id: str) -> bool: ...
    async def commit_override(self, profile: CanonicalProfile, entry: LedgerEntry) -> None: ...
    async def commit_analysis(
        self,
        *,
        profile: CanonicalProfile,
        proposals: tuple[ClaimProposal, ...],
        decisions: tuple[LedgerEntry, ...],
        evidence: tuple[EvidenceLink, ...],
        analyzer_id: str,
        checkpoint: str,
    ) -> None: ...


class SqliteUnderstandingRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    async def load_profile(self, profile_id: str, *, now: datetime) -> CanonicalProfile:
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT profile_json FROM understanding_profiles WHERE profile_id=?",
                (profile_id,),
            )
        return (
            CanonicalProfile.model_validate_json(str(row[0]))
            if row
            else CanonicalProfile.empty(profile_id, now)
        )

    async def checkpoint(self, analyzer_id: str) -> str | None:
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT cursor FROM understanding_checkpoints WHERE analyzer_id=?", (analyzer_id,)
            )
        return str(row[0]) if row else None

    async def ledger(self, profile_id: str) -> tuple[LedgerEntry, ...]:
        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT entry_json FROM understanding_ledger WHERE profile_id=? ORDER BY rowid",
                (profile_id,),
            )
        return tuple(LedgerEntry.model_validate_json(str(row[0])) for row in rows)

    async def proposals_for_claims(
        self, profile_id: str, claim_ids: tuple[str, ...]
    ) -> tuple[ClaimProposal, ...]:
        if not claim_ids:
            return ()
        placeholders = ",".join("?" for _ in claim_ids)
        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT proposal_json FROM understanding_proposals "
                "WHERE profile_id=? AND json_extract(proposal_json,'$.claim.claim_id') IN ("
                + placeholders
                + ") ORDER BY rowid",
                (profile_id, *claim_ids),
            )
        return tuple(ClaimProposal.model_validate_json(str(row[0])) for row in rows)

    async def proposal_exists(self, proposal_id: str) -> bool:
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT 1 FROM understanding_proposals WHERE proposal_id=?", (proposal_id,)
            )
        return row is not None

    async def commit_override(self, profile: CanonicalProfile, entry: LedgerEntry) -> None:
        async with self._database.transaction() as session:
            await session.execute(
                "INSERT INTO understanding_profiles("
                "profile_id,revision,profile_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(profile_id) DO UPDATE SET "
                "revision=excluded.revision,profile_json=excluded.profile_json,"
                "updated_at=excluded.updated_at",
                (
                    profile.profile_id,
                    profile.revision,
                    profile.model_dump_json(),
                    profile.updated_at.isoformat(),
                ),
            )
            await session.execute(
                "INSERT INTO understanding_ledger("
                "ledger_id,profile_id,entry_json,created_at) VALUES(?,?,?,?)",
                (
                    entry.ledger_id,
                    profile.profile_id,
                    entry.model_dump_json(),
                    entry.decided_at.isoformat(),
                ),
            )

    async def commit_analysis(
        self,
        *,
        profile: CanonicalProfile,
        proposals: tuple[ClaimProposal, ...],
        decisions: tuple[LedgerEntry, ...],
        evidence: tuple[EvidenceLink, ...],
        analyzer_id: str,
        checkpoint: str,
    ) -> None:
        async with self._database.transaction() as session:
            # Proposals are inserted before decisions/profile in the transaction;
            # rollback keeps the ordering auditable without partial state.
            for proposal in proposals:
                await session.execute(
                    "INSERT OR IGNORE INTO understanding_proposals("
                    "proposal_id,profile_id,analyzer_id,proposal_json,created_at"
                    ") VALUES(?,?,?,?,?)",
                    (
                        proposal.proposal_id,
                        profile.profile_id,
                        analyzer_id,
                        proposal.model_dump_json(),
                        proposal.proposed_at.isoformat(),
                    ),
                )
            await session.execute(
                "INSERT INTO understanding_profiles("
                "profile_id,revision,profile_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(profile_id) DO UPDATE SET "
                "revision=excluded.revision,profile_json=excluded.profile_json,"
                "updated_at=excluded.updated_at",
                (
                    profile.profile_id,
                    profile.revision,
                    profile.model_dump_json(),
                    profile.updated_at.isoformat(),
                ),
            )
            for item in evidence:
                await session.execute(
                    "INSERT OR IGNORE INTO understanding_evidence("
                    "evidence_id,profile_id,observation_id,kind,weight,created_at"
                    ") VALUES(?,?,?,?,?,?)",
                    (
                        item.evidence_id,
                        profile.profile_id,
                        item.observation_id,
                        "observation",
                        item.trust,
                        item.occurred_at.isoformat(),
                    ),
                )
            for decision in decisions:
                await session.execute(
                    "INSERT OR IGNORE INTO understanding_ledger("
                    "ledger_id,profile_id,entry_json,created_at) VALUES(?,?,?,?)",
                    (
                        decision.ledger_id,
                        profile.profile_id,
                        decision.model_dump_json(),
                        decision.decided_at.isoformat(),
                    ),
                )
            await session.execute(
                "INSERT INTO understanding_checkpoints("
                "analyzer_id,cursor,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(analyzer_id) DO UPDATE SET "
                "cursor=excluded.cursor,updated_at=excluded.updated_at",
                (analyzer_id, checkpoint, profile.updated_at.isoformat()),
            )


def ledger_identity(proposal_id: str, status: str) -> str:
    return "ledger_" + hashlib.sha256(f"{proposal_id}:{status}".encode()).hexdigest()[:32]
