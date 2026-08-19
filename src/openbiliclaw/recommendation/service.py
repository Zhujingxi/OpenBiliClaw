"""Model-free feed facade over selected recommendation records."""

from datetime import datetime  # noqa: TC003  # Runtime method annotation.

from .models import CandidateInventorySummary, RecommendationFeedItem
from .repositories import RecommendationRepository


class RecommendationService:
    def __init__(self, repository: RecommendationRepository) -> None:
        self.repository = repository

    async def inventory_summary(self) -> CandidateInventorySummary:
        return await self.repository.inventory_summary()

    async def deliver_feed(
        self, *, limit: int = 20, shown_at: datetime
    ) -> tuple[RecommendationFeedItem, ...]:
        return await self.repository.deliver_feed(limit=limit, shown_at=shown_at)
