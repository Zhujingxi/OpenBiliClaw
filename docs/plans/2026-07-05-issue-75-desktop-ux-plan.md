# Issue #75 Desktop Web UX — Implementation Plan

> **Spec:** [`2026-07-05-issue-75-desktop-ux-spec.md`](./2026-07-05-issue-75-desktop-ux-spec.md)
> **Status:** Final — 2026-07-05. Implement task-by-task; do not start a task before the
> previous one's tests are green.
> **Execution order (from Spec):** Task 1 → 2 → 3 → 4 → 6 → 7 → 5 (dark mode last so its
> sweep covers all new UI) → 8 (docs + issue reply). Task 4 depends on Task 3; all others
> independent — safe stopping point after every task.
> **Tech:** vanilla JS/CSS (no build step for `web/desktop`), Python 3.11+ backend, pytest
> (`asyncio_mode=auto`), Ruff, MyPy strict, 100-char lines. Interpreter is `.venv/bin/python`
> (plain `python`/`python3` has no deps).
> Run per task: `.venv/bin/python -m pytest <touched test files> -q`; for backend-touching
> tasks also `.venv/bin/python -m ruff check` / `ruff format --check` on touched files +
> `.venv/bin/python -m mypy src/openbiliclaw/`.
> **Frontend test convention:** `tests/test_desktop_web_*.py` files assert on the raw text of
> `index.html` / `app.js` / `app.css` (see `tests/test_desktop_web_pool_status.py` for the
> pattern) — contract tests, not DOM tests. Real behavior is verified manually (or via
> chrome-devtools MCP) against a running `serve-api`; **routes are fixed at server start but
> JS/CSS are served live** — a plain browser refresh picks up frontend edits, no restart needed.

**Invariants that MUST hold (from Spec — re-read before each task):**
- `RecommendationOut` changes are additive with defaults; the later 2026-07-11 publication-time
  follow-up also updates the extension popup under its own cross-platform contract.
- Content opens are real anchors; click tracking fires on both `click` and `auxclick`.
- Zero/absent metadata hides the element (no "0 播放", no empty badges on xhs/X/YouTube items).
- Dark mode = one token override block; no per-component color forks; tokenize literals first.
- New animations sit behind a `prefers-reduced-motion` guard.
- Auto-load: single-flight, ≥ 8s cooldown, suspended while `pool_available_count === 0`,
  manual button always available.
- Profile-edit state only ever updates from server responses; Task 7 is visuals-only.
- New persisted prefs use the `storageGet`/`storageSet` pattern and surface in settings.

---

### Task 1: Anchor-ize content opens (middle-click / Ctrl+click / context menu)

**Files:** Modify `src/openbiliclaw/web/desktop/assets/js/app.js`,
`src/openbiliclaw/web/desktop/assets/css/app.css`;
Test add `tests/test_desktop_web_card_links.py`

**Steps:**
1. Failing contract tests: `app.js` contains `<a class="cover` (anchor template) and an
   `auxclick` listener registration; `app.js` does NOT contain the `openRecommendation`
   `window.open` call pattern for the main card path.
2. Main recommendation card (`app.js:1669`): template emits
   `<a class="cover…" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" …>`
   when `contentUrl(item)` is truthy; keep the `<button>` variant verbatim when URL is empty
   (existing "后端没有返回可打开链接" path must keep working — the status-line message on
   click stays).
3. Rework `openRecommendation` (`app.js:1770`): drop `window.open`; it becomes the shared
   tracking + status-line routine. Wire it to the anchor's `click` (no `preventDefault` —
   browser opens the tab natively, which also fixes Ctrl/Cmd/Shift+click) and `auxclick`
   (fire tracking only when `event.button === 1`).
   The shipped path has an exact regression contract requiring left `click` and middle
   `auxclick(button === 1)` to invoke the same `openRecommendation(item, card)` handler.
