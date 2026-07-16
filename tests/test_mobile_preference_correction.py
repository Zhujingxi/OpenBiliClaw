import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mobile_recommendation_header_has_no_correction_entry() -> None:
    js = (ROOT / "src/openbiliclaw/web/js/views/recommend.js").read_text(encoding="utf-8")
    header = js.split("function renderRecommendationHeader()", 1)[1].split(
        "/** Re-render only the header", 1
    )[0]

    assert "推荐不准？" not in header
    assert "编辑画像" not in header
    assert "直接告诉阿B" not in header
    assert "data-correction-target" not in header
    assert "focusChatInputWhenReady" not in js
    assert "CHAT_INPUT_FOCUS_TIMEOUT_MS" not in js
    assert "MutationObserver" not in js
    assert not re.search(r"document\.getElementById\s*\(\s*['\"]chat-input['\"]\s*\)", js)


def test_mobile_recommendation_view_does_not_import_correction_navigation() -> None:
    recommend_js = (ROOT / "src/openbiliclaw/web/js/views/recommend.js").read_text(encoding="utf-8")
    profile_js = (ROOT / "src/openbiliclaw/web/js/views/profile.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/css/app.css").read_text(encoding="utf-8")

    assert not re.search(
        r"\bimport\s+(?:[^;]*?\s+from\s+)?['\"]\.\./app\.js['\"]", recommend_js, re.DOTALL
    )
    assert "enterProfileEditMode" not in recommend_js
    assert "export async function enterProfileEditMode()" not in profile_js
    assert ".preference-correction-callout" not in css
