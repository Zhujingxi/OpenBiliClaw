from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mobile_recommendation_header_has_no_correction_entry() -> None:
    js = (ROOT / "src/openbiliclaw/web/js/views/recommend.js").read_text(encoding="utf-8")
    header = js.split("function renderRecommendationHeader()", 1)[1].split(
        "/** Re-render only the header", 1
    )[0]

    assert "推荐不准？" not in header
    assert "data-correction-target" not in header
    assert "focusChatInputWhenReady" not in js
    assert "CHAT_INPUT_FOCUS_TIMEOUT_MS" not in js


def test_mobile_recommendation_view_does_not_import_correction_navigation() -> None:
    recommend_js = (ROOT / "src/openbiliclaw/web/js/views/recommend.js").read_text(encoding="utf-8")
    profile_js = (ROOT / "src/openbiliclaw/web/js/views/profile.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/css/app.css").read_text(encoding="utf-8")

    assert 'import { navigateToTab } from "../app.js";' not in recommend_js
    assert "enterProfileEditMode" not in recommend_js
    assert "export async function enterProfileEditMode()" not in profile_js
    assert ".preference-correction-callout" not in css