4. Same anchor treatment for: saved watch-later/favorites cards (`app.js:1305,1317` —
   `renderSavedList`), message-drawer content links (`app.js:2880` uses a confirm-style
   `window.open`; convert the "view" affordance to an anchor where a URL exists), delight
   banner cover.
5. CSS: ensure `.cover` renders identically as an anchor (`display:block`, no underline,
   inherit color; check focus-visible outline still appears).
6. Manual verification against a running backend: left / middle / Ctrl+click on all four card
   types open exactly one tab each and each records a click (check backend log or
   `/api/…click` in devtools network); right-click shows the link context menu; a no-URL item
   still shows the fallback message.
7. Run `pytest tests/test_desktop_web_card_links.py -q`.

### Task 2: CSS polish bundle (scrollbar gutter, drawer exit, page fade, reduced motion)

**Files:** Modify `src/openbiliclaw/web/desktop/assets/css/app.css`,
`src/openbiliclaw/web/desktop/assets/js/app.js`;
Test add `tests/test_desktop_web_motion_polish.py`

**Steps:**
1. Failing contract tests: `app.css` contains `scrollbar-gutter: stable`,
   `prefers-reduced-motion`, `.drawer.is-closing`, and a `page-enter` keyframe; `app.js`
   `closePanel` references `is-closing`.
2. `html { scrollbar-gutter: stable; }` next to the existing `html` rule (`app.css:124`).
3. Drawer exit: extend `closePanel` (`app.js:1200`) — if panel lacks `.is-open` or already has
   `.is-closing`, no-op; else add `.is-closing`, then on `animationend` (once) or a ~220ms
   fallback timeout remove `.is-open`, `.is-closing`, `from-mobile-menu` and run the existing
   messagesDrawer snapshot cleanup. `openPanel` (`app.js:1199`) cancels an in-flight close
   (clear timeout, strip `.is-closing`) before adding `.is-open`. Store the timeout handle on
   the element (`panel._closeTimer`) to avoid a module-level map.
4. Drawer exit CSS: `.drawer.is-closing` stays `display:block`; `.drawer.is-closing
   .drawer-panel` plays slide-out+fade (~200ms, mirror of entry); backdrop fades. Mobile
   full-screen variant (`app.css:688`) gets the same fade.
5. Page fade: `@keyframes page-enter` (opacity 0 → 1, `translate: 0 6px → 0`, ≤ 180ms) applied
   to the six `MAIN_PAGE_IDS` sections — keyframes re-fire when `hidden` is removed; no JS
   change to `showMainPage`.
6. Reduced-motion guard: `@media (prefers-reduced-motion: reduce)` zeroing the new animations
   and drawer/side-drawer transitions.
7. Manual verification: open/close messages + activity + mobile-QR drawers (smooth both ways,
   rapid open-close-open doesn't strand a hidden drawer); switch all six pages (fade-in, no
   lag); shrink viewport height until scrollbar appears (topbar no longer shifts — test on a
   classic-scrollbar platform or Chrome with overlay scrollbars disabled).
8. Run `pytest tests/test_desktop_web_motion_polish.py -q`.

### Task 3: Card metadata — additive payload + meta row

**Files:** Modify `src/openbiliclaw/api/models.py`, `src/openbiliclaw/api/app.py`,
`src/openbiliclaw/web/desktop/assets/js/app.js`,
`src/openbiliclaw/web/desktop/assets/css/app.css`;
Test `tests/test_api_app.py`, add `tests/test_desktop_web_card_metadata.py`

**Steps:**
1. Failing backend tests (extend the existing recommendation-endpoint tests around
   `tests/test_api_app.py:3180`): `/api/recommendations` items include `duration`,
   `view_count`, `like_count`, `danmaku_count`, `up_mid` matching the stubbed
   `DiscoveredContent`; a content object missing the attrs (plain stub) serializes to `0`s;
   the reshuffle endpoint (`:4038` test family) carries the same fields.
