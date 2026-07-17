"""Baseline snapshots for the Phase 1 dialogue window + regurgitation work.

Committed BEFORE the window/regurgitation/builder changes (plan Task 3, step
"基线先行"). These lock the ≤-window prompt bytes and the current
dialogue-insight extraction prompt so the later refactor is provably
behaviour-equivalent for in-window sessions.
"""

from __future__ import annotations

from openbiliclaw.soul.dialogue import DialogueTurn, SocraticDialogue


def _dialogue_with_history(exchanges: int) -> SocraticDialogue:
    dialogue = SocraticDialogue(llm=None, soul_engine=object(), session="popup")  # type: ignore[arg-type]
    for i in range(exchanges):
        dialogue._history.append(DialogueTurn(role="user", content=f"用户消息{i}"))
        dialogue._history.append(DialogueTurn(role="agent", content=f"助手回复{i}"))
    # An in-flight user turn is appended by respond() before _history_to_messages.
    dialogue._history.append(DialogueTurn(role="user", content="当前用户消息"))
    return dialogue


def test_history_to_messages_within_window_is_unchanged() -> None:
    # 20 exchanges = 40 prior messages, all retained (window == 20 exchanges).
    dialogue = _dialogue_with_history(20)
    messages = dialogue._history_to_messages()
    assert len(messages) == 40
    assert messages[0] == {"role": "user", "content": "用户消息0"}
    assert messages[1] == {"role": "assistant", "content": "助手回复0"}
    assert messages[-1] == {"role": "assistant", "content": "助手回复19"}


def test_history_to_messages_maps_agent_role_to_assistant() -> None:
    dialogue = _dialogue_with_history(1)
    messages = dialogue._history_to_messages()
    assert messages == [
        {"role": "user", "content": "用户消息0"},
        {"role": "assistant", "content": "助手回复0"},
    ]
