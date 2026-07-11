# Remove Recommendation Correction Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the newly added recommendation-area correction prompt and shortcuts from desktop, mobile, and extension while preserving the existing profile editor, chat surfaces, and all Issue #91 probe-feedback fixes.

**Architecture:** Treat the recommendation correction entry as a presentation-only rollback. Each surface removes its own markup, bindings, helper code, CSS, and dedicated positive-presence tests; replacement tests assert the recommendation header remains free of correction guidance. Backend feedback scoring, source provenance, probe descriptors, defer submission, profile editing, and chat navigation outside the recommendation area remain unchanged.

**Tech Stack:** Python static-contract tests with Pytest, vanilla JavaScript/HTML/CSS, Node test runner with TypeScript stripping, Ruff, MyPy, extension TypeScript build.

## Global Constraints

- Desktop, mobile, and extension recommendation areas must not render `推荐不准？`, `编辑画像`, or `直接告诉阿B` correction controls.
- Existing profile-page `编辑画像` controls and existing chat tabs/drawers must remain unchanged.
- Interest and avoidance probe copy remains `确认喜欢 / 暂时搁置 / 确认不喜欢 / 多聊聊` and `确认避雷 / 搁置避雷 / 不是雷点 / 多聊聊`.
- Probe actions remain `confirm / defer / reject / chat`; defer must not be converted to reject.
- Backend topic-key/topic-group scoring, feedback source provenance, Issue #98 optimistic UI, undo, and failed-request rollback are out of scope and must not change.
- No new API, configuration field, dependency, feature flag, or shared frontend abstraction.
- Mandatory module documentation and changelog must describe the final no-entry behavior.

---

### Task 1: Remove the desktop recommendation correction entry

**Files:**
- Modify: `tests/test_desktop_preference_correction.py`
- Modify: `src/openbiliclaw/web/desktop/index.html:166-178`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:1402-1409,6193-6199`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css:398-420`

**Interfaces:**
- Consumes: existing `openProfilePage()` and `openChatPage()` navigation, which remain owned by the sidebar buttons.
- Produces: a recommendation header with no correction prompt or shortcut-specific bindings.

- [ ] **Step 1: Replace the positive desktop tests with failing absence contracts**

Replace `tests/test_desktop_preference_correction.py` with:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_recommendation_header_has_no_correction_entry() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    header = html.split('<section data-od-id="recommendations">', 1)[1].split(
        '<div class="drawer-actions recommendation-actions">', 1
    )[0]

    assert "推荐不准？" not in header
    assert 'id="editProfileFromRecommendations"' not in header
    assert 'id="chatFromRecommendations"' not in header


def test_desktop_has_no_recommendation_correction_helpers_or_styles() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(
        encoding="utf-8"
    )

    for marker in (
        "openProfileCorrection",
        "openChatCorrection",
        "editProfileFromRecommendations",
        "chatFromRecommendations",
    ):
        assert marker not in js
    assert ".preference-correction-callout" not in css
```

- [ ] **Step 2: Run the desktop absence tests and verify RED**

Run:

```bash
PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest \
  tests/test_desktop_preference_correction.py -q
```

Expected: both tests fail because the recommendation callout, helper functions, bindings, and CSS still exist.

- [ ] **Step 3: Remove only the desktop recommendation-entry implementation**

In `index.html`, delete the complete `<p class="preference-correction-callout">...</p>` block under the recommendation description.

In `app.js`, delete:

```javascript
async function openProfileCorrection() {
  openProfilePage();
  await enterProfileEdit();
}

function openChatCorrection() {
  openChatPage();
}
```

and delete only these bindings:

```javascript
safeBind("#editProfileFromRecommendations", "click", openProfileCorrection);
safeBind("#chatFromRecommendations", "click", openChatCorrection);
```

Keep `openProfilePage()`, `openChatPage()`, `#profileBtn`, and `#chatBtn` intact.

In desktop `app.css`, delete the three selector blocks rooted at `.preference-correction-callout` and leave adjacent recommendation-action styles unchanged.

- [ ] **Step 4: Verify desktop behavior**

Run:

```bash
PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest \
  tests/test_desktop_preference_correction.py \
  tests/test_desktop_web_probe_defer.py \
  tests/test_desktop_web_issue_98_e2e.py -q
/Users/white/workspace/OpenBiliClaw/.venv/bin/ruff check \
  tests/test_desktop_preference_correction.py
git diff --check
```

