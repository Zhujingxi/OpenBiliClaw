# Issue #79 Zhihu Delight Card Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the desktop `/web` delight card by showing Zhihu/text-first body previews, accessible expand/collapse controls, and a text-media fallback when no cover is available.

**Architecture:** Keep the existing backend payload unchanged: `body_text` already reaches `normalizeDelight()`. Preserve it in the desktop view model, render the full safe text into a clamped DOM region, measure overflow after each candidate switch, and reuse the same text content in the media panel whenever the cover is absent or fails. Limit all production changes to the desktop Web HTML, JavaScript, and CSS.

**Tech Stack:** Vanilla JavaScript, semantic HTML, CSS line clamping/glassmorphism, Pytest static contract tests, Node `--check` syntax validation.

## Global Constraints

- Scope is desktop Web `/web` only; do not redesign mobile Web or the extension popup.
- Do not add API fields, database migrations, dependencies, config, or CLI changes.
- Keep existing title fallback, engagement stats, queue navigation, chat, feedback, and typing guard behavior unchanged.
- Put untrusted body content into `textContent`; never inject it as HTML.
- Default body preview is at most 5 visual lines; show the toggle only when the rendered body actually overflows.
- Candidate switches and the empty-queue state reset the body to collapsed.
- Image failure must degrade to the same text-media state as a missing cover.

---

### Task 1: Delight Body Preview and Accessible Expansion

**Files:**
- Create: `tests/test_desktop_web_delight_body_preview.py`
- Modify: `src/openbiliclaw/web/desktop/index.html:126-134`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:4965-5120`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css:444-466`

**Interfaces:**
- Consumes: existing pending-batch/realtime fields `item.body_text: unknown` and `decodeHtmlEntities(value): string`.
- Produces: `normalizeDelight(item).body_text: string`, `resetDelightExcerpt(): void`, `syncDelightExcerpt(delight: object | null): void`, and DOM IDs `delightExcerpt`, `delightExcerptText`, `delightExcerptToggle`.

- [ ] **Step 1: Write the failing preview contract tests**

Create `tests/test_desktop_web_delight_body_preview.py` with:

```python
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    match = re.search(rf"function {name}\([^)]*\) \{{(?P<body>.*?)\n    \}}", APP_JS, flags=re.S)
    assert match is not None, f"{name} function not found"
    return match.group("body")


def test_delight_view_model_keeps_decoded_body_text() -> None:
    normalize = _function_body("normalizeDelight")
    assert "body_text: delightBody" in normalize


def test_delight_excerpt_has_accessible_expand_controls() -> None:
    assert 'id="delightExcerpt"' in INDEX_HTML
    assert 'id="delightExcerptText"' in INDEX_HTML
    assert 'id="delightExcerptToggle"' in INDEX_HTML
    assert 'aria-controls="delightExcerptText"' in INDEX_HTML
    assert 'aria-expanded="false"' in INDEX_HTML

    sync = _function_body("syncDelightExcerpt")
    assert ".textContent = bodyText" in sync
    assert "excerpt.scrollHeight > excerpt.clientHeight + 1" in sync
    assert 'toggle.hidden = !overflows' in sync
    assert 'toggle.setAttribute("aria-expanded", "false")' in sync


def test_delight_excerpt_css_clamps_five_lines_until_expanded() -> None:
    assert ".delight-excerpt-text" in APP_CSS
    assert "-webkit-line-clamp: 5" in APP_CSS
    assert ".delight-excerpt.is-expanded .delight-excerpt-text" in APP_CSS
    assert "-webkit-line-clamp: unset" in APP_CSS
```

- [ ] **Step 2: Run the preview tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_desktop_web_delight_body_preview.py -q
```

Expected: failures because `body_text`, the excerpt DOM, helpers, and CSS do not exist yet.

- [ ] **Step 3: Add the semantic excerpt DOM**

In `src/openbiliclaw/web/desktop/index.html`, insert the excerpt between `#delightStats` and `#delightReason`:

```html
<div id="delightExcerpt" class="delight-excerpt" hidden>
  <p id="delightExcerptText" class="delight-excerpt-text"></p>
  <button id="delightExcerptToggle" class="delight-excerpt-toggle" type="button"
    aria-expanded="false" aria-controls="delightExcerptText" hidden>展开正文</button>
</div>
```

- [ ] **Step 4: Preserve body text and implement reset/sync helpers**

In `normalizeDelight()`, add the already-decoded value to the returned object:

```javascript
body_text: delightBody,
```

