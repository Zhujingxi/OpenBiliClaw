"""Concrete repository assembly; each repository remains domain-owned."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openbiliclaw.assistant.repository import SqliteConversationRepository
from openbiliclaw.observations.repository import SqliteObservationRepository
from openbiliclaw.recommendation.repositories import SqliteRecommendationRepository
from openbiliclaw.understanding.repository import SqliteUnderstandingRepository

if TYPE_CHECKING:
    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase


@dataclass(frozen=True, slots=True)
class RepositoryGraph:
    observations: SqliteObservationRepository
    understanding: SqliteUnderstandingRepository
    recommendations: SqliteRecommendationRepository
    conversations: SqliteConversationRepository


def build_repositories(database: SqliteDatabase) -> RepositoryGraph:
    """Construct adapters without opening a database connection."""
    return RepositoryGraph(
        observations=SqliteObservationRepository(database),
        understanding=SqliteUnderstandingRepository(database),
        recommendations=SqliteRecommendationRepository(database),
        conversations=SqliteConversationRepository(database),
    )
