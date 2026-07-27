"""Desktop Web autostart drift rendering regression tests."""

from pathlib import Path


def test_desktop_autostart_switch_surfaces_residual_registration() -> None:
    app_js = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openbiliclaw"
        / "web"
        / "desktop"
        / "assets"
        / "js"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "current.enabled || current.registered" in app_js
    assert "检测到系统自启动残留项" in app_js