2. `RecommendationOut` (`api/models.py:99`): add the five `int = 0` fields with a comment
   marking them additive for card metadata (issue #75).
3. Populate at both sites — `_serialize_recommendation_items` (`api/app.py:2867`) and the
   reshuffle serializer (`api/app.py:3641`) — via `int(getattr(item.content, "...", 0) or 0)`.
4. Failing frontend contract tests: `app.js` contains `formatDuration` and `formatCountCn`
   helpers and `normalizeRecommendation` parses the new fields; `app.css` has a
   `.duration-badge` rule.
5. Frontend: `normalizeRecommendation` (`app.js:657`) — `duration: Number(...) || 0` (note:
   replaces today's dead `String(item?.duration ?? "")`), plus the three counts and `up_mid`.
   Helpers: `formatDuration(seconds)` → `m:ss` / `h:mm:ss`; `formatCountCn(n)` → `9999` /
   `1.2万` / `3.4亿`, returning `""` for `n <= 0`.
6. Card template (`app.js:1669`): duration badge overlaid on the cover corner (only when
   `content_type === "video"` and `duration > 0`); stats line under `.video-meta` — e.g.
   `▶ 1.2万 · 👍 3400 · 弹幕 890` — each segment omitted when its count is 0; whole line
   omitted when all are 0 (invariant 3 — xhs/X/YouTube cards must look unchanged when data
   is absent). Keep `recommendationMeta` (author) as-is; stats are a sibling line.
7. CSS: `.duration-badge` (bottom-right of cover, `--overlay`-style bg, small text),
   `.video-stats` muted line — token colors only (Task 5 will inherit them for free).
8. Manual verification for this phase: bilibili cards show badge + stats; an X/tweet card
   shows neither; the extension popup still renders against the additive payload. Its later
   publication-time rendering is covered by the 2026-07-11 cross-platform plan.
9. Run `pytest tests/test_api_app.py -q tests/test_desktop_web_card_metadata.py -q` + ruff +
   mypy (backend files).

### Task 4: UP 主 author link (bilibili-only)

**Files:** Modify `src/openbiliclaw/web/desktop/assets/js/app.js`,
`src/openbiliclaw/web/desktop/assets/css/app.css`;
Test extend `tests/test_desktop_web_card_metadata.py`

**Steps:**
1. Failing contract test: `app.js` contains `space.bilibili.com/` inside an anchor template
   guarded by a `up_mid` check.
2. In the card template's meta rendering: when `item.platform === "bilibili" && item.up_mid > 0`,
   wrap the author name in `<a class="up-link" href="https://space.bilibili.com/${item.up_mid}"
   target="_blank" rel="noopener noreferrer">`; otherwise render plain text exactly as today.
   `stopPropagation` is unnecessary (author link is not inside the cover anchor) — verify no
   parent click handler intercepts it.
3. CSS: `.up-link` inherits `.video-meta` color, no underline, underline + `--fg-2` on hover.
4. No click tracking (spec decision — do not invent an author-browsing signal silently).
5. Manual verification: bilibili card author opens the space page in a new tab (left + middle
   click); xhs/YouTube authors stay plain text.
6. Run `pytest tests/test_desktop_web_card_metadata.py -q`.

### Task 5: Dark mode (auto / light / dark)

**Files:** Modify `src/openbiliclaw/web/desktop/assets/css/app.css`,
`src/openbiliclaw/web/desktop/assets/js/app.js`, `src/openbiliclaw/web/desktop/index.html`;
Test add `tests/test_desktop_web_dark_mode.py`

**Steps:**
1. Tokenize stragglers first (pure refactor, zero visual delta in light mode): promote
   `app.css:393,395,403,404` probe colors → `--probe-challenge` / `--probe-avoidance`,
   `:783` star → `--star-active`, `:850` overlay → `--overlay-faint`; audit the ~5 hardcoded
   color strings in `app.js` (`grep -n '#[0-9a-fA-F]\{6\}\|rgba(' app.js`) and move them to
   CSS classes/tokens where they set colors.
2. Failing contract tests: `app.css` contains `[data-theme="dark"]` and
   `prefers-color-scheme: dark`; `index.html` contains `name="color-scheme"` and an inline
   head script referencing the theme storage key; `app.js` contains the three-state cycle
   (`auto` / `light` / `dark`) and the storage key constant.
3. Dark token block: `:root[data-theme="dark"] { … }` — warm dark surface ramp (bg `#1a1915`
   territory, never cool blue-black), warm off-white fg ramp, borders, shadows,
   `--accent-on`, semantic colors, `--overlay-faint`, `color-scheme: dark`. Duplicate the
   block under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) … }`
   with an adjacency comment ("keep both blocks in sync — no build step to dedupe").
   Spot-check AA contrast for `--muted`/`--meta` on `--bg`.
4. Boot + toggle: inline `<head>` script sets `data-theme` from `localStorage` before first
   paint (no flash); topbar icon button cycles 跟随系统 → 浅色 → 深色 (title reflects state);
   settings page gets the same control via the frontend-settings pattern
   (`restoreFrontendSettings`, `app.js:429`); persist with `storageSet`.
5. `<meta name="color-scheme" content="light dark">` in `index.html`.
6. **Dark sweep** (the real work): walk every surface in dark mode — six main pages, three
   drawers, mobile menu, delight banner, init onboarding, profile edit chips, chat page,
   settings forms, toasts, modals, Task 3's duration badge / stats line, empty states, error
   banner. Fix issues at token level only; if a component needs its own color, that's a new
   token, not a literal.
7. Manual verification: toggle cycles correctly and persists across reload; auto mode follows
   an OS theme flip live; no white flash on load in dark mode; scrollbars/inputs follow the
   theme.
8. Run `pytest tests/test_desktop_web_dark_mode.py -q` plus the other `test_desktop_web_*`
   files (text assertions can break on CSS refactors).

### Task 6: Auto-load on scroll (pool-aware)

**Files:** Modify `src/openbiliclaw/web/desktop/assets/js/app.js`,
`src/openbiliclaw/web/desktop/index.html`;
Test add `tests/test_desktop_web_autoload.py`

**Steps:**
1. Failing contract tests: `app.js` contains `IntersectionObserver` wired to a load sentinel,
   a cooldown constant (≥ 8000ms), a `pool_available_count` guard in the auto-load path, and
   an autoload settings key; `index.html` contains the sentinel element and a settings-page
   toggle input.
2. `index.html`: `<div id="loadMoreSentinel" aria-hidden="true"></div>` just above the
   `.load-row` (`:170`); settings page gains a "滚动到底自动加载推荐" checkbox (default on).
3. `app.js`: observer on the sentinel (`rootMargin: "300px"`). Callback fires `appendMore`
   only when ALL hold: toggle on, not in flight (single-flight flag around `appendMore`),
   `now - lastAutoLoadAt >= 8000`, `state.runtimeStatus?.pool_available_count > 0`, home page
   visible (`#homePage` not hidden), grid non-empty, and `#loadMoreBtn` not hidden (rides the
   existing init/empty-state gating at `app.js:1014,1037,1652`). During an auto-fire the
   button shows "正在自动加载…" and disables; restore after.
