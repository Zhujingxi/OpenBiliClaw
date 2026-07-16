"""Repository ports and synchronous SQLAlchemy adapters for vNext features."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import HttpUrl
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.profile.domain import ProfileFacet, ProfileSnapshot
from openbiliclaw.infrastructure.database.models import (
    ActivityEventModel,
    AIRunModel,
    CandidateAssessmentModel,
    ChatTurnModel,
    CollectionItemModel,
    CollectionModel,
    ContentItemModel,
    FeedEntryModel,
    InteractionModel,
    JobRunModel,
    ProfileConsumedEvidenceModel,
    ProfileEvidenceModel,
    ProfileRevisionModel,
    SettingModel,
    SourceAccountModel,
)
from openbiliclaw.infrastructure.jobs.tasks import JobRunSnapshot, JobRunStatus
from openbiliclaw.infrastructure.security.credentials import EncryptedCredential

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session

    from openbiliclaw.features.activity.domain import ActivityEvent
    from openbiliclaw.features.chat.domain import ChatTurn
    from openbiliclaw.features.feed.domain import CandidateAssessment, FeedEntry, Interaction
    from openbiliclaw.features.library.domain import CollectionItem, CollectionKind
    from openbiliclaw.features.system.service import SettingValue

_SOURCE_ACCOUNT_NAMESPACE = UUID("644b8dba-8301-4e9f-a2d0-b1a54cb854be")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _json_metadata(model: object) -> dict[str, object]:
    dumped = model.model_dump(mode="json")  # type: ignore[attr-defined]
    metadata = dumped["metadata"]
    if not isinstance(metadata, dict):
        raise TypeError("serialized metadata must be an object")
    return metadata


class ProfileRevisionConflict(RuntimeError):  # noqa: N818 - public domain terminology
    """Raised when a profile write was based on a stale revision."""


@dataclass(frozen=True)
class CollectionRecord:
    """Stable identity and display metadata for a predefined collection."""

    id: UUID
    slug: str
    display_name: str


class SettingsRepository(Protocol):
    """Persistence port for typed system settings."""

    def get_all(self) -> dict[str, SettingValue]: ...

    def replace(self, values: Mapping[str, SettingValue]) -> None: ...


class SourceAccountRepository(Protocol):
    """Persistence port that accepts only already-encrypted credentials."""

    def upsert_credentials(
        self,
        *,
        source_id: str,
        account_key: str,
        encrypted_credentials: EncryptedCredential,
    ) -> UUID: ...


class ActivityRepository(Protocol):
    """Persistence port for normalized activity evidence."""

    def add(self, event: ActivityEvent) -> None: ...

    def add_if_absent(self, event: ActivityEvent) -> bool: ...

    def list_all(self) -> tuple[ActivityEvent, ...]: ...


class ProfileRepository(Protocol):
    """Persistence port for immutable profile revisions."""

    def latest(self) -> ProfileSnapshot | None: ...

    def append(self, snapshot: ProfileSnapshot, expected_revision: int | None) -> None: ...

    def consumed_evidence_ids(self) -> frozenset[UUID]: ...

    def mark_evidence_consumed(
        self, evidence_ids: frozenset[UUID], *, profile_revision: int
    ) -> None: ...


class ContentRepository(Protocol):
    """Persistence port for normalized content."""

    def add(self, item: ContentItem) -> None: ...

    def get_by_identity(self, source_id: str, external_id: str) -> ContentItem | None: ...

    def flush(self) -> None: ...


class AssessmentRepository(Protocol):
    """Persistence port for profile-relative candidate assessments."""

    def add(self, assessment: CandidateAssessment) -> None: ...

    def excluded_content_ids(self, profile_revision: int) -> frozenset[UUID]: ...

    def excluded_content_identities(self, profile_revision: int) -> frozenset[tuple[str, str]]: ...


class FeedRepository(Protocol):
    """Persistence port for ordered feed entries."""

    def add(self, entry: FeedEntry) -> None: ...

    def unseen_count(self) -> int: ...

    def next_position(self) -> int: ...


class InteractionRepository(Protocol):
    """Persistence port for immutable user interactions."""

    def add(self, interaction: Interaction) -> None: ...

    def adjustment(self, content_id: UUID) -> float: ...


class CollectionRepository(Protocol):
    """Persistence port for local-only collections."""

    def list_predefined(self) -> tuple[CollectionRecord, ...]: ...

    def add(self, item: CollectionItem) -> None: ...

    def remove(self, collection: CollectionKind, content_id: UUID) -> bool: ...


class ChatRepository(Protocol):
    """Persistence port for chat turns."""

    def add(self, turn: ChatTurn) -> None: ...


class JobRunRepository(Protocol):
    """Persistence port on which Task 20 builds durable job state."""

    def add_pending(self, *, job_name: str, idempotency_key: str, priority: int) -> UUID: ...


class AIRunRepository(Protocol):
    """Secret-safe persistence port for typed AI run lifecycle metadata."""

    def add_started(self, *, task_name: str, model_alias: str) -> UUID: ...

    def start(self, *, task_name: str, model_alias: str) -> UUID: ...

    def succeed(
        self,
        run_id: UUID,
        *,
        usage: dict[str, int],
    ) -> None: ...

    def fail(self, run_id: UUID, *, error_kind: str) -> None: ...


class SQLAlchemySettingsRepository:
    """SQLAlchemy settings adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_all(self) -> dict[str, SettingValue]:
        rows = self._session.scalars(select(SettingModel)).all()
        return {row.key: row.value for row in rows}

    def replace(self, values: Mapping[str, SettingValue]) -> None:
        now = _utc_now()
        stored = {row.key: row for row in self._session.scalars(select(SettingModel)).all()}
        stale_keys = set(stored) - set(values)
        if stale_keys:
            self._session.execute(delete(SettingModel).where(SettingModel.key.in_(stale_keys)))
        for key, value in values.items():
            row = stored.get(key)
            if row is None:
                self._session.add(SettingModel(key=key, value=value, updated_at=now))
            else:
                row.value = value
                row.updated_at = now


