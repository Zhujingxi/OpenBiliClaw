"""Per-run limits translated directly to PydanticAI usage limits."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from pydantic_ai.usage import UsageLimits


class RunPriority(IntEnum):
    """Admission priority; smaller values are served first by future queues."""

    INTERACTIVE = 0
    SCHEDULED = 10
    EVALUATION = 20
    MAINTENANCE = 30


@dataclass(frozen=True, slots=True)
class RunPolicy:
    """Hard limits for one model run."""

    request_limit: int = 3
    input_tokens_limit: int = 16_000
    output_tokens_limit: int = 4_096
    total_tokens_limit: int = 20_096
    tool_calls_limit: int = 8
    tool_result_bytes_limit: int = 65_536
    timeout_seconds: float = 60.0
    retries: int = 1
    priority: RunPriority = RunPriority.SCHEDULED

    def __post_init__(self) -> None:
        positive = (
            self.request_limit,
            self.input_tokens_limit,
            self.output_tokens_limit,
            self.total_tokens_limit,
            self.tool_calls_limit,
            self.tool_result_bytes_limit,
            self.timeout_seconds,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("run limits and timeout must be positive")
        if self.retries < 0:
            raise ValueError("retries must not be negative")

    def to_usage_limits(self) -> UsageLimits:
        """Build the native PydanticAI limits without a parallel counter."""

        return UsageLimits(
            request_limit=self.request_limit,
            input_tokens_limit=self.input_tokens_limit,
            output_tokens_limit=self.output_tokens_limit,
            total_tokens_limit=self.total_tokens_limit,
            tool_calls_limit=self.tool_calls_limit,
        )
