"""Narrow recommendation persistence ports and one SQLite implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .models import (
    AdmissionRecord,
    Candidate,
    CandidateState,
    EvaluationRecord,
    ExpressionRecord,
    FeedbackRecord,
    RejectionRecord,
    SelectionRecord,
    ShownRecord,
)

if TYPE_CHECKING:
    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase, SqliteSession


class InventoryRepository(Protocol):
    async def add_candidate(self, candidate: Candidate) -> bool: ...
    async def load(self, candidate_id: str) -> Candidate: ...
    async def transition(
        self, candidate_id: str, expected: CandidateState, target: CandidateState
    ) -> Candidate: ...
    async def expire(self, candidate_id: str, expected: CandidateState) -> Candidate: ...


class EvaluationRepository(Protocol):
    async def save_evaluation(self, record: EvaluationRecord) -> bool: ...
    async def save_rejection(self, record: RejectionRecord) -> bool: ...


class RecommendationRepository(Protocol):
    async def admit_and_select(
        self,
        candidate: Candidate,
        admission: AdmissionRecord,
        selection: SelectionRecord,
    ) -> Candidate: ...
    async def feed(self, *, limit: int) -> tuple[SelectionRecord, ...]: ...


class ShownHistoryRepository(Protocol):
    async def mark_shown(self, record: ShownRecord) -> Candidate: ...


class FeedbackRepository(Protocol):
    async def save_feedback(self, record: FeedbackRecord) -> bool: ...


class ExpressionRepository(Protocol):
    async def save_expression(self, record: ExpressionRecord) -> None: ...


class SqliteRecommendationRepository:
    """One adapter implementing the aggregate-specific repository ports."""

    def __init__(self, database: SqliteDatabase) -> None:
        self.db = database

    async def add_candidate(self, candidate: Candidate) -> bool:
        async with self.db.transaction() as session:
            rows = await session.execute(
                "INSERT OR IGNORE INTO recommendation_candidates("
                "candidate_id,state,candidate_json,created_at) VALUES(?,?,?,?)",
                (
                    candidate.candidate_id,
                    candidate.state.value,
                    candidate.model_dump_json(),
                    candidate.provenance.discovered_at.isoformat(),
                ),
            )
        return rows == 1

    async def load(self, candidate_id: str) -> Candidate:
        async with self.db.transaction() as session:
            row = await session.fetch_one(
                "SELECT candidate_json FROM recommendation_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
        if row is None:
            raise KeyError(candidate_id)
        return Candidate.model_validate_json(str(row[0]))

    async def transition(
        self, candidate_id: str, expected: CandidateState, target: CandidateState
    ) -> Candidate:
        current = await self.load(candidate_id)
        if current.state is not expected:
            raise ValueError("candidate state changed concurrently")
        updated = current.transition(target)
        async with self.db.transaction() as session:
            await self._update_state(session, updated, expected)
        return updated

    async def expire(self, candidate_id: str, expected: CandidateState) -> Candidate:
        return await self.transition(candidate_id, expected, CandidateState.EXPIRED)

    async def save_evaluation(self, record: EvaluationRecord) -> bool:
        return await self._insert(
            "recommendation_evaluations",
            "evaluation_id",
            record.evaluation_id,
            record.model_dump_json(),
            record.evaluated_at.isoformat(),
        )

    async def save_rejection(self, record: RejectionRecord) -> bool:
        return await self._insert(
            "recommendation_rejections",
            "rejection_id",
            record.rejection_id,
            record.model_dump_json(),
            record.rejected_at.isoformat(),
        )

    async def admit_and_select(
        self,
        candidate: Candidate,
        admission: AdmissionRecord,
        selection: SelectionRecord,
    ) -> Candidate:
        if candidate.state is not CandidateState.EVALUATED:
            raise ValueError("admission requires evaluated candidate")
        if admission.candidate_id != candidate.candidate_id:
            raise ValueError("admission candidate mismatch")
        if selection.candidate_id != candidate.candidate_id:
            raise ValueError("selection candidate mismatch")
        admitted = candidate.transition(CandidateState.ADMITTED)
        selected = admitted.transition(CandidateState.SELECTED)
        async with self.db.transaction() as session:
            await self._update_state(session, admitted, CandidateState.EVALUATED)
            await self._insert_session(
                session,
                "recommendation_admissions",
                "admission_id",
                admission.admission_id,
                admission.model_dump_json(),
                admission.admitted_at.isoformat(),
            )
            await self._update_state(session, selected, CandidateState.ADMITTED)
            await self._insert_session(
                session,
                "recommendation_selections",
                "recommendation_id",
                selection.recommendation_id,
                selection.model_dump_json(),
                selection.selected_at.isoformat(),
            )
        return selected

    async def mark_shown(self, record: ShownRecord) -> Candidate:
        current = await self.load(record.candidate_id)
        if current.state is not CandidateState.SELECTED:
            raise ValueError("shown record requires selected candidate")
        selected = current.transition(CandidateState.SHOWN)
        async with self.db.transaction() as session:
            await self._update_state(session, selected, CandidateState.SELECTED)
            await self._insert_session(
                session,
                "recommendation_shown",
                "shown_id",
                record.shown_id,
                record.model_dump_json(),
                record.shown_at.isoformat(),
            )
        return selected

    async def save_feedback(self, record: FeedbackRecord) -> bool:
        return await self._insert(
            "recommendation_feedback",
            "feedback_id",
            record.feedback_id,
            record.model_dump_json(),
            record.occurred_at.isoformat(),
        )

    async def save_expression(self, record: ExpressionRecord) -> None:
        await self._insert(
            "recommendation_expressions",
            "recommendation_id",
            record.recommendation_id,
            record.model_dump_json(),
            record.generated_at.isoformat(),
        )

    async def feed(self, *, limit: int) -> tuple[SelectionRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self.db.transaction() as session:
            rows = await session.fetch_all(
                "SELECT record_json FROM recommendation_selections "
                "ORDER BY created_at DESC,recommendation_id LIMIT ?",
                (limit,),
            )
        return tuple(SelectionRecord.model_validate_json(str(row[0])) for row in rows)

    async def _insert(
        self, table: str, key_name: str, key: str, data: str, created_at: str
    ) -> bool:
        async with self.db.transaction() as session:
            return await self._insert_session(session, table, key_name, key, data, created_at)

    @staticmethod
    async def _insert_session(
        session: SqliteSession,
        table: str,
        key_name: str,
        key: str,
        data: str,
        created_at: str,
    ) -> bool:
        allowed = {
            "recommendation_admissions",
            "recommendation_evaluations",
            "recommendation_expressions",
            "recommendation_feedback",
            "recommendation_rejections",
            "recommendation_selections",
            "recommendation_shown",
        }
        if table not in allowed:
            raise ValueError("unknown repository table")
        rows = await session.execute(
            f"INSERT OR IGNORE INTO {table}({key_name},record_json,created_at) VALUES(?,?,?)",
            (key, data, created_at),
        )
        return rows == 1

    @staticmethod
    async def _update_state(
        session: SqliteSession, candidate: Candidate, expected: CandidateState
    ) -> None:
        rows = await session.execute(
            "UPDATE recommendation_candidates SET state=?,candidate_json=? "
            "WHERE candidate_id=? AND state=?",
            (
                candidate.state.value,
                candidate.model_dump_json(),
                candidate.candidate_id,
                expected.value,
            ),
        )
        if rows != 1:
            raise ValueError("candidate state changed concurrently")
