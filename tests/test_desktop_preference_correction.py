from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_recommendation_header_exposes_correction_actions() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    assert "推荐不准？" in html
    assert 'id="editProfileFromRecommendations"' in html
    assert 'id="chatFromRecommendations"' in html


def test_desktop_correction_actions_reuse_profile_and_chat_flows() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    assert "async function openProfileCorrection()" in js
    assert "openProfilePage();" in js
    assert "await enterProfileEdit();" in js
    assert "function openChatCorrection()" in js
    assert "openChatPage();" in js
    assert 'safeBind("#editProfileFromRecommendations", "click", openProfileCorrection)' in js
    assert 'safeBind("#chatFromRecommendations", "click", openChatCorrection)' in js