4. Persist the toggle via `storageGet`/`storageSet` + `restoreFrontendSettings`; when off,
   disconnect the observer (and reconnect on enable) so it's genuinely inert.
5. Manual verification: scroll to bottom → one batch auto-loads, then a rapid re-scroll waits
   for cooldown; with the pool drained (or `pool_available_count` stubbed 0) auto-fire stops
   but the button still works; toggle off → pure manual behavior; init screen never
   auto-fires.
6. Run `pytest tests/test_desktop_web_autoload.py -q`.

### Task 7: Instant pending-state feedback on profile edits

**Files:** Modify `src/openbiliclaw/web/desktop/assets/js/app.js`,
`src/openbiliclaw/web/desktop/assets/css/app.css`;
Test add `tests/test_desktop_web_profile_edit_feedback.py`

**Steps:**
1. Failing contract tests: `app.css` contains `.edit-chip.is-pending`; `app.js` applies
   `is-pending` before awaiting `applyProfileEdit` in the chip-remove path.
2. In the `data-edit-remove` / `data-edit-remove-specific` handlers (delegated listeners near
   `app.js:2507,2544,2549` wiring): before the `await`, add `.is-pending` to the closest
   `.edit-chip` and `disabled` to the ✕ button. `applyProfileEdit` already re-renders on both
   success and failure (`app.js:2453-2467`), which clears or restores the chip — no rollback
   code needed; just make sure every code path re-renders (the `!res` branch already toasts).
