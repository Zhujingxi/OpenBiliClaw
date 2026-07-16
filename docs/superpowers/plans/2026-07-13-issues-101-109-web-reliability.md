# Issues #101–#109 Web Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the remaining behavior in issues #101–#109 by making desktop boot progressive, retaining in-flight and persisted UI state across every render, using available-width responsive Delight layout, and treating failed dialogue as a safe failed operation rather than learned conversation.

**Architecture:** Keep the existing HTTP schemas and three independently bundled Web clients. Correct the boundaries that currently conflate unrelated resources or states: desktop resources settle and render independently; probe and Delight DOM is projected from retained state; dialogue exceptions propagate to callers and are classified only at public boundaries; CSS container queries adapt Delight to flex-allocated width. Already-landed #102 and #105 behavior is preserved and locked with browser coverage rather than rewritten.

**Tech Stack:** Python 3.11+, FastAPI, asyncio, browser JavaScript modules, Chrome extension popup JavaScript, CSS container queries, pytest, Playwright/Chromium, Node `node:test`, Ruff, MyPy.

## Global Constraints

- Do not add a service worker, localStorage recommendation snapshot, offline Web cache, direct CDN image path, favorite/watch-later batch API, shared frontend framework, or new dependency.
- Do not relax `/api/image-proxy` validation or change the existing Delight API schema; `state="liked"` remains authoritative.
- Do not redesign or roll back PR #102's flex-flow drawer behavior.
- Keep `AUTO_LOAD_ROOT_MARGIN_PX = 50`; #105 is a verification/documentation closeout, not a second implementation.
- Keep the drag activation dead zone at exactly 10px and the Delight navigation switch threshold at exactly 50px.
- Preserve recommendation/runtime 1/2/4/8-second bounded, single-flight recovery and generation guards.
- Preserve the post-recommendation runtime reread because serving recommendations consumes inventory.
- The first four desktop recommendation covers are eager; later recommendation covers are native-lazy. Delight remains eager/high priority.
- Never expose raw provider exceptions, credentials, request bodies, or upstream payloads to a client.
- A failed dialogue turn must not retain provisional history, append a synthetic agent reply, call `learn_from_dialogue()`, write success cognition, or publish success events.
- Update the applicable module docs and `docs/changelog.md` in every production-code commit. Update all required architecture surfaces for the dialogue data-flow change.
- Do not modify config or installer documentation: no config field, dependency, or installation flow changes.
- Do not close/comment on GitHub issues, push, or publish a PR during local implementation.
- Preserve the pre-existing untracked `.playwright-cli/` directory and unrelated user changes.

---

## File Structure

### Desktop boot and cover scheduling

- `src/openbiliclaw/web/desktop/assets/js/app.js`: bounded auth/liveness reads, independently applied hydration resources, recommendation-cover loading policy.
- `tests/test_desktop_web_issue_98_e2e.py`: real-browser progressive-hydration and runtime-reconciliation regression fixture.
- `tests/test_desktop_web_card_metadata.py`: source contract for the four-cover eager boundary.
- `tests/test_desktop_web_pool_status.py`: source contract that broad `Promise.all` no longer gates primary resources and recovery remains resource-specific.
- `tests/test_desktop_web_autoload_margin_e2e.py`: stub compatibility with lightweight `/api/ping`; existing #105 geometry remains unchanged.
- `docs/modules/runtime.md`: desktop boot ownership, cover pressure, #102/#105 contracts, and responsive drawer behavior.

### Drawer, drag, and responsive Delight

- `src/openbiliclaw/web/desktop/assets/css/app.css`: inline-size container declaration and Delight-only compact layout rules.
- `tests/test_desktop_web_issue_98_e2e.py`: drawer geometry/ARIA, drag threshold, and medium-width no-overflow browser regressions.
- `tests/test_desktop_web_motion_polish.py`: exact source constants and container-query fallback contracts.

### Probe pending state and domain-aware feedback

- `src/openbiliclaw/web/js/views/chat.js`: module-scoped keyed in-flight probe state and render projection.
- `src/openbiliclaw/web/js/probe-notification-helpers.js`: existing normalized probe key consumed by the in-flight map.
- `src/openbiliclaw/web/desktop/assets/js/app.js`: one domain-aware helper for profile/inbox result copy and toast copy.
- `tests/test_mobile_web_probe_delight_e2e.py`: new loopback mobile-browser fixture for overlay rebuild and duplicate-submit behavior.
- `tests/js/mobile-probe-notification-helpers.test.mjs`: key isolation contract for interest and avoidance probes.
- `tests/test_probe_message_treatments.py`: desktop domain-aware copy source contract.
- `tests/test_desktop_web_issue_98_e2e.py`: desktop toast subject regression.
- `docs/mobile-web-spec.md`: retained pending-state lifecycle.

### Cross-surface Delight projection

- `src/openbiliclaw/web/js/view-models.js`: explicit mobile Delight projection fields.
- `src/openbiliclaw/web/js/views/recommend.js`: render result copy and action group independently; expose selected like state.
- `src/openbiliclaw/web/css/app.css`: mobile liked-button selected styling.
- `src/openbiliclaw/web/desktop/index.html`: initial desktop Delight like ARIA state.
- `src/openbiliclaw/web/desktop/assets/js/app.js`: synchronize active/reloaded desktop Delight liked state.
- `extension/popup/popup-helpers.js`: matching extension Delight projection fields.
- `extension/popup/popup.js`: generated extension like ARIA/disabled projection.
- `tests/test_mobile_web_view_models.py`: mobile projection unit contracts.
- `tests/test_mobile_web_delight_layout.py`: mobile renderer source contracts.
- `tests/test_mobile_web_probe_delight_e2e.py`: mobile local-click and reload DOM regressions.
- `tests/test_desktop_web_issue_98_e2e.py`: desktop rehydrated liked state regression.
- `extension/tests/popup-helpers.test.ts`: extension projection unit contracts.
- `docs/modules/recommendation.md`: liked retention and duplicate-like semantics.
- `docs/modules/extension.md`: side-panel liked-state projection.

### Typed, non-learning dialogue failure

- `src/openbiliclaw/llm/base.py`: recognize service-layer empty response and built-in timeout; expose a safe fallback classifier.
- `src/openbiliclaw/soul/dialogue.py`: provisional history rollback and success-only learning.
- `src/openbiliclaw/api/app.py`: safe contextual failure responses and failed durable-turn persistence.
- `src/openbiliclaw/cli.py`: print a safe reason and keep the interactive session running.
- `src/openbiliclaw/integrations/openclaw/operations.py`: safe adapter failure copy with the original exception chained only internally.
- `tests/test_llm_service.py`: classification table including empty service response, timeout, and unknown fallback.
- `tests/test_soul_dialogue.py`: history rollback and no-learning regression.
- `tests/test_api_app.py`: durable and contextual API failure contracts.
- `tests/test_cli.py`: recoverable CLI dialogue failure.
- `tests/test_openclaw_cli.py`: adapter-safe failure contract.
- `tests/test_desktop_web_pool_status.py`, `tests/test_mobile_web_delight_layout.py`, `extension/tests/popup-helpers.test.ts`: clients render `turn.error` for `status="failed"`.
- `docs/modules/soul.md`, `docs/modules/llm.md`, `docs/modules/cli.md`: public failure semantics.
- `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`, `docs/diagrams/web-architecture.html`: progressive resource flow and success-only dialogue-to-learning flow.
- `docs/changelog.md`: one current-release entry covering the user-visible fixes.

---

### Task 1: Progressive desktop hydration and bounded cover pressure (#101)