Add these helpers before `setActiveDelight()`:

```javascript
function resetDelightExcerpt() {
  const wrapper = $("#delightExcerpt");
  const excerpt = $("#delightExcerptText");
  const toggle = $("#delightExcerptToggle");
  if (!wrapper || !excerpt || !toggle) return;
  wrapper.classList.remove("is-expanded");
  wrapper.hidden = true;
  excerpt.textContent = "";
  toggle.hidden = true;
  toggle.textContent = "展开正文";
  toggle.setAttribute("aria-expanded", "false");
}

function syncDelightExcerpt(delight) {
  resetDelightExcerpt();
  const wrapper = $("#delightExcerpt");
  const excerpt = $("#delightExcerptText");
  const toggle = $("#delightExcerptToggle");
  const bodyText = String(delight?.body_text || "").trim();
  if (!wrapper || !excerpt || !toggle || !bodyText) return;
  excerpt.textContent = bodyText;
  wrapper.hidden = false;
  requestAnimationFrame(() => {
    const overflows = excerpt.scrollHeight > excerpt.clientHeight + 1;
    toggle.hidden = !overflows;
    toggle.setAttribute("aria-expanded", "false");
  });
}
```

Call `resetDelightExcerpt()` in the empty-queue branch, and call
`syncDelightExcerpt(state.delight)` inside `applyContent()` immediately after setting
`#delightTitle`.

Bind `#delightExcerptToggle` once immediately after the existing
`document.querySelectorAll("[data-delight]")...` binding near the bottom of the IIFE:

```javascript
$("#delightExcerptToggle")?.addEventListener("click", () => {
  const wrapper = $("#delightExcerpt");
  const toggle = $("#delightExcerptToggle");
  if (!wrapper || !toggle) return;
  const expanded = wrapper.classList.toggle("is-expanded");
  toggle.textContent = expanded ? "收起正文" : "展开正文";
  toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  scheduleActivityRailHeightSync();
});
```

- [ ] **Step 5: Add the five-line visual treatment**

Add near `.delight-copy p` in `app.css`:

```css
.delight-excerpt { margin-top: var(--space-3); }
.delight-excerpt[hidden] { display: none; }
.delight-excerpt-text { margin: 0; color: var(--fg-2); font-size: var(--text-sm); line-height: 1.65; white-space: pre-wrap; word-break: break-word; display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden; }
.delight-excerpt.is-expanded .delight-excerpt-text { display: block; -webkit-line-clamp: unset; overflow: visible; }
.delight-excerpt-toggle { margin-top: 6px; padding: 0; border: 0; background: transparent; color: var(--accent); font: inherit; font-size: var(--text-sm); font-weight: 650; cursor: pointer; }
.delight-excerpt-toggle:hover { text-decoration: underline; }
.delight-excerpt-toggle:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }
```

- [ ] **Step 6: Run focused tests and syntax validation**

Run:

```bash
.venv/bin/python -m pytest tests/test_desktop_web_delight_body_preview.py tests/test_desktop_web_card_metadata.py tests/test_desktop_web_delight_typing_guard.py -q
node --check src/openbiliclaw/web/desktop/assets/js/app.js
```

Expected: all Pytest cases pass and `node --check` exits 0 with no output.

- [ ] **Step 7: Commit the body preview**

```bash
git add tests/test_desktop_web_delight_body_preview.py \
  src/openbiliclaw/web/desktop/index.html \
  src/openbiliclaw/web/desktop/assets/js/app.js \
  src/openbiliclaw/web/desktop/assets/css/app.css
git commit -m "feat(web): add expandable delight body preview"
```

---

### Task 2: Text Media Fallback for Missing or Failed Covers

**Files:**
- Modify: `tests/test_desktop_web_delight_body_preview.py`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:5005-5050`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css:423-444`

**Interfaces:**
- Consumes: `delight.body_text: string`, `delight.source_platform: string`, existing `platformName()` and `#delightThumb`.
- Produces: `renderDelightTextMedia(thumb: HTMLElement, delight: object): void` and `renderDelightFallbackMedia(thumb: HTMLElement, delight: object): void`; `renderDelightCover()` invokes the fallback router for missing and failed images.

- [ ] **Step 1: Extend the failing contract tests**

Append to `tests/test_desktop_web_delight_body_preview.py`:

