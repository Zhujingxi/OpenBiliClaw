"""Compose Chrome Web Store listing screenshots from sanitized current UI."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "docs/images/chrome-web-store/source"
OUTPUT_DIR = ROOT / "docs/images/chrome-web-store"
CANVAS = (1280, 800)
PLATFORMS = ("B站", "小红书", "抖音", "YouTube", "X", "知乎", "Reddit")

BG = "#F7F5EF"
INK = "#171714"
MUTED = "#68665F"
LINE = "#DEDAD0"
PINK = "#FF6B96"
PINK_SOFT = "#FFE3EB"
BLUE = "#3186FF"
BLUE_SOFT = "#E8F2FF"
GREEN = "#18A66A"
GREEN_SOFT = "#E5F6EE"
ORANGE = "#E66A3B"


def font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc"
        if bold
        else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_BRAND = font(21, bold=True)
FONT_KICKER = font(16, bold=True)
FONT_TITLE = font(48, bold=True)
FONT_SUBTITLE = font(23)
FONT_BODY = font(19)
FONT_SMALL = font(15)
FONT_CHIP = font(17, bold=True)


def _gradient() -> Image.Image:
    image = Image.new("RGB", CANVAS, BG)
    pixels = image.load()
    for y in range(CANVAS[1]):
        for x in range(CANVAS[0]):
            pink_weight = max(0.0, 1.0 - ((x - 90) ** 2 + (y - 70) ** 2) ** 0.5 / 720)
            blue_weight = max(0.0, 1.0 - ((x - 1210) ** 2 + (y - 690) ** 2) ** 0.5 / 840)
            base = (247, 245, 239)
            pixels[x, y] = tuple(
                int(
                    base[channel]
                    + pink_weight * ((255, 227, 235)[channel] - base[channel]) * 0.42
                    + blue_weight * ((232, 242, 255)[channel] - base[channel]) * 0.5
                )
                for channel in range(3)
            )
    return image


def _text_width(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=face)
    return box[2] - box[0]


def _brand(draw: ImageDraw.ImageDraw, index: int) -> None:
    draw.rounded_rectangle((58, 42, 102, 86), radius=13, fill=INK)
    draw.text((72, 49), "B", font=font(24, bold=True), fill="white")
    draw.text((116, 48), "OpenBiliClaw", font=FONT_BRAND, fill=INK)
    draw.text((116, 73), "本地优先的全网推荐入口", font=FONT_SMALL, fill=MUTED)
    draw.text((1170, 50), f"0{index} / 05", font=FONT_KICKER, fill=MUTED)


def _footer(draw: ImageDraw.ImageDraw) -> None:
    draw.line((58, 748, 1222, 748), fill=LINE, width=1)
    draw.ellipse((60, 768, 68, 776), fill=GREEN)
    draw.text((78, 761), "本地优先 · 数据默认留在你的设备上", font=FONT_SMALL, fill=MUTED)
    draw.text((1106, 761), "openbiliclaw.com", font=FONT_SMALL, fill=MUTED)


def _headline(
    draw: ImageDraw.ImageDraw,
    kicker: str,
    title: str,
    subtitle: str,
    *,
    x: int = 60,
    y: int = 112,
    max_width: int | None = None,
) -> None:
    draw.text((x, y), kicker.upper(), font=FONT_KICKER, fill=ORANGE)
    draw.text((x, y + 30), title, font=FONT_TITLE, fill=INK)
    if max_width and _text_width(draw, subtitle, FONT_SUBTITLE) > max_width:
        midpoint = max(1, len(subtitle) // 2)
        split = subtitle.rfind("，", 0, midpoint + 8)
        if split < 0:
            split = midpoint
        draw.text((x, y + 94), subtitle[: split + 1], font=FONT_SUBTITLE, fill=MUTED)
        draw.text((x, y + 128), subtitle[split + 1 :], font=FONT_SUBTITLE, fill=MUTED)
    else:
        draw.text((x, y + 94), subtitle, font=FONT_SUBTITLE, fill=MUTED)


def _chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    *,
    fill: str = "#FFFFFF",
    color: str = INK,
    outline: str = LINE,
) -> int:
    width = _text_width(draw, label, FONT_CHIP) + 34
    draw.rounded_rectangle((x, y, x + width, y + 42), radius=21, fill=fill, outline=outline)
    draw.text((x + 17, y + 9), label, font=FONT_CHIP, fill=color)
    return x + width


def _rounded_screenshot(
    base: Image.Image,
    path: Path,
    box: tuple[int, int, int, int],
    *,
    fit: bool = True,
    radius: int = 24,
    centering: tuple[float, float] = (0.5, 0.5),
) -> None:
    if not path.exists():
        raise FileNotFoundError(f"missing sanitized source screenshot: {path}")
    x1, y1, x2, y2 = box
    size = (x2 - x1, y2 - y1)
    with Image.open(path) as source:
        source = source.convert("RGB")
        if fit:
            layer = ImageOps.fit(
                source,
                size,
                method=Image.Resampling.LANCZOS,
                centering=centering,
            )
        else:
            layer = ImageOps.contain(source, size, method=Image.Resampling.LANCZOS)
            padded = Image.new("RGB", size, "white")
            padded.paste(layer, ((size[0] - layer.width) // 2, (size[1] - layer.height) // 2))
            layer = padded
    shadow = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (x1 + 7, y1 + 10, x2 + 7, y2 + 10),
        radius=radius,
        fill=(23, 23, 20, 28),
    )
    base.alpha_composite(shadow)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *size), radius=radius, fill=255)
    base.paste(layer, (x1, y1), mask)
    ImageDraw.Draw(base).rounded_rectangle(box, radius=radius, outline="#D7D4CB", width=2)


def _base(index: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = _gradient().convert("RGBA")
    draw = ImageDraw.Draw(image)
    _brand(draw, index)
    _footer(draw)
    return image, draw


def build_local_platform_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(1)
    draw.text((60, 112), "SEVEN PLATFORMS · ONE PRIVATE AGENT", font=FONT_KICKER, fill=ORANGE)
    draw.text((60, 144), "本地私有的", font=font(46, bold=True), fill=INK)
    draw.text((60, 202), "七平台内容 Agent", font=font(46, bold=True), fill=INK)
    draw.text((60, 268), "把分散的信息流，变成真正懂你", font=FONT_SUBTITLE, fill=MUTED)
    draw.text((60, 302), "且可反馈的跨平台推荐。", font=FONT_SUBTITLE, fill=MUTED)
    x, y = 60, 354
    colors = ((PINK_SOFT, PINK), (BLUE_SOFT, BLUE), (GREEN_SOFT, GREEN))
    for index, label in enumerate(PLATFORMS):
        fill, color = colors[index % len(colors)]
        x = _chip(draw, x, y, label, fill=fill, color=color, outline=fill) + 10
        if index == 3:
            x, y = 60, y + 54
    draw.rounded_rectangle((60, 468, 475, 688), radius=26, fill="#FFFFFFD9", outline=LINE)
    draw.text((84, 496), "你的数据，不是我们的数据", font=font(23, bold=True), fill=INK)
    for offset, text in enumerate(
        ("本地后端运行", "画像与反馈默认保存在本机", "状态文案不冒充实时登录验证")
    ):
        cy = 550 + offset * 42
        draw.ellipse((84, cy + 5, 94, cy + 15), fill=GREEN)
        draw.text((108, cy), text, font=FONT_BODY, fill=MUTED)
    _rounded_screenshot(image, source_dir / "desktop-recommend.png", (520, 128, 1218, 712))
    return image.convert("RGB")


def build_three_surfaces_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(2)
    _headline(
        draw,
        "Three surfaces",
        "插件、PC、手机，一套体验",
        "浏览器侧边栏随手看，桌面端深度管理，手机端轻量反馈。",
    )
    labels = (("PC Web", 62), ("浏览器插件", 756), ("Mobile Web", 1004))
    for label, x in labels:
        draw.text((x, 238), label, font=FONT_KICKER, fill=MUTED)
    _rounded_screenshot(image, source_dir / "desktop-recommend.png", (58, 270, 734, 704), radius=22)
    _rounded_screenshot(
        image,
        source_dir / "extension-recommend.png",
        (752, 270, 984, 704),
        fit=False,
        radius=22,
    )
    _rounded_screenshot(
        image,
        source_dir / "mobile-recommend.png",
        (1000, 270, 1222, 704),
        fit=False,
        radius=22,
    )
    return image.convert("RGB")


def build_recommendation_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(3)
    _headline(
        draw,
        "Cross-platform recommendations",
        "一次浏览，汇合七个平台",
        "统一筛选、解释推荐理由，再用喜欢、不感兴趣和对话继续校准。",
        x=60,
        y=112,
        max_width=1120,
    )
    draw.rounded_rectangle((60, 276, 258, 674), radius=26, fill="#FFFFFFD9", outline=LINE)
    draw.text((84, 304), "不是简单聚合", font=font(23, bold=True), fill=INK)
    bullets = (
        ("跨平台混排", PINK),
        ("每条都有理由", BLUE),
        ("反馈即时进入画像", GREEN),
    )
    for index, (label, color) in enumerate(bullets):
        y = 374 + index * 82
        draw.rounded_rectangle((84, y, 108, y + 24), radius=8, fill=color)
        draw.text((84, y + 34), label, font=FONT_BODY, fill=MUTED)
    _rounded_screenshot(image, source_dir / "desktop-recommend.png", (286, 258, 1220, 704))
    return image.convert("RGB")


def build_profile_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(4)
    _headline(
        draw,
        "Trainable private profile",
        "画像不是黑盒：看得见，也改得动",
        "兴趣、避雷、认知风格和推荐理由都可查看；反馈会继续修正它。",
    )
    _rounded_screenshot(image, source_dir / "desktop-profile.png", (58, 262, 970, 704))
    draw.rounded_rectangle((994, 262, 1222, 704), radius=26, fill="#FFFFFFD9", outline=LINE)
    draw.text((1022, 294), "你始终有控制权", font=font(22, bold=True), fill=INK)
    points = (
        ("查看", "画像如何理解你", PINK_SOFT, PINK),
        ("纠正", "兴趣与避雷方向", BLUE_SOFT, BLUE),
        ("反馈", "喜欢 / 少来点 / 对话", GREEN_SOFT, GREEN),
    )
    for index, (title, detail, fill, color) in enumerate(points):
        y = 364 + index * 98
        draw.rounded_rectangle((1020, y, 1198, y + 76), radius=18, fill=fill)
        draw.text((1038, y + 12), title, font=FONT_CHIP, fill=color)
        draw.text((1038, y + 42), detail, font=FONT_SMALL, fill=MUTED)
    return image.convert("RGB")


def build_settings_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(5)
    _headline(
        draw,
        "Truthful status · Local data",
        "登录状态说人话，数据默认在本机",
        "区分“已保存凭据”“待验证”“无需登录”，不再把本地令牌误报成接入成功。",
        max_width=1100,
    )
    draw.rounded_rectangle((58, 286, 306, 704), radius=26, fill="#FFFFFFD9", outline=LINE)
    status_rows = (
        ("凭据已就绪", "只说明本地已保存", GREEN, GREEN_SOFT),
        ("状态待验证", "没有假装访问平台", BLUE, BLUE_SOFT),
        ("无需登录", "公开内容直接发现", MUTED, "#EFEEE9"),
    )
    draw.text((84, 314), "接入状态 ≠ 来源开关", font=font(22, bold=True), fill=INK)
    for index, (title, detail, color, fill) in enumerate(status_rows):
        y = 378 + index * 92
        draw.rounded_rectangle((82, y, 282, y + 70), radius=17, fill=fill)
        draw.ellipse((98, y + 17, 110, y + 29), fill=color)
        draw.text((122, y + 10), title, font=FONT_CHIP, fill=INK)
        draw.text((98, y + 40), detail, font=FONT_SMALL, fill=MUTED)
    _rounded_screenshot(
        image,
        source_dir / "desktop-settings.png",
        (332, 270, 1220, 704),
        centering=(0.5, 0.7),
    )
    return image.convert("RGB")


def build_assets(source_dir: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    builders = (
        ("01-local-seven-platforms.png", build_local_platform_slide),
        ("02-three-surfaces.png", build_three_surfaces_slide),
        ("03-cross-platform-recommendations.png", build_recommendation_slide),
        ("04-trainable-profile.png", build_profile_slide),
        ("05-truthful-login-local-data.png", build_settings_slide),
    )
    outputs: list[Path] = []
    for filename, builder in builders:
        image = builder(source_dir)
        if image.size != CANVAS:
            raise RuntimeError(f"invalid asset dimensions for {filename}: {image.size}")
        path = output_dir / filename
        image.convert("RGB").save(path, optimize=True)
        outputs.append(path)
        print(path)
    return outputs


if __name__ == "__main__":
    build_assets(SOURCE_DIR, OUTPUT_DIR)
