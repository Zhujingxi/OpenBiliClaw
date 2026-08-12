from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from openbiliclaw.assistant.history import HistoryPolicy, compact_history
from openbiliclaw.assistant.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationScope,
    ConversationSummary,
    PendingActionSummary,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _message(index: int, content: str, *, correction: bool = False) -> ConversationMessage:
    return ConversationMessage(
        message_id=f"msg_{index:032x}",
        role=ConversationRole.USER,
        content=content,
        created_at=NOW + timedelta(seconds=index),
        idempotency_key=f"turn:{index:08d}",
        user_correction=correction,
        references=("bilibili:video:BV1",) if correction else (),
    )


def test_conversation_scope_and_secret_bounds() -> None:
    scope = ConversationScope(local_user_id="local", device_id="desktop")
    conversation = Conversation(
        conversation_id="conv_" + "a" * 32,
        scope=scope,
        created_at=NOW,
        updated_at=NOW,
        retention_days=30,
    )
    assert conversation.scope == scope
    with pytest.raises(ValueError, match="forbidden secret"):
        _message(1, "authorization: CANARY")
    with pytest.raises(ValueError):
        _message(1, "x" * 16_001)


def test_compaction_only_when_required_and_preserves_audit_state() -> None:
    unresolved = PendingActionSummary(
        pending_action_id="pending_" + "b" * 32,
        effect="save video",
        expires_at=NOW + timedelta(minutes=5),
    )
    summary = ConversationSummary(
        text="confirmed fact: likes science",
        model_id="summary-model",
        version=1,
        confirmed_facts=("likes science",),
    )
    messages = tuple(_message(i, f"message-{i}" * 30, correction=i == 1) for i in range(5))
    unchanged = compact_history(
        messages,
        previous_summary=summary,
        unresolved_actions=(unresolved,),
        policy=HistoryPolicy(max_messages=10, max_chars=10_000),
    )
    assert unchanged.messages == messages
    assert not unchanged.compacted

    compacted = compact_history(
        messages,
        previous_summary=summary,
        unresolved_actions=(unresolved,),
        policy=HistoryPolicy(max_messages=2, max_chars=500),
    )
    assert compacted.compacted
    assert len(compacted.messages) <= 2
    assert compacted.summary.confirmed_facts == ("likes science",)
    assert compacted.summary.user_corrections
    assert compacted.summary.references == ("bilibili:video:BV1",)
    assert compacted.summary.unresolved_actions == (unresolved,)


def test_history_policy_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="history limits"):
        HistoryPolicy(max_messages=-1, max_chars=1)


def test_summary_cannot_introduce_confirmed_facts() -> None:
    previous = ConversationSummary(
        text="safe",
        model_id="v1",
        version=1,
        confirmed_facts=("likes science",),
    )
    proposed = ConversationSummary(
        text="unsafe invention",
        model_id="v2",
        version=2,
        confirmed_facts=("likes science", "owns a yacht"),
    )
    with pytest.raises(ValueError, match="confirmed facts"):
        compact_history(
            (_message(1, "hello"),),
            previous_summary=previous,
            proposed_summary=proposed,
            unresolved_actions=(),
            policy=HistoryPolicy(max_messages=0, max_chars=1),
        )