```python
def test_missing_or_failed_delight_cover_uses_text_media_fallback() -> None:
    render_cover = _function_body("renderDelightCover")
    render_text = _function_body("renderDelightTextMedia")
    render_fallback = _function_body("renderDelightFallbackMedia")
    assert "renderDelightFallbackMedia(thumb, delight)" in render_cover
    assert "image.addEventListener(\"error\"" in render_cover
    assert "image.parentElement !== thumb" in render_cover
    assert "renderDelightTextMedia(thumb, delight)" in render_fallback
    assert "String(delight?.body_text || \"\").trim()" in render_fallback
    assert 'text.className = "delight-text-media-copy"' in render_text
    assert "text.textContent = bodyText" in render_text
    assert 'thumb.classList.add("is-text-media")' in render_text
    assert "thumb.dataset.platform" in render_text
    assert ".delight .thumb.is-text-media" in APP_CSS
    assert ".delight-text-media-copy" in APP_CSS
    assert '.delight .thumb.is-text-media[data-platform="zhihu"]' in APP_CSS
```

- [ ] **Step 2: Run the fallback test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_desktop_web_delight_body_preview.py::test_missing_or_failed_delight_cover_uses_text_media_fallback -q
```

Expected: fail because `renderDelightTextMedia` and its CSS do not exist.

- [ ] **Step 3: Implement the text-media renderer and route both fallback paths through it**

Add before `renderDelightCover()`:

```javascript
function renderDelightTextMedia(thumb, delight) {
  if (!thumb || !delight) return;
  const bodyText = String(delight.body_text || "").trim();
  if (!bodyText) return;
  thumb.replaceChildren();
  thumb.classList.remove("has-image");
  thumb.classList.add("is-text-media");
  thumb.dataset.platform = String(delight.source_platform || "bilibili").toLowerCase();
  const text = document.createElement("p");
  text.className = "delight-text-media-copy";
  text.textContent = bodyText;
  const badge = document.createElement("span");
  badge.className = "platform";
  badge.textContent = platformName(delight.source_platform);
  thumb.append(text, badge);
}

function renderDelightFallbackMedia(thumb, delight) {
  const bodyText = String(delight?.body_text || "").trim();
  if (bodyText) {
    renderDelightTextMedia(thumb, delight);
    return;
  }
  thumb.replaceChildren();
  thumb.classList.remove("has-image", "is-text-media");
  delete thumb.dataset.platform;
  if (!delight) return;
  const badge = document.createElement("span");
  badge.className = "platform";
  badge.textContent = platformName(delight.source_platform);
  thumb.append(badge);
}
```

At the start of `renderDelightCover()`, clear both media state classes:

```javascript
thumb.classList.remove("has-image", "is-text-media");
delete thumb.dataset.platform;
```

Replace the no-cover early return with:

```javascript
if (!url) {
  renderDelightFallbackMedia(thumb, delight);
  return;
}
```

Replace the image error callback with:

```javascript
image.addEventListener("error", () => {
  if (!image.isConnected || image.parentElement !== thumb) return;
  renderDelightFallbackMedia(thumb, delight);
  if (banner) banner.style.setProperty("--cover-url", "none");
});
```

Do not reuse the pre-created platform badge in fallback branches; the helper owns the
fallback DOM and creates its badge exactly once.

- [ ] **Step 4: Add source-aware glass text-media styling**

Add near `.delight .thumb`:

```css
.delight .thumb.is-text-media { display: flex; align-items: flex-start; padding: var(--space-5); background: linear-gradient(135deg, color-mix(in oklab, var(--accent), var(--surface) 58%), color-mix(in oklab, var(--fg), var(--surface-warm) 76%)); cursor: pointer; }
.delight .thumb.is-text-media[data-platform="zhihu"] { background: linear-gradient(135deg, color-mix(in oklab, var(--platform-zhihu), var(--surface) 58%), color-mix(in oklab, var(--fg), var(--surface-warm) 82%)); }
.delight .thumb.is-text-media::before { inset: 0; border: 0; border-radius: inherit; background: color-mix(in oklab, var(--surface), transparent 28%); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); }
.delight-text-media-copy { position: relative; z-index: 1; margin: 0; padding-right: 70px; color: var(--fg-2); font-size: var(--text-sm); line-height: 1.65; font-weight: 600; text-align: left; white-space: pre-wrap; word-break: break-word; display: -webkit-box; -webkit-line-clamp: 7; -webkit-box-orient: vertical; overflow: hidden; }
```

Keep `.platform` above the scrim using its existing absolute positioning. Ensure the existing
`.delight .thumb::before` selector does not override the new `is-text-media::before` rule by
placing the new block after it or using equal/higher selector specificity.

- [ ] **Step 5: Run focused tests and syntax validation**

Run:

```bash
.venv/bin/python -m pytest tests/test_desktop_web_delight_body_preview.py tests/test_desktop_web_pool_status.py tests/test_zhihu_recommendation_card_styles.py -q
node --check src/openbiliclaw/web/desktop/assets/js/app.js
```

Expected: all tests pass and `node --check` exits 0.

- [ ] **Step 6: Commit the cover fallback**

```bash
git add tests/test_desktop_web_delight_body_preview.py \
  src/openbiliclaw/web/desktop/assets/js/app.js \
  src/openbiliclaw/web/desktop/assets/css/app.css