class SQLAlchemySourceAccountRepository:
    """SQLAlchemy source-account adapter that accepts ciphertext only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_credentials(
        self,
        *,
        source_id: str,
        account_key: str,
        encrypted_credentials: EncryptedCredential,
    ) -> UUID:
        if not isinstance(encrypted_credentials, EncryptedCredential):
            raise TypeError("source credentials must be produced by CredentialCipher")
        query = select(SourceAccountModel).where(
            SourceAccountModel.source_id == source_id,
            SourceAccountModel.account_key == account_key,
        )
        row = self._session.scalar(query)
        now = _utc_now()
        if row is None:
            account_id = uuid5(_SOURCE_ACCOUNT_NAMESPACE, f"{source_id}\0{account_key}")
            row = SourceAccountModel(
                id=str(account_id),
                source_id=source_id,
                account_key=account_key,
                encrypted_credentials=str(encrypted_credentials),
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            account_id = UUID(row.id)
            row.encrypted_credentials = str(encrypted_credentials)
            row.updated_at = now
        return account_id


class SQLAlchemyActivityRepository:
    """SQLAlchemy activity adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: ActivityEvent) -> None:
        self._session.add(
            ActivityEventModel(
                id=str(event.id),
                source_id=event.source_id,
                account_id=str(event.account_id) if event.account_id else None,
                kind=event.kind.value,
                occurred_at=event.occurred_at,
                content_external_id=event.content_external_id,
                url=str(event.url) if event.url else None,
                title=event.title,
                text=event.text,
                duration_seconds=event.duration_seconds,
                event_metadata=_json_metadata(event),
            )
        )

    def add_if_absent(self, event: ActivityEvent) -> bool:
        if self._session.get(ActivityEventModel, str(event.id)) is not None:
            return False
        self.add(event)
        return True

    def list_all(self) -> tuple[ActivityEvent, ...]:
        rows = self._session.scalars(
            select(ActivityEventModel).order_by(
                ActivityEventModel.occurred_at, ActivityEventModel.id
            )
        ).all()
        return tuple(_activity_from_row(row) for row in rows)


