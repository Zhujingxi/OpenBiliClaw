"""Repository ports and synchronous SQLAlchemy adapters for vNext features."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import HttpUrl
from sqlalchemy import delete, select
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
    ProfileEvidenceModel,
    ProfileRevisionModel,
    SettingModel,
    SourceAccountModel,
    SourceTaskModel,
)
from openbiliclaw.infrastructure.security.credentials import EncryptedCredential

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session

    from openbiliclaw.features.activity.domain import ActivityEvent
    from openbiliclaw.features.chat.domain import ChatTurn
    from openbiliclaw.features.feed.domain import CandidateAssessment, FeedEntry, Interaction
    from openbiliclaw.features.library.domain import CollectionItem
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


class ProfileRepository(Protocol):
    """Persistence port for immutable profile revisions."""

    def latest(self) -> ProfileSnapshot | None: ...

    def append(self, snapshot: ProfileSnapshot, expected_revision: int | None) -> None: ...


class ContentRepository(Protocol):
    """Persistence port for normalized content."""

    def add(self, item: ContentItem) -> None: ...

    def get_by_identity(self, source_id: str, external_id: str) -> ContentItem | None: ...


class AssessmentRepository(Protocol):
    """Persistence port for profile-relative candidate assessments."""

    def add(self, assessment: CandidateAssessment) -> None: ...


class FeedRepository(Protocol):
    """Persistence port for ordered feed entries."""

    def add(self, entry: FeedEntry) -> None: ...


class InteractionRepository(Protocol):
    """Persistence port for immutable user interactions."""

    def add(self, interaction: Interaction) -> None: ...


class CollectionRepository(Protocol):
    """Persistence port for local-only collections."""

    def list_predefined(self) -> tuple[CollectionRecord, ...]: ...

    def add(self, item: CollectionItem) -> None: ...


class ChatRepository(Protocol):
    """Persistence port for chat turns."""

    def add(self, turn: ChatTurn) -> None: ...


class SourceTaskRepository(Protocol):
    """Persistence port on which Task 19 builds lease-safe source work."""

    def add_pending(
        self, *, source_id: str, operation: str, payload: dict[str, object]
    ) -> UUID: ...


class JobRunRepository(Protocol):
    """Persistence port on which Task 20 builds durable job state."""

    def add_pending(self, *, job_name: str, idempotency_key: str, priority: int) -> UUID: ...


class AIRunRepository(Protocol):
    """Persistence port on which Task 18 builds auditable typed AI runs."""

    def add_started(self, *, task_name: str, model_alias: str) -> UUID: ...


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


class SQLAlchemySourceTaskRepository:
    """Low-level adapter reserved for the Task 19 source-task service."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_pending(self, *, source_id: str, operation: str, payload: dict[str, object]) -> UUID:
        task_id = uuid4()
        now = _utc_now()
        self._session.add(
            SourceTaskModel(
                id=str(task_id),
                source_id=source_id,
                operation=operation,
                status="pending",
                request_payload=payload,
                result_payload=None,
                lease_token=None,
                lease_expires_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        return task_id


class SQLAlchemyJobRunRepository:
    """Low-level adapter reserved for the Task 20 job service."""

    def __init__(self, session: Session) -> None:
        self._session = session

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
                error=None,
                created_at=now,
                updated_at=now,
            )
        )
        return run_id


class SQLAlchemyAIRunRepository:
    """Low-level adapter reserved for the Task 18 typed AI runner."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_started(self, *, task_name: str, model_alias: str) -> UUID:
        run_id = uuid4()
        self._session.add(
            AIRunModel(
                id=str(run_id),
                task_name=task_name,
                model_alias=model_alias,
                status="running",
                output_payload=None,
                usage=None,
                error=None,
                started_at=_utc_now(),
                finished_at=None,
            )
        )
        return run_id
