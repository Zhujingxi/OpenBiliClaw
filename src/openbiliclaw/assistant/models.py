"""Typed Assistant conversation and output models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import AwareDatetime, ConfigDict, Field, field_validator

from openbiliclaw.ai.runtime.history import MessageAuditError, audit_text
from openbiliclaw.core._pydantic import StrictBaseModel


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ConversationScope(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    local_user_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)


class ToolCallSummary(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_name: str = Field(min_length=1, max_length=64)
    outcome: Literal["succeeded", "failed", "pending"]
    safe_summary: str = Field(max_length=1000)

    @field_validator("safe_summary")
    @classmethod
    def audit_summary(cls, value: str) -> str:
        audit_text(value)
        return value


class PendingActionSummary(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pending_action_id: str = Field(pattern=r"^pending_[0-9a-f]{32}$")
    effect: str = Field(min_length=1, max_length=500)
    expires_at: AwareDatetime

    @field_validator("effect")
    @classmethod
    def audit_effect(cls, value: str) -> str:
        audit_text(value)
        return value


class ContextMeter(StrictBaseModel):
    """Approximate model-window use for one Assistant turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    estimated_input_tokens: int = Field(ge=0)
    context_window_tokens: int = Field(gt=0)
    approximate_usage_percent: int = Field(ge=0, le=100)
    excluded_oldest_turns: int = Field(ge=0)


class TurnUsage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class TurnStarted(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["turn_started"] = "turn_started"
    context_meter: ContextMeter


class ReasoningStarted(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["reasoning_started"] = "reasoning_started"


class ReasoningDelta(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["reasoning_delta"] = "reasoning_delta"
    delta: str = Field(min_length=1, max_length=8000)

    @field_validator("delta")
    @classmethod
    def audit_delta(cls, value: str) -> str:
        audit_text(value)
        return value


class ReasoningFinished(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["reasoning_finished"] = "reasoning_finished"


class ToolStarted(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["tool_started"] = "tool_started"
    name: str = Field(min_length=1, max_length=64)


class ToolFinished(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["tool_finished"] = "tool_finished"
    name: str = Field(min_length=1, max_length=64)
    status: Literal["succeeded", "failed"]
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("summary")
    @classmethod
    def audit_summary(cls, value: str) -> str:
        audit_text(value)
        return value


class ResponseDelta(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["response_delta"] = "response_delta"
    delta: str = Field(min_length=1, max_length=8000)

    @field_validator("delta")
    @classmethod
    def audit_delta(cls, value: str) -> str:
        audit_text(value)
        return value


class ConversationMessage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    message_id: str = Field(pattern=r"^msg_[0-9a-f]{32}$")
    role: ConversationRole
    content: str = Field(max_length=16_000)
    created_at: AwareDatetime
    idempotency_key: str = Field(min_length=8, max_length=200)
    tool_calls: tuple[ToolCallSummary, ...] = Field(default=(), max_length=16)
    pending_action: PendingActionSummary | None = None
    usage: TurnUsage | None = None
    user_correction: bool = False
    references: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("content")
    @classmethod
    def audit_content(cls, value: str) -> str:
        try:
            audit_text(value)
        except MessageAuditError as exc:
            raise ValueError("message contains forbidden secret material") from exc
        return value


class ConversationSummary(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(max_length=8000)
    model_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    confirmed_facts: tuple[str, ...] = Field(default=(), max_length=100)
    user_corrections: tuple[str, ...] = Field(default=(), max_length=50)
    references: tuple[str, ...] = Field(default=(), max_length=100)
    unresolved_actions: tuple[PendingActionSummary, ...] = Field(default=(), max_length=25)

    @field_validator("text", "confirmed_facts", "user_corrections", "references")
    @classmethod
    def audit_fields(cls, value: str | tuple[str, ...]) -> str | tuple[str, ...]:
        values = (value,) if isinstance(value, str) else value
        for item in values:
            audit_text(item)
        return value


class Conversation(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    conversation_id: str = Field(pattern=r"^conv_[0-9a-f]{32}$")
    scope: ConversationScope
    created_at: AwareDatetime
    updated_at: AwareDatetime
    retention_days: int = Field(default=30, ge=1, le=365)
    summary: ConversationSummary | None = None


class AssistantMessage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["message"] = "message"
    text: str = Field(min_length=1, max_length=8000)


class AssistantRecommendationPresentation(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["recommendations"] = "recommendations"
    intro: str = Field(min_length=1, max_length=1000)
    recommendation_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class AssistantClarification(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["clarification"] = "clarification"
    question: str = Field(min_length=1, max_length=1000)
    choices: tuple[str, ...] = Field(default=(), max_length=10)


class AssistantPendingAction(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["pending_action"] = "pending_action"
    action: PendingActionSummary


AssistantOutput: TypeAlias = Annotated[
    AssistantMessage
    | AssistantRecommendationPresentation
    | AssistantClarification
    | AssistantPendingAction,
    Field(discriminator="kind"),
]


class TurnFinished(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["turn_finished"] = "turn_finished"
    output: AssistantOutput
    context_meter: ContextMeter
    usage: TurnUsage


class AssistantStreamError(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["error"] = "error"
    code: Literal["unavailable", "temporary_failure"]
    message: str = Field(min_length=1, max_length=500)


AssistantLifecycleEvent: TypeAlias = Annotated[
    TurnStarted
    | ReasoningStarted
    | ReasoningDelta
    | ReasoningFinished
    | ToolStarted
    | ToolFinished
    | ResponseDelta
    | TurnFinished
    | AssistantStreamError,
    Field(discriminator="kind"),
]
