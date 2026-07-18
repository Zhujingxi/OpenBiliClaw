"""Issue #111: desktop saved cards expose a decluttered feedback action row.

These are source-level contract tests (matching the other test_desktop_web_*
suites): they keep the recommendation bar isolated, enforce one cross-list
toggle per saved card, and prevent content feedback from drifting back to the
recommendation-only endpoint.
"""

import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def _read() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _read_css() -> str:
    return APP_CSS.read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    """Return the brace-balanced body of a top-level function."""
    match = re.search(rf"function {re.escape(name)}\(", src)
    assert match, f"{name} not found"
    start = src.index("{", match.start())
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def test_recommendation_feedback_bar_stays_isolated_from_saved_cards() -> None:
    app_js = _read()
    recommendation_bar = _fn_body(app_js, "cardFeedbackBarHtml")
    saved_render = _fn_body(app_js, "renderSavedList")
    recommendation_render = _fn_body(app_js, "renderVideos")

    # Recommendation cards keep their original full bar, including dismiss and
    # the inline composer. Saved cards must never mutate or reuse that renderer.
    assert app_js.count('aria-label="推荐反馈操作"') == 1
    assert "function cardFeedbackBarHtml()" in app_js
    assert app_js.count("${cardFeedbackBarHtml()}") == 1
    assert "${cardFeedbackBarHtml()}" in recommendation_render
    assert "cardFeedbackBarHtml" not in saved_render
    for action in ("like", "dislike", "dismiss", "watch-later", "favorite"):
        assert f'data-action="{action}"' in recommendation_bar
    assert 'data-action="comment"' in recommendation_bar
    assert 'data-action="cancel-comment"' in recommendation_bar
    assert 'class="comment-field"' in recommendation_bar


def test_saved_card_renders_dedicated_icon_bar_and_wires_it() -> None:
    app_js = _read()
    saved_bar = _fn_body(app_js, "savedCardFeedbackBarHtml")

    assert 'class="card-actions saved-feedback-bar"' in saved_bar
    assert 'aria-label="反馈与保存操作"' in saved_bar
    assert saved_bar.count('data-action="like"') == 1
    assert saved_bar.count('data-action="dislike"') == 1
    assert saved_bar.count('data-action="saved-comment"') == 1
    assert saved_bar.count("cross-toggle") == 1
    assert 'const crossIsFavorite = listKind === "watch_later";' in saved_bar
    assert 'const crossAction = crossIsFavorite ? "favorite" : "watch-later";' in saved_bar
    assert 'data-action="dismiss"' not in saved_bar
    assert 'class="comment-field"' not in saved_bar
    assert "composer-cancel" not in saved_bar

    # The dedicated bar is injected before the existing sync/remove row.
    assert (
        "${savedCardFeedbackBarHtml(listKind)}\n"
        '          <div class="card-actions saved-card-actions">'
    ) in app_js
    assert "wireSavedCardFeedback(card, item, listKind);" in app_js
    assert "function wireSavedCardFeedback(card, item, listKind)" in app_js


def test_saved_cards_only_toggle_the_cross_list_without_reloading() -> None:
    body = _fn_body(_read(), "wireSavedCardFeedback")
    assert 'const crossKind = listKind === "watch_later" ? "favorite" : "watch_later";' in body
    assert "await desktopSavedMutations.toggle(crossKind, savedItem.item_key" in body
    assert "add: () => saveDesktopItem(crossKind, item)" in body
    assert "remove: () => removeDesktopSavedItem(crossKind, savedItem.item_key)" in body
    assert "desktopSavedMutations.hydrate(" in body
    assert "() => savedStatus(crossKind, savedItem)" in body
    assert "setCrossState(!wasSaved);" in body
    assert "setCrossState(wasSaved);" in body
    assert "handleCardAction" not in body
    assert "setSaved(listKind" not in body
    assert "reload" not in body


def test_saved_comment_uses_prompt_without_inline_composer() -> None:
    app_js = _read()
    body = _fn_body(app_js, "wireSavedCardFeedback")
    assert "card.querySelector('[data-action=\"saved-comment\"]')" in body
    assert 'window.prompt("想围绕这条聊什么？")' in body
    assert 'postSavedContentFeedback(item, "comment", note)' in body
    assert "SAVED_FEEDBACK_COPY.comment.saving" in body
    assert "SAVED_FEEDBACK_COPY.comment.done" in body
    assert "showToast(SAVED_FEEDBACK_COPY.comment.toast)" in body
    assert "comment-field" not in body
    assert "openCardComposer" not in body
    assert "submitSavedCardComment" not in app_js


def test_saved_feedback_bar_uses_ghost_icon_styles() -> None:
    css = _read_css()
    assert ".saved-feedback-bar" in css
    assert ".saved-feedback-bar .feedback-icon-btn" in css
    assert "width: 36px" in css
    assert "height: 36px" in css
    assert "border: 0" in css
    assert "background: transparent" in css
    assert "background: var(--accent-subtle)" in css
    assert "transform: scale(0.94)" in css
    assert ".saved-feedback-bar .cross-toggle" in css
    assert "color: #e8a33d" in css
    assert "fill: currentColor" in css


def test_saved_feedback_uses_content_signal_not_recommendation_endpoint() -> None:
    app_js = _read()
    wire = _fn_body(app_js, "wireSavedCardFeedback")
    # like / dislike on a saved card go to the content-based handler...
    assert "handleSavedCardFeedback(btn.dataset.action, item, card)" in wire
    post = _fn_body(app_js, "postSavedContentFeedback")
    # ...which posts a content feedback event to /api/events (NOT /api/feedback),
    # because saved items carry no recommendation_id (that endpoint 404s without one).
    assert "ENDPOINTS.events" in post
    assert 'type: "feedback"' in post
    assert "feedback_type: feedbackType" in post
    assert "content_id: contentId" in post
    assert "saved_feedback: true" in post
    assert "recommendation_id" not in post
    assert 'events: "/events",' in app_js


def test_saved_feedback_never_calls_recommendation_submit_feedback() -> None:
    app_js = _read()
    # submitFeedback is recommendation_id-bound; saved-card handlers must not
    # use it, or like/dislike/comment on saved items would 404.
    for fn in (
        "wireSavedCardFeedback",
        "handleSavedCardFeedback",
        "postSavedContentFeedback",
    ):
        assert "submitFeedback(" not in _fn_body(app_js, fn)
