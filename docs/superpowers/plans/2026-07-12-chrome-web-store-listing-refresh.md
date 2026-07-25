# Chrome Web Store Listing Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stale Chrome Web Store description and five screenshots with a truthful seven-platform, local-first listing built from sanitized current UI.

**Architecture:** A deterministic demo API serves the real desktop, mobile, and extension UI without reading the user's database. A capture script records sanitized source screenshots, and a Pillow builder composes five branded 1280×800 listing assets. Repository tests enforce dimensions, file order, required platform names, links, and privacy language before the Developer Dashboard submission is replaced.

**Tech Stack:** Python 3.12, stdlib `ThreadingHTTPServer`, Playwright Chromium, Pillow, existing desktop/mobile static assets, unpacked Manifest V3 extension, pytest.

## Global Constraints

- Final screenshots are exactly 1280×800 and use filenames `01-` through `05-` in submission order.
- Use only deterministic demo data; never read `config.toml`, `data/openbiliclaw.db`, Chrome cookies, API keys, device keys, account names, or real profile text.
- Platform names are exactly B站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit.
- Screenshots may show only controls present on current `main`; no invented cloud service, telemetry, or background platform probe.
- Short and detailed descriptions state that the local backend is required and data is stored locally by default.
- Listing metadata changes do not move or recreate the published `0.3.163` tags.
- Chrome Web Store submission is not reported complete until the dashboard shows the refreshed listing in `PENDING_REVIEW` or an equivalent review state.

---

### Task 1: Lock the listing and asset contract

**Files:**
- Create: `tests/test_chrome_webstore_listing.py`
- Test: `tests/test_chrome_webstore_listing.py`

**Interfaces:**
- Consumes: `docs/chrome-webstore-listing.md`, `docs/images/chrome-web-store/*.png`.
- Produces: pytest contract for required copy, asset filenames, dimensions, and seven-platform coverage.

- [ ] **Step 1: Write the failing contract test**

```python
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
LISTING = ROOT / "docs/chrome-webstore-listing.md"
ASSET_DIR = ROOT / "docs/images/chrome-web-store"
EXPECTED = [
    "01-local-seven-platforms.png",
    "02-three-surfaces.png",
    "03-cross-platform-recommendations.png",
    "04-trainable-profile.png",
    "05-truthful-login-local-data.png",
]


def test_store_listing_names_all_supported_platforms_and_local_backend() -> None:
    text = LISTING.read_text(encoding="utf-8")
    for label in ("B站", "小红书", "抖音", "YouTube", "X", "知乎", "Reddit"):
        assert label in text
    assert "本地后端" in text
    assert "数据默认保存在你的本机" in text


def test_store_listing_assets_have_stable_order_and_dimensions() -> None:
    assert [path.name for path in sorted(ASSET_DIR.glob("*.png"))] == EXPECTED
    for name in EXPECTED:
        with Image.open(ASSET_DIR / name) as image:
            assert image.size == (1280, 800)
            assert image.mode in {"RGB", "RGBA"}
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `.venv/bin/pytest -q tests/test_chrome_webstore_listing.py`

Expected: FAIL because the five new files do not exist and the current listing omits X / 知乎 / Reddit.

- [ ] **Step 3: Commit the failing contract**

```bash
git add tests/test_chrome_webstore_listing.py
git commit -m "test: lock Chrome Web Store listing contract"
```

### Task 2: Build the deterministic demo and capture current UI

**Files:**
- Create: `scripts/chrome_webstore_demo.py`
- Create: `scripts/capture_chrome_webstore_ui.py`
- Create: `tests/test_chrome_webstore_demo.py`
- Create: `docs/images/chrome-web-store/source/.gitkeep`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `src/openbiliclaw/web/desktop/`, `src/openbiliclaw/web/`, `extension/`, current extension build output.
- Produces: `demo_payload(path: str) -> tuple[int, object]`, `DemoServer`, and sanitized source captures named `desktop-recommend.png`, `desktop-profile.png`, `desktop-settings.png`, `mobile-recommend.png`, and `extension-recommend.png`.

- [ ] **Step 1: Write failing demo-payload tests**

```python
from scripts.chrome_webstore_demo import demo_payload


def test_demo_recommendations_cover_multiple_platforms_without_private_data() -> None:
    status, payload = demo_payload("/api/recommendations")
    assert status == 200
    assert {item["source_platform"] for item in payload["items"]} >= {
        "bilibili", "xiaohongshu", "zhihu", "reddit"
    }
    serialized = repr(payload)
    assert "cookie" not in serialized.lower()
    assert "token" not in serialized.lower()


def test_demo_source_status_uses_truthful_login_states() -> None:
    status, payload = demo_payload("/api/sources/status")
    assert status == 200
    assert payload["xiaohongshu"]["state"] == "ready"
    assert payload["douyin"]["state"] == "unverified"
    assert payload["reddit"]["detail"].endswith("未实时访问 Reddit 验证）。")
```

- [ ] **Step 2: Run demo tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_chrome_webstore_demo.py`