Expected: PASS; Issue #98 pending/undo/failure tests remain green.

- [ ] **Step 5: Commit Task 1**

```bash
git add tests/test_desktop_preference_correction.py \
  src/openbiliclaw/web/desktop/index.html \
  src/openbiliclaw/web/desktop/assets/js/app.js \
  src/openbiliclaw/web/desktop/assets/css/app.css
git commit -m "fix(web): remove recommendation correction entry"
```

---

### Task 2: Remove the mobile recommendation correction entry

**Files:**
- Modify: `tests/test_mobile_preference_correction.py`
- Modify: `src/openbiliclaw/web/js/views/recommend.js:27,56,72-74,171-207,237-258`
- Modify: `src/openbiliclaw/web/js/views/profile.js:542-544`
- Modify: `src/openbiliclaw/web/css/app.css:339-363`

**Interfaces:**
- Consumes: existing mobile recommendation rendering and existing profile/chat tabs.
- Produces: no `navigateToTab()` or profile-edit dependency from the recommendation view; no recommendation-only chat-focus observer.

- [ ] **Step 1: Replace mobile correction tests with failing absence contracts**

Replace `tests/test_mobile_preference_correction.py` with:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mobile_recommendation_header_has_no_correction_entry() -> None:
    js = (ROOT / "src/openbiliclaw/web/js/views/recommend.js").read_text(encoding="utf-8")
    header = js.split("function renderRecommendationHeader()", 1)[1].split(
        "/** Re-render only the header", 1
    )[0]

    assert "推荐不准？" not in header
    assert "data-correction-target" not in header
    assert "focusChatInputWhenReady" not in js
    assert "CHAT_INPUT_FOCUS_TIMEOUT_MS" not in js


def test_mobile_recommendation_view_does_not_import_correction_navigation() -> None:
    recommend_js = (ROOT / "src/openbiliclaw/web/js/views/recommend.js").read_text(
        encoding="utf-8"
    )
    profile_js = (ROOT / "src/openbiliclaw/web/js/views/profile.js").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "src/openbiliclaw/web/css/app.css").read_text(encoding="utf-8")

    assert 'import { navigateToTab } from "../app.js";' not in recommend_js
    assert "enterProfileEditMode" not in recommend_js
    assert "export async function enterProfileEditMode()" not in profile_js
    assert ".preference-correction-callout" not in css
```

- [ ] **Step 2: Run mobile tests and verify RED**

Run:

```bash
PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest \
  tests/test_mobile_preference_correction.py -q
```

Expected: both tests fail on the current correction markup, helper, imports, wrapper, and CSS.

- [ ] **Step 3: Remove the mobile recommendation-entry implementation**

In `recommend.js`:

- remove the `navigateToTab` import;
- remove the `enterProfileEditMode` import;
- remove `CHAT_INPUT_FOCUS_TIMEOUT_MS` and its recommendation-only comment;
- remove the complete exported `focusChatInputWhenReady()` function;
- remove the complete `correction` paragraph construction/listener/append block from `renderRecommendationHeader()`.

In `profile.js`, remove only the wrapper:

```javascript
export async function enterProfileEditMode() {
  await enterEdit();
}
```

Keep private `enterEdit()` and the profile page's own edit button behavior intact.

In mobile `app.css`, delete all selectors rooted at `.preference-correction-callout`.

- [ ] **Step 4: Verify mobile behavior and formatting**

Run:

```bash
PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest \
  tests/test_mobile_preference_correction.py \
  tests/test_mobile_web_view_models.py \
  tests/test_mobile_recommend_load_resilience.py -q
/Users/white/workspace/OpenBiliClaw/.venv/bin/ruff format --check \
  tests/test_mobile_preference_correction.py \
  tests/test_mobile_web_view_models.py
/Users/white/workspace/OpenBiliClaw/.venv/bin/ruff check \
  tests/test_mobile_preference_correction.py \
  tests/test_mobile_web_view_models.py
node --check src/openbiliclaw/web/js/views/recommend.js
git diff --check
```

Expected: PASS; canonical profile probe actions and defer recovery remain covered.

- [ ] **Step 5: Commit Task 2**

```bash
git add tests/test_mobile_preference_correction.py \
  src/openbiliclaw/web/js/views/recommend.js \
  src/openbiliclaw/web/js/views/profile.js \
  src/openbiliclaw/web/css/app.css