def _activity_from_row(row: ActivityEventModel) -> ActivityEvent:
    from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind

    occurred_at = _aware(row.occurred_at)
    assert occurred_at is not None
    return ActivityEvent(
        id=UUID(row.id),
        source_id=row.source_id,
        account_id=UUID(row.account_id) if row.account_id else None,
        kind=ActivityKind(row.kind),
        occurred_at=occurred_at,
        content_external_id=row.content_external_id,
        url=HttpUrl(row.url) if row.url else None,
        title=row.title,
        text=row.text,
        duration_seconds=row.duration_seconds,
        metadata=row.event_metadata,
    )


class SQLAlchemyProfileRepository:
    """SQLAlchemy profile adapter with explicit optimistic revision checks."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def latest(self) -> ProfileSnapshot | None:
        row = self._session.scalar(
            select(ProfileRevisionModel).order_by(ProfileRevisionModel.revision.desc()).limit(1)
        )
        return None if row is None else _profile_from_row(row)

    def append(self, snapshot: ProfileSnapshot, expected_revision: int | None) -> None:
        current = self.latest()
        actual_revision = None if current is None else current.revision
        if actual_revision != expected_revision:
            raise ProfileRevisionConflict(
                f"expected revision {expected_revision}, found {actual_revision}"
            )
        required_revision = 0 if expected_revision is None else expected_revision + 1
        if snapshot.revision != required_revision:
            raise ProfileRevisionConflict(
                f"next profile revision must be {required_revision}, got {snapshot.revision}"
            )
        if current is not None and snapshot.id != current.id:
            raise ProfileRevisionConflict("profile identity cannot change between revisions")

        facets = [facet.model_dump(mode="json") for facet in snapshot.facets]
        revision_row = ProfileRevisionModel(
            profile_id=str(snapshot.id),
            revision=snapshot.revision,
            narrative=snapshot.narrative,
            facets=facets,
            confidence=snapshot.confidence,
            created_at=snapshot.created_at,
        )
        self._session.add(revision_row)
        # These mappings deliberately avoid ORM relationships. Flush the parent rows before
        # adding evidence links so SQLite foreign keys remain valid within one transaction.
        try:
            self._session.flush()
        except IntegrityError as error:
            raise ProfileRevisionConflict(
                f"profile revision {snapshot.revision} was written concurrently"
            ) from error
        except OperationalError as error:
            if "locked" not in str(error).casefold():
                raise
            raise ProfileRevisionConflict(
                f"profile revision {snapshot.revision} was written concurrently"
            ) from error
        for facet in snapshot.facets:
            for evidence_id in dict.fromkeys(facet.evidence_ids):
                self._session.add(
                    ProfileEvidenceModel(
                        profile_id=str(snapshot.id),
                        profile_revision=snapshot.revision,
                        facet_name=facet.name,
                        facet_value=facet.value,
                        activity_event_id=str(evidence_id),
                    )
                )

    def consumed_evidence_ids(self) -> frozenset[UUID]:
        return frozenset(
            UUID(value)
            for value in self._session.scalars(
                select(ProfileConsumedEvidenceModel.activity_event_id)
            ).all()
        )

    def mark_evidence_consumed(
        self, evidence_ids: frozenset[UUID], *, profile_revision: int
    ) -> None:
        now = _utc_now()
        existing = self.consumed_evidence_ids()
        for evidence_id in sorted(evidence_ids - existing, key=str):
            self._session.add(
                ProfileConsumedEvidenceModel(
                    activity_event_id=str(evidence_id),
                    profile_revision=profile_revision,
                    consumed_at=now,
                )
            )


def _profile_from_row(row: ProfileRevisionModel) -> ProfileSnapshot:
    created_at = _aware(row.created_at)
    assert created_at is not None
    return ProfileSnapshot(
        id=UUID(row.profile_id),
        revision=row.revision,
        narrative=row.narrative,
        facets=tuple(ProfileFacet.model_validate(facet) for facet in row.facets),
        confidence=row.confidence,
        created_at=created_at,
    )


class SQLAlchemyContentRepository:
    """SQLAlchemy content adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, item: ContentItem) -> None:
        self._session.add(
            ContentItemModel(
                id=str(item.id),
                source_id=item.source_id,
                external_id=item.external_id,
                url=str(item.url),
                title=item.title,
                summary=item.summary,
                creator=item.creator,
                published_at=item.published_at,
                media_type=item.media_type,
                content_metadata=_json_metadata(item),
            )
        )

    def flush(self) -> None:
        self._session.flush()

    def get_by_identity(self, source_id: str, external_id: str) -> ContentItem | None:
        row = self._session.scalar(
            select(ContentItemModel).where(
                ContentItemModel.source_id == source_id,
                ContentItemModel.external_id == external_id,
            )
        )
        return None if row is None else _content_from_row(row)


