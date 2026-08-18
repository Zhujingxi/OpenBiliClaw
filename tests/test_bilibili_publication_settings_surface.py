"""Static contracts for the Bilibili publication settings surface."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_bilibili_publication_controls_round_trip_config() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "biliDatePreset",
        "biliDateMode",
        "biliDateStart",
        "biliDateEnd",
        "biliDateWeight",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js

    assert "recommendation_date_preset" in js
    assert "recommendation_date_start" in js
    assert "recommendation_date_end" in js
    assert "recommendation_date_weight" in js
    assert "validateBilibiliDateSettings" in js