**Files:**
- Modify: `tests/test_desktop_web_issue_98_e2e.py`
- Modify: `tests/test_desktop_web_card_metadata.py`
- Modify: `tests/test_desktop_web_pool_status.py`
- Modify: `tests/test_desktop_web_autoload_margin_e2e.py`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: `fetchAuthStatus()`, `requestJson()`, `requestJsonStrict()`, `readRecommendationSnapshot()`, `applyRuntimeStatus()`, `renderVideos()`, existing desktop recovery schedulers.
- Produces: `DESKTOP_EAGER_COVER_COUNT = 4`; `coverImg(item, { eager = true } = {})`; independently settled primary/secondary branches inside `hydrateFromBackend()`; lightweight `/api/ping` liveness.

- [ ] **Step 1: Add failing progressive-hydration browser coverage**

Extend `Issue98Stub` with `health_delay_seconds`, `recommendation_reads`, and `runtime_reads`. Serve `/api/ping` immediately and delay only `/api/health`:

```python
class Issue98Stub:
    def __init__(self) -> None:
        self.health_delay_seconds = 0.0
        self.recommendation_reads = 0
        self.runtime_reads = 0
        self.feedback_posts: list[dict[str, Any]] = []
        self.feedback_received = threading.Event()
        self.feedback_delay_seconds = 0.0
        self.feedback_status = 200
        self.probe_posts: list[dict[str, Any]] = []
        self.probe_received = threading.Event()
        self.probe_status = 200


if path == "/api/ping":
    return _json_response(self, {"ok": True})
if path == "/api/health":
    if state.health_delay_seconds:
        time.sleep(state.health_delay_seconds)
    return _json_response(self, {"ok": True, "embedding_ready": True})
if path == "/api/recommendations":
    state.recommendation_reads += 1
    return _json_response(self, {"items": _recommendations()})
if path == "/api/runtime-status":
    state.runtime_reads += 1
    available = 30 if state.runtime_reads == 1 else 27
    return _json_response(self, {"initialized": True, "pool_available_count": available, "pool_size": 30})
```

Add a browser test whose health delay is 4 seconds but whose card deadline is 1.5 seconds, then prove the post-recommendation runtime reread reaches 27:

```python
def test_fast_recommendations_render_before_slow_health_and_runtime_reconciles(
    issue_98_server: tuple[str, Issue98Stub], chromium_page: Page
) -> None:
    base_url, stub = issue_98_server
    stub.health_delay_seconds = 4.0
    chromium_page.goto(f"{base_url}/web/", wait_until="domcontentloaded")

    expect(chromium_page.locator("#videoGrid .video-card")).to_have_count(3, timeout=1500)
    expect(chromium_page.locator("#poolAvailable")).to_contain_text("27", timeout=3000)
    assert stub.recommendation_reads == 1
    assert stub.runtime_reads >= 2
```

Add `/api/ping` responses to every desktop Playwright loopback stub that serves `/api/health`, including `tests/test_desktop_web_autoload_margin_e2e.py`, so tests exercise the production boot path instead of falling through to 404.

- [ ] **Step 2: Add failing cover-scheduling and source-structure contracts**

In `tests/test_desktop_web_card_metadata.py`, require the first-four boundary and both native loading modes:

```python
def test_desktop_recommendation_covers_bound_eager_loading_to_first_four() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "const DESKTOP_EAGER_COVER_COUNT = 4;" in app_js
    assert "index < DESKTOP_EAGER_COVER_COUNT" in app_js
    assert 'loading="${eager ? "eager" : "lazy"}"' in app_js
    assert 'fetchpriority="${eager ? "high" : "low"}"' in app_js
```

Add a real-browser scheduling case to the issue-98 fixture. Let the stub return 80 image-backed recommendations, count `/api/image-proxy` requests, and return a local 1×1 PNG for every proxy request. Before scrolling, inspect the rendered attributes and request count:

```python
cards = chromium_page.locator("#videoGrid .video-card")
expect(cards).to_have_count(80)
for index in range(4):
    expect(cards.nth(index).locator("img")).to_have_attribute("loading", "eager")
    expect(cards.nth(index).locator("img")).to_have_attribute("fetchpriority", "high")
for index in range(4, 80):
    expect(cards.nth(index).locator("img")).to_have_attribute("loading", "lazy")
    expect(cards.nth(index).locator("img")).to_have_attribute("fetchpriority", "low")
chromium_page.wait_for_timeout(400)
assert 4 <= stub.image_proxy_reads < 80
```

This fixture must use only the loopback proxy endpoint; it must not allow the browser or test server to fetch the synthetic upstream cover URL.

In `tests/test_desktop_web_pool_status.py`, reject the old eleven-resource barrier and require independently attached recommendation/runtime handlers plus secondary `Promise.allSettled` bookkeeping:

```python
def test_desktop_hydration_does_not_gate_cards_on_secondary_resources() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    body = _function_body(app_js, "hydrateFromBackend")

    assert "Promise.all([" not in body
    assert "recommendationsPromise.then" in body
    assert "runtimePromise.then" in body
    assert "Promise.allSettled(secondaryPromises)" in body
    assert "ENDPOINTS.ping" in body
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/test_desktop_web_card_metadata.py tests/test_desktop_web_pool_status.py -q
.venv/bin/pytest tests/test_desktop_web_issue_98_e2e.py::test_fast_recommendations_render_before_slow_health_and_runtime_reconciles -q
```

Expected: FAIL because all resources are still applied after one broad `Promise.all`, `/api/health` is still liveness, and every cover is eager.

- [ ] **Step 4: Implement independent resource application**

Insert the `ping` property into the existing `ENDPOINTS` object and add the first-four constant beside the auto-load constants:

```js
ping: "/ping",
const DESKTOP_EAGER_COVER_COUNT = 4;
```

Bound `fetchAuthStatus()` with the existing request timeout/abort pattern so it either returns a real auth status or throws within 5 seconds. Do not interpret a timeout as authenticated; render the public shell unavailable and allow the existing reconnect/hydration path to retry.

Refactor `hydrateFromBackend()` so authentication remains the only prerequisite and each resource owns its application. Use strict reads for recommendations/runtime, keep their existing failure-state helpers, and settle secondary resources without gating primary render:

```js
const recommendationsPromise = readRecommendationSnapshot();
const runtimePromise = readRuntimeSnapshot();

recommendationsPromise.then(
  (items) => applyInitialRecommendations(items),
  () => markDesktopRecommendationFailedAndRecover(),
);
runtimePromise.then(
  (snapshot) => applyInitialRuntimeSnapshot(snapshot),
  () => markDesktopRuntimeFailedAndRecover(),
);

const secondaryPromises = [
  requestJson(ENDPOINTS.health).then(applyHealthSnapshot),
  requestJson(ENDPOINTS.initStatus).then(applyInitStatusSnapshot),
  requestJson(`${ENDPOINTS.activityFeed}?limit=5`).then(applyActivitySnapshot),
  requestJson(ENDPOINTS.profile).then(applyProfileSnapshot),
  requestJson(ENDPOINTS.delightBatch).then(applyDelightSnapshot),
  requestJson(ENDPOINTS.notificationPending).then(applyNotificationSnapshot),
  requestJson(`${ENDPOINTS.chatTurns}?session=webui&scope=chat&limit=20`).then(applyChatSnapshot),
  requestJson(`${ENDPOINTS.chatTurns}?session=webui&scope=delight&limit=80`).then(applyDelightChatSnapshot),
  requestJson(ENDPOINTS.config).then(applyConfigSnapshot),
];
await Promise.allSettled(secondaryPromises);
```

Define those helpers with exact single-argument signatures and route them to the existing owners:

- `applyInitialRecommendations(items)`: call `applyDesktopRecommendationSnapshot(items, { replace: true })`.
- `markDesktopRecommendationFailedAndRecover()`: when `state.videos` is empty, set `desktopRecommendationLoadState = "failed"` and call `scheduleDesktopRecommendationRecovery()`; otherwise call `clearDesktopRecommendationRecovery("ready")`.
- `readRuntimeSnapshot()`: return `readRuntimeStatusSnapshot()`.
- `applyInitialRuntimeSnapshot(snapshot)`: call `applyDesktopRuntimeSnapshot(snapshot, capturedGeneration)` only when its captured generation still equals `desktopRuntimeGeneration`.
- `markDesktopRuntimeFailedAndRecover()`: set `desktopRuntimeLoadState = "failed"`, call `scheduleDesktopRuntimeRecovery()`, then `renderDesktopRuntimeFailure()` when the generation still matches.
- `applyHealthSnapshot(snapshot)`: set `#statusLabel` to `已连接本地后端` only for a truthy response.
- `applyInitStatusSnapshot(snapshot)`: assign `state.initStatus`, render, and attach the existing init poll when running, pulling an embedding model, or waiting for the first pool.
- `applyActivitySnapshot(snapshot)`: assign `state.activity`, `activityItems`, cursor, and `activityHasMore` exactly as the current hydration block does.
- `applyProfileSnapshot(snapshot)`: unwrap `.profile`, assign it when initialized, then hydrate both interest and avoidance inbox items.
- `applyDelightSnapshot(snapshot)`: call `applyDelights(snapshot)`.
- `applyNotificationSnapshot(snapshot)`: merge `snapshot.item` as a notification when present.
- `applyChatSnapshot(snapshot)`: normalize the ordinary chat rows into `state.chat`, using a failed row's `error` for the agent bubble and never its status word.
- `applyDelightChatSnapshot(snapshot)`: call `applyTurnToDelight()` for every returned row with `scope: "delight"` as fallback.
- `applyConfigSnapshot(snapshot)`: call `applyConfig(snapshot?.config || snapshot)`.

Preserve these invariants:

- recommendation success renders immediately, including a real empty array;
- recommendation failure does not clear an existing list and enters the existing 1/2/4/8 recovery controller;
- first runtime success applies immediately;
- after recommendation serving settles, issue the existing second runtime read and apply it independently;
- health/init polling, QR info, config controls, activity, profile, Delight, notification, and chat history keep their current owned render paths;
- a rejected secondary promise does not discard a successful sibling;
- hydration debounce, generation, and single-flight guards remain intact.

Use `/api/ping` only for the initial connected/unavailable indicator. Keep `/api/health` as a secondary readiness resource.

- [ ] **Step 5: Implement the cover loading boundary**

Make the cover helper parameterized while preserving proxy/fallback/escaping behavior:

```js
function coverImg(item, { eager = true } = {}) {
  const url = imageProxyUrl(item.cover_url);
  if (!url) return "";
  return `<img src="${escapeHtml(url)}"${imgCrossOriginAttr()} alt="${escapeHtml(item.title)} 的封面" loading="${eager ? "eager" : "lazy"}" fetchpriority="${eager ? "high" : "low"}" decoding="async" referrerpolicy="no-referrer">`;
}

function recommendationMediaHtml(item, index = 0) {
  const eager = index < DESKTOP_EAGER_COVER_COUNT;
  if (recommendationIsTextCard(item)) {
    const backdrop = recommendationTextCardBackdrop(item);
    const backdropHtml = backdrop
      ? `<img class="cover-backdrop" src="${escapeHtml(backdrop)}"${imgCrossOriginAttr()} alt="" aria-hidden="true" loading="${eager ? "eager" : "lazy"}" fetchpriority="${eager ? "high" : "low"}" decoding="async" referrerpolicy="no-referrer">`
      : "";
    return `${backdropHtml}<p class="cover-text">${escapeHtml(recommendationTextCardText(item))}</p>`;
  }
  return coverImg(item, { eager });
}
```

Change only the recommendation-grid map to pass `(item, index)`. Saved-list covers keep their existing default behavior, and `renderDelightCover()` remains `loading="eager"` plus `fetchPriority="high"`.

- [ ] **Step 6: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_desktop_web_card_metadata.py tests/test_desktop_web_pool_status.py tests/test_desktop_web_issue_98_e2e.py tests/test_desktop_web_autoload_margin_e2e.py -q
node --check src/openbiliclaw/web/desktop/assets/js/app.js
```

Expected: PASS; the 4-second health response is no longer on the card critical path, runtime reads at least twice, and #105's unchanged 50px browser regression remains green.

- [ ] **Step 7: Update runtime documentation and changelog**

In `docs/modules/runtime.md`, document:

```markdown
- 桌面 Web 首屏以 `/api/ping` 判断连接，推荐与 runtime 状态各自返回即各自渲染；
  health/init/profile/activity/config 等次级读取不会再挡住推荐卡。推荐消费后仍独立复读
  runtime 库存，失败沿用 1/2/4/8 秒的资源级恢复。
- 桌面推荐仅前 4 张封面 eager/high，后续封面使用 lazy/low；Delight 保持 eager/high。
```

Add one concise #101 bullet under the current `v0.3.164` block in `docs/changelog.md`.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/openbiliclaw/web/desktop/assets/js/app.js tests/test_desktop_web_issue_98_e2e.py tests/test_desktop_web_card_metadata.py tests/test_desktop_web_pool_status.py tests/test_desktop_web_autoload_margin_e2e.py docs/modules/runtime.md docs/changelog.md
git commit -m "fix(web): render desktop resources progressively"
```

---

### Task 2: Lock drawer/drag behavior and adapt Delight to available width (#102, #105, #106)

**Files:**
- Modify: `tests/test_desktop_web_issue_98_e2e.py`
- Modify: `tests/test_desktop_web_motion_polish.py`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: `setSideDrawerOpen(open, { persist = true } = {})`, `#sideDrawer`, `#sideDrawerBtn`, `#delightBanner`, `_DELIGHT_DRAG_DEAD_ZONE = 10`, navigation switch threshold `50`.
- Produces: named inline-size container `desktop-main`; Delight-only container breakpoints that mirror the current 820/620/430px intent without changing viewport-owned navigation.

- [ ] **Step 1: Add failing source contracts for exact landed behavior and container ownership**

Extend `tests/test_desktop_web_motion_polish.py`:

```python
def test_delight_drag_thresholds_and_autoload_margin_remain_calibrated() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "const _DELIGHT_DRAG_DEAD_ZONE = 10;" in app_js
    assert "Math.abs(dx) < _DELIGHT_DRAG_DEAD_ZONE" in app_js
    assert "wasActive && Math.abs(dx) >= 50" in app_js
    assert "const AUTO_LOAD_ROOT_MARGIN_PX = 50;" in app_js


def test_desktop_delight_has_available_width_container_rules() -> None:
    app_css = APP_CSS.read_text(encoding="utf-8")

    assert "container-type: inline-size" in app_css
    assert "container-name: desktop-main" in app_css
    assert "@container desktop-main (max-width:" in app_css
```

- [ ] **Step 2: Add failing real-browser geometry, ARIA, drag, and overflow tests**

Make the issue-98 fixture return at least two Delight candidates so switching is observable. Add these helpers/assertions:

```python
def _rect(locator: Any) -> dict[str, float]:
    return locator.evaluate(
        """el => { const r = el.getBoundingClientRect();
        return {left: r.left, right: r.right, top: r.top, width: r.width}; }"""
    )


def _drag_horizontally(page: Page, locator: Any, delta_x: int) -> None:
    box = locator.bounding_box()
    assert box is not None
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + delta_x, y)
    page.mouse.up()
```