def _content_from_row(row: ContentItemModel) -> ContentItem:
    return ContentItem(
        id=UUID(row.id),
        source_id=row.source_id,
        external_id=row.external_id,
        url=HttpUrl(row.url),
        title=row.title,
        summary=row.summary,
        creator=row.creator,
        published_at=_aware(row.published_at),
        media_type=row.media_type,
        metadata=row.content_metadata,
    )


class SQLAlchemyAssessmentRepository:
    """SQLAlchemy candidate-assessment adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, assessment: CandidateAssessment) -> None:
        self._session.add(
            CandidateAssessmentModel(
                id=str(assessment.id),
                content_id=str(assessment.content_id),
                profile_revision=assessment.profile_revision,
                relevance=assessment.relevance,
                quality=assessment.quality,
                novelty=assessment.novelty,
                risk=assessment.risk,
                topics=list(assessment.topics),
                explanation=assessment.explanation,
            )
        )

    def excluded_content_ids(self, profile_revision: int) -> frozenset[UUID]:
        assessed = self._session.scalars(
            select(CandidateAssessmentModel.content_id).where(
                CandidateAssessmentModel.profile_revision == profile_revision
            )
        ).all()
        admitted = self._session.scalars(select(FeedEntryModel.content_id)).all()
        interacted = self._session.scalars(select(InteractionModel.content_id)).all()
        return frozenset(UUID(value) for value in {*assessed, *admitted, *interacted})

    def excluded_content_identities(self, profile_revision: int) -> frozenset[tuple[str, str]]:
        excluded = tuple(str(value) for value in self.excluded_content_ids(profile_revision))
        if not excluded:
            return frozenset()
        rows = self._session.execute(
            select(ContentItemModel.source_id, ContentItemModel.external_id).where(
                ContentItemModel.id.in_(excluded)
            )
        ).all()
        return frozenset((source_id, external_id) for source_id, external_id in rows)


class SQLAlchemyFeedRepository:
    """SQLAlchemy feed adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, entry: FeedEntry) -> None:
        self._session.add(
            FeedEntryModel(
                id=str(entry.id),
                content_id=str(entry.content_id),
                assessment_id=str(entry.assessment_id) if entry.assessment_id else None,
                position=entry.position,
                admitted_at=entry.admitted_at,
                explanation=entry.explanation,
            )
        )

    def unseen_count(self) -> int:
        seen = select(InteractionModel.content_id).where(
            InteractionModel.kind.in_(("impression", "open", "dismiss"))
        )
        return int(
            self._session.scalar(
                select(func.count(FeedEntryModel.id)).where(FeedEntryModel.content_id.not_in(seen))
            )
            or 0
        )

    def next_position(self) -> int:
        latest = self._session.scalar(select(func.max(FeedEntryModel.position)))
        return 0 if latest is None else int(latest) + 1


