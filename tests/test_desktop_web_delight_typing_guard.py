"""Static contract: background delight pushes must not stomp an in-progress chat.

Field report 2026-07-05: the backend pushes a new delight candidate every
``proactive_push_interval_seconds`` and the desktop web unconditionally
switched the active card (``setActiveDelight`` closes the composer), so a
user typing on the card had it swapped mid-sentence — and a subsequent send
would attach the feedback to the NEW card (state.delight had moved).
"""

from pathlib import Path

_DESKTOP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
_MOBILE_RECOMMEND_JS = Path("src/openbiliclaw/web/js/views/recommend.js")


def test_desktop_delight_push_respects_in_progress_typing() -> None:
    app_js = _DESKTOP_JS.read_text(encoding="utf-8")

    # The engagement predicate exists and considers composer / focus / draft.
    assert "function delightUserEngaged" in app_js
    assert ".delight-main-actions.is-composing" in app_js
    assert "delightCommentInput" in app_js

    # The stream handler consults it before switching to a new candidate, and
    # keeps the queue counter fresh when suppressing the switch.
    assert "function syncDelightCount" in app_js
    candidate_block = app_js.split('event.type === "delight.candidate"', 1)[1][:1600]
    assert "delightUserEngaged()" in candidate_block
    assert "syncDelightCount()" in candidate_block
    # The non-engaged default still auto-advances to the newest candidate.
    assert "setActiveDelight(state.delights.length - 1)" in candidate_block


def test_desktop_delight_queue_refresh_respects_in_progress_typing() -> None:
    app_js = _DESKTOP_JS.read_text(encoding="utf-8")

    apply_block = app_js.split("function applyDelights", 1)[1][:2400]
    # While engaged the refresh only syncs data/count — no setActiveDelight
    # (which would close the composer); the active card object is retained
    # even if the backend consumed it, so a send lands on the card the user
    # is actually looking at.
    assert "delightUserEngaged()" in apply_block
    assert "syncDelightCount()" in apply_block
    assert apply_block.index("delightUserEngaged()") < apply_block.index("setActiveDelight(")


def test_mobile_delight_push_keeps_composer_focus() -> None:
    recommend_js = _MOBILE_RECOMMEND_JS.read_text(encoding="utf-8")

    candidate_block = recommend_js.split('type === "delight.candidate"', 1)[1][:900]
    # Skip the DOM rebuild while the composer textarea is focused — rebuilding
    # drops focus and closes the mobile keyboard (drafts already live in state).
    assert "delight-composer-input" in candidate_block
    assert "rerenderDelightOnly()" in candidate_block
