"""Model-free feed facade over selected recommendation records."""

from .models import SelectionRecord
from .repositories import RecommendationRepository


class RecommendationService:
    def __init__(self, repository: RecommendationRepository) -> None:
        self.repository = repository

    async def feed(self, *, limit: int = 20) -> tuple[SelectionRecord, ...]:
        return await self.repository.feed(limit=limit)