git commit -m "fix(mobile): remove recommendation correction entry"
```

---

### Task 3: Remove the extension recommendation correction entry

**Files:**
- Modify: `extension/tests/popup-preference-correction.test.ts`
- Modify: `extension/popup/popup.html:518-543,4632-4637`
- Modify: `extension/popup/popup.js:196-197,5796-5808,7399`

**Interfaces:**
- Consumes: existing extension tab bindings and profile-page edit toggle.
- Produces: no recommendation-specific element references or bootstrap binding; profile `enterProfileEditMode()` remains because the profile toggle still calls it.

- [ ] **Step 1: Replace extension correction tests with failing absence contracts**

Replace `extension/tests/popup-preference-correction.test.ts` with:

```typescript
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const html = readFileSync(resolve("popup", "popup.html"), "utf8");
const js = readFileSync(resolve("popup", "popup.js"), "utf8");

test("recommendation header has no preference correction entry", () => {
  const header = html.slice(
    html.indexOf('class="recommendation-header-intro"'),
    html.indexOf('id="refreshRecommendationsButton"'),
  );

  assert.doesNotMatch(header, /推荐不准？/);
  assert.doesNotMatch(header, /editProfileFromRecommendations/);
  assert.doesNotMatch(header, /chatFromRecommendations/);
  assert.doesNotMatch(html, /\.preference-correction-callout/);
});

test("popup bootstrap has no recommendation correction binding", () => {
  assert.doesNotMatch(js, /bindPreferenceCorrectionActions/);
  assert.doesNotMatch(js, /editProfileFromRecommendations/);
  assert.doesNotMatch(js, /chatFromRecommendations/);
});
```

- [ ] **Step 2: Run the focused extension tests and verify RED**

Run:

```bash
cd extension
node --test --experimental-strip-types tests/popup-preference-correction.test.ts
```

Expected: both tests fail because the popup still contains the callout and bootstrap binding.

- [ ] **Step 3: Remove only the extension recommendation-entry code**

In `popup.html`, delete:

- the three `.preference-correction-callout` CSS selector blocks;
- the complete recommendation-header correction `<p>` block.

In `popup.js`, delete:

- `editProfileFromRecommendations` and `chatFromRecommendations` from `elements`;
- the complete `bindPreferenceCorrectionActions()` function;
- its single call from `initializePopup()`.

Keep `enterProfileEditMode()` because `profileEditToggle` still uses it. Keep normal `bindTabs()` and `bindChat()` unchanged.

- [ ] **Step 4: Verify extension behavior**

Run:

```bash
cd extension
node --test --experimental-strip-types \
  tests/popup-preference-correction.test.ts \
  tests/popup-message-actions.test.ts \
  tests/popup-profile-edit.test.ts
npm test
npm run typecheck
npm run build
```

Expected: all focused and full extension tests pass; typecheck and bundle build succeed.

- [ ] **Step 5: Commit Task 3**

```bash
git add extension/tests/popup-preference-correction.test.ts \
  extension/popup/popup.html extension/popup/popup.js
git commit -m "fix(extension): remove recommendation correction entry"
```

---

### Task 4: Synchronize documentation and run final branch verification

**Files:**
- Modify: `docs/superpowers/plans/2026-07-11-issue-91-preference-feedback.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: completed desktop, mobile, and extension no-entry behavior.
- Produces: final documentation and verification evidence matching the user-approved scope.

- [ ] **Step 1: Mark the original implementation plan's entry work as superseded**

Add immediately below the original plan header:

```markdown
> **2026-07-11 用户决策更新：** Tasks 3-6 中新增推荐区“推荐不准？ / 编辑画像 /
> 直接告诉阿B”入口的步骤已由
> `2026-07-11-remove-recommendation-correction-entry.md` 取代。最终实现不在桌面、移动或
> 插件推荐区保留任何纠偏引导入口；原有画像页和对话页功能保持不变。
```

Do not rewrite the historical TDD steps; the supersession notice makes their status explicit and points to the authoritative removal plan.

- [ ] **Step 2: Update module docs and changelog to the final behavior**

