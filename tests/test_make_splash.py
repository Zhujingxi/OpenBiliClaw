"""Tests for the boot-splash image generator (packaging/make_splash.py).

``make_splash`` renders the PNG PyInstaller shows while the packaged app starts.
Verify it produces a valid PNG of the expected size regardless of which fonts
the host happens to have (CJK or ASCII fallback)."""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def _load_module() -> ModuleType:
    root = Path(__file__).resolve().parent.parent
    path = root / "packaging" / "make_splash.py"
    spec = importlib.util.spec_from_file_location("obc_make_splash", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_splash_mod = _load_module()


def test_make_splash_writes_valid_png(tmp_path: Path) -> None:
    out = tmp_path / "splash.png"
    result = make_splash_mod.make_splash(out)

    assert result == out
    assert out.exists()
    # PNG magic number.
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_make_splash_has_expected_dimensions(tmp_path: Path) -> None:
    from PIL import Image

    out = make_splash_mod.make_splash(tmp_path / "splash.png", version="1.2.3")
    with Image.open(out) as img:
        assert img.size == (make_splash_mod._W, make_splash_mod._H)
        # The launch screen carries the canonical pink brand mark instead of a
        # text-only placeholder. Its transparent corners blend into the splash.
        center = (
            make_splash_mod._ICON_X + make_splash_mod._ICON_SIZE // 2,
            make_splash_mod._ICON_Y + make_splash_mod._ICON_SIZE // 2,
        )
        pixel = img.convert("RGB").getpixel(center)
        assert isinstance(pixel, tuple)
        red, green, blue = pixel
        assert red > 220
        assert green < 180
        assert blue > 120


def test_make_splash_renders_requested_version(tmp_path: Path) -> None:
    first = make_splash_mod.make_splash(tmp_path / "first.png", version="1.2.3")
    second = make_splash_mod.make_splash(tmp_path / "second.png", version="9.8.7")

    assert first.read_bytes() != second.read_bytes()
    assert make_splash_mod._display_version("1.2.3") == "v1.2.3"
    assert make_splash_mod._display_version("v1.2.3") == "v1.2.3"


def test_read_project_version_uses_package_metadata(tmp_path: Path) -> None:
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text('[project]\nname = "demo"\nversion = "2.4.6"\n', encoding="utf-8")

    assert make_splash_mod._read_project_version(project_file) == "2.4.6"


def test_make_splash_creates_parent_dirs(tmp_path: Path) -> None:
    # The spec writes into project_root/build/, which may not exist yet.
    nested = tmp_path / "build" / "deep" / "splash.png"
    make_splash_mod.make_splash(nested)
    assert nested.exists()
    # Sanity: width WORD in the IHDR chunk matches _W (PNG stores it big-endian
    # at byte offset 16).
    width = struct.unpack(">I", nested.read_bytes()[16:20])[0]
    assert width == make_splash_mod._W