The #102 test must assert closed/open state and flex ownership:

```python
button = chromium_page.locator("#sideDrawerBtn")
drawer = chromium_page.locator("#sideDrawer")
layout = chromium_page.locator(".layout")
expect(button).to_have_attribute("aria-expanded", "false")
expect(drawer).to_have_attribute("aria-hidden", "true")
closed_width = _rect(layout)["width"]
button.click()
expect(button).to_have_attribute("aria-expanded", "true")
expect(drawer).to_have_attribute("aria-hidden", "false")
chromium_page.wait_for_timeout(400)
assert _rect(layout)["width"] < closed_width
```

Use fresh page loads for horizontal deltas 9, 10, 49, and 50. Assert 9 does not add `is-dragging`, 10 activates drag visual state during the move, 49 leaves `#delightCount` unchanged after release, and 50 changes it. Start from the first item and drag left so the next item exists.

At viewport 860×900, assert both drawer states satisfy:

```python
geometry = chromium_page.evaluate(
    """() => {
      const layout = document.querySelector('.layout').getBoundingClientRect();
      const delight = document.querySelector('#delightBanner').getBoundingClientRect();
      return {
        layoutRight: layout.right,
        delightRight: delight.right,
        docClient: document.documentElement.clientWidth,
        docScroll: document.documentElement.scrollWidth,
        columns: getComputedStyle(document.querySelector('#delightBanner')).gridTemplateColumns,
      };
    }"""
)
assert geometry["delightRight"] <= geometry["layoutRight"] + 1
assert geometry["docScroll"] <= geometry["docClient"] + 1
```

At 1440px with the drawer closed, assert `gridTemplateColumns` contains two pixel tracks; at 860px with the drawer open, assert it resolves to one track.

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/test_desktop_web_motion_polish.py -q
.venv/bin/pytest tests/test_desktop_web_issue_98_e2e.py -q
```

Expected: the exact #102/#105 source guards pass, while the 860px overflow/container assertions fail because compact Delight still depends only on viewport width.

- [ ] **Step 4: Add Delight-only container queries**

Declare the container on the flex-allocated main layout element without changing `.app-body`, drawer, topbar, or mobile navigation ownership:

```css
.layout {
  container-type: inline-size;
  container-name: desktop-main;
}
```

Add container equivalents of the current Delight rules. Use thresholds selected from the actual component geometry: the two-column card needs at least 700px, the action row needs at least 620px, and the narrow action composition needs at least 430px.

```css
@container desktop-main (max-width: 700px) {
  .delight { grid-template-columns: 1fr; }
  .delight-body { align-items: start; }
  .delight-actions { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); }
  .delight-main-actions { flex-basis: 212px; grid-template-columns: 116px 88px; }
  .delight-feedback-actions { width: 128px; }
}

@container desktop-main (max-width: 620px) {
  .delight-actions { flex-wrap: wrap; justify-content: stretch; }
  .delight-main-actions { flex: 1 1 212px; grid-template-columns: minmax(116px, 1fr) 88px; }
  .delight-feedback-actions { flex: 0 0 128px; margin-left: auto; }
}

@container desktop-main (max-width: 430px) {
  .delight { padding: 20px; border-radius: 22px; }
  .delight-meta { align-items: center; flex-direction: row; }
  .delight-queue-tools { justify-content: end; margin-left: auto; }
  .delight-actions { flex-direction: row; flex-wrap: nowrap; align-items: center; justify-content: space-between; gap: var(--space-2); }
  .delight-actions.is-composing { gap: 0; }
  .delight-main-actions { order: 2; flex: 1 1 auto; grid-template-columns: minmax(72px, .82fr) minmax(76px, .88fr); gap: 6px; transition: none; }
  .delight-feedback-actions.card-feedback-icons { order: 1; flex: 0 0 auto; width: auto; max-width: none; height: 40px; min-height: 40px; margin-left: 0; align-self: center; justify-content: center; padding: 3px 8px; transition: none; }
  .delight-actions .delight-main-actions .small-btn,
  .delight-actions .delight-main-actions [data-delight="view"],
  .delight-actions .delight-main-actions .comment-field,
  .delight-actions .delight-main-actions .chat-action,
  .delight-actions .delight-feedback-actions.card-feedback-icons { transition: none; }
  .delight-main-actions .small-btn { width: 100%; padding-inline: 10px; }
  .delight-main-actions [data-delight="view"] { padding-inline: 10px; }
  .delight-actions.is-composing .delight-feedback-actions.card-feedback-icons { display: none; }
  .delight-main-actions.is-composing { flex: 1 1 auto; grid-template-columns: minmax(0, 1fr) 38px 38px; align-items: center; min-height: 38px; }
  .delight-main-actions.is-composing .comment-field { grid-column: 1; grid-row: 1; align-self: center; }
  .delight-main-actions.is-composing .comment-field input { height: 38px; }
  .delight-main-actions.is-composing .composer-cancel { display: inline-flex; grid-column: 2; grid-row: 1; width: 38px; min-width: 38px; padding: 0; align-self: center; background: var(--surface); border-color: var(--border-soft); color: var(--fg-2); font-size: 22px; line-height: 1; }
  .delight-main-actions.is-composing .chat-action { grid-column: 3; grid-row: 1; width: 38px; min-width: 38px; align-self: center; }
  .delight-main-actions.is-composing .small-btn { width: auto; min-height: 38px; }
  .delight-feedback-actions .feedback-icon-btn { flex: 0 0 26px; width: 26px; height: 26px; min-height: 26px; }
}
```

Keep the viewport media queries as the fallback and as owners of true mobile page/navigation behavior. Do not change drawer width or flex transition.

- [ ] **Step 5: Run targeted browser regressions and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_desktop_web_motion_polish.py tests/test_desktop_web_issue_98_e2e.py tests/test_desktop_web_autoload_margin_e2e.py -q
```

Expected: PASS at 860px drawer open/closed, wide Delight remains two-column, 10/50px drag semantics remain unchanged, and #105 remains 50px.

- [ ] **Step 6: Update runtime documentation and changelog**

Add to `docs/modules/runtime.md`:

```markdown
- 桌面侧栏是 flex 行内项：按钮的 `aria-expanded` 与侧栏的 `aria-hidden` 同步，内容宽度随
  312px 侧栏平滑让渡。Delight 以主内容实际 inline-size 响应，而非只看 viewport。
- Delight 拖拽 10px 才进入拖动态，50px 才切换卡片；滚动自动加载仍使用 50px
  root margin。前者避免点击抖动，后两者分别控制明确切换与接近视口时加载。
```

Add one #102/#105 verification plus #106 overflow-fix bullet to the current changelog block.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/openbiliclaw/web/desktop/assets/css/app.css tests/test_desktop_web_issue_98_e2e.py tests/test_desktop_web_motion_polish.py docs/modules/runtime.md docs/changelog.md
git commit -m "fix(web): adapt delight to drawer width"
```

---

### Task 3: Retain mobile probe pending state and include probe subjects in feedback (#103, #109)

**Files:**
- Create: `tests/test_mobile_web_probe_delight_e2e.py`
- Modify: `tests/js/mobile-probe-notification-helpers.test.mjs`
- Modify: `tests/test_probe_message_treatments.py`
- Modify: `tests/test_desktop_web_issue_98_e2e.py`
- Modify: `src/openbiliclaw/web/js/views/chat.js`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `docs/mobile-web-spec.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: `probeNotificationKey(type, domain)`, `rememberHandledProbe(type, domain)`, `renderOverlay()`, `respondToInterestProbe()`, `respondToAvoidanceProbe()`, `showToast()`.
- Produces: module-scoped `pendingProbeActions: Map<string, { response: string }>`; `probeFeedbackMessage(type, response, domain, apiResponse)` shared by desktop card result and toast.