3. Same pre-await disable for `data-edit-add` / `data-edit-add-specific` buttons.
4. CSS: `.edit-chip.is-pending { opacity: .45; pointer-events: none; }` (token-only).
5. Manual verification with a slow backend (devtools throttling): chip dims the instant ✕ is
   clicked; on a forced 500 the chip returns and the toast fires; double-click can't send two
   ops.
6. Run `pytest tests/test_desktop_web_profile_edit_feedback.py -q`.

### Task 8: Documentation sync + issue #75 reply (mandatory, per CLAUDE.md)

**Files:** `docs/modules/runtime.md`, `docs/modules/extension.md`, `docs/changelog.md`;
issue reply draft (not committed)

**Steps:**
1. `runtime.md` desktop-web section: card anchors + metadata row (fields, zero-hide rule),
   dark mode (three-state, token architecture, storage key), auto-load behavior + pool guard,
   pending-state edit feedback, new frontend settings keys.
2. `extension.md`: record the additive metadata contract. Historical duration/engagement
   fields remained ignorable; the 2026-07-11 follow-up additionally consumes
   `published_at` / `published_label` in popup recommendation and delight cards.
3. `docs/changelog.md`: bullet under the current version block, e.g.
   `feat: 桌面 Web 交互打磨(issue #75)——视频卡片改真链接(中键/Ctrl+点击可用)+时长/播放/点赞
   元信息 + UP 主跳转;新增暗色模式(跟随系统/手动);抽屉退出动画、分区切换过渡、滚动条防抖动;
   滚动到底自动加载(带候选池保护);画像编辑即时反馈`.
4. Draft the issue #75 reply from the Spec's triage table: adopted (items 1/2/5/6 + dark
   mode), adopted-with-conditions (metadata subset — publication time is continued by the
   [2026-07-11 design](../superpowers/specs/2026-07-11-multiplatform-published-time-design.md)
   and [plan](../superpowers/plans/2026-07-11-multiplatform-published-time.md), while coin count
   remains rejected by maintainer decision; UP link bilibili-only; auto-load throttled with the pool-protection reason),
   downgraded (item 4 → instant feedback, optimistic updates rejected for now). **Post only
   after maintainer approval.**
5. Full suite once at the end: `.venv/bin/python -m pytest -q` + ruff + mypy.

---

## Verification after merge

1. Manual smoke on a real backend: middle-click every card type; drawer close animation;
   page-switch fade; dark mode across all pages; auto-load at bottom with a healthy pool.
2. Extension popup regression: load the extension against the updated backend. The original
   additive duration/engagement fields remain compatible; the 2026-07-11 follow-up verifies
   publication time on recommendation and delight cards.
3. Watch `pool_available_count` behavior for a few days with auto-load on — if the pool sits
   at 0 noticeably more often, lengthen the cooldown before considering pool-side changes.
4. Issue #75: post the approved reply; close or tag `partially-adopted` per maintainer call.

## Explicitly out of scope

- Extension popup UI (`extension/popup/`) — out of scope for this 2026-07-05 plan; the
  2026-07-11 publication-time follow-up updates its recommendation and delight cards.
- `web/setup/` guided-init page styling.
- Publish-time capture is continued by the 2026-07-11 cross-platform design/plan without
  detail requests or historical network backfill; coin-count capture remains rejected.
- Any recommendation/discovery behavior change; any non-additive API change.