class SQLAlchemyInteractionRepository:
    """SQLAlchemy interaction adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, interaction: Interaction) -> None:
        self._session.add(
            InteractionModel(
                id=str(interaction.id),
                content_id=str(interaction.content_id),
                kind=interaction.kind.value,
                occurred_at=interaction.occurred_at,
                interaction_metadata=_json_metadata(interaction),
            )
        )

    def adjustment(self, content_id: UUID) -> float:
        kinds = self._session.scalars(
            select(InteractionModel.kind).where(InteractionModel.content_id == str(content_id))
        ).all()
        adjustment = 0.0
        for kind in kinds:
            if kind == "positive":
                adjustment += 0.2
            elif kind in {"negative", "dismiss"}:
                adjustment -= 0.5
        return max(-1.0, min(1.0, adjustment))


class SQLAlchemyCollectionRepository:
    """SQLAlchemy local-collection adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_predefined(self) -> tuple[CollectionRecord, ...]:
        rows: Sequence[CollectionModel] = self._session.scalars(
            select(CollectionModel).order_by(CollectionModel.slug)
        ).all()
        return tuple(
            CollectionRecord(id=UUID(row.id), slug=row.slug, display_name=row.display_name)
            for row in rows
        )

    def add(self, item: CollectionItem) -> None:
        collection = self._session.scalar(
            select(CollectionModel).where(CollectionModel.slug == item.collection.value)
        )
        if collection is None:
            raise LookupError(f"predefined collection {item.collection.value!r} is missing")
        self._session.add(
            CollectionItemModel(
                id=str(item.id),
                collection_id=collection.id,
                content_id=str(item.content_id),
                added_at=item.added_at,
                note=item.note,
            )
        )

    def remove(self, collection: CollectionKind, content_id: UUID) -> bool:
        collection_id = self._session.scalar(
            select(CollectionModel.id).where(CollectionModel.slug == collection.value)
        )
        if collection_id is None:
            raise LookupError(f"predefined collection {collection.value!r} is missing")
        result = self._session.execute(
            delete(CollectionItemModel).where(
                CollectionItemModel.collection_id == collection_id,
                CollectionItemModel.content_id == str(content_id),
            )
        )
        return bool(getattr(result, "rowcount", 0))


