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
    from PIL import Image, ImageFont

_W, _H = 560, 280
_BG_TOP = (19, 17, 27)
_BG_BOTTOM = (31, 23, 35)
_ACCENT = (247, 105, 157)  # OpenBiliClaw pink
_ACCENT_SOFT = (255, 153, 190)
_FG = (248, 246, 250)
_SUB = (185, 177, 193)
_MUTED = (132, 124, 143)
_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "brand" / "openbiliclaw-icon.png"
_PROJECT_FILE = Path(__file__).resolve().parent.parent / "pyproject.toml"
_ICON_X, _ICON_Y, _ICON_SIZE = 38, 40, 94

# Known CJK-capable fonts (Windows / macOS). PIL's ``truetype`` resolves bare
# names against the OS font directory, so listing filenames is enough.
_CJK_FONTS = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "PingFang.ttc", "STHeiti Medium.ttc"]
_ASCII_FONTS = ["arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"]


def _load_fonts() -> tuple[
    ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ImageFont.FreeTypeFont | ImageFont.ImageFont,
    bool,
]:
    """Return title, body and label fonts plus CJK availability."""
    from PIL import ImageFont

    for name in _CJK_FONTS:
        try:
            return (
                ImageFont.truetype(name, 32),
                ImageFont.truetype(name, 16),
                ImageFont.truetype(name, 12),
                True,
            )
        except Exception:  # noqa: BLE001 — font just isn't on this host
            continue
    for name in _ASCII_FONTS:
        try:
            return (
                ImageFont.truetype(name, 32),
                ImageFont.truetype(name, 16),
                ImageFont.truetype(name, 12),
                False,
            )
        except Exception:  # noqa: BLE001
            continue
    default = ImageFont.load_default()
    return default, default, default, False


def _read_project_version(project_file: Path = _PROJECT_FILE) -> str:
    """Read the release version used by the package being built."""
    import tomllib

    try:
        data = tomllib.loads(project_file.read_text(encoding="utf-8"))
        version = str(data["project"]["version"]).strip()
    except (KeyError, OSError, tomllib.TOMLDecodeError):
        return "dev"
    return version or "dev"


def _display_version(version: str) -> str:
    """Normalize a package version for the compact splash badge."""
    clean = version.strip()
    return clean if clean.lower().startswith("v") else f"v{clean}"


def _vertical_gradient() -> Image.Image:
    """Create the splash background without depending on external assets."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (_W, _H), _BG_TOP)
    draw = ImageDraw.Draw(image)
    for y in range(_H):
        ratio = y / max(_H - 1, 1)
        color = tuple(
            round(start + (end - start) * ratio)
            for start, end in zip(_BG_TOP, _BG_BOTTOM, strict=True)
        )
        draw.line((0, y, _W, y), fill=color)
    return image


def make_splash(
    path: Path,
    *,
    icon_path: Path | None = None,
    version: str | None = None,
) -> Path:
    """Render the boot splash PNG to ``path`` (creating parents) and return it."""
    from PIL import Image, ImageDraw, ImageFilter

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    img = _vertical_gradient()

    # A restrained ambient glow gives the native rectangular splash depth
    # without making transparent-window behavior platform-dependent.
    glow = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-50, -90, 265, 225), fill=(*_ACCENT, 74))
    glow_draw.ellipse((390, 130, 650, 390), fill=(129, 73, 151, 28))
    glow = glow.filter(ImageFilter.GaussianBlur(62))
    img = Image.alpha_composite(img.convert("RGBA"), glow)
    draw = ImageDraw.Draw(img, "RGBA")

    # Hairline frame and soft highlights keep the splash crisp on both light
    # and dark Windows desktops.
    draw.rectangle((0, 0, _W - 1, _H - 1), outline=(50, 43, 55, 255), width=1)
    draw.line((24, 1, 212, 1), fill=(*_ACCENT_SOFT, 255), width=2)

    with Image.open(icon_path or _ICON_PATH) as source:
        icon = source.convert("RGBA")
        icon.thumbnail((_ICON_SIZE, _ICON_SIZE), Image.Resampling.LANCZOS)
        icon_x = _ICON_X + (_ICON_SIZE - icon.width) // 2
        icon_y = _ICON_Y + (_ICON_SIZE - icon.height) // 2
        img.paste(icon, (icon_x, icon_y), icon)

    title_font, body_font, label_font, cjk = _load_fonts()
    subtitle = "懂你的内容伙伴" if cjk else "Your personal content companion"
    status = "正在准备，只需片刻" if cjk else "Getting things ready"
    detail = "启动本地服务与智能引擎" if cjk else "Starting local services and intelligence"

    draw.text((158, 49), "OpenBiliClaw", font=title_font, fill=_FG)
    draw.text((160, 98), subtitle, font=body_font, fill=_SUB)

    version_text = _display_version(version or _read_project_version())
    version_box = draw.textbbox((0, 0), version_text, font=label_font)
    version_width = version_box[2] - version_box[0]
    pill = (_W - version_width - 46, 28, _W - 24, 54)
    draw.rounded_rectangle(
        pill,
        radius=13,
        fill=(48, 40, 52, 255),
        outline=(68, 59, 72, 255),
        width=1,
    )
    draw.text(
        (pill[0] + (pill[2] - pill[0] - version_width) / 2, 34),
        version_text,
        font=label_font,
        fill=(224, 217, 229, 255),
    )

    panel = (32, 164, _W - 32, 244)
    draw.rounded_rectangle(
        panel,
        radius=18,
        fill=(39, 32, 44, 255),
        outline=(59, 50, 64, 255),
        width=1,
    )
    draw.ellipse((52, 187, 66, 201), fill=(98, 51, 71, 255))
    draw.ellipse((56, 191, 62, 197), fill=(*_ACCENT_SOFT, 255))
    draw.text((78, 179), status, font=body_font, fill=_FG)
    draw.text((78, 211), detail, font=label_font, fill=_MUTED)

    # Static indeterminate track: PyInstaller displays the PNG before Python is
    # available, so this deliberately communicates activity rather than a fake
    # percentage.
    draw.rounded_rectangle((32, 261, _W - 32, 265), radius=2, fill=(58, 49, 62, 255))
    draw.rounded_rectangle((32, 261, 184, 265), radius=2, fill=(*_ACCENT, 255))

    img.convert("RGB").save(path, "PNG")
    return path


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("splash.png")
    print(make_splash(out))