- [ ] **Step 1: Add a loopback mobile-browser fixture and failing pending-state tests**

Create `tests/test_mobile_web_probe_delight_e2e.py` with the same `pytest.importorskip("playwright.sync_api")`, `ThreadingHTTPServer`, loopback-only static serving, fake WebSocket, and Chrome channel pattern used by `tests/test_desktop_web_issue_98_e2e.py`. Serve `/m/`, `/m/index.html`, `/m/css/*`, `/m/js/*`, and the mobile boot APIs. Return two notification items:

```python
{
    "type": "interest.probe",
    "domain": "系统设计",
    "message": "你似乎常看系统设计。",
},
{
    "type": "avoidance.probe",
    "domain": "标题党",
    "message": "你似乎会避开标题党。",
}
```

The stub must expose a response gate per endpoint, POST counters, and configurable success status. Add a parametrized test for both probe types:

```python
@pytest.mark.parametrize(
    ("probe_type", "domain", "button_action"),
    [
        ("interest.probe", "系统设计", "confirm"),
        ("avoidance.probe", "标题党", "confirm"),
    ],
)
def test_mobile_probe_stays_busy_when_overlay_is_rebuilt(
    mobile_web_server: tuple[str, MobileWebStub],
    chromium_page: Page,
    probe_type: str,
    domain: str,
    button_action: str,
) -> None:
    # open messages, click the target action, wait for POST arrival
    # close and reopen before releasing the server gate
    rebuilt = page.locator(f'[data-probe-domain="{domain}"]')
    expect(rebuilt.locator("button")).to_be_disabled()
    expect(rebuilt).to_have_attribute("aria-busy", "true")
    rebuilt.locator(f'[data-probe="{button_action}"]').click(force=True)
    assert stub.post_counts[probe_type] == 1
    # release success and assert the card is removed
```

Add a failure variant that releases a 500 response and asserts the rebuilt card remains, `aria-busy="false"`, and all actions are enabled for retry.

- [ ] **Step 2: Add failing key-isolation and desktop subject-copy tests**

Extend `tests/js/mobile-probe-notification-helpers.test.mjs` to prove the same domain produces different keys for interest and avoidance:

```js
assert.notEqual(
  probeNotificationKey("interest.probe", "同一主题"),
  probeNotificationKey("avoidance.probe", "同一主题"),
);
```

In `tests/test_probe_message_treatments.py`, require a single four-argument helper and bounded subject:

```python
def test_desktop_probe_feedback_copy_is_domain_aware_and_bounded() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "function probeFeedbackMessage(type, response, domain, apiResponse = null)" in app_js
    assert "probeFeedbackMessage(type, response, domain, apiResponse)" in app_js
    assert ".slice(0," in _function_body(app_js, "probeFeedbackMessage")
```

Extend the issue-98 probe browser test: after the 10-second undo window is shortened by the existing test override and the successful POST commits, require the newest toast to contain `系统设计`. Add table-driven unit/source expectations for:

```text
interest confirm -> 已确认兴趣「系统设计」
interest defer   -> 已搁置兴趣「短视频热点」，过阵子可能再提
avoidance confirm -> 已确认避雷「标题党」
avoidance reject  -> 已排除避雷「长视频」
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
node --test tests/js/mobile-probe-notification-helpers.test.mjs
.venv/bin/pytest tests/test_probe_message_treatments.py -q
.venv/bin/pytest tests/test_mobile_web_probe_delight_e2e.py tests/test_desktop_web_issue_98_e2e.py -q
```

Expected: mobile overlay rebuild re-enables controls, the duplicate POST is possible, and the desktop toast omits its domain.

- [ ] **Step 4: Implement retained mobile in-flight state**

Import the existing normalized key helper and add module state in `views/chat.js`:

```js
import { probeNotificationKey, rememberHandledProbe } from "../probe-notification-helpers.js";

const pendingProbeActions = new Map();

function pendingProbeAction(type, domain) {
  return pendingProbeActions.get(probeNotificationKey(type, domain)) || null;
}
```

Every probe card render must derive its state, never trust a previous DOM mutation:

```js
const pending = pendingProbeAction(notification.type, notification.domain);
card.setAttribute("aria-busy", pending ? "true" : "false");
card.classList.toggle("is-processing", Boolean(pending));
for (const button of card.querySelectorAll("button")) button.disabled = Boolean(pending);
```

For each non-chat probe action:

```js
const key = probeNotificationKey(type, domain);
if (pendingProbeActions.has(key)) return;
pendingProbeActions.set(key, { response });
renderOverlay();
try {
  const result = await submitProbe(type, domain, response);
  pendingProbeActions.delete(key);
  rememberHandledProbe(type, domain);
  removeNotification(type, domain);
  renderOverlay();
  updateNotificationBadge();
} catch (error) {
  pendingProbeActions.delete(key);
  renderOverlay();
  throw error;
}
```

Do not call `rememberHandledProbe()` before settlement. Treat the endpoint's accepted terminal no-op response the same as success. On failure, preserve the notification and display the existing safe error feedback without rethrowing into an unhandled event promise.

- [ ] **Step 5: Implement one domain-aware desktop feedback helper**

Replace `probeToast()` with a helper used for both inline result text and `showToast()`:

```js
function probeFeedbackMessage(type, response, domain, apiResponse = null) {
  const raw = String(domain || apiResponse?.domain || "这个方向").replace(/\s+/g, " ").trim();
  const subject = raw.length > 24 ? `${raw.slice(0, 23)}…` : raw;
  const quoted = `「${subject || "这个方向"}」`;
  const avoidance = type === "avoidance.probe";
  if (response === "confirm") return avoidance ? `已确认避雷${quoted}` : `已确认兴趣${quoted}`;
  if (response === "defer") return avoidance
    ? `已搁置避雷${quoted}，过阵子可能再提`
    : `已搁置兴趣${quoted}，过阵子可能再提`;
  return avoidance ? `已排除避雷${quoted}` : `已排除兴趣${quoted}`;
}
```

Pass the explicit domain at every inbox and profile call site. Keep assignment through `textContent`/`showToast(text)`; do not introduce `innerHTML` interpolation.

- [ ] **Step 6: Run targeted tests and verify GREEN**

Run:

```bash
node --test tests/js/mobile-probe-notification-helpers.test.mjs
.venv/bin/pytest tests/test_probe_message_treatments.py tests/test_mobile_web_probe_delight_e2e.py tests/test_desktop_web_issue_98_e2e.py -q
node --check src/openbiliclaw/web/js/views/chat.js
node --check src/openbiliclaw/web/desktop/assets/js/app.js
```

Expected: PASS for both probe types, duplicate count remains one, failures restore retry, and committed toasts contain the bounded domain.

- [ ] **Step 7: Update mobile/runtime docs and changelog**

