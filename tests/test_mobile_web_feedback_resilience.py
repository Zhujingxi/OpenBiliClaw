"""Static regressions for feedback submission resilience.

Covers two user-visible contracts:
1. Mobile ``submitFeedback`` carries a timeout (a hung backend must not leave
   the like/dislike/comment buttons spinning forever).
2. Delight card like/reject failures never pretend success: the card is kept,
   not marked sent, and a retry hint is shown.
"""

from pathlib import Path

_API_JS = Path("src/openbiliclaw/web/js/api.js")
_RECOMMEND_JS = Path("src/openbiliclaw/web/js/views/recommend.js")
_VIEW_MODELS_JS = Path("src/openbiliclaw/web/js/view-models.js")
_DESKTOP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")


def test_mobile_submit_feedback_has_timeout() -> None:
    api_js = _API_JS.read_text(encoding="utf-8")

    assert "FEEDBACK_SUBMIT_TIMEOUT_MS = 30_000" in api_js
    submit = api_js.split("export async function submitFeedback", 1)[1][:900]
    assert "rememberPendingRequestId" in submit
    assert "const body = { ...payload, request_id:" in submit
    assert 'requestJson("/feedback", {' in submit
    assert "...json(body)," in submit
    assert "timeoutMs: FEEDBACK_SUBMIT_TIMEOUT_MS," in submit


def test_mobile_delight_reject_failure_keeps_card() -> None:
    recommend_js = _RECOMMEND_JS.read_text(encoding="utf-8")
    view_models_js = _VIEW_MODELS_JS.read_text(encoding="utf-8")

    handler = recommend_js.split("async function handleDelightAction", 1)[1][:2600]
    # like/reject failures return before markDelightSent / removal, restore the
    # action buttons, and surface a retry hint instead of pretending success.
    assert 'action === "like" || action === "reject"' in handler
    assert 'response_tone: "error"' in handler
    assert r"\u64CD\u4F5C\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5" in handler
    assert handler.index('action === "like" || action === "reject"') < handler.index(
        "markDelightSent("
    )
    assert "/* Other legacy actions remain best-effort. */" in handler

    # The error tone survives re-normalization so the status line renders red.
    assert 'response_tone: normalizeText(item?.response_tone) || "info"' in view_models_js
    assert 'response_tone: normalized.response_tone || "info"' in view_models_js


def test_desktop_delight_failure_keeps_card() -> None:
    app_js = _DESKTOP_JS.read_text(encoding="utf-8")

    handler = app_js.split('const feedbackToast = response === "like"', 1)[1][:1600]
    # A failed like/dislike/dismiss keeps the current card and shows a retry
    # toast; card removal only happens after a successful response.
    assert "这次喜欢还没记上，可以再试一次" in handler
    assert "这次还没记上，请再试一次" in handler
    assert handler.index("这次还没记上，请再试一次") < handler.index(
        "state.delights = state.delights.filter((item) => item.bvid !== delight.bvid)"
    )
