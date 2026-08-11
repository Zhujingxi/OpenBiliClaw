"""Bounded history primitives and model-visible secret audit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ToolReturnPart,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_CREDENTIAL_REFERENCE_RE = re.compile(r"\bcred_[0-9a-f]{32}\b", re.IGNORECASE)
_SECRET_MARKERS = (
    "vault:",
    "authorization:",
    '"authorization":',
    "'authorization':",
    "api_key=",
    '"api_key":',
    "'api_key':",
    "password=",
    '"password":',
    "'password':",
    "cookie=",
    "cookie:",
    '"cookie":',
    "'cookie':",
)


class MessageAuditError(ValueError):
    """Model-visible content contains a credential reference or secret marker."""


class ToolResultTooLargeError(ValueError):
    """A tool result exceeds the bounded history policy."""


@dataclass(frozen=True, slots=True)
class ContextProjection:
    """A volatile, already-bounded domain context projection."""

    label: str
    text: str
    max_bytes: int

    def __post_init__(self) -> None:
        if not self.label or self.max_bytes < 1:
            raise ValueError("projection label and positive limit are required")
        audit_text(self.text)
        if len(self.text.encode("utf-8")) > self.max_bytes:
            raise ValueError(f"context projection {self.label} exceeds its byte limit")


@dataclass(frozen=True, slots=True)
class HistoryPolicy:
    """Byte bounds for prior messages and individual tool results."""

    max_bytes: int
    max_tool_result_bytes: int

    def __post_init__(self) -> None:
        if self.max_bytes < 1 or self.max_tool_result_bytes < 1:
            raise ValueError("history limits must be positive")


def audit_text(text: str) -> None:
    """Reject known secret-reference forms before model execution."""

    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS) or _CREDENTIAL_REFERENCE_RE.search(
        text
    ):
        raise MessageAuditError("model-visible content contains a forbidden secret marker")


def audit_model_messages(messages: Sequence[ModelMessage]) -> None:
    """Audit the complete serialized model message sequence."""

    audit_text(ModelMessagesTypeAdapter.dump_json(list(messages)).decode("utf-8"))


def history_size(messages: Sequence[ModelMessage]) -> int:
    """Return deterministic serialized history size in bytes."""

    return len(ModelMessagesTypeAdapter.dump_json(list(messages)))


def audit_tool_result(tool_name: str, content: object, limit: int) -> None:
    """Reject a native tool result before PydanticAI makes its next request."""

    text = str(content)
    audit_text(text)
    if len(text.encode("utf-8")) > limit:
        raise ToolResultTooLargeError(f"tool result {tool_name} exceeds {limit} bytes")


def audit_tool_results(messages: Sequence[ModelMessage], limit: int) -> None:
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                audit_tool_result(part.tool_name, part.content, limit)


def _turns(messages: Sequence[ModelMessage]) -> tuple[tuple[ModelMessage, ...], ...]:
    turns: list[list[ModelMessage]] = []
    for message in messages:
        if isinstance(message, ModelRequest) or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return tuple(tuple(turn) for turn in turns)


def trim_history(
    messages: Sequence[ModelMessage], policy: HistoryPolicy
) -> tuple[ModelMessage, ...]:
    """Keep the newest complete turns that fit; never summarize here."""

    audit_tool_results(messages, policy.max_tool_result_bytes)
    audit_model_messages(messages)
    kept: tuple[ModelMessage, ...] = ()
    for turn in reversed(_turns(messages)):
        candidate = turn + kept
        if history_size(candidate) > policy.max_bytes:
            break
        kept = candidate
    return kept