Add the exact pending lifecycle to `docs/mobile-web-spec.md`: keyed by normalized type+domain, in-flight state survives overlay rebuild, terminal handled state is recorded only after settlement, and failure restores the card. Add the shared domain-aware result/toast copy contract to `docs/modules/runtime.md`. Add concise #103 and #109 bullets to the current changelog block.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/openbiliclaw/web/js/views/chat.js src/openbiliclaw/web/desktop/assets/js/app.js tests/test_mobile_web_probe_delight_e2e.py tests/js/mobile-probe-notification-helpers.test.mjs tests/test_probe_message_treatments.py tests/test_desktop_web_issue_98_e2e.py docs/mobile-web-spec.md docs/modules/runtime.md docs/changelog.md
git commit -m "fix(web): retain probe action state"
```

---

### Task 4: Project liked Delight state consistently across all graphical clients (#104, #108)

**Files:**
- Modify: `tests/test_mobile_web_view_models.py`
- Modify: `tests/test_mobile_web_delight_layout.py`
- Modify: `tests/test_mobile_web_probe_delight_e2e.py`
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `tests/test_desktop_web_issue_98_e2e.py`
- Modify: `src/openbiliclaw/web/js/view-models.js`
- Modify: `src/openbiliclaw/web/js/views/recommend.js`
- Modify: `src/openbiliclaw/web/css/app.css`
- Modify: `src/openbiliclaw/web/desktop/index.html`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `extension/popup/popup-helpers.js`
- Modify: `extension/popup/popup.js`
- Modify: `docs/mobile-web-spec.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: `normalizeDelightCandidate(item)`, `getDelightUiState(delight, { highlightBvid = "" } = {})`, desktop `normalizeDelight()`/`setActiveDelight()`, existing `state="liked"` payload/event.
- Produces: projection fields `show_status`, `show_actions`, `like_pressed`, `like_disabled` in both mobile and extension helpers; equivalent desktop `aria-pressed`/disabled synchronization.

- [ ] **Step 1: Replace the failing mobile/extension projection expectations**

Update `tests/test_mobile_web_view_models.py` so a liked candidate is not treated as globally handled and has independent fields:

```python
assert liked["visible"] is True
assert liked["handled"] is False
assert liked["show_status"] is True
assert liked["show_actions"] is True
assert liked["like_pressed"] is True
assert liked["like_disabled"] is True
assert liked["response_message"] == "好，这类多来点。"
```

For pending, viewed, rejected, and chatted, assert exact projection defaults. The required matrix is:

| state | show_status | show_actions | like_pressed | like_disabled |
| --- | --- | --- | --- | --- |
| pending | false when no response copy | true | false | false |
| liked | true | true | true | true |
| viewed | true | false | false | true |
| rejected | true | false | false | true |
| chatted/chatting | response-dependent | true | false | false |

Mirror the same tests in `extension/tests/popup-helpers.test.ts`.

- [ ] **Step 2: Add failing renderer and browser regressions**

In `tests/test_mobile_web_delight_layout.py`, remove the old liked-is-handled assertion and require independent result/action blocks plus `aria-pressed`:

```python
assert "if (uiState.show_status)" in recommend_js
assert "if (uiState.show_actions)" in recommend_js
assert 'btn.setAttribute("aria-pressed", uiState.like_pressed ? "true" : "false")' in recommend_js
assert "btn.disabled = uiState.like_disabled" in recommend_js
```

Extend the new mobile browser fixture with one `state="pending"` Delight, successful like POST, queue reload payload switch to `state="liked"`, and an injectable `delight.liked` WebSocket event. For local click, reload, and event paths, assert:

```python
like = page.locator('.delight-actions [data-delight-action="like"]')
expect(page.locator(".delight-result-state")).to_contain_text("好，这类多来点。")
expect(page.locator(".delight-actions")).to_be_visible()
expect(like).to_have_attribute("aria-pressed", "true")
expect(like).to_be_disabled()
expect(page.locator('[data-delight-action="view"]')).to_be_enabled()
expect(page.locator('[data-delight-action="watch-later"]')).to_be_enabled()
expect(page.locator('[data-delight-action="favorite"]')).to_be_enabled()
expect(page.locator('[data-delight-action="reject"]')).to_be_enabled()
expect(page.locator('[data-delight-action="chat"]')).to_be_enabled()
```

In the desktop issue-98 fixture return a liked Delight and assert the static `[data-delight="like"]` has `aria-pressed="true"`, is disabled, and the other controls remain enabled. In the extension tests require `popup.js` to assign the same ARIA/disabled fields to the generated like control.

- [ ] **Step 3: Run projection and renderer tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/test_mobile_web_view_models.py tests/test_mobile_web_delight_layout.py -q
.venv/bin/pytest tests/test_mobile_web_probe_delight_e2e.py tests/test_desktop_web_issue_98_e2e.py -q
cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts
```

Expected: FAIL because mobile hides the action group for liked state, desktop does not set like ARIA, and extension has no explicit liked projection.

- [ ] **Step 4: Implement explicit mobile and extension projection fields**

Return all projection fields on every branch of both `getDelightUiState()` implementations. Use one local factory/default object to avoid a missing field:

```js
const base = {
  visible: true,
  highlighted: highlight,
  handled: false,
  show_status: Boolean(normalized.response_message),
  show_actions: true,
  like_pressed: false,
  like_disabled: false,
  score_label: scoreLabel,
  response_tone: "info",
  response_message: normalized.response_message,
};
```

The liked branch is:

```js
return {
  ...base,
  show_status: true,
  show_actions: true,
  like_pressed: true,
  like_disabled: true,
  response_tone: "success",
  response_message: normalized.response_message || "好，这类多来点。",
};
```

Viewed/rejected keep their existing terminal presentation and set `show_actions: false`, `like_disabled: true`; chatted/chatting keep actions. Keep `handled` only as backward-compatible terminal meaning for viewed/rejected, not as the renderer's liked action-group gate.

- [ ] **Step 5: Render mobile and extension status/actions independently**

In mobile `renderDelightTray()`, replace the result/action `if/else` with two independent blocks. Give action buttons stable `data-delight-action` values. For the like button:

```js
btn.setAttribute("aria-pressed", uiState.like_pressed ? "true" : "false");
btn.disabled = uiState.like_disabled;
```

On a failed like POST, restore the prior candidate state and rerender; do not leave selected/disabled styling. Add CSS using the accessibility state:

```css
.delight-actions [data-delight-action="like"][aria-pressed="true"] {
  border-color: var(--accent);
  background: color-mix(in oklab, var(--accent), var(--surface) 84%);
  color: var(--accent-strong);
}
```

Apply the same projection to the extension's generated `likeButton`. Do not disable reject, view, save toggles, or chat merely because the candidate is liked.

- [ ] **Step 6: Synchronize desktop static Delight state**

Add `aria-pressed="false"` to the static desktop like button in `desktop/index.html`. In `setActiveDelight()` set it from the active candidate on every render, including queue reload and stream reconciliation:

```js
const likeBtn = document.querySelector('[data-delight="like"]');
const liked = state.delight?.state === "liked";
if (likeBtn) {
  likeBtn.setAttribute("aria-pressed", liked ? "true" : "false");
  likeBtn.disabled = liked;
}
```

The existing generic `[aria-pressed="true"]` feedback style is the visual source of truth. Ensure the subsequent generic `controls.forEach(btn => btn.disabled = false)` does not re-enable the liked control: enable controls first, then apply state-specific disabling.

- [ ] **Step 7: Run cross-surface tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_mobile_web_view_models.py tests/test_mobile_web_delight_layout.py tests/test_mobile_web_probe_delight_e2e.py tests/test_desktop_web_issue_98_e2e.py -q
cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts
node --check ../src/openbiliclaw/web/js/view-models.js
node --check ../src/openbiliclaw/web/js/views/recommend.js
node --check popup/popup-helpers.js
node --check popup/popup.js
```

Expected: PASS for local click, reload, and stream state; negative removal, chat, watch-later, and favorite regressions remain green.

- [ ] **Step 8: Update recommendation/mobile/extension docs and changelog**

