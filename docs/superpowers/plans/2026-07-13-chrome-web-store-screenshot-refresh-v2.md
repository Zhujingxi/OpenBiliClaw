# Chrome Web Store Screenshot Refresh V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the five current Chrome Web Store images with three concise 1280×800 screenshots whose real OpenBiliClaw UI visibly renders local sanitized content covers.

**Architecture:** A deterministic Pillow script generates eight copyright-safe demo covers. The loopback demo server exposes them only through its existing `/api/image-proxy` data path, the real desktop/extension/mobile UIs render them, Playwright waits for image decode before capture, and the compositor produces three high-contrast store assets.

**Tech Stack:** Python 3.12+, Pillow, Playwright, existing static desktop/mobile/extension UI, pytest.

## Global Constraints

- Exactly three final PNG files, each RGB/RGBA at 1280×800.
- All seven recommendation rows and the delight hero have a visible local cover.
- No real platform thumbnail, creator artwork, user database, `config.toml`, Cookie, token, or account data.
- Capture permits loopback traffic only and blocks all direct external requests.
- Covers render through the real UI `cover_url -> /api/image-proxy` path; the compositor never pastes a cover into a captured UI frame.
- Use the real `extension/icons/icon128.png`; do not show a synthetic `B` mark or `openbiliclaw.com`.
- Final order is hero, three surfaces, truthful status.

---

### Task 1: Deterministic local cover and demo API pipeline

**Files:**
- Create: `scripts/build_chrome_webstore_demo_covers.py`
- Create: `docs/images/chrome-web-store/demo-covers/*.png`
- Modify: `scripts/chrome_webstore_demo.py`
- Modify: `tests/test_chrome_webstore_demo.py`

**Interfaces:**
- Produces `build_demo_covers(output_dir: Path) -> list[Path]` with filenames `01-system-design.png` through `08-delight-local-first.png`.
- Produces recommendation `cover_url` values under `https://covers.openbiliclaw.invalid/`.
- Produces `/api/delight/pending-batch` with one sanitized pending item.
- Demo HTTP `/api/image-proxy?url=<demo-cover-url>` returns the matching local PNG and rejects all other hosts.

- [ ] **Step 1: Add failing demo-cover contract tests**

```python
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from scripts.chrome_webstore_demo import DEMO_COVER_HOST, demo_payload


def test_demo_recommendations_and_delight_use_local_generated_covers() -> None:
    _, recommendations = demo_payload("/api/recommendations")
    assert len(recommendations["items"]) == 7
    for item in recommendations["items"]:
        parsed = urlsplit(item["cover_url"])
        assert parsed.scheme == "https"
        assert parsed.hostname == DEMO_COVER_HOST

    _, delight = demo_payload("/api/delight/pending-batch")
    assert len(delight["items"]) == 1
    assert urlsplit(delight["items"][0]["cover_url"]).hostname == DEMO_COVER_HOST


def test_demo_cover_files_are_complete_16_by_9_images() -> None:
    cover_dir = Path("docs/images/chrome-web-store/demo-covers")
    covers = sorted(cover_dir.glob("*.png"))
    assert len(covers) == 8
    for cover in covers:
        with Image.open(cover) as image:
            assert image.size == (640, 360)
            assert image.mode in {"RGB", "RGBA"}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_chrome_webstore_demo.py -q`

Expected: FAIL because `DEMO_COVER_HOST`, cover URLs, delight data, and cover files do not exist.

- [ ] **Step 3: Implement the deterministic editorial cover builder**

Create `scripts/build_chrome_webstore_demo_covers.py` with one focused public entry point and fixed cover specifications:

```python
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
    CoverSpec("01-system-design.png", "SYSTEM DESIGN", "一次真实重构", ("#18212F", "#FF7A59", "#F7E8D7"), "nodes"),
    CoverSpec("02-research-workflow.png", "RESEARCH FLOW", "把信息流变成工作台", ("#251B36", "#E95D9B", "#F8DCE9"), "cards"),
    CoverSpec("03-cognitive-science.png", "COGNITIVE SCIENCE", "长期兴趣如何形成", ("#0D3434", "#29B89A", "#DFF7EF"), "orbit"),
    CoverSpec("04-local-first.png", "LOCAL FIRST", "数据首先属于你", ("#202A44", "#6D8CFF", "#E6EBFF"), "device"),
    CoverSpec("05-recommendation-systems.png", "RECOMMENDATION", "推荐系统可视指南", ("#352414", "#F3A23A", "#FFF0D6"), "funnel"),
    CoverSpec("06-knowledge-flow.png", "KNOWLEDGE FLOW", "个人知识库的数据流", ("#262626", "#E3C85B", "#FFF7C9"), "layers"),
    CoverSpec("07-agent-memory.png", "AGENT MEMORY", "记忆如何真正工作", ("#2E1E35", "#B58CFF", "#EFE6FF"), "memory"),
    CoverSpec("08-delight-local-first.png", "SURPRISE PICK", "本地优先，不只是隐私", ("#172D26", "#41C98E", "#E2F7ED"), "window"),
)


def build_demo_covers(output_dir: Path = OUTPUT_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for spec in SPECS:
        image = Image.new("RGB", SIZE, spec.palette[0])
        draw = ImageDraw.Draw(image)
        draw_editorial_motif(draw, spec)
        draw_cover_copy(draw, spec)
        path = output_dir / spec.filename
        image.save(path, optimize=True)
        outputs.append(path)
    return outputs
```

