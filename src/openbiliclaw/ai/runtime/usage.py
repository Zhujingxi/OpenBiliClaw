"""Typed model-usage attribution and persistence port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pydantic_ai.usage import RunUsage

    from openbiliclaw.ai.runtime.capabilities import AgentId


@dataclass(frozen=True, slots=True)
class UsageAttribution:
    """Business dimensions attached to every model run."""

    agent_id: AgentId
    workflow: str
    model_instance: str
    provider: str
    recommendation_batch: str | None = None

    def __post_init__(self) -> None:
        if not self.workflow or not self.model_instance or not self.provider:
            raise ValueError("workflow, model instance, and provider must not be empty")


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Provider-independent counters captured after a run."""

    attribution: UsageAttribution
    requests: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    elapsed_seconds: float

    @classmethod
    def from_run_usage(
        cls, attribution: UsageAttribution, usage: RunUsage, elapsed_seconds: float
    ) -> UsageRecord:
        return cls(
            attribution=attribution,
            requests=usage.requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            tool_calls=usage.tool_calls,
            elapsed_seconds=elapsed_seconds,
        )


class UsageSink(Protocol):
    """Persistence port implemented by future owning repositories."""

    async def record(self, record: UsageRecord) -> None:
        """Persist one completed usage record."""
        ...
