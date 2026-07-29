from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def test_brand_png_assets_have_expected_dimensions() -> None:
    expected_sizes = {
        "assets/brand/openbiliclaw-icon.png": (1024, 1024),
        "extension/icons/icon16.png": (16, 16),
        "extension/icons/icon48.png": (48, 48),
        "extension/icons/icon128.png": (128, 128),
        "src/openbiliclaw/web/icon-192.png": (192, 192),
        "src/openbiliclaw/web/icon-512.png": (512, 512),
        "docs/images/openbiliclaw-icon-512.png": (512, 512),
    }

    for relative_path, expected_size in expected_sizes.items():
        with Image.open(ROOT / relative_path) as icon:
            assert icon.format == "PNG", relative_path
            assert icon.size == expected_size, relative_path
            rgba = icon.convert("RGBA")
            assert rgba.getpixel((0, 0))[3] == 0, relative_path
            assert rgba.getpixel((expected_size[0] // 2, expected_size[1] // 2))[3] == 255, (
                relative_path
            )


def test_desktop_icon_containers_include_all_required_sizes() -> None:
    with Image.open(ROOT / "packaging" / "icon.ico") as windows_icon:
        assert windows_icon.format == "ICO"
        assert {(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)}.issubset(
            windows_icon.ico.sizes()
        )
        assert windows_icon.convert("RGBA").getpixel((0, 0))[3] == 0

    with Image.open(ROOT / "packaging" / "icon.icns") as macos_icon:
        assert macos_icon.format == "ICNS"
        assert macos_icon.size == (1024, 1024)
        assert macos_icon.convert("RGBA").getpixel((0, 0))[3] == 0


def test_user_facing_brand_marks_reference_canonical_icon_assets() -> None:
    popup = (ROOT / "extension" / "popup" / "popup.html").read_text(encoding="utf-8")
    desktop = (ROOT / "src" / "openbiliclaw" / "web" / "desktop" / "index.html").read_text(
        encoding="utf-8"
    )
    setup = (ROOT / "src" / "openbiliclaw" / "web" / "setup" / "index.html").read_text(
        encoding="utf-8"
    )
    mobile = (ROOT / "src" / "openbiliclaw" / "web" / "js" / "app.js").read_text(encoding="utf-8")
    homepage = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    assert '<img class="brand-mark" src="../icons/icon128.png"' in popup
    assert '<img class="brand-mark" src="/m/icon-192.png"' in desktop
    assert '<img class="mark" src="/m/icon-192.png"' in setup
    assert 'class="status-brand-icon" src="icon-192.png"' in mobile
    assert 'class="mark" src="images/openbiliclaw-icon-512.png"' in homepage
