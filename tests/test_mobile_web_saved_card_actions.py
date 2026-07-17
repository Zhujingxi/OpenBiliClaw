"""Issue #111: mobile saved-list cards expose recommendation-style actions.

Source-level contract tests. The feedback controls render as one light inline
icon row: 喜欢 / 不感兴趣 / 聊一聊 grouped left + a single CROSS-list toggle
(watch_later list → 收藏; favorite list → 稍后再看). The owning-list membership
is managed by the existing 移除 button, so there is no owning toggle and no
dismiss — keeping a compact card clean.
"""

import re
from pathlib import Path

API_JS = Path("src/openbiliclaw/web/js/api.js")
SAVED_JS = Path("src/openbiliclaw/web/js/views/saved.js")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    match = re.search(rf"function {re.escape(name)}\(", src)
    assert match, f"{name} not found"
    start = src.index("{", match.start())
    depth = 0
    for index in range(start, len(src)):
        if src[index] == "{":
            depth += 1
        elif src[index] == "}":
            depth -= 1
            if depth == 0:
                return src[start : index + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def test_mobile_web_api_exposes_content_behavior_events() -> None:
    api = _read(API_JS)
    body = _fn_body(api, "sendBehaviorEvents")
    assert "export async function sendBehaviorEvents(events)" in api
    assert 'requestJson("/events", json({ events }))' in body
    assert '"/feedback"' not in body


def test_mobile_web_saved_cards_build_light_row_no_owning_no_dismiss() -> None:
    saved = _read(SAVED_JS)
    wire = _fn_body(saved, "wireSavedCardActions")

    assert 'actionsRow.className = "card-actions saved-card-feedback";' in wire
    # like / dislike / comment grouped, then ONE cross toggle — four controls, no owning, no dismiss.
    assert "actionsRow.append(likeBtn, dislikeBtn, commentBtn, crossBtn);" in wire
    assert 'card.querySelector(".saved-card-body")?.appendChild(actionsRow);' in wire
    labels = ('ariaLabel: "喜欢"', 'ariaLabel: "不感兴趣"', 'ariaLabel: "聊一聊"')
    assert all(label in wire for label in labels)
    assert '"dismiss"' not in wire
    # owning toggle is gone (no owning button, no setSaved priming, no reload-on-owning)
    assert "owningBtn" not in wire
    assert "toggleOwning" not in saved
    assert "setSaved(cfg.listKind" not in wire
    # no cover-chip overlay
    assert "createCoverChip" not in saved
    assert "cover-actions" not in saved
    assert "wireSavedCardActions(card, item);" in saved


def test_mobile_web_single_cross_toggle_hydrates_and_reuses_saved_apis() -> None:
    saved = _read(SAVED_JS)
    wire = _fn_body(saved, "wireSavedCardActions")
    cross = _fn_body(saved, "toggleCross")

    assert 'const crossKind = cfg.listKind === "watch_later" ? "favorite" : "watch_later";' in wire
    assert 'crossBtn.classList.add("cross-toggle"' in wire
    assert "savedMutations.hydrate(crossKind, item.item_key" in wire
    assert "savedItemStatus(crossKind, item.item_key)" in wire
    assert "savedMutations.toggle(crossKind, item.item_key" in cross
    assert "saveItem(crossKind, item)" in cross
    assert "removeSavedItem(crossKind, item.item_key)" in cross


def test_mobile_web_saved_feedback_uses_events_not_recommendation_feedback() -> None:
    saved = _read(SAVED_JS)
    post = _fn_body(saved, "postSavedFeedback")
    handle = _fn_body(saved, "handleSavedCardFeedback")
    wire = _fn_body(saved, "wireSavedCardActions")

    assert "sendBehaviorEvents([{" in post
    assert 'type: "feedback"' in post
    assert "feedback_type: feedbackType" in post
    assert "content_id: contentId" in post
    assert "saved_feedback: true" in post
    assert "recommendation_id" not in post
    assert "postSavedFeedback(item, feedbackType)" in handle
    assert 'postSavedFeedback(item, "comment", note)' in wire
    assert "submitFeedback(" not in saved


def test_mobile_web_existing_saved_open_sync_and_remove_actions_remain() -> None:
    saved = _read(SAVED_JS)
    assert 'data-saved-action="open"' in saved
    assert 'data-saved-action="sync"' in saved
    assert 'data-saved-action="remove"' in saved
    assert "openContentUrl(open.dataset.url)" in saved
    assert "runSync([item], event.currentTarget)" in saved
    assert "await removeSavedItem(cfg.listKind, item.item_key);" in saved
