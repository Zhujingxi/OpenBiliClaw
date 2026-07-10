from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mobile_recommendation_header_exposes_correction_actions() -> None:
    js = (
        ROOT / "src/openbiliclaw/web/js/views/recommend.js"
    ).read_text(encoding="utf-8")
    assert "推荐不准？" in js
    assert 'data-correction-target="profile"' in js
    assert 'data-correction-target="chat"' in js
    assert 'navigateToTab("profile")' in js
    assert "enterProfileEditMode" in js
    assert 'navigateToTab("chat")' in js
    assert 'document.getElementById("chat-input")?.focus()' in js
