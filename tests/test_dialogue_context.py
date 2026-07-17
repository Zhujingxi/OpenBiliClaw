"""Baseline snapshots for the Phase 1 dialogue window + regurgitation work.

Committed BEFORE the window/regurgitation/builder changes (plan Task 3, step
"基线先行"). These lock the ≤-window prompt bytes and the current
dialogue-insight extraction prompt so the later refactor is provably
behaviour-equivalent for in-window sessions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.soul.dialogue import DIALOGUE_WINDOW_TURNS, DialogueTurn, SocraticDialogue
from openbiliclaw.soul.identity import build_hash8_map, insight_hash8
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


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


def test_history_truncated_to_window_when_over_limit() -> None:
    # 25 exchanges → only the last DIALOGUE_WINDOW_TURNS (20) are kept.
    dialogue = _dialogue_with_history(25)
    messages = dialogue._history_to_messages()
    assert len(messages) == DIALOGUE_WINDOW_TURNS * 2
    # Oldest retained exchange is #5 (25 - 20).
    assert messages[0] == {"role": "user", "content": "用户消息5"}
    assert messages[-1] == {"role": "assistant", "content": "助手回复24"}


# ---------------------------------------------------------------------------
# Regurgitation (popup + scope='chat' + completed only)
# ---------------------------------------------------------------------------


def _db_with_turns(tmp_path: Path) -> Database:
    db = Database(tmp_path / "chat.db")
    db.initialize()
    return db


def test_regurgitation_loads_completed_popup_chat_turns(tmp_path: Path) -> None:
    db = _db_with_turns(tmp_path)
    db.create_chat_turn(turn_id="t1", message="用户一", session="popup", scope="chat")
    db.complete_chat_turn("t1", reply="回复一")
    db.create_chat_turn(turn_id="t2", message="用户二", session="popup", scope="chat")
    db.complete_chat_turn("t2", reply="回复二")

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=object(),
        session="popup",
        database=db,  # type: ignore[arg-type]
    )
    dialogue._ensure_history_loaded()
    contents = [(t.role, t.content) for t in dialogue.history]
    assert contents == [
        ("user", "用户一"),
        ("agent", "回复一"),
        ("user", "用户二"),
        ("agent", "回复二"),
    ]


def test_regurgitation_excludes_pending_probe_and_cli(tmp_path: Path) -> None:
    db = _db_with_turns(tmp_path)
    # Pending (not completed) — excluded.
    db.create_chat_turn(turn_id="p1", message="待处理", session="popup", scope="chat")
    # Probe scope — excluded.
    db.create_chat_turn(turn_id="pr1", message="探针", session="popup", scope="probe")
    db.complete_chat_turn("pr1", reply="探针回复")
    # Completed chat — included.
    db.create_chat_turn(turn_id="c1", message="正常", session="popup", scope="chat")
    db.complete_chat_turn("c1", reply="正常回复")

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=object(),
        session="popup",
        database=db,  # type: ignore[arg-type]
    )
    dialogue._ensure_history_loaded()
    assert [(t.role, t.content) for t in dialogue.history] == [
        ("user", "正常"),
        ("agent", "正常回复"),
    ]


def test_regurgitation_skipped_for_cli_session(tmp_path: Path) -> None:
    db = _db_with_turns(tmp_path)
    db.create_chat_turn(turn_id="c1", message="正常", session="popup", scope="chat")
    db.complete_chat_turn("c1", reply="正常回复")
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=object(),
        session="cli",
        database=db,  # type: ignore[arg-type]
    )
    dialogue._ensure_history_loaded()
    assert dialogue.history == []


# ---------------------------------------------------------------------------
# hash8 identity
# ---------------------------------------------------------------------------


def test_insight_hash8_is_stable_and_canonicalizes_whitespace() -> None:
    a = insight_hash8("用户 通过 深度内容 获得掌控感")
    b = insight_hash8("  用户   通过 深度内容\n获得掌控感  ")
    assert a == b  # NFC + strip + whitespace-collapse → same key
    assert len(a) == 8
    assert insight_hash8("完全不同的假设") != a


def test_build_hash8_map_distinct_texts_use_hex8() -> None:
    mapping = build_hash8_map(["假设甲", "假设乙"])
    assert len(mapping) == 2
    assert all(len(k) == 8 for k in mapping)
    assert set(mapping.values()) == {"假设甲", "假设乙"}
