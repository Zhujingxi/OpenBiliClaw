"""Narrow recommendation persistence ports and one SQLite implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openbiliclaw.content.integration.projections import CardData

from .models import (
    AdmissionRecord,
    Candidate,
    CandidateState,
    EvaluationRecord,
    ExpressionRecord,
    FeedbackRecord,
    RecommendationFeedItem,
    RejectionRecord,
    SelectionRecord,
    ShownRecord,
    record_identity,
)

if TYPE_CHECKING:
    from datetime import datetime

    from openbiliclaw.content.integration.identity import ContentRef
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
    async def load_evaluation(self, candidate_id: str) -> EvaluationRecord: ...
    async def save_rejection(self, record: RejectionRecord) -> bool: ...


class RecommendationRepository(Protocol):
    async def admit_and_select(
        self,
        candidate: Candidate,
        admission: AdmissionRecord,
        selection: SelectionRecord,
    ) -> Candidate: ...
    async def load_admission(self, candidate_id: str) -> AdmissionRecord: ...
    async def load_selection(self, recommendation_id: str) -> SelectionRecord: ...
    async def load_selections_for_seed(self, seed: int) -> tuple[SelectionRecord, ...]: ...
    async def load_shown(self, recommendation_id: str) -> ShownRecord: ...
    async def content_ref_for_shown(self, shown_id: str) -> ContentRef: ...
    async def deliver_feed(
        self, *, limit: int, shown_at: datetime
    ) -> tuple[RecommendationFeedItem, ...]: ...


class FeedbackRepository(Protocol):
    async def save_feedback(self, record: FeedbackRecord, content_ref: ContentRef) -> bool: ...
    async def load_feedback(self, recommendation_id: str) -> tuple[FeedbackRecord, ...]: ...


class ExpressionRepository(Protocol):
    async def save_expression(self, record: ExpressionRecord) -> None: ...
    async def load_expression(self, recommendation_id: str) -> ExpressionRecord: ...


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

    async def expire_due(self, *, now: str) -> int:
        """Expire selected/admitted candidates whose typed payload deadline passed."""
        async with self.db.transaction() as session:
            rows = await session.fetch_all(
                "SELECT candidate_id,state,candidate_json FROM recommendation_candidates "
                "WHERE state IN ('admitted','selected')"
            )
        expired = 0
        for candidate_id, state, payload in rows:
            candidate = Candidate.model_validate_json(str(payload))
            if candidate.expires_at.isoformat() > now:
                continue
            await self.expire(str(candidate_id), CandidateState(str(state)))
            expired += 1
        return expired

    async def save_evaluation(self, record: EvaluationRecord) -> bool:
        return await self._insert(
            "recommendation_evaluations",
            "evaluation_id",
            record.evaluation_id,
            record.model_dump_json(),
            record.evaluated_at.isoformat(),
        )

    async def load_evaluation(self, candidate_id: str) -> EvaluationRecord:
        async with self.db.transaction() as session:
            row = await session.fetch_one(
                "SELECT record_json FROM recommendation_evaluations "
                "WHERE json_extract(record_json,'$.candidate_id')=?",
                (candidate_id,),
            )
        if row is None:
            raise KeyError(candidate_id)
        return EvaluationRecord.model_validate_json(str(row[0]))

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

    async def load_admission(self, candidate_id: str) -> AdmissionRecord:
        async with self.db.transaction() as session:
            row = await session.fetch_one(
                "SELECT record_json FROM recommendation_admissions "
                "WHERE json_extract(record_json,'$.candidate_id')=?",
                (candidate_id,),
            )
        if row is None:
            raise KeyError(candidate_id)
        return AdmissionRecord.model_validate_json(str(row[0]))

    async def load_selection(self, recommendation_id: str) -> SelectionRecord:
        async with self.db.transaction() as session:
            row = await session.fetch_one(
                "SELECT record_json FROM recommendation_selections WHERE recommendation_id=?",
                (recommendation_id,),
            )
        if row is None:
            raise KeyError(recommendation_id)
        return SelectionRecord.model_validate_json(str(row[0]))

    async def load_selections_for_seed(self, seed: int) -> tuple[SelectionRecord, ...]:
        async with self.db.transaction() as session:
            rows = await session.fetch_all(
                "SELECT record_json FROM recommendation_selections "
                "WHERE json_extract(record_json,'$.seed')=? "
                "ORDER BY CAST(json_extract(record_json,'$.rank') AS INTEGER),recommendation_id",
                (seed,),
            )
        return tuple(SelectionRecord.model_validate_json(str(row[0])) for row in rows)

    async def load_shown(self, recommendation_id: str) -> ShownRecord:
        async with self.db.transaction() as session:
            row = await session.fetch_one(
                "SELECT record_json FROM recommendation_shown "
                "WHERE json_extract(record_json,'$.recommendation_id')=?",
                (recommendation_id,),
            )
        if row is None:
            raise KeyError(recommendation_id)
        return ShownRecord.model_validate_json(str(row[0]))

    async def content_ref_for_shown(self, shown_id: str) -> ContentRef:
        """Resolve the immutable content identity behind one delivered impression."""

        async with self.db.transaction() as session:
            row = await session.fetch_one(
                "SELECT c.candidate_json FROM recommendation_shown s "
                "JOIN recommendation_candidates c "
                "ON c.candidate_id=json_extract(s.record_json,'$.candidate_id') "
                "WHERE s.shown_id=?",
                (shown_id,),
            )
        if row is None:
            raise KeyError(shown_id)
        return Candidate.model_validate_json(str(row[0])).preview.ref

    async def save_feedback(self, record: FeedbackRecord, content_ref: ContentRef) -> bool:
        async with self.db.transaction() as session:
            return await self.save_feedback_session(session, record, content_ref)

    async def save_feedback_session(
        self, session: SqliteSession, record: FeedbackRecord, content_ref: ContentRef
    ) -> bool:
        """Validate one delivered recommendation and persist feedback in the caller transaction."""

        shown_row = await session.fetch_one(
            "SELECT record_json FROM recommendation_shown WHERE shown_id=?",
            (record.shown_id,),
        )
        if shown_row is None:
            raise KeyError(record.shown_id)
        shown = ShownRecord.model_validate_json(str(shown_row[0]))
        selection_row = await session.fetch_one(
            "SELECT record_json FROM recommendation_selections WHERE recommendation_id=?",
            (shown.recommendation_id,),
        )
        if selection_row is None:
            raise KeyError(shown.recommendation_id)
        selection = SelectionRecord.model_validate_json(str(selection_row[0]))
        if selection.candidate_id != shown.candidate_id:
            raise ValueError("shown recommendation does not match selection")
        candidate_row = await session.fetch_one(
            "SELECT candidate_json FROM recommendation_candidates WHERE candidate_id=?",
            (shown.candidate_id,),
        )
        if candidate_row is None:
            raise KeyError(shown.candidate_id)
        candidate = Candidate.model_validate_json(str(candidate_row[0]))
        if candidate.preview.ref != content_ref:
            raise ValueError("feedback content does not match shown recommendation")
        if candidate.state is not CandidateState.SHOWN:
            existing = await session.fetch_one(
                "SELECT feedback_id FROM recommendation_feedback WHERE feedback_id=?",
                (record.feedback_id,),
            )
            if existing is not None:
                return False
            raise ValueError("feedback requires a shown recommendation")
        interacted = candidate.transition(CandidateState.INTERACTED)
        inserted = await self._insert_session(
            session,
            "recommendation_feedback",
            "feedback_id",
            record.feedback_id,
            record.model_dump_json(),
            record.occurred_at.isoformat(),
        )
        if inserted:
            await self._update_state(session, interacted, CandidateState.SHOWN)
        return inserted

    async def load_feedback(self, recommendation_id: str) -> tuple[FeedbackRecord, ...]:
        async with self.db.transaction() as session:
            rows = await session.fetch_all(
                "SELECT f.record_json FROM recommendation_feedback AS f "
                "JOIN recommendation_shown AS s "
                "ON s.shown_id=json_extract(f.record_json,'$.shown_id') "
                "WHERE json_extract(s.record_json,'$.recommendation_id')=? "
                "ORDER BY f.created_at,f.feedback_id",
                (recommendation_id,),
            )
        return tuple(FeedbackRecord.model_validate_json(str(row[0])) for row in rows)

    async def save_expression(self, record: ExpressionRecord) -> None:
        await self._insert(
            "recommendation_expressions",
            "recommendation_id",
            record.recommendation_id,
            record.model_dump_json(),
            record.generated_at.isoformat(),
        )

    async def load_expression(self, recommendation_id: str) -> ExpressionRecord:
        async with self.db.transaction() as session:
            row = await session.fetch_one(
                "SELECT record_json FROM recommendation_expressions WHERE recommendation_id=?",
                (recommendation_id,),
            )
        if row is None:
            raise KeyError(recommendation_id)
        return ExpressionRecord.model_validate_json(str(row[0]))

    async def deliver_feed(
        self, *, limit: int, shown_at: datetime
    ) -> tuple[RecommendationFeedItem, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self.db.transaction() as session:
            rows = await session.fetch_all(
                "SELECT s.record_json,c.candidate_json,e.record_json "
                "FROM recommendation_selections AS s "
                "JOIN recommendation_candidates AS c "
                "ON c.candidate_id=json_extract(s.record_json,'$.candidate_id') "
                "JOIN recommendation_expressions AS e "
                "ON e.recommendation_id=s.recommendation_id "
                "WHERE c.state IN ('selected','shown') "
                "ORDER BY s.created_at DESC,"
                "CAST(json_extract(s.record_json,'$.rank') AS INTEGER),s.recommendation_id LIMIT ?",
                (limit,),
            )
            items = []
            for row in rows:
                selection = SelectionRecord.model_validate_json(str(row[0]))
                candidate = Candidate.model_validate_json(str(row[1]))
                expression = ExpressionRecord.model_validate_json(str(row[2]))
                shown = ShownRecord(
                    shown_id=record_identity("shown", selection.recommendation_id),
                    recommendation_id=selection.recommendation_id,
                    candidate_id=candidate.candidate_id,
                    shown_at=shown_at,
                )
                if candidate.state is CandidateState.SELECTED:
                    delivered = candidate.transition(CandidateState.SHOWN)
                    await self._update_state(session, delivered, CandidateState.SELECTED)
                    await self._insert_session(
                        session,
                        "recommendation_shown",
                        "shown_id",
                        shown.shown_id,
                        shown.model_dump_json(),
                        shown.shown_at.isoformat(),
                    )
                preview = candidate.preview
                items.append(
                    RecommendationFeedItem(
                        shown_id=shown.shown_id,
                        selection=selection,
                        ref=preview.ref,
                        card=CardData(
                            ref=preview.ref,
                            title=preview.title,
                            summary=preview.summary,
                            source_timestamp=preview.source_timestamp,
                            provenance=preview.provenance,
                        ),
                        reason=expression.reason,
                    )
                )
        return tuple(items)

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
