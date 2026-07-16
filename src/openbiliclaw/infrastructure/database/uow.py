"""Explicit synchronous transaction boundary for vNext repositories."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

    from sqlalchemy.orm import Session, sessionmaker

from openbiliclaw.infrastructure.database.repositories import (
    SQLAlchemyActivityRepository,
    SQLAlchemyAIRunRepository,
    SQLAlchemyAssessmentRepository,
    SQLAlchemyChatRepository,
    SQLAlchemyCollectionRepository,
    SQLAlchemyContentRepository,
    SQLAlchemyFeedRepository,
    SQLAlchemyInteractionRepository,
    SQLAlchemyJobRunRepository,
    SQLAlchemyProfileRepository,
    SQLAlchemySettingsRepository,
    SQLAlchemySourceAccountRepository,
)
from openbiliclaw.infrastructure.sources.browser_tasks import SQLAlchemyBrowserTaskRepository


class UnitOfWork:
    """Own one SQLAlchemy session and rollback unless explicitly committed."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session = session_factory()
        self.settings = SQLAlchemySettingsRepository(self.session)
        self.source_accounts = SQLAlchemySourceAccountRepository(self.session)
        self.activities = SQLAlchemyActivityRepository(self.session)
        self.profiles = SQLAlchemyProfileRepository(self.session)
        self.content = SQLAlchemyContentRepository(self.session)
        self.assessments = SQLAlchemyAssessmentRepository(self.session)
        self.feed = SQLAlchemyFeedRepository(self.session)
        self.interactions = SQLAlchemyInteractionRepository(self.session)
        self.collections = SQLAlchemyCollectionRepository(self.session)
        self.chat = SQLAlchemyChatRepository(self.session)
        self.source_tasks = SQLAlchemyBrowserTaskRepository(self.session)
        self.job_runs = SQLAlchemyJobRunRepository(self.session)
        self.ai_runs = SQLAlchemyAIRunRepository(self.session)

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.rollback()
        finally:
            self.session.close()

    def commit(self) -> None:
        """Atomically commit all repository changes."""

        self.session.commit()

    def rollback(self) -> None:
        """Discard all uncommitted repository changes."""

        self.session.rollback()