git commit -m "feat(web): render text fallback for coverless delights"
```

---

### Task 3: Documentation and End-to-End Verification

**Files:**
- Modify: `docs/changelog.md:1-20`
- Modify: `docs/modules/recommendation.md:40-50`
- Verify: `src/openbiliclaw/web/desktop/index.html`
- Verify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Verify: `src/openbiliclaw/web/desktop/assets/css/app.css`

**Interfaces:**
- Consumes: completed desktop delight preview/fallback behavior from Tasks 1 and 2.
- Produces: user-facing changelog entry and verification evidence; no new runtime interface.

- [ ] **Step 1: Add the changelog entry**

Under the current version heading in `docs/changelog.md`, add:

```markdown
- **知乎惊喜卡正文与无封面体验收尾（issue #79）**：桌面 Web 惊喜推荐现在保留后端已下发的 `body_text`，在标题、互动指标和推荐原因之间展示最多 5 行正文预览；仅在真实溢出时出现可键盘操作的“展开正文 / 收起正文”。无封面或封面加载失败时，左侧媒体区改用正文开头与来源徽章组成的毛玻璃文字卡，不再只显示空泛渐变；切换候选和空队列会恢复折叠态，现有聊天输入保护、反馈和队列行为不变。
```

In the implemented-features table of `docs/modules/recommendation.md`, add:

```markdown
| issue #79 桌面惊喜文字卡收尾 | ✅ | 桌面 Web 惊喜卡保留 `body_text` 并显示最多 5 行正文预览，仅在实际溢出时提供可访问的展开/收起；无封面或封面加载失败时，左侧媒体区以正文和来源徽章渲染毛玻璃文字卡。候选切换与空队列重置折叠态，不改变标题兜底、互动指标、聊天输入保护和反馈语义。 |
```

- [ ] **Step 2: Run focused regression checks**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_desktop_web_delight_body_preview.py \
  tests/test_desktop_web_card_metadata.py \
  tests/test_desktop_web_delight_typing_guard.py \
  tests/test_desktop_web_pool_status.py \
  tests/test_zhihu_recommendation_card_styles.py \
  tests/test_zhihu_tasks.py -q
node --check src/openbiliclaw/web/desktop/assets/js/app.js
```

Expected: all tests pass and JavaScript syntax validation exits 0.

- [ ] **Step 3: Run repository quality gates**

Run:

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
.venv/bin/python -m pytest -q
```

Expected: Ruff, MyPy, and the complete Pytest suite pass. If an unrelated pre-existing failure
appears, record the exact command and failure without changing unrelated code.

- [ ] **Step 4: Run browser acceptance against local desktop Web**

Start the backend using the repository's configured local environment:

```bash
.venv/bin/openbiliclaw start
```

In `/web`, verify with a seeded or live Zhihu delight candidate:

1. Long body + cover: image remains; right-side body is clamped and toggles with mouse/keyboard.
2. Long body + no cover: left-side text media appears; right-side toggle works.
3. Short body: complete text is visible and the toggle is hidden.
4. Empty body: no empty excerpt block or toggle appears.
5. Failed cover request: card changes to text media without duplicate platform badges.
6. Switching candidates resets expansion; typing/chat/feedback controls retain existing behavior.

Expected: all six states match the design and no browser console errors occur.

- [ ] **Step 5: Review the final diff and commit documentation**

Run:

```bash
git diff --check
git status --short
git diff origin/main...HEAD --stat
```

Then commit:

```bash
git add docs/changelog.md docs/modules/recommendation.md
git commit -m "docs: close issue 79 zhihu delight card gap"
```

- [ ] **Step 6: Record final branch state**

Run:

```bash
git status --short --branch
git log --oneline origin/main..HEAD
```

Expected: clean `codex/issue-79-closeout` branch with the design, implementation, and changelog
commits ahead of `origin/main`.