Document the state matrix and positive retention in `docs/modules/recommendation.md` and `docs/mobile-web-spec.md`: liked shows status plus actions, like is pressed/duplicate-disabled, all other existing actions remain. Document the extension projection in `docs/modules/extension.md`. Add concise #104/#108 bullets to the current changelog block.

- [ ] **Step 9: Commit Task 4**

```bash
git add src/openbiliclaw/web/js/view-models.js src/openbiliclaw/web/js/views/recommend.js src/openbiliclaw/web/css/app.css src/openbiliclaw/web/desktop/index.html src/openbiliclaw/web/desktop/assets/js/app.js extension/popup/popup-helpers.js extension/popup/popup.js tests/test_mobile_web_view_models.py tests/test_mobile_web_delight_layout.py tests/test_mobile_web_probe_delight_e2e.py tests/test_desktop_web_issue_98_e2e.py extension/tests/popup-helpers.test.ts docs/mobile-web-spec.md docs/modules/recommendation.md docs/modules/extension.md docs/changelog.md
git commit -m "fix(web): preserve liked delight actions"
```

---

### Task 5: Propagate typed dialogue failures without learning (#107)

**Files:**
- Modify: `tests/test_llm_service.py`
- Modify: `tests/test_soul_dialogue.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_openclaw_cli.py`
- Modify: `tests/test_desktop_web_pool_status.py`
- Modify: `tests/test_mobile_web_delight_layout.py`
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `src/openbiliclaw/llm/base.py`
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/integrations/openclaw/operations.py`
- Modify: `docs/modules/llm.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/diagrams/web-architecture.html`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: `describe_llm_failure(exc: BaseException) -> str | None`, `LLMResponseContentError`, `SocraticDialogue.respond()`, `_fail_chat_turn_row(turn_id, error, reply="")`.
- Produces: `safe_llm_failure_message(exc: BaseException) -> str`; failure-atomic `SocraticDialogue.respond()`; durable `status="failed", reply="", error=<safe message>`.

- [ ] **Step 1: Add failing safe-classification tests**

Extend `tests/test_llm_service.py` with a table that covers moderation, auth, rate/quota, `LLMTimeoutError`, built-in `TimeoutError`, `LLMFallbackError("No provider was available")`, `LLMResponseError`, `LLMResponseContentError`, and unknown `RuntimeError("secret-upstream-detail")`. Import the new helper and assert:

```python
def test_safe_llm_failure_message_never_returns_raw_unknown_detail() -> None:
    message = safe_llm_failure_message(RuntimeError("secret-upstream-detail"))

    assert message == "AI 服务暂时不可用；请稍后重试，或检查设置中的模型与网络。"
    assert "secret-upstream-detail" not in message
```

For `LLMResponseContentError("LLM returned an empty response")`, `describe_llm_failure()` must return the existing empty/unparseable Chinese explanation. For built-in timeout it must return the timeout explanation.

- [ ] **Step 2: Add failing dialogue rollback/no-learning tests**

Replace the old test that expects the generic successful-looking fallback. Use a service whose `complete_socratic_dialogue()` raises `LLMResponseContentError` and a soul engine with an `AsyncMock` learner:

```python
@pytest.mark.asyncio
async def test_failed_dialogue_rolls_back_history_and_never_learns() -> None:
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=failing_service,
        session="popup",
    )

    with pytest.raises(LLMResponseContentError):
        await dialogue.respond("这是不能被学进去的内容")

    assert dialogue.history == []
    soul_engine.learn_from_dialogue.assert_not_awaited()
```

Add a cancellation/timeout rollback test by starting `respond()`, cancelling the task while the service awaits an event, and asserting history is empty and learning was not scheduled.

- [ ] **Step 3: Add failing API, CLI, adapter, and client contracts**

In `tests/test_api_app.py`, make `ctx.dialogue.respond()` raise each of `LLMResponseContentError`, built-in `TimeoutError`, and an unknown exception containing `sk-live-secret`. Assert a durable turn eventually becomes:

```python
assert turn["status"] == "failed"
assert turn["reply"] == ""
assert turn["error"]
assert "sk-live-secret" not in turn["error"]
```

Retry the same `turn_id` and assert it returns the same terminal failed row without a second model call. Add contextual Delight/interest/avoidance chat tests asserting `ok is False`, the existing response shape remains, `reply` is the classified safe copy, and no cognition/publish success call occurs. For legacy `/api/chat`, preserve `{ "reply": ... }` but require safe classified copy rather than the old single generic string.

In `tests/test_cli.py`, feed two prompts then `exit`; make the first `dialogue.respond()` fail and the second succeed. Assert the safe reason and successful second reply are printed, proving the loop remains usable.

In `tests/test_openclaw_cli.py`, require `AdapterOperationError` text to contain the safe classification and not raw exception detail.

Add/retain source contracts proving desktop, mobile, and extension render `turn.error` when `turn.status === "failed"`.

- [ ] **Step 4: Run the new tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/test_llm_service.py tests/test_soul_dialogue.py -q
.venv/bin/pytest tests/test_api_app.py -q
.venv/bin/pytest tests/test_cli.py tests/test_openclaw_cli.py -q
.venv/bin/pytest tests/test_desktop_web_pool_status.py tests/test_mobile_web_delight_layout.py -q
cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts
```

Expected: failures currently become ordinary replies, remain in history, can trigger learning, complete durable rows, and leak `str(exc)` into failed durable state.

- [ ] **Step 5: Extend safe LLM classification**

In `src/openbiliclaw/llm/base.py`, lazily import both service-layer exception classes and treat them exactly:

```python
from openbiliclaw.llm.service import LLMProviderExecutionError, LLMResponseContentError

if isinstance(current, LLMTimeoutError | TimeoutError):
    timed_out = True
if isinstance(current, LLMResponseError | LLMResponseContentError):
    empty_response = True
```

Add the safe public boundary helper:

```python
def safe_llm_failure_message(exc: BaseException) -> str:
    """Return actionable LLM failure copy without exposing upstream detail."""
    return describe_llm_failure(exc) or (
        "AI 服务暂时不可用；请稍后重试，或检查设置中的模型与网络。"
    )
```

Keep `describe_llm_failure()` returning `None` for unknown failures so existing diagnostic callers can retain their distinct internal fallback behavior.

- [ ] **Step 6: Make dialogue history and learning failure-atomic**

In `SocraticDialogue.respond()`, append the user turn provisionally and roll back to the prior length on every exception/cancellation:

```python
history_length = len(self._history)
self._history.append(DialogueTurn(role="user", content=user_message))
try:
    service = self._llm_service or self._build_service()
    if self._tools and self._tool_dispatcher:
        reply = await self._respond_with_tools(service, user_message)
    else:
        response = await service.complete_socratic_dialogue(
            user_message=user_message,
            history=self._history_to_messages(),
            caller="soul.dialogue",
        )
        reply = response.content
except BaseException:
    del self._history[history_length:]
    logger.exception("Failed to generate Socratic dialogue response.")
    raise
```

Only after successful completion append the agent turn and schedule `learn_from_dialogue()`. Remove the synthetic “我刚刚思路断了一下” path entirely.

- [ ] **Step 7: Persist durable failures as failures with safe copy**

Make `_generate_durable_chat_reply()` raise on missing dialogue, timeout, provider failure, or empty reply. Keep cognition/publish and sentiment classification below the successful reply boundary.

In `_complete_durable_chat_turn()` classify only at the boundary:

```python
except Exception as exc:
    logger.exception("Failed to complete durable chat turn %s", turn_id)
    _fail_chat_turn_row(
        turn_id,
        error=safe_llm_failure_message(exc),
        reply="",
    )
```

