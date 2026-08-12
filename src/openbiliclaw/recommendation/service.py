"""Model-free feed facade over selected recommendation records."""

from .models import RecommendationFeedItem
from .repositories import RecommendationRepository


class RecommendationService:
    def __init__(self, repository: RecommendationRepository) -> None:
        self.repository = repository

    async def feed(self, *, limit: int = 20) -> tuple[RecommendationFeedItem, ...]:
        return await self.repository.feed(limit=limit)
