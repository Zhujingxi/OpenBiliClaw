"""Generic typed task specifications for PydanticAI execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from pydantic_ai import Agent, UsageLimits

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)
GenerativeAlias = Literal["obc-interactive", "obc-analysis"]
ModelAlias = Literal["obc-interactive", "obc-analysis", "obc-embedding"]


class TaskLane(StrEnum):
    """Application latency lane, with one stable LiteLLM alias per lane."""

    INTERACTIVE = "interactive"
    ANALYSIS = "analysis"

    @property
    def model_alias(self) -> GenerativeAlias:
        """Return the only model alias permitted for this lane."""

        if self is TaskLane.INTERACTIVE:
            return "obc-interactive"
        return "obc-analysis"


class CachePolicy(StrEnum):
    """Semantic cache intent forwarded as task metadata in later composition tasks."""

    DEFAULT = "default"
    BYPASS = "bypass"


@dataclass(frozen=True, slots=True)
class TaskSpec(Generic[InputT, OutputT]):
    """Reusable, typed policy for one semantic PydanticAI task."""

    name: str
    input_type: type[InputT]
    output_type: type[OutputT]
    agent: Agent[None, OutputT]
    model_alias: GenerativeAlias
    semantic_retry_limit: int
    timeout_seconds: float
    usage_limits: UsageLimits
    cache_policy: CachePolicy
    lane: TaskLane

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("task name cannot be empty")
        if self.model_alias not in ("obc-interactive", "obc-analysis"):
            raise ValueError(f"unsupported generative model alias: {self.model_alias}")
        if self.model_alias != self.lane.model_alias:
            raise ValueError(f"{self.lane.value} lane requires model alias {self.lane.model_alias}")
        if self.semantic_retry_limit < 0:
            raise ValueError("semantic retry limit cannot be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("task timeout must be positive")