In `docs/modules/runtime.md`, replace the correction-entry row with:

```markdown
| 三端 probe 反馈语义 | ✅ | 桌面、移动和插件的兴趣/避雷 probe 统一使用 confirm/defer/reject/chat 语义，所有操作均有可见文字；推荐区不新增画像或对话纠偏引导入口。 |
```

In `docs/modules/extension.md`, delete the `推荐区画像纠偏入口` feature row. The existing `画像可编辑（编辑模式）` row remains unchanged.

In `docs/modules/soul.md`, replace the Issue #91 paragraph with:

```markdown
卡片 like/dislike 属于可撤销的软信号，并由后台批处理学习。单次 dislike 不会直接把某个
主题永久写成硬屏蔽；需要确定性修正时，用户仍可主动前往原有画像页写入持久 override，
或在原有对话页用自由文本说明偏好。本 Issue 不在推荐区新增纠偏引导入口。
```

In the current `docs/changelog.md` block, change the Issue #91 bullet to:

```markdown
- 修复 Issue #91：推荐反馈同时作用于细/粗 topic 且保留真实平台来源；三端兴趣/避雷
  操作改为明确文字并补齐 defer，推荐区保持原布局，不新增画像或对话引导入口。
```

- [ ] **Step 3: Verify documentation and absence markers**

Run:

```bash
git diff --check
rg -n "推荐区不新增|不新增纠偏引导入口|保持原布局" \
  docs/modules/runtime.md docs/modules/soul.md docs/changelog.md
! rg -n "推荐区画像纠偏入口" docs/modules/extension.md
! rg -n "preference-correction-callout|editProfileFromRecommendations|chatFromRecommendations|data-correction-target" \
  src/openbiliclaw/web extension/popup
```

Expected: documentation contains the final policy and no recommendation-entry implementation marker remains.

- [ ] **Step 4: Run focused regression tests**

Run:

```bash
PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest \
  tests/test_storage.py \
  tests/test_pool_curator.py \
  tests/test_api_app.py \
  tests/test_desktop_web_probe_defer.py \
  tests/test_desktop_preference_correction.py \
  tests/test_desktop_web_issue_98_e2e.py \
  tests/test_mobile_web_view_models.py \
  tests/test_mobile_preference_correction.py -q --tb=short
```

Expected: PASS with only existing environment warnings/skips.

- [ ] **Step 5: Run full repository verification**

Run:

```bash
/Users/white/workspace/OpenBiliClaw/.venv/bin/ruff format --check src tests
/Users/white/workspace/OpenBiliClaw/.venv/bin/ruff check src tests
/Users/white/workspace/OpenBiliClaw/.venv/bin/mypy src
PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest -q --tb=short
cd extension
npm test
npm run typecheck
npm run build
```

Expected branch-specific outcome: all changed Python files are formatted, Ruff lint and MyPy pass, full Python tests pass, extension tests/typecheck/build pass. If the global Ruff formatter still reports the seven byte-unchanged historical files identified in Task 6, record them without formatting unrelated scope.

- [ ] **Step 6: Perform responsive smoke verification**

At 375px, 768px, 1024px, and 1440px verify:

```text
1. No recommendation header contains “推荐不准？”, “编辑画像”, or “直接告诉阿B”.
2. Existing profile-page “编辑画像” and existing chat tab/drawer are still reachable.
3. Probe buttons retain all approved semantic labels and wrap without horizontal overflow.
4. Keyboard focus remains visible on probe actions.
5. Desktop defer submits defer, reject submits reject, and Issue #98 undo/rollback tests remain green.
```

- [ ] **Step 7: Commit Task 4**

```bash
git add docs/superpowers/plans/2026-07-11-issue-91-preference-feedback.md \
  docs/modules/runtime.md docs/modules/extension.md docs/modules/soul.md docs/changelog.md
git commit -m "docs: remove recommendation correction entry"
```

- [ ] **Step 8: Audit final branch scope**

Run:

```bash
git status --short
git diff --check main...HEAD
git diff --name-only main...HEAD | rg -n "zhihu|dispatcher|token|diet" || true
git log --oneline main..HEAD
```

Expected: clean worktree, no whitespace errors, no Zhihu dispatcher or token-diet changes, and only Issue #91 implementation/spec/plan/docs commits.