Implement `draw_editorial_motif()` as deterministic rounded rectangles, lines, circles, grids, and window/device silhouettes selected by `spec.motif`; implement `draw_cover_copy()` with the repository's existing Chinese font fallback pattern. No downloaded assets or random values.

- [ ] **Step 4: Generate covers and wire demo payloads plus proxy serving**

Add to `scripts/chrome_webstore_demo.py`:

```python
DEMO_COVER_HOST = "covers.openbiliclaw.invalid"
DEMO_COVER_DIR = ROOT / "docs/images/chrome-web-store/demo-covers"


def _cover_url(filename: str) -> str:
    return f"https://{DEMO_COVER_HOST}/{filename}"


def _demo_cover_path(raw_url: str) -> Path | None:
    parsed = urlsplit(raw_url)
    if parsed.scheme != "https" or parsed.hostname != DEMO_COVER_HOST:
        return None
    candidate = (DEMO_COVER_DIR / Path(parsed.path).name).resolve()
    try:
        candidate.relative_to(DEMO_COVER_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None
```

Attach the first seven URLs to `_recommendations()`, return one fixed delight item using cover 08, and intercept `/api/image-proxy` in `do_GET()` to decode the `url` query and serve only `_demo_cover_path(url)`.

- [ ] **Step 5: Run demo tests and commit**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_chrome_webstore_demo.py -q`

Expected: all demo cover/payload tests PASS.

```bash
git add scripts/build_chrome_webstore_demo_covers.py scripts/chrome_webstore_demo.py tests/test_chrome_webstore_demo.py docs/images/chrome-web-store/demo-covers
git commit -m "feat: add local covers to store demo"
```

### Task 2: Cover-aware capture and concise three-slide composition

**Files:**
- Modify: `scripts/capture_chrome_webstore_ui.py`
- Modify: `scripts/build_chrome_webstore_assets.py`
- Modify: `tests/test_chrome_webstore_listing.py`
- Replace: `docs/images/chrome-web-store/*.png`
- Replace: `docs/images/chrome-web-store/source/*.png`

**Interfaces:**
- Capture produces four source images: `desktop-recommend.png`, `desktop-settings.png`, `mobile-recommend.png`, `extension-recommend.png`.
- Composition produces exactly:
  - `01-seven-platform-recommendations.png`
  - `02-three-surfaces.png`
  - `03-truthful-status-local-data.png`

- [ ] **Step 1: Change the asset contract tests to the new ordered set**

```python
EXPECTED = [
    "01-seven-platform-recommendations.png",
    "02-three-surfaces.png",
    "03-truthful-status-local-data.png",
]


def test_store_listing_assets_have_stable_order_dimensions_and_visual_detail() -> None:
    assert [path.name for path in sorted(ASSET_DIR.glob("*.png"))] == EXPECTED
    for name in EXPECTED:
        with Image.open(ASSET_DIR / name) as image:
            assert image.size == (1280, 800)
            assert image.mode in {"RGB", "RGBA"}
            assert len(image.convert("RGB").resize((64, 40)).getcolors(maxcolors=2560) or []) > 80
```

Update the document-order test to use the same three names.

- [ ] **Step 2: Run listing tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_chrome_webstore_listing.py -q`

Expected: FAIL because five old asset names still exist.

- [ ] **Step 3: Make capture wait for real covers and remove the unused profile capture**

Set `EXPECTED` to four source files. After each recommendation surface becomes visible, wait for decoded images:

```python
def _wait_for_covers(page: Page, selector: str, minimum: int) -> None:
    page.wait_for_function(
        """({selector, minimum}) => {
          const images = [...document.querySelectorAll(selector)];
          return images.length >= minimum && images.every((img) => img.complete && img.naturalWidth > 0);
        }""",
        {"selector": selector, "minimum": minimum},
        timeout=15_000,
    )
```

Use the real selectors for desktop cards/delight, mobile cards, and extension cards before each screenshot. Keep the loopback route guard unchanged and delete the profile navigation/capture.

- [ ] **Step 4: Rewrite composition around three dominant UI crops**

Keep `CANVAS=(1280, 800)` and the existing font helper, but replace the five builders with:

```python
def build_hero_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(1, count=3)
    _brand(draw)
    draw.text((64, 118), "七平台内容推荐，", font=font(50, bold=True), fill=INK)
    draw.text((64, 178), "数据默认留在本机", font=font(50, bold=True), fill=INK)
    _platform_row(draw, x=64, y=258)
    _rounded_screenshot(image, source_dir / "desktop-recommend.png", (64, 322, 1218, 714), centering=(0.60, 0.44))
    return image.convert("RGB")


def build_three_surfaces_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(2, count=3)
    draw.text((64, 112), "PC、插件、手机，一套推荐体验", font=font(46, bold=True), fill=INK)
    _rounded_screenshot(image, source_dir / "desktop-recommend.png", (58, 232, 706, 704), centering=(0.63, 0.48))
    _rounded_screenshot(image, source_dir / "extension-recommend.png", (732, 232, 966, 704), fit=False)
    _rounded_screenshot(image, source_dir / "mobile-recommend.png", (990, 232, 1222, 704), fit=False)
    return image.convert("RGB")


def build_status_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(3, count=3)
    draw.text((64, 112), "登录状态说人话，数据默认在本机", font=font(46, bold=True), fill=INK)
    _status_legend(draw, x=64, y=238)
    _rounded_screenshot(image, source_dir / "desktop-settings.png", (314, 224, 1220, 704), centering=(0.64, 0.66))
    return image.convert("RGB")
```

Use `extension/icons/icon128.png` in `_brand()`, a darker neutral canvas, and no domain footer. Delete old final PNGs before writing the three new files.

- [ ] **Step 5: Build, capture, compose, and inspect all three images**

```bash
cd extension && npm run build && cd ..
PYTHONPATH=src .venv/bin/python scripts/capture_chrome_webstore_ui.py --output-dir docs/images/chrome-web-store/source
.venv/bin/python scripts/build_chrome_webstore_assets.py
```

Expected: four source captures, three final files, and capture output confirming only loopback was allowed. Inspect each final file at original resolution and correct any crop that hides covers or makes text unreadable.

- [ ] **Step 6: Run focused tests and commit**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_chrome_webstore_demo.py tests/test_chrome_webstore_listing.py -q`

Expected: all demo and final-asset contract tests PASS.

```bash
git add scripts/capture_chrome_webstore_ui.py scripts/build_chrome_webstore_assets.py tests/test_chrome_webstore_listing.py docs/images/chrome-web-store
git commit -m "docs: rebuild concise Chrome Web Store screenshots"
```

### Task 3: Listing documentation, reproducibility, and final verification

**Files:**
- Modify: `docs/chrome-webstore-listing.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Documents the three filenames/order and cover-generation command.
- Preserves the Developer Dashboard upload boundary.

- [ ] **Step 1: Update the canonical listing instructions**

Replace the five-image list with:

```markdown
1. `01-seven-platform-recommendations.png` — 七平台推荐主视觉，所有推荐和惊喜位都有本地脱敏头图
2. `02-three-surfaces.png` — PC、插件、手机三端推荐体验
3. `03-truthful-status-local-data.png` — 诚实接入状态与本地数据
```

Add the reproducible cover step before capture:

```bash
.venv/bin/python scripts/build_chrome_webstore_demo_covers.py
cd extension && npm run build && cd ..
PYTHONPATH=src .venv/bin/python scripts/capture_chrome_webstore_ui.py --output-dir docs/images/chrome-web-store/source
.venv/bin/python scripts/build_chrome_webstore_assets.py
```

State that the eight covers are deterministic local illustrations, not real platform media.

- [ ] **Step 2: Synchronize extension module and changelog documentation**

Update `docs/modules/extension.md` to describe the three-image output, local cover builder, loopback image proxy, and four source captures. Update the current changelog bullet from “V2 design” to completed V2 assets and name the test guarantees.

- [ ] **Step 3: Run full verification**

```bash
PYTHONPATH=src .venv/bin/ruff check src tests scripts/build_chrome_webstore_demo_covers.py scripts/chrome_webstore_demo.py scripts/capture_chrome_webstore_ui.py scripts/build_chrome_webstore_assets.py
PYTHONPATH=src .venv/bin/pytest -q
cd extension && npm test && npm run typecheck && npm run build
```

Expected: Ruff, all Python tests, all extension tests, typecheck, and build PASS.

- [ ] **Step 4: Verify deterministic regeneration and repository boundaries**

```bash
.venv/bin/python scripts/build_chrome_webstore_demo_covers.py
.venv/bin/python scripts/build_chrome_webstore_assets.py
git diff --check
git status --short
```

Expected: regeneration creates no diff; only intended tracked files are changed; `.playwright-cli/` remains untouched and untracked.

- [ ] **Step 5: Commit documentation and prepare final preview**

```bash
git add docs/chrome-webstore-listing.md docs/modules/extension.md docs/changelog.md
git commit -m "docs: document concise store screenshot pipeline"
```

Show the three final PNGs directly in the handoff. Do not claim they are live in Chrome Web Store until Developer Dashboard state is independently verified.
