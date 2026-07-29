#!/usr/bin/env python3
"""Generate the boot splash image shown while the packaged app starts.

The Windows tray app has no window for several seconds after launch — Python has
to start, the bundled Ollama preflight runs (up to ~15s), and the backend is
assembled before the system-tray icon appears. Without any feedback the first
double-click looks dead and users click again. PyInstaller paints this PNG at
the OS level the instant the exe starts (before Python is even loaded), and
``packaging/entry.py`` closes it once the tray icon is up.

The subtitle is rendered in Chinese when a CJK-capable font is available on the
build host (Windows ships Microsoft YaHei), and falls back to English otherwise
so a generated PNG never shows tofu boxes on a runner without CJK fonts.

Run standalone to preview:  python packaging/make_splash.py out.png
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import ImageFont

_W, _H = 440, 168
_BG = (22, 22, 30)  # on-brand near-black
_ACCENT = (251, 114, 153)  # bilibili pink
_FG = (236, 236, 240)
_SUB = (158, 158, 170)
_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "brand" / "openbiliclaw-icon.png"
_ICON_X, _ICON_Y, _ICON_SIZE = 28, 38, 92

# Known CJK-capable fonts (Windows / macOS). PIL's ``truetype`` resolves bare
# names against the OS font directory, so listing filenames is enough.
_CJK_FONTS = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "PingFang.ttc", "STHeiti Medium.ttc"]
_ASCII_FONTS = ["arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"]


def _load_fonts() -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, object, bool]:
    """Return (title_font, subtitle_font, cjk_available)."""
    from PIL import ImageFont

    for name in _CJK_FONTS:
        try:
            return ImageFont.truetype(name, 34), ImageFont.truetype(name, 16), True
        except Exception:  # noqa: BLE001 — font just isn't on this host
            continue
    for name in _ASCII_FONTS:
        try:
            return ImageFont.truetype(name, 34), ImageFont.truetype(name, 16), False
        except Exception:  # noqa: BLE001
            continue
    default = ImageFont.load_default()
    return default, default, False


def make_splash(path: Path, *, icon_path: Path | None = None) -> Path:
    """Render the boot splash PNG to ``path`` (creating parents) and return it."""
    from PIL import Image, ImageDraw

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (_W, _H), _BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, _W, 4], fill=_ACCENT)  # accent bar along the top

    with Image.open(icon_path or _ICON_PATH) as source:
        icon = source.convert("RGBA")
        icon.thumbnail((_ICON_SIZE, _ICON_SIZE), Image.Resampling.LANCZOS)
        icon_x = _ICON_X + (_ICON_SIZE - icon.width) // 2
        icon_y = _ICON_Y + (_ICON_SIZE - icon.height) // 2
        img.paste(icon, (icon_x, icon_y), icon)

    title_font, sub_font, cjk = _load_fonts()
    subtitle = "正在启动,请稍候…" if cjk else "Starting up, please wait…"
    draw.text((142, 48), "OpenBiliClaw", font=title_font, fill=_FG)
    draw.text((144, 103), subtitle, font=sub_font, fill=_SUB)

    img.save(path, "PNG")
    return path


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("splash.png")
    print(make_splash(out))
