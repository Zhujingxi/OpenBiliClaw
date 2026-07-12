"""Build deterministic, copyright-safe covers for store-listing demo data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs/images/chrome-web-store/demo-covers"
SIZE = (640, 360)


@dataclass(frozen=True)
class CoverSpec:
    filename: str
    eyebrow: str
    title: str
    palette: tuple[str, str, str]
    motif: str


SPECS = (
    CoverSpec(
        "01-system-design.png",
        "SYSTEM DESIGN",
        "一次真实重构",
        ("#17212E", "#FF7658", "#F6D8C7"),
        "nodes",
    ),
    CoverSpec(
        "02-research-workflow.png",
        "RESEARCH FLOW",
        "信息流变成工作台",
        ("#2A1C35", "#EC609C", "#F7D6E5"),
        "cards",
    ),
    CoverSpec(
        "03-cognitive-science.png",
        "COGNITIVE SCIENCE",
        "长期兴趣如何形成",
        ("#113634", "#2BC29C", "#D5F4E9"),
        "orbit",
    ),
    CoverSpec(
        "04-local-first.png",
        "LOCAL FIRST",
        "数据首先属于你",
        ("#202A44", "#6E8EFF", "#DFE6FF"),
        "device",
    ),
    CoverSpec(
        "05-recommendation-systems.png",
        "RECOMMENDATION",
        "推荐系统可视指南",
        ("#382615", "#F2A23D", "#FFE8BE"),
        "funnel",
    ),
    CoverSpec(
        "06-knowledge-flow.png",
        "KNOWLEDGE FLOW",
        "知识库的数据流",
        ("#262626", "#E4CA60", "#FFF2AE"),
        "layers",
    ),
    CoverSpec(
        "07-agent-memory.png",
        "AGENT MEMORY",
        "记忆如何真正工作",
        ("#301F39", "#B58CFF", "#E9DCFF"),
        "memory",
    ),
    CoverSpec(
        "08-delight-local-first.png",
        "SURPRISE PICK",
        "本地优先，不只是隐私",
        ("#172E26", "#42CB8F", "#D8F5E8"),
        "window",
    ),
)


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/STHeiti Medium.ttc"
        if bold
        else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex(color: str) -> tuple[int, int, int]:
    value = color.removeprefix("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _mix(first: str, second: str, ratio: float) -> tuple[int, int, int]:
    left, right = _hex(first), _hex(second)
    return tuple(round(left[index] * (1 - ratio) + right[index] * ratio) for index in range(3))


def _background(spec: CoverSpec) -> Image.Image:
    image = Image.new("RGB", SIZE, spec.palette[0])
    pixels = image.load()
    for y in range(SIZE[1]):
        for x in range(SIZE[0]):
            horizontal = (x / SIZE[0]) * 0.18
            glow = max(0.0, 1.0 - (((x - 530) / 330) ** 2 + ((y - 80) / 260) ** 2))
            pixels[x, y] = _mix(spec.palette[0], spec.palette[1], horizontal + glow * 0.18)
    return image


def _draw_grid(draw: ImageDraw.ImageDraw, color: tuple[int, int, int]) -> None:
    for x in range(24, SIZE[0], 40):
        draw.line((x, 0, x, SIZE[1]), fill=color, width=1)
    for y in range(20, SIZE[1], 40):
        draw.line((0, y, SIZE[0], y), fill=color, width=1)


def _nodes(draw: ImageDraw.ImageDraw, spec: CoverSpec) -> None:
    accent, pale = spec.palette[1], spec.palette[2]
    points = ((420, 86), (548, 126), (452, 222), (570, 270))
    for first, second in ((0, 1), (0, 2), (1, 3), (2, 3), (1, 2)):
        draw.line((*points[first], *points[second]), fill=_mix(accent, pale, 0.45), width=5)
    for index, (x, y) in enumerate(points):
        radius = 33 if index in {0, 3} else 24
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=pale, outline=accent, width=5)
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=accent)


def _cards(draw: ImageDraw.ImageDraw, spec: CoverSpec) -> None:
    accent, pale = spec.palette[1], spec.palette[2]
    for index in range(3):
        x, y = 390 + index * 38, 70 + index * 54
        draw.rounded_rectangle((x, y, x + 164, y + 116), radius=18, fill=_mix(pale, "#FFFFFF", 0.12), outline=accent, width=3)
        draw.rounded_rectangle((x + 18, y + 20, x + 76, y + 36), radius=8, fill=accent)
        draw.line((x + 18, y + 58, x + 136, y + 58), fill=spec.palette[0], width=7)
        draw.line((x + 18, y + 80, x + 108, y + 80), fill=_mix(spec.palette[0], pale, 0.5), width=5)


def _orbit(draw: ImageDraw.ImageDraw, spec: CoverSpec) -> None:
    accent, pale = spec.palette[1], spec.palette[2]
    center = (500, 178)
    for radius in (54, 94, 132):
        draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), outline=_mix(accent, pale, 0.25), width=3)
    draw.ellipse((456, 134, 544, 222), fill=pale)
    draw.ellipse((480, 158, 520, 198), fill=accent)
    for x, y in ((500, 46), (594, 178), (432, 246), (555, 98)):
        draw.ellipse((x - 13, y - 13, x + 13, y + 13), fill=accent, outline=pale, width=4)


def _device(draw: ImageDraw.ImageDraw, spec: CoverSpec) -> None:
    accent, pale = spec.palette[1], spec.palette[2]
    draw.rounded_rectangle((372, 78, 568, 224), radius=18, fill=pale, outline=accent, width=5)
    draw.rounded_rectangle((390, 98, 550, 204), radius=9, fill=spec.palette[0])
    draw.rectangle((350, 228, 590, 244), fill=pale)
    draw.rounded_rectangle((514, 154, 602, 286), radius=18, fill=spec.palette[0], outline=pale, width=5)
    draw.rounded_rectangle((526, 172, 590, 258), radius=8, fill=accent)
    draw.ellipse((554, 270, 562, 278), fill=pale)


def _funnel(draw: ImageDraw.ImageDraw, spec: CoverSpec) -> None:
    accent, pale = spec.palette[1], spec.palette[2]
    for index, width in enumerate((210, 162, 112)):
        x = 498 - width // 2
        y = 72 + index * 64
        draw.rounded_rectangle((x, y, x + width, y + 38), radius=19, fill=_mix(pale, accent, index * 0.26), outline=accent, width=2)
    draw.polygon(((466, 238), (530, 238), (512, 290), (484, 290)), fill=accent)
    draw.ellipse((482, 300, 514, 332), fill=pale, outline=accent, width=4)


def _layers(draw: ImageDraw.ImageDraw, spec: CoverSpec) -> None:
    accent, pale = spec.palette[1], spec.palette[2]
    for index in range(4):
        inset = index * 18
        y = 78 + index * 54
        draw.rounded_rectangle((374 + inset, y, 602 - inset, y + 42), radius=14, fill=_mix(pale, accent, index * 0.18), outline=accent, width=2)
        draw.ellipse((392 + inset, y + 13, 408 + inset, y + 29), fill=spec.palette[0])
        draw.line((422 + inset, y + 21, 558 - inset, y + 21), fill=spec.palette[0], width=5)


def _memory(draw: ImageDraw.ImageDraw, spec: CoverSpec) -> None:
    accent, pale = spec.palette[1], spec.palette[2]
    for row in range(3):
        for column in range(3):
            x, y = 382 + column * 70, 72 + row * 70
            active = (row + column) % 2 == 0
            draw.rounded_rectangle(
                (x, y, x + 52, y + 52),
                radius=14,
                fill=accent if active else pale,
                outline=pale,
                width=3,
            )
    draw.rounded_rectangle((426, 278, 550, 310), radius=16, fill=pale)
    draw.ellipse((441, 288, 453, 300), fill=accent)
    draw.line((466, 294, 532, 294), fill=spec.palette[0], width=5)


def _window(draw: ImageDraw.ImageDraw, spec: CoverSpec) -> None:
    accent, pale = spec.palette[1], spec.palette[2]
    draw.rounded_rectangle((360, 70, 606, 286), radius=24, fill=pale, outline=accent, width=5)
    draw.rounded_rectangle((360, 70, 606, 112), radius=24, fill=accent)
    draw.rectangle((360, 92, 606, 112), fill=accent)
    for index in range(3):
        draw.ellipse((380 + index * 24, 86, 392 + index * 24, 98), fill=spec.palette[0])
    draw.rounded_rectangle((386, 140, 580, 206), radius=16, fill=spec.palette[0])
    draw.ellipse((404, 162, 424, 182), fill=accent)
    draw.line((438, 172, 554, 172), fill=pale, width=7)
    draw.rounded_rectangle((386, 224, 498, 252), radius=14, fill=accent)


MOTIFS = {
    "nodes": _nodes,
    "cards": _cards,
    "orbit": _orbit,
    "device": _device,
    "funnel": _funnel,
    "layers": _layers,
    "memory": _memory,
    "window": _window,
}


def _draw_cover(spec: CoverSpec) -> Image.Image:
    image = _background(spec)
    draw = ImageDraw.Draw(image)
    _draw_grid(draw, _mix(spec.palette[0], spec.palette[2], 0.13))
    draw.rounded_rectangle((28, 28, 326, 332), radius=26, fill=_mix(spec.palette[0], "#000000", 0.08))
    eyebrow_font = _font(14, bold=True)
    eyebrow_box = draw.textbbox((0, 0), spec.eyebrow, font=eyebrow_font)
    eyebrow_width = eyebrow_box[2] - eyebrow_box[0] + 28
    draw.rounded_rectangle((52, 58, 52 + eyebrow_width, 86), radius=14, fill=spec.palette[1])
    draw.text((66, 64), spec.eyebrow, font=eyebrow_font, fill=spec.palette[0])
    title_y = 118
    for line in spec.title.replace("，", "，\n").splitlines():
        draw.text((52, title_y), line, font=_font(32, bold=True), fill="#FFFFFF")
        title_y += 47
    draw.line((52, 278, 176, 278), fill=spec.palette[1], width=5)
    draw.text((52, 294), "OpenBiliClaw Demo", font=_font(15, bold=True), fill=spec.palette[2])
    MOTIFS[spec.motif](draw, spec)
    return image


def build_demo_covers(output_dir: Path = OUTPUT_DIR) -> list[Path]:
    """Build and return the eight deterministic demo-cover paths."""

    output_dir.mkdir(parents=True, exist_ok=True)
    expected = {spec.filename for spec in SPECS}
    for stale in output_dir.glob("*.png"):
        if stale.name not in expected:
            stale.unlink()
    outputs: list[Path] = []
    for spec in SPECS:
        path = output_dir / spec.filename
        _draw_cover(spec).save(path, optimize=True)
        outputs.append(path)
        print(path)
    return outputs


if __name__ == "__main__":
    build_demo_covers()