Never use `str(exc)` for the persisted/client-visible error. Do not complete a turn whose reply is empty. Preserve existing `turn_id` idempotency and polling.

For Delight, interest-probe, and avoidance-probe contextual chat endpoints, return `ok=false` plus `safe_llm_failure_message(exc)` in the existing `reply` field. For legacy `/api/chat`, keep its response shape and put the safe message in `reply`. No failure path may record cognition or publish a success event.

- [ ] **Step 8: Keep CLI and OpenClaw boundaries actionable and safe**

Wrap only the per-message call in `cli.chat()`:

```python
try:
    reply = asyncio.run(dialogue.respond(user_message))
except Exception as exc:
    console.print(f"阿花：{safe_llm_failure_message(exc)}")
    continue
console.print(f"阿花：{reply}")
```

In OpenClaw operations, raise an adapter error whose message includes `safe_llm_failure_message(exc)` while retaining `from exc` for internal logs/traceback. Do not serialize the cause.

- [ ] **Step 9: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_llm_service.py tests/test_soul_dialogue.py tests/test_api_app.py tests/test_cli.py tests/test_openclaw_cli.py tests/test_desktop_web_pool_status.py tests/test_mobile_web_delight_layout.py -q
cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts
```

Expected: PASS; successful dialogue still appends two turns and schedules learning, while all failures roll back, store `failed/reply=""/safe error`, and remain retry-visible to clients.

- [ ] **Step 10: Update module, architecture, and changelog documentation**

Update `docs/modules/llm.md` public API with:

```markdown
- `describe_llm_failure(exc) -> str | None`：识别 moderation、鉴权、额度/限流、超时、
  provider 全部不可用、provider/service 空响应。
- `safe_llm_failure_message(exc) -> str`：公共边界使用；未知异常退化为固定安全提示，
  不回传上游异常文本。
```

Update `docs/modules/soul.md`: user history is provisional until a real reply; only a completed user+agent pair enters `learn_from_dialogue()`. Update `docs/modules/cli.md`: a failed turn prints safe guidance and the REPL continues. Update `docs/modules/runtime.md`: durable failed rows use `status="failed"`, `reply=""`, safe `error`, and same-ID polling/idempotency.

Synchronize the data-flow diagrams in all required architecture surfaces:

```text
Web / CLI / OpenClaw
        │ dialogue request
        ▼
SocraticDialogue ── success ──> user+agent history ──> background learning
        │
        └─ failure/timeout ──> rollback provisional history
                              └─> boundary-safe error / failed durable turn
```

Apply this flow to `docs/architecture.md`, `docs/spec.md` §3, the top architecture diagrams in `README.md` and `README_EN.md`, and `docs/diagrams/web-architecture.html`. Also show desktop recommendation/runtime/secondary hydration as independent branches. Do not turn README into a changelog and do not change its release highlight callout because this is not a release operation. Add one concise #107 bullet to the current changelog block.

- [ ] **Step 11: Run docs/source formatting checks and commit Task 5**

Run:

```bash
.venv/bin/ruff format src/openbiliclaw/llm/base.py src/openbiliclaw/soul/dialogue.py src/openbiliclaw/api/app.py src/openbiliclaw/cli.py src/openbiliclaw/integrations/openclaw/operations.py tests/test_llm_service.py tests/test_soul_dialogue.py tests/test_api_app.py tests/test_cli.py tests/test_openclaw_cli.py
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
```

Expected: PASS.

```bash
git add src/openbiliclaw/llm/base.py src/openbiliclaw/soul/dialogue.py src/openbiliclaw/api/app.py src/openbiliclaw/cli.py src/openbiliclaw/integrations/openclaw/operations.py tests/test_llm_service.py tests/test_soul_dialogue.py tests/test_api_app.py tests/test_cli.py tests/test_openclaw_cli.py tests/test_desktop_web_pool_status.py tests/test_mobile_web_delight_layout.py extension/tests/popup-helpers.test.ts docs/modules/llm.md docs/modules/soul.md docs/modules/cli.md docs/modules/runtime.md docs/architecture.md docs/spec.md README.md README_EN.md docs/diagrams/web-architecture.html docs/changelog.md
git commit -m "fix(chat): preserve typed dialogue failures"
```

---

### Task 6: Integrated verification and issue-range audit

**Files:**
- Verify: all files changed in Tasks 1–5
- Verify: `docs/superpowers/specs/2026-07-13-issues-101-109-web-reliability-design.md`
- Verify: `docs/superpowers/plans/2026-07-13-issues-101-109-web-reliability.md`

**Interfaces:**
- Consumes: every task deliverable and the accepted design acceptance criteria.
- Produces: fresh verification evidence and an explicit #101–#109 disposition table for handoff.

- [ ] **Step 1: Run all focused regressions together**

```bash
.venv/bin/pytest \
  tests/test_api_image_proxy.py \
  tests/test_desktop_web_autoload.py \
  tests/test_desktop_web_autoload_margin_e2e.py \
  tests/test_desktop_web_card_metadata.py \
  tests/test_desktop_web_issue_98_e2e.py \
  tests/test_desktop_web_motion_polish.py \
  tests/test_desktop_web_pool_status.py \
  tests/test_mobile_web_probe_delight_e2e.py \
  tests/test_mobile_web_view_models.py \
  tests/test_mobile_web_delight_layout.py \
  tests/test_probe_message_treatments.py \
  tests/test_llm_service.py \
  tests/test_soul_dialogue.py \
  tests/test_api_app.py \
  tests/test_cli.py \
  tests/test_openclaw_cli.py -q
node --test tests/js/mobile-probe-notification-helpers.test.mjs
cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts
```

Expected: PASS, with browser modules skipped only when Playwright/Chrome is genuinely unavailable and that limitation recorded.

- [ ] **Step 2: Run repository quality gates**

```bash
.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
.venv/bin/pytest
.venv/bin/pytest --cov=openbiliclaw
```

Expected: PASS and total coverage remains at least 70%. If the full suite or coverage suite exposes a pre-existing environment-only failure, reproduce the focused tests separately and report the exact command/output; do not weaken tests or silently omit the gate.

- [ ] **Step 3: Run JavaScript syntax checks**

```bash
node --check src/openbiliclaw/web/desktop/assets/js/app.js
node --check src/openbiliclaw/web/js/view-models.js
node --check src/openbiliclaw/web/js/views/chat.js
node --check src/openbiliclaw/web/js/views/recommend.js
node --check extension/popup/popup-helpers.js
node --check extension/popup/popup.js
```

Expected: PASS.

- [ ] **Step 4: Audit documentation requirements and issue disposition**

Confirm the diff contains updates to every required document named in Tasks 1–5 and no config/installer files. Prepare this exact handoff table from fresh test evidence:

| Issue | Final disposition |
| --- | --- |
| #101 | implemented: progressive hydration + four-cover eager boundary |
| #102 | already merged; browser geometry/ARIA/10px/50px regressions added |
| #103 | implemented: keyed retained pending state |
| #104 | implemented: liked status and actions coexist |
| #105 | already on main; unchanged 50px Chromium regression rerun |
| #106 | implemented: available-width container responsiveness |
| #107 | implemented: typed safe failed turns, no learning |
| #108 | implemented: cross-surface pressed/disabled projection |
| #109 | implemented: bounded domain-aware feedback copy |

- [ ] **Step 5: Inspect final branch scope**

```bash
git status --short --branch
git log --oneline --decorate origin/main..HEAD
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
```

Expected: only the pre-existing `.playwright-cli/` remains untracked, all planned commits are present, and `git diff --check` reports no whitespace errors. Do not push or close issues.
