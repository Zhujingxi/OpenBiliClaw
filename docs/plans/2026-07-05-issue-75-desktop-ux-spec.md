# Issue #75 Desktop Web UX Spec — interaction & visual polish

**Created:** 2026-07-05
**Source:** [GitHub issue #75](../../../../issues/75) (DongLanQwQ0) — 7 suggestions + dark mode
(comment). Triage verdict (agreed with maintainer): 5 adopt fully, 3 adopt with conditions,
1 downgraded to a cheaper equivalent.
**Scope:** desktop web UI (`src/openbiliclaw/web/desktop/` — `index.html`, `assets/js/app.js`,
`assets/css/app.css`) plus one **additive** API payload change (`RecommendationOut`).
**Out of scope:** extension popup (issue's selectors are all desktop-web classes), the guided-init
setup page (`web/setup/`), and a generic optimistic-update framework. Publication-time
(`pubdate`) capture was continued by the 2026-07-11 cross-platform design/plan with additive
schema migration and no historical network backfill; coin-count capture remains rejected by
maintainer decision (see Rejected/Deferred).

## Goal

Close the perceived-quality gap the issue documents: content opens that ignore browser
conventions (no middle-click / Ctrl+click), dead-feeling transitions (drawer close, page switch),
layout jitter (scrollbar), information-poor cards (no duration / stats), click-heavy pagination,
and an all-light UI. All changes are presentation-layer or additive-payload; no recommendation
logic, no discovery behavior, no storage schema changes.

## Design invariants (MUST hold in every phase)

1. **API changes are additive-only.** `RecommendationOut` gains optional fields with defaults
   (`api/models.py:99`). The extension popup consumes the same endpoint and must keep working
   unmodified (it ignores unknown fields).
2. **Real anchor semantics for content opens.** Every "open content" affordance becomes a real
   `<a href … target="_blank" rel="noopener noreferrer">`. Middle-click fires `auxclick`, not
   `click` — click-signal tracking (`trackRecommendationClick`, `app.js:1752`) must be wired to
   **both** events or middle-click opens go unrecorded.
3. **Multi-platform display degrades cleanly.** Stats/duration/author-link render only when the
   data exists (`duration > 0`, `view_count > 0`, `up_mid > 0`); zero/absent values hide the
   element rather than showing "0" — xhs / X / YouTube items must not grow empty chrome.
4. **Dark theme is token-level only.** One `[data-theme="dark"]` override block on the `:root`
   custom properties; no per-component color forks. The warm Claude aesthetic carries over
   (warm dark grays, terracotta accent) — no cool blue-blacks. Hardcoded colors outside the
   token block (8 in CSS, ~5 in JS) are tokenized first.
5. **Motion respects `prefers-reduced-motion`.** New animations (drawer exit, page fade) sit
   behind one global reduced-motion guard (none exists in `app.css` today — add it).
6. **Auto-load protects the candidate pool.** Pool replenishment is slow (known issue); infinite
   scroll must not drain it: single-flight, cooldown ≥ 8s between auto-triggers, and auto-fire
   suspends when `state.runtimeStatus.pool_available_count` is 0 (manual button remains always).
7. **Frontend prefs persist via the existing pattern** — `storageGet`/`storageSet` keys like
   `DISMISS_ON_RESHUFFLE_KEY` (`app.js:299`), surfaced in the settings page.
8. **Profile-edit writes stay authoritative on the backend.** The instant-feedback change is
   visual only (disable + pending style while the request runs, restore + toast on failure);
   `state.profileEditState` is still only updated from server responses
   (`applyProfileEdit`, `app.js:2453`).

## Current diagnosis

### D1. Content opens are buttons, not links

Every card cover is `<button class="cover">` with a `click` → `window.open` handler: main
recommendation card (`app.js:1669`, opened via `openRecommendation` `app.js:1770`), saved
watch-later/favorites cards (`app.js:1305,1317`), message-drawer items (`app.js:2880`), delight
banner (`app.js:1317` area). Middle-click, Ctrl/Cmd+click, right-click→"open in new tab", and
link drag all do nothing. This is the root cause of issue item 1.

### D2. Drawer close is instant; open is animated

`.drawer/.overlay` toggle `display: none ↔ block` via `.is-open` (`app.css:555-556`);
`closePanel` (`app.js:1200`) removes the class synchronously. `display` can't transition, so any
exit animation needs a short `.is-closing` phase in JS before the class flip. The left
`side-drawer-panel` already has a transform transition (`app.css:199-200`) — the right-sheet
`drawer-panel` (`app.css:558`) is the one that pops.

### D3. Cards show only title + author

`RecommendationOut` (`api/models.py:99`) carries no `duration` / stats / `up_mid`, even though:
the frontend normalizer **already reads `item?.duration`** (`app.js:670`, always empty today);
`Recommendation.content` is a `DiscoveredContent` (`recommendation/engine.py:141`) which has
`duration`, `view_count`, `like_count`, `danmaku_count`, `up_mid` populated
(`discovery/engine.py:422-431`); `content_cache` persists all of them (`database.py:177-191`)
and both row→object rebuild sites map them (`recommendation/engine.py:2489`,
`discovery/engine.py:2221`). The gap is exactly two serialization sites:
`_serialize_recommendation_items` (`api/app.py:2867`) and the reshuffle path (`api/app.py:3641`).
At the time of this spec, publish time and coin count were captured **nowhere** (pubdate only
existed for watch history, `bilibili/api.py:471`). Publication time is now continued as
cross-platform full-pipeline work in the
[multi-platform design](../superpowers/specs/2026-07-11-multiplatform-published-time-design.md)
and [implementation plan](../superpowers/plans/2026-07-11-multiplatform-published-time.md).
Coin count remains rejected by maintainer decision: it is Bilibili-only and not part of the
cross-platform card contract.

### D4. No author-page link

`up_mid` never reaches the frontend (same serialization gap as D3). For Bilibili,
`https://space.bilibili.com/{mid}` is a stable URL; other platforms have no uniform author URL —
hence the bilibili-only condition (`up_mid > 0` ∧ platform bilibili).

### D5. Scrollbar appearance shifts the topbar

Page scrolls on the root; when content exceeds the viewport the classic-scrollbar gutter
appears and shifts centered/right-aligned topbar content (`.top-actions`, `app.css:141-143`)
by the scrollbar width. `html { scrollbar-gutter: stable; }` reserves the gutter permanently.

### D6. Page switches are hard cuts

`showMainPage` (`app.js:1211`) flips the `hidden` attribute across the six `MAIN_PAGE_IDS`
sections. No enter transition exists. A CSS-only entry animation (keyframes fire when `hidden`
is removed) fixes this without touching JS timing.

### D7. Load-more is manual and click-heavy

`#loadMoreBtn` (`index.html:170`) → `appendMore` (`app.js:3547`), one POST per click, no
prefetch, no scroll trigger. `state.runtimeStatus.pool_available_count` is already maintained
client-side (`app.js:3583`) — the throttle signal for invariant 6 is free.

### D8. Profile chip removal feels laggy

Chip ✕ buttons (`data-edit-remove`, `app.js:2507,2544,2549`) → `applyProfileEdit`
(`app.js:2453`) awaits the POST, then re-renders. On a slow backend the chip sits inert for the
full round-trip. True optimistic removal needs rollback of nested edit-state — poor
cost/benefit for a low-frequency surface. Downgrade: immediate pending-state visuals
(chip dims + disables instantly, restores on failure), backend stays authoritative.

### D9. Light-only UI, but fully tokenized

`:root` in `app.css` defines a complete token system (bg/surface ramp, 4-level fg ramp,
borders, accent, semantic colors) with **414 `var()` references** and only 8 hardcoded color
literals outside it (`app.css:393,395,403,404,783,850` + 2 comment mentions) plus ~5 in JS.
Dark mode is structurally cheap: tokenize the stragglers, add one dark token block, a
three-state toggle (auto / light / dark), and `color-scheme` metadata for native controls.

## Priority classification

| Phase | Content | Issue item | Tier | Why |
| --- | --- | --- | --- | --- |
| 1 | Anchor-ize all content opens (middle/Ctrl-click, context menu) | 一(中键) | **MUST** | Browser-convention bug, small diff, no API surface |
| 2 | CSS polish bundle: scrollbar gutter + drawer exit + page-switch fade + reduced-motion guard | 二 / 五 / 六 | **MUST** | Three cheap fixes, one file each; bundling avoids churn |
| 3 | Card metadata: serialize + render `duration` / `view_count` / `like_count` / `danmaku_count` / `up_mid` | 三 (subset) | **MUST** | Data already captured & stored; gap is 2 serialization sites + card meta row. Publication time is continued in the 2026-07-11 cross-platform design/plan; coin remains rejected |
| 4 | UP 主 author link (bilibili-only, consumes Phase 3's `up_mid`) | 一(UP跳转) | RECOMMENDED | Depends on Phase 3 payload; conditional render per invariant 3 |
| 5 | Dark mode (auto/light/dark, token-level) | comment | RECOMMENDED | Highest user-visible value; structurally ready (D9) but widest visual blast radius → own phase |
| 6 | Auto-load on scroll (IntersectionObserver sentinel, pool-aware throttle) | 七 | RECOMMENDED | UX win but must not drain the pool (invariant 6); button kept as fallback |
| 7 | Instant pending-state feedback on profile edits | 四 (downgraded) | OPTIONAL | 90% of perceived fix at ~1/3 of optimistic-update cost |
| 8 | Docs sync + issue #75 reply draft | — | **MUST** (docs) | CLAUDE.md obligation; reply posts only after maintainer approval |

Dependencies: Phase 4 → Phase 3 (same payload). Everything else independent.
**Recommended order:** 1 → 2 → 3 → 4 (one API-touching wave) → 6 → 7 → 5 last (dark mode
review wants all new UI elements already in place so the dark pass covers them) → 8.
Work can stop after any phase with shipped value retained.

## Phase designs

### Phase 1 — Real links for content opens

Replace `<button class="cover">` with `<a class="cover" href="${url}" target="_blank"
rel="noopener noreferrer">` in the four card templates (main rec card `app.js:1669`, saved
cards `app.js:1305`, message items, delight banner). Keep visual parity (`.cover` styles apply
to the anchor; add `display:block` resets as needed). Event wiring:

- `click` handler: `preventDefault()` is **not** needed — let the browser open the tab natively;
  the handler only fires tracking (`trackRecommendationClick`) + status-line update. This also
  makes Ctrl/Cmd+click and Shift+click behave natively.
- `auxclick` (button 1) handler: tracking only — the browser handles the open.
- No-URL fallback: when `contentUrl(item)` is empty, render the existing `<button>` variant
  unchanged (keeps the "后端没有返回可打开链接" path).
- The old `openRecommendation` `window.open` call is deleted, not conditionally kept — one
  open path.

Acceptance: middle-click and Ctrl+click open new tabs on every card type; both record a click
signal; right-click shows the native link menu; keyboard Enter still opens (native anchor
behavior); no-URL cards unchanged.

Regression status: the recommendation cover's left-click and `auxclick(button === 1)` paths
now have an exact contract test requiring both to call `openRecommendation(item, card)`, so
tracking, status-line updates and toast behavior cannot silently diverge.

### Phase 2 — CSS polish bundle

1. **Scrollbar gutter:** `html { scrollbar-gutter: stable; }` (`app.css:124`). Overlay-scrollbar
   platforms (macOS default) are unaffected — the property reserves space only where a classic
   gutter exists.
2. **Drawer exit animation:** add `.drawer.is-closing` state — `closePanel` (`app.js:1200`)
   adds `.is-closing`, waits `animationend` (fallback timeout ~220ms), then removes both
   classes. CSS: right-sheet `drawer-panel` gets symmetric slide-out (`translateX(16px)` +
   fade), overlay backdrop fades. Guard against double-close (`is-closing` present → no-op).
   `openPanel` on a closing drawer cancels the pending close (clear timeout, remove
   `.is-closing`) so rapid re-open never strands a hidden drawer.
3. **Page-switch fade:** `@keyframes page-enter { from { opacity: 0; translate: 0 6px; } }`
   applied to the six main page sections (`#homePage, #watchLaterPage, …`) — keyframes restart
   automatically when `hidden` is removed; no JS change. Duration ≤ 180ms so switching feels
   faster, not slower.
4. **Reduced-motion guard:** `@media (prefers-reduced-motion: reduce)` disabling the new
   animations (and ideally existing transitions via `transition-duration: 0.01ms`-style
   blanket).

### Phase 3 — Card metadata (additive payload + meta row)

Backend: `RecommendationOut` gains `duration: int = 0`, `view_count: int = 0`,
`like_count: int = 0`, `danmaku_count: int = 0`, `up_mid: int = 0`. Populate at both
serialization sites (`api/app.py:2867`, `:3641`) from `item.content` with the same
`getattr(..., 0)` defensiveness as the existing multi-source fields.

Frontend: `normalizeRecommendation` (`app.js:657`) parses the numbers; card template renders a
meta row under the title:

- `duration` → `mm:ss` / `h:mm:ss`, rendered as a badge on the cover corner (video content
  types only — `content_type === "video"`).
- `view_count` / `like_count` / `danmaku_count` → CN-style compact formatting (`1.2万`,
  `3.4亿`) in the `.video-meta` line, each hidden when 0 (invariant 3).
- Saved-card and message templates reuse the same formatter helpers but only where the data
  flows (watch-later/favorites rows come from their own endpoints — check payloads; render
  only what's present, do not extend those endpoints in this phase).

Publication time is no longer deferred here: the 2026-07-11 cross-platform design/plan carries
it through discovery, storage, recommendation/delight APIs and all four user surfaces. Coin
count remains explicitly rejected (see Rejected/Deferred).

### Phase 4 — UP 主 author link

When `platform === "bilibili"` and `up_mid > 0`: render the author name inside `.video-meta`
as `<a href="https://space.bilibili.com/${up_mid}" target="_blank" rel="noopener noreferrer">`.
Muted link styling (underline on hover only) so the card doesn't grow visual noise. All other
platforms / missing mid: plain text exactly as today. No click tracking (author browsing is
not a recommendation signal today — do not invent one silently).

### Phase 5 — Dark mode

1. **Tokenize stragglers:** promote the 8 CSS literals to tokens (probe accents `#6d28d9` /
   `#1d4ed8` → `--probe-challenge` / `--probe-avoidance`, favorite star `#e8a33d` →
   `--star-active`, `rgba(0,0,0,.06)` overlay → `--overlay-faint`); audit the ~5 JS-side color
   strings (`app.js`) and route them through CSS classes or tokens.
2. **Dark token block:** `:root[data-theme="dark"] { … }` redefining the surface ramp (warm
   near-blacks: e.g. bg ≈ `#1a1915`, surface ≈ `#211f1a`, warm sand → deep warm gray), fg ramp
   (warm off-whites), borders, `--accent-on`, semantic colors (adjusted for contrast),
   `--overlay-faint`, shadows. Terracotta accent stays; verify WCAG AA contrast for body text
   and `.video-meta` grays.
3. **Auto mode:** `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { … } }`
   sharing the same block via CSS nesting or duplication-by-build (given no build step: define
   the dark values once as `--dark-*`-independent block — simplest is to duplicate the override
   selector list; keep the two selector paths adjacent with a comment).
4. **Toggle:** three-state (跟随系统 / 浅色 / 深色) — a topbar icon button cycling states +
   a settings-page control; persisted via `storageSet("obc.theme", …)`; applied on boot before
   first paint (inline `<script>` in `<head>` reading localStorage to avoid flash-of-light).
5. **Native controls:** `<meta name="color-scheme" content="light dark">` +
   `color-scheme: light dark` on `:root`, flipped appropriately per theme, so scrollbars /
   inputs / popups follow.
6. **Sweep:** manual pass over every page/drawer/modal (six main pages, three drawers, mobile
   menu, delight banner, init onboarding, toasts) in dark mode; fix stragglers at token level.

### Phase 6 — Auto-load on scroll

`IntersectionObserver` on a sentinel just above `#loadMoreBtn`'s `.load-row`. On intersect:
call the existing `appendMore` (`app.js:3547`) iff **all** hold — not already in flight
(single-flight flag), ≥ 8s since last auto-trigger, `pool_available_count > 0`, home page
visible, and recommendation grid non-empty (never auto-fire on the init/empty states —
`#loadMoreBtn` is already hidden there, `app.js:1014,1037`). The button stays visible as
manual fallback and shows a brief "正在自动加载…" state during auto-fires. When the pool is
dry, the observer stays connected but the callback no-ops until `pool_available_count`
recovers (runtime-stream updates arrive for free). Add a settings-page toggle (default on)
via the frontend-settings pattern (`restoreFrontendSettings`, `app.js:429`).

### Phase 7 — Instant profile-edit feedback

On chip ✕ click (`data-edit-remove` / `data-edit-remove-specific`): synchronously add
`.is-pending` (opacity .45, `pointer-events: none`) to the chip and disable the button, then
run `applyProfileEdit` as today. Success path re-renders (chip gone — no change needed);
failure path already re-renders from server state + toasts (`app.js:2464`), which restores the
chip automatically. Same treatment for the add-input buttons (disable while in flight).
No changes to `applyProfileEdit`'s state semantics (invariant 8).

## Rejected / deferred (with reasons, for the issue reply)

- **Publish time on cards** — continued and implemented by the
  [2026-07-11 cross-platform design](../superpowers/specs/2026-07-11-multiplatform-published-time-design.md)
  and [plan](../superpowers/plans/2026-07-11-multiplatform-published-time.md): best-effort exact
  time plus source-relative fallback flows through seven platforms and four surfaces, without
  detail-page requests or network backfill of old cache rows.
- **Coin count on cards** — rejected by maintainer decision: Bilibili-only metadata is not
  collected, stored or rendered by the cross-platform contract.
- **True optimistic updates for profile edits** — rejected for now: rollback complexity on
  nested edit-state vs. a low-frequency surface; Phase 7 delivers the perceived-latency fix.
- **Unbounded infinite scroll** — rejected: pool replenishment is slow; throttled auto-load
  (invariant 6) is the shipped compromise.

## Documentation obligations (per CLAUDE.md)

- `docs/modules/runtime.md` — desktop web UI: card anchors + metadata row, dark mode,
  auto-load behavior and its pool guard, new frontend settings keys
- `docs/modules/extension.md` — note the additive `RecommendationOut` fields (popup unaffected)
- `docs/changelog.md` — bullet under the current version block
- Issue #75 — triage reply (adopted / conditional / deferred with reasons); draft in Phase 8,
  post only after maintainer approval