class SQLAlchemyChatRepository:
    """SQLAlchemy persisted-chat adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, turn: ChatTurn) -> None:
        self._session.add(
            ChatTurnModel(
                id=str(turn.id),
                conversation_id=str(turn.conversation_id),
                role=turn.role.value,
                content=turn.content,
                created_at=turn.created_at,
                ai_run_id=str(turn.ai_run_id) if turn.ai_run_id else None,
            )
        )


class SQLAlchemyJobRunRepository:
    """Low-level adapter reserved for the Task 20 job service."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _fresh_row(self, run_id: UUID) -> JobRunModel | None:
        return self._session.scalar(
            select(JobRunModel)
            .where(JobRunModel.id == str(run_id))
            .execution_options(populate_existing=True)
        )

    def add_pending(self, *, job_name: str, idempotency_key: str, priority: int) -> UUID:
        run_id = uuid4()
        now = _utc_now()
        self._session.add(
            JobRunModel(
                id=str(run_id),
                job_name=job_name,
                idempotency_key=idempotency_key,
                status="pending",
                priority=priority,
                progress=0.0,
                attempts=0,
                error=None,
                created_at=now,
                updated_at=now,
                started_at=None,
                finished_at=None,
                dispatched_at=None,
            )
        )
        return run_id

    def create_or_get(
        self, *, job_name: str, idempotency_key: str, priority: int
    ) -> tuple[UUID, bool]:
        existing = self._session.scalar(
            select(JobRunModel).where(JobRunModel.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return UUID(existing.id), False
        run_id = self.add_pending(
            job_name=job_name,
            idempotency_key=idempotency_key,
            priority=priority,
        )
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            existing = self._session.scalar(
                select(JobRunModel).where(JobRunModel.idempotency_key == idempotency_key)
            )
            if existing is None:
                raise
            return UUID(existing.id), False
        return run_id, True

    def get(self, run_id: UUID) -> JobRunSnapshot:
        row = self._fresh_row(run_id)
        if row is None:
            raise LookupError(f"job run does not exist: {run_id}")
        return _job_snapshot(row)

    def claim(self, run_id: UUID) -> bool:
        now = _utc_now()
        result = self._session.execute(
            update(JobRunModel)
            .where(
                JobRunModel.id == str(run_id),
                JobRunModel.status == JobRunStatus.PENDING.value,
            )
            .values(
                status=JobRunStatus.RUNNING.value,
                attempts=JobRunModel.attempts + 1,
                error=None,
                started_at=now,
                updated_at=now,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    def mark_dispatched(self, run_id: UUID) -> None:
        self._session.execute(
            update(JobRunModel)
            .where(
                JobRunModel.id == str(run_id),
                JobRunModel.status == JobRunStatus.PENDING.value,
                JobRunModel.dispatched_at.is_(None),
            )
            .values(dispatched_at=_utc_now(), updated_at=_utc_now())
        )

    def pending_undispatched(self) -> tuple[UUID, ...]:
        rows = self._session.scalars(
            select(JobRunModel)
            .where(
                JobRunModel.status == JobRunStatus.PENDING.value,
                JobRunModel.dispatched_at.is_(None),
            )
            .order_by(JobRunModel.created_at, JobRunModel.id)
        ).all()
        return tuple(UUID(row.id) for row in rows)

    def pending(self) -> tuple[UUID, ...]:
        rows = self._session.scalars(
            select(JobRunModel)
            .where(JobRunModel.status == JobRunStatus.PENDING.value)
            .order_by(JobRunModel.created_at, JobRunModel.id)
        ).all()
        return tuple(UUID(row.id) for row in rows)

    def guard_running(self, run_id: UUID) -> bool:
        result = self._session.execute(
            update(JobRunModel)
            .where(
                JobRunModel.id == str(run_id),
                JobRunModel.status == JobRunStatus.RUNNING.value,
            )
            .values(updated_at=JobRunModel.updated_at)
        )
        return bool(getattr(result, "rowcount", 0))

    def checkpoint(self, run_id: UUID, progress: float) -> bool:
        resolved_progress = max(0.0, min(1.0, progress))
        result = self._session.execute(
            update(JobRunModel)
            .where(
                JobRunModel.id == str(run_id),
                JobRunModel.status == JobRunStatus.RUNNING.value,
            )
            .values(
                progress=func.max(JobRunModel.progress, resolved_progress),
                updated_at=_utc_now(),
            )
        )
        if getattr(result, "rowcount", 0):
            return True
        if self._fresh_row(run_id) is None:
            raise LookupError(f"job run does not exist: {run_id}")
        return False

    def update(
        self,
        run_id: UUID,
        *,
        status: JobRunStatus,
        progress: float,
        error: str | None = None,
    ) -> None:
        now = _utc_now()
        values: dict[str, object] = {
            "status": status.value,
            "progress": max(0.0, min(1.0, progress)),
            "error": error,
            "updated_at": now,
            "finished_at": (
                now
                if status in {JobRunStatus.SUCCEEDED, JobRunStatus.FAILED, JobRunStatus.CANCELLED}
                else None
            ),
        }
        if status is JobRunStatus.PENDING:
            values["dispatched_at"] = None
        result = self._session.execute(
            update(JobRunModel)
            .where(
                JobRunModel.id == str(run_id),
                JobRunModel.status != JobRunStatus.CANCELLED.value,
            )
            .values(**values)
        )
        if getattr(result, "rowcount", 0):
            return
        if self._fresh_row(run_id) is None:
            raise LookupError(f"job run does not exist: {run_id}")

    def cancel(self, run_id: UUID) -> bool:
        now = _utc_now()
        result = self._session.execute(
            update(JobRunModel)
            .where(
                JobRunModel.id == str(run_id),
                JobRunModel.status.in_((JobRunStatus.PENDING.value, JobRunStatus.RUNNING.value)),
            )
            .values(
                status=JobRunStatus.CANCELLED.value,
                progress=JobRunModel.progress,
                updated_at=now,
                finished_at=now,
            )
        )
        if getattr(result, "rowcount", 0):
            return True
        row = self._fresh_row(run_id)
        if row is None:
            raise LookupError(f"job run does not exist: {run_id}")
        return row.status == JobRunStatus.CANCELLED.value

    def recover_running(self) -> tuple[UUID, ...]:
        now = _utc_now()
        rows = self._session.scalars(
            update(JobRunModel)
            .where(JobRunModel.status == JobRunStatus.RUNNING.value)
            .values(
                status=JobRunStatus.PENDING.value,
                error="WorkerInterrupted",
                updated_at=now,
                started_at=None,
                dispatched_at=None,
            )
            .returning(JobRunModel.id)
        ).all()
        return tuple(UUID(row_id) for row_id in sorted(rows))

    def cleanup_finished(self, *, older_than: datetime) -> int:
        result = self._session.execute(
            delete(JobRunModel).where(
                JobRunModel.status.in_(
                    (
                        JobRunStatus.SUCCEEDED.value,
                        JobRunStatus.FAILED.value,
                        JobRunStatus.CANCELLED.value,
                    )
                ),
                JobRunModel.finished_at < older_than,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)


def _job_snapshot(row: JobRunModel) -> JobRunSnapshot:
    created_at = _aware(row.created_at)
    updated_at = _aware(row.updated_at)
    assert created_at is not None and updated_at is not None
    return JobRunSnapshot(
        id=UUID(row.id),
        job_name=row.job_name,
        idempotency_key=row.idempotency_key,
        status=JobRunStatus(row.status),
        priority=row.priority,
        progress=row.progress,
        attempts=row.attempts,
        error=row.error,
        created_at=created_at,
        updated_at=updated_at,
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        dispatched_at=_aware(row.dispatched_at),
    )


class SQLAlchemyAIRunRepository:
    """Persist AI outcomes without prompts, inputs, or provider credentials."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_started(self, *, task_name: str, model_alias: str) -> UUID:
        """Backward-compatible name for creating a running record."""

        return self.start(task_name=task_name, model_alias=model_alias)

    def start(self, *, task_name: str, model_alias: str) -> UUID:
        """Create the metadata-only start of an AI run."""

        run_id = uuid4()
        self._session.add(
            AIRunModel(
                id=str(run_id),
                task_name=task_name,
                model_alias=model_alias,
                status="running",
                usage=None,
                error=None,
                started_at=_utc_now(),
                finished_at=None,
            )
        )
        return run_id

    def succeed(
        self,
        run_id: UUID,
        *,
        usage: dict[str, int],
    ) -> None:
        """Finish a run with provider-neutral usage counters only."""

        row = self._require_running(run_id)
        row.status = "succeeded"
        row.usage = usage
        row.error = None
        row.finished_at = _utc_now()

    def fail(self, run_id: UUID, *, error_kind: str) -> None:
        """Finish a run with a classification, never a raw exception message."""

        row = self._require_running(run_id)
        row.status = "failed"
        row.usage = None
        row.error = (
            error_kind if error_kind.isidentifier() and len(error_kind) <= 100 else "AIError"
        )
        row.finished_at = _utc_now()

    def _require_running(self, run_id: UUID) -> AIRunModel:
        row = self._session.get(AIRunModel, str(run_id))
        if row is None:
            raise LookupError(f"AI run does not exist: {run_id}")
        if row.status != "running":
            raise RuntimeError(f"AI run is already finished: {run_id}")
        return row
