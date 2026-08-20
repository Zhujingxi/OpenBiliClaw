from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from openbiliclaw.assistant.history import select_history
from openbiliclaw.assistant.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationScope,
    ToolCallSummary,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _message(index: int, role: ConversationRole, content: str) -> ConversationMessage:
    return ConversationMessage(
        message_id=f"msg_{index:032x}",
        role=role,
        content=content,
        created_at=NOW + timedelta(seconds=index),
        idempotency_key=f"turn:{index:08d}",
    )


def _turn(index: int, text: str) -> tuple[ConversationMessage, ConversationMessage]:
    return (
        _message(index * 2, ConversationRole.USER, text),
        _message(
            index * 2 + 1,
            ConversationRole.ASSISTANT,
            f'{{"kind":"message","text":"reply {text}"}}',
        ),
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
        _message(1, ConversationRole.USER, "authorization: CANARY")
    with pytest.raises(ValueError):
        _message(1, ConversationRole.USER, "x" * 16_001)


def test_full_window_keeps_all_complete_turns_when_they_fit() -> None:
    messages = (*_turn(0, "first"), *_turn(1, "second"))
    selected = select_history(
        messages,
        context_window_tokens=1_000,
        base_input_tokens=100,
        input_tokens_limit=800,
    )

    assert len(selected.messages) == 4
    assert selected.meter.excluded_oldest_turns == 0
    assert selected.meter.approximate_usage_percent > 0


def test_capacity_excludes_only_oldest_complete_turns() -> None:
    messages = (*_turn(0, "x" * 200), *_turn(1, "short"), *_turn(2, "newest"))
    selected = select_history(
        messages,
        context_window_tokens=500,
        base_input_tokens=0,
        input_tokens_limit=400,
    )

    assert selected.meter.excluded_oldest_turns == 1
    assert "short" in str(selected.messages[0])
    assert "newest" in str(selected.messages[-1])
    assert "x" * 200 not in str(selected.messages)


def test_incomplete_turns_and_tool_payloads_never_enter_model_history() -> None:
    user, assistant = _turn(0, "safe")
    assistant = assistant.model_copy(
        update={
            "tool_calls": (
                ToolCallSummary(
                    tool_name="search_content",
                    outcome="succeeded",
                    safe_summary="Found readable matches",
                ),
            )
        }
    )
    orphan = _message(2, ConversationRole.USER, "unfinished")
    tool = _message(3, ConversationRole.TOOL, "internal payload")
    selected = select_history(
        (user, assistant, orphan, tool),
        context_window_tokens=1_000,
        base_input_tokens=0,
        input_tokens_limit=800,
    )

    text = str(selected.messages)
    assert "Found readable matches" in text
    assert "internal payload" not in text
    assert "unfinished" not in text
    assert selected.meter.excluded_oldest_turns == 0


def test_context_policy_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="context window"):
        select_history((), context_window_tokens=0, base_input_tokens=0, input_tokens_limit=1)
