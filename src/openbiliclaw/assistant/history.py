"""Bounded Assistant history and fact-preserving compaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ConversationMessage, ConversationSummary, PendingActionSummary


@dataclass(frozen=True, slots=True)
class HistoryPolicy:
    max_messages: int = 20
    max_chars: int = 16_000

    def __post_init__(self) -> None:
        if self.max_messages < 0 or self.max_chars < 1:
            raise ValueError("history limits must be non-negative messages and positive chars")


@dataclass(frozen=True, slots=True)
class CompactedHistory:
    messages: tuple[ConversationMessage, ...]
    summary: ConversationSummary
    compacted: bool


def compact_history(
    messages: tuple[ConversationMessage, ...],
    *,
    previous_summary: ConversationSummary,
    unresolved_actions: tuple[PendingActionSummary, ...],
    policy: HistoryPolicy,
    proposed_summary: ConversationSummary | None = None,
) -> CompactedHistory:
    """Compact only over bounds; never let a summary invent confirmed facts."""

    total_chars = sum(len(item.content) for item in messages)
    if len(messages) <= policy.max_messages and total_chars <= policy.max_chars:
        return CompactedHistory(messages, previous_summary, False)
    if proposed_summary is not None and not set(proposed_summary.confirmed_facts) <= set(
        previous_summary.confirmed_facts
    ):
        raise ValueError("compaction summary cannot introduce confirmed facts")
    kept: list[ConversationMessage] = []
    used = 0
    for message in reversed(messages):
        if len(kept) >= policy.max_messages or used + len(message.content) > policy.max_chars:
            continue
        kept.append(message)
        used += len(message.content)
    corrections = tuple(item.content for item in messages if item.user_correction)
    references = tuple(dict.fromkeys(ref for item in messages for ref in item.references))
    base = proposed_summary or previous_summary
    summary = base.model_copy(
        update={
            "user_corrections": tuple(dict.fromkeys((*base.user_corrections, *corrections))),
            "references": tuple(dict.fromkeys((*base.references, *references))),
            "unresolved_actions": unresolved_actions,
        }
    )
    return CompactedHistory(tuple(reversed(kept)), summary, True)