Expected: FAIL with `ModuleNotFoundError: scripts.chrome_webstore_demo`.

- [ ] **Step 3: Implement deterministic payloads and static routes**

Implement `demo_payload()` as a pure path-to-JSON mapping. Include health, auth status, recommendations, runtime status, profile summary, config, source status, favorites, watch-later, chat turns, activity feed, delight, notifications, and QR info. Serve `/web/`, `/m/`, and their assets from the repository root through `ThreadingHTTPServer`; never call `load_config()` or instantiate `Database`.

```python
class DemoServer:
    def __enter__(self) -> str:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
```

- [ ] **Step 4: Implement the capture command**

`scripts/capture_chrome_webstore_ui.py` must accept `--output-dir`. It starts `DemoServer`, launches Chromium, captures desktop and mobile pages with fixed viewports, then launches the built unpacked extension, writes the demo backend endpoint to `chrome.storage.local`, opens `popup/popup.html`, and captures the recommendation surface. It must fail if any output contains a non-demo backend host or if the extension build is missing.

Run: `.venv/bin/python scripts/capture_chrome_webstore_ui.py --output-dir docs/images/chrome-web-store/source`

Expected: five sanitized PNG source captures and no request outside loopback.

- [ ] **Step 5: Verify demo tests and inspect network logs**

Run: `.venv/bin/pytest -q tests/test_chrome_webstore_demo.py`

Expected: PASS; capture output reports only `127.0.0.1` requests.

- [ ] **Step 6: Commit the capture harness and source screenshots**

```bash
git add scripts/chrome_webstore_demo.py scripts/capture_chrome_webstore_ui.py \
  tests/test_chrome_webstore_demo.py docs/images/chrome-web-store/source .gitignore
git commit -m "feat(docs): capture sanitized store listing UI"
```

### Task 3: Compose the five branded 1280×800 assets

**Files:**
- Create: `scripts/build_chrome_webstore_assets.py`
- Create: `docs/images/chrome-web-store/01-local-seven-platforms.png`
- Create: `docs/images/chrome-web-store/02-three-surfaces.png`
- Create: `docs/images/chrome-web-store/03-cross-platform-recommendations.png`
- Create: `docs/images/chrome-web-store/04-trainable-profile.png`
- Create: `docs/images/chrome-web-store/05-truthful-login-local-data.png`
- Modify: `tests/test_chrome_webstore_listing.py`

**Interfaces:**
- Consumes: the five `source/*.png` captures from Task 2.
- Produces: `build_assets(source_dir: Path, output_dir: Path) -> list[Path]` and the five stable listing PNGs.

- [ ] **Step 1: Extend the failing asset test with required semantic order**

```python
def test_listing_document_declares_dashboard_upload_order() -> None:
    text = LISTING.read_text(encoding="utf-8")
    offsets = [text.index(name) for name in EXPECTED]
    assert offsets == sorted(offsets)
```

- [ ] **Step 2: Run the asset test and verify RED**

Run: `.venv/bin/pytest -q tests/test_chrome_webstore_listing.py`

Expected: FAIL because the listing has no asset order and the final PNGs are absent.

- [ ] **Step 3: Implement the Pillow builder**

Reuse the cross-platform font fallback and palette from `scripts/build_readme_hero_demo.py`. Each slide uses a shared header, one clear headline, at most three bullets, rounded real-UI frames, and a footer reading `本地优先 · 数据默认留在你的设备上`.

```python
CANVAS = (1280, 800)
PLATFORMS = ("B站", "小红书", "抖音", "YouTube", "X", "知乎", "Reddit")


def build_assets(source_dir: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    builders = (
        ("01-local-seven-platforms.png", build_local_platform_slide),
        ("02-three-surfaces.png", build_three_surfaces_slide),
        ("03-cross-platform-recommendations.png", build_recommendation_slide),
        ("04-trainable-profile.png", build_profile_slide),
        ("05-truthful-login-local-data.png", build_settings_slide),
    )
    outputs = []
    for filename, builder in builders:
        image = builder(source_dir)
        assert image.size == CANVAS
        path = output_dir / filename
        image.convert("RGB").save(path, optimize=True)
        outputs.append(path)
    return outputs
```

- [ ] **Step 4: Generate final assets**

Run: `.venv/bin/python scripts/build_chrome_webstore_assets.py`

Expected: five PNG paths printed in order.

- [ ] **Step 5: Run dimension/order tests and visually inspect all five files**

Run: `.venv/bin/pytest -q tests/test_chrome_webstore_listing.py`

Expected: asset dimension checks pass; copy-order check remains RED until Task 4.

- [ ] **Step 6: Commit builder and assets**

```bash
git add scripts/build_chrome_webstore_assets.py docs/images/chrome-web-store \
  tests/test_chrome_webstore_listing.py
git commit -m "docs: add refreshed Chrome Web Store screenshots"
```

### Task 4: Rewrite the listing source and documentation

