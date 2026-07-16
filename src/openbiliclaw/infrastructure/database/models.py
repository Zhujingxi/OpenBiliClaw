"""SQLAlchemy mappings for the fresh vNext schema."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLAlchemy resolves mapped annotations at runtime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from openbiliclaw.infrastructure.database.base import Base

JsonObject = dict[str, Any]


class SettingModel(Base):
    """One typed user-facing setting serialized as JSON."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceAccountModel(Base):
    """A source account whose opaque credentials are always encrypted."""

    __tablename__ = "source_accounts"
    __table_args__ = (UniqueConstraint("source_id", "account_key", name="source_account_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    account_key: Mapped[str] = mapped_column(String(200), nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActivityEventModel(Base):
    """Normalized immutable activity evidence."""

    __tablename__ = "activity_events"
    __table_args__ = (Index("activity_source_occurred", "source_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_accounts.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_metadata: Mapped[JsonObject] = mapped_column("metadata", JSON, nullable=False)


class ProfileRevisionModel(Base):
    """An immutable evidence-profile revision."""

    __tablename__ = "profile_revisions"
    __table_args__ = (UniqueConstraint("revision", name="profile_revision_number"),)

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    facets: Mapped[list[JsonObject]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProfileEvidenceModel(Base):
    """Relational evidence links for facets in a profile revision."""

    __tablename__ = "profile_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "profile_revision"],
            ["profile_revisions.profile_id", "profile_revisions.revision"],
            ondelete="CASCADE",
        ),
    )

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    facet_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    facet_value: Mapped[str] = mapped_column(String(500), primary_key=True)
    activity_event_id: Mapped[str] = mapped_column(
        ForeignKey("activity_events.id", ondelete="RESTRICT"), primary_key=True
    )


class ContentItemModel(Base):
    """Source-neutral content with a unique source/external identity."""

    __tablename__ = "content_items"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="content_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    creator: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content_metadata: Mapped[JsonObject] = mapped_column("metadata", JSON, nullable=False)


class CandidateAssessmentModel(Base):
    """A profile-revision-relative assessment of content."""

    __tablename__ = "candidate_assessments"
    __table_args__ = (
        UniqueConstraint("content_id", "profile_revision", name="assessment_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_revision: Mapped[int] = mapped_column(
        ForeignKey("profile_revisions.revision", ondelete="RESTRICT"), nullable=False
    )
    relevance: Mapped[float] = mapped_column(Float, nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)
    novelty: Mapped[float] = mapped_column(Float, nullable=False)
    risk: Mapped[float] = mapped_column(Float, nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


class FeedEntryModel(Base):
    """One ordered feed admission."""

    __tablename__ = "feed_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_assessments.id", ondelete="SET NULL"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


class InteractionModel(Base):
    """An immutable user interaction with content."""

    __tablename__ = "interactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interaction_metadata: Mapped[JsonObject] = mapped_column("metadata", JSON, nullable=False)


class CollectionModel(Base):
    """A predefined local-only collection."""

    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)


class CollectionItemModel(Base):
    """Content membership in a local collection."""

    __tablename__ = "collection_items"
    __table_args__ = (
        UniqueConstraint("collection_id", "content_id", name="collection_content_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collection_id: Mapped[str] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    content_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)


class ChatTurnModel(Base):
    """One persisted chat turn."""

    __tablename__ = "chat_turns"
    __table_args__ = (Index("chat_conversation_created", "conversation_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ai_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True
    )


class SourceTaskModel(Base):
    """Durable generic work claimed by a source transport."""

    __tablename__ = "source_tasks"
    __table_args__ = (Index("source_task_claim", "source_id", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    request_payload: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    result_payload: Mapped[JsonObject | None] = mapped_column(JSON, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobRunModel(Base):
    """Application-owned durable status for one background job run."""

    __tablename__ = "job_runs"
    __table_args__ = (Index("job_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIRunModel(Base):
    """Auditable semantic AI task execution, without provider credentials."""

    __tablename__ = "ai_runs"
    __table_args__ = (Index("ai_task_started", "task_name", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    input_payload: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    output_payload: Mapped[JsonObject | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[JsonObject | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
