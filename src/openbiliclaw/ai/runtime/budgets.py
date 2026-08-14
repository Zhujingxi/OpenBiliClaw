"""Per-run limits translated directly to PydanticAI usage limits."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from pydantic_ai.usage import UsageLimits

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.ai.runtime.capabilities import AgentId


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


_POLICY_OVERRIDABLE_FIELDS = frozenset(
    {
        "request_limit",
        "input_tokens_limit",
        "output_tokens_limit",
        "total_tokens_limit",
        "tool_calls_limit",
        "tool_result_bytes_limit",
        "timeout_seconds",
        "retries",
    }
)


@dataclass(frozen=True, slots=True)
class PolicyBook:
    """Config-driven per-agent RunPolicy overrides, resolved at the execution choke point.

    Code keeps named, validated defaults per agent; deployment config may tune them
    (``[runtime.agents.<agent-id>]``). Overrides are validated at construction, so
    a bad budget fails at startup, never mid-run.
    """

    overrides: Mapping[AgentId, RunPolicy]

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, Mapping[str, int | float]]) -> PolicyBook:
        """Build validated policies from raw per-agent field overrides."""

        from openbiliclaw.ai.runtime.capabilities import AgentId

        base = RunPolicy()
        book: dict[AgentId, RunPolicy] = {}
        for agent, fields in overrides.items():
            unknown = set(fields) - _POLICY_OVERRIDABLE_FIELDS
            if unknown:
                raise ValueError(f"unknown run policy fields for {agent}: {sorted(unknown)}")
            book[AgentId(agent)] = RunPolicy(
                request_limit=int(fields.get("request_limit", base.request_limit)),
                input_tokens_limit=int(fields.get("input_tokens_limit", base.input_tokens_limit)),
                output_tokens_limit=int(
                    fields.get("output_tokens_limit", base.output_tokens_limit)
                ),
                total_tokens_limit=int(fields.get("total_tokens_limit", base.total_tokens_limit)),
                tool_calls_limit=int(fields.get("tool_calls_limit", base.tool_calls_limit)),
                tool_result_bytes_limit=int(
                    fields.get("tool_result_bytes_limit", base.tool_result_bytes_limit)
                ),
                timeout_seconds=float(fields.get("timeout_seconds", base.timeout_seconds)),
                retries=int(fields.get("retries", base.retries)),
            )
        return cls(book)

    def resolve(self, agent_id: AgentId, default: RunPolicy) -> RunPolicy:
        """Return the configured override for an agent, or its code default."""

        return self.overrides.get(agent_id, default)
