from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_recommendation_header_has_no_correction_entry() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    recommendations = html.split('<section data-od-id="recommendations">', 1)[1].split(
        "</section>", 1
    )[0]

    assert "推荐不准？" not in recommendations
    assert "编辑画像" not in recommendations
    assert "直接告诉阿B" not in recommendations
    assert 'id="editProfileFromRecommendations"' not in recommendations
    assert 'id="chatFromRecommendations"' not in recommendations


def test_desktop_has_no_recommendation_correction_helpers_or_styles() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    for marker in (
        "openProfileCorrection",
        "openChatCorrection",
        "editProfileFromRecommendations",
        "chatFromRecommendations",
    ):
        assert marker not in js
    assert ".preference-correction-callout" not in css
