"""Bounded recommendation refresh admission through Core supervision."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.core.jobs import JobDecision


class RefreshSupervisor(Protocol):
    def trigger(self, job_id: str, *, maximum_items: int) -> JobDecision: ...


class RefreshRecommendationsCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    maximum_items: int = Field(default=50, ge=1, le=100)


class RefreshRecommendationsResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: JobDecision


@dataclass(frozen=True, slots=True)
class RefreshRecommendations:
    supervisor: RefreshSupervisor

    async def __call__(
        self, command: RefreshRecommendationsCommand
    ) -> RefreshRecommendationsResult:
        # The workflow never creates an unmanaged task; it only admits bounded
        # work through the supervisor, which owns the replenishment job.
        return RefreshRecommendationsResult(
            decision=self.supervisor.trigger(
                "recommendation.replenishment", maximum_items=command.maximum_items
            )
        )