**Files:**
- Modify: `docs/chrome-webstore-listing.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Test: `tests/test_chrome_webstore_listing.py`

**Interfaces:**
- Consumes: asset filenames and order from Task 3.
- Produces: exact Short Description, Detailed Description, dashboard URL fields, screenshot upload order, and submission checklist.

- [ ] **Step 1: Replace stale four-platform copy**

Set Short Description to:

```text
本地优先的七平台内容发现 AI Agent：跨平台推荐、私有画像与可反馈的侧边栏
```

Detailed Description must start with:

```text
OpenBiliClaw 是本地优先、私有、开源的七平台内容发现 Agent。它连接 B站、小红书、抖音、YouTube、X、知乎和 Reddit，把你授权范围内的使用与反馈信号交给本机运行的 OpenBiliClaw 后端，生成可解释的跨平台推荐和可持续纠正的个人画像。

插件需要本地 OpenBiliClaw 后端才能提供完整体验；数据默认保存在你的本机 SQLite 数据库，不会发送到 OpenBiliClaw 开发者服务器。
```

Then include core abilities, four installation steps, privacy boundaries, Homepage, GitHub, Releases, Support, Privacy Policy, and README_EN links. Add a `Screenshot upload order` section listing the exact five filenames.

- [ ] **Step 2: Update extension documentation and changelog**

Document that listing metadata and screenshots are maintained in the repository, generated from sanitized fixtures, and manually applied in Developer Dashboard. Record the five new assets under the current changelog block without claiming public visibility before review.

- [ ] **Step 3: Run copy and asset contracts**

Run: `.venv/bin/pytest -q tests/test_chrome_webstore_listing.py`

Expected: PASS.

- [ ] **Step 4: Commit listing copy and docs**

```bash
git add docs/chrome-webstore-listing.md docs/modules/extension.md docs/changelog.md \
  tests/test_chrome_webstore_listing.py
git commit -m "docs: refresh Chrome Web Store listing copy"
```

### Task 5: Final local verification and repository delivery

**Files:**
- Verify: all Task 1–4 files.

**Interfaces:**
- Consumes: complete listing assets and copy.
- Produces: a clean, pushed `main` ready for dashboard submission.

- [ ] **Step 1: Run focused and repository checks**

```bash
.venv/bin/ruff format --check scripts tests
.venv/bin/ruff check scripts tests
.venv/bin/pytest -q tests/test_chrome_webstore_listing.py tests/test_chrome_webstore_demo.py
cd extension && npm test && npm run typecheck && npm run build
```

Expected: all commands PASS.

- [ ] **Step 2: Inspect ignored/untracked artifacts**

Run: `git status --short --ignored`

Expected: only deliberate tracked listing files are staged/committed; `.playwright-cli/`, `.superpowers/`, temporary screenshots, zips, and build directories remain untracked/ignored and are not committed.

- [ ] **Step 3: Push main and wait for CI**

```bash
git push origin main
run_id=$(gh run list --repo whiteguo233/OpenBiliClaw --commit "$(git rev-parse HEAD)" \
  --workflow ci.yml --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$run_id" --repo whiteguo233/OpenBiliClaw --exit-status
```

Expected: main CI, Firefox build, and Web guided-init E2E all succeed.

### Task 6: Replace the Chrome Web Store listing submission

**Files:**
- Upload: `docs/images/chrome-web-store/01-local-seven-platforms.png`
- Upload: `docs/images/chrome-web-store/02-three-surfaces.png`
- Upload: `docs/images/chrome-web-store/03-cross-platform-recommendations.png`
- Upload: `docs/images/chrome-web-store/04-trainable-profile.png`
- Upload: `docs/images/chrome-web-store/05-truthful-login-local-data.png`
- Copy from: `docs/chrome-webstore-listing.md`

**Interfaces:**
- Consumes: Developer Dashboard listing form and existing `0.3.163` uploaded package.
- Produces: refreshed listing metadata and screenshots in Chrome Web Store review.

- [ ] **Step 1: Open the exact item in Developer Dashboard**

Open the OpenBiliClaw item, confirm the package version is `0.3.163`, and cancel the current submission only if the dashboard blocks listing edits while it is in review.

- [ ] **Step 2: Replace listing metadata and media**

Paste Short Description and Detailed Description exactly from the repository source. Confirm Website, Support, and Privacy Policy URLs. Delete the five old screenshots, upload the new five in filename order, and verify each preview is uncropped and readable.

- [ ] **Step 3: Submit for review**

Submit the existing `0.3.163` package plus refreshed listing. Do not enable staged rollout unless the dashboard requires it for an already approved package.

- [ ] **Step 4: Verify authoritative review state**

Expected: Developer Dashboard or the Chrome Web Store API reports `PENDING_REVIEW` for `0.3.163`. Record the submission time and dashboard state; public listing may continue showing `0.3.157` and old media until approval.

- [ ] **Step 5: Report completion**

Include commit SHA, pushed branch, CI URL, five asset paths, listing source path, and review state. Explicitly state that public visibility is pending Chrome review.
