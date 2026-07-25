# Event Capture Depth Spec — accuracy & coverage of behavioral signals

**Created:** 2026-07-05
**Predecessor:** `2026-07-05-event-acquisition-fixes-spec.md` (reliability batch, committed
as `ab05bd67` on this worktree branch). This batch addresses the remaining audit gaps.
**Scope:** X un-action capture (unlike/unbookmark), pressed-state plumbing for DOM
strong signals, playback-accurate watch_seconds, content-page view+dwell for
xhs/zhihu/reddit/X, URL-derived search capture + weight correction, bilibili
favorites/following incremental caps.
**Out of scope:** xhs DOM-based like/collect/comment capture (source code explicitly
documents the DOM as unstable — `xiaohongshu.ts:3-5`), comment-text capture, 弹幕,
backend profile-side *interpretation* of retraction events (this batch only records
them faithfully; how the soul pipeline discounts retracted likes is a separate spec).

## Goal

The 2026-07-05 audit's remaining gaps make the raw signal stream *inaccurate* (unlikes
counted as likes on X — actually dropped; watch_seconds is wall-clock including paused
time), *blind* (xhs/zhihu/reddit content pages produce zero view/dwell — reading a
zhihu answer for 15 minutes looks identical to bouncing), and *lossy* (button-click
searches missed; bilibili favorites beyond folder 10 and follows beyond page 1 never
sync). Target outcomes:

- An X unlike/unbookmark produces a recorded retraction event instead of vanishing.
- `watch_seconds` counts only time the video was actually playing.
- Opening and dwelling on a xhs note / zhihu answer / reddit post / X status produces
  a `view` event and a visibility-gated dwell measurement.
- Every search reaches the backend regardless of how it was submitted, and search is
  weighted as the strong intent signal it is (0.25 → 0.5).
- account_sync sees all favorite folders (budget-capped) and follows beyond page 1.

## Design invariants (MUST hold in every phase)

1. **Fail open to current behavior.** Every new detection (pressed state, un-action
   ops, URL search extraction) degrades to today's behavior when the signal is absent
   or ambiguous — never suppress an event on uncertainty.
2. **Retraction ≠ dislike.** An unlike is a *neutralization*, not a negative
   preference. Retraction events must not be classifiable as `negative` satisfaction
   and must not ride the dislike/feedback strong-signal path into VALUES/CORE layers.
3. **The X GraphQL tap stays observe-only** (forwards original bytes, reads
   `Response.clone()` only — `x-graphql-tap.ts:291-371`).
4. **Extension test conventions hold**: pure logic in adapters/trackers (unit-tested
   with plain strings/objects), DOM wiring in kernel (covered by the existing
   source-regex style of `kernel.test.ts`). No jsdom.
5. **Dwell events keep their existing envelope** (`type: "click"` on the previous URL
   with `watch_seconds` / `dwell_source` / `dwell_reason` metadata) so backend
   satisfaction classification and the buffer's immediate-flush rule
   (`shouldFlushImmediately`) keep working unchanged.
6. **bilibili request growth is budget-capped**: the favorites raise must carry a
   `max_total_items` budget; following pagination is bounded by a page cap. The 0.2s
   `_min_request_interval` (`bilibili/api.py:221`) is the only throttle these
   endpoints have — total request count per sync must stay bounded by constants.
7. **Search dedup is time-windowed, not stateful across pages**: URL-derived and
   Enter-derived capture of the same query within a short window emit once.

## Current diagnosis (verified against code 2026-07-05)

### D1. X un-actions are silently dropped

`GRAPHQL_OP_EVENTS` (`x-graphql-tap.ts:61-67`) maps only `FavoriteTweet` /
`CreateBookmark` / `CreateRetweet` / `CreateTweet` / `TweetDetail` (+ REST follow).
`UnfavoriteTweet` / `DeleteBookmark` / `DeleteRetweet` return `null` from
`classifyXResponseUrl` and are ignored. The consumer whitelist (`x.ts:80`) lists the
same 6 types — both sides must extend together. Net effect: the profile never learns
a like was withdrawn.

### D2. DOM strong signals can't see pressed state

`buildActionHintFromClickTarget` (`behavior.ts:45-56`) reduces the attributed element
to `{text, ariaLabel, className}` strings; adapters never see the element, so
`aria-pressed` (or "Liked"-style label states, noted in `twitter.ts:75-77`) is
unreadable. Clicking an already-active like button (= un-liking) emits a positive
`like` on every DOM platform.

### D3. watch_seconds is wall-clock

`VideoDwellTracker` records a single `startedAt` and reports
`(now - startedAt) / 1000` (`video-dwell-tracker.ts:54-101`) — paused time and
backgrounded-paused time all count. Kernel already receives `play` / `pause`
callbacks (`kernel.ts:260-265`) but the tracker has no segment model and no video
reference.

### D4. Content-page dwell doesn't exist

Dwell starts only when `detectPageType(url) === "video"` (`kernel.ts:70-84`).
xhs (`note`), zhihu (`answer`/`question`/`article`), reddit (`post`), X (`status`)
all have precise PageTypes and content-id extractors already
(`xiaohongshu.ts:10,23-29`, `zhihu.ts:7-9,28-36`, `reddit.ts:7,24-38`,
`twitter.ts:49-63`) — but produce zero view and zero dwell. The SPA history patch
(`kernel.ts:294-334`) is platform-generic and already flushes dwell on navigation.

### D5. Search capture is Enter-only and under-weighted

`observeSearch` (`kernel.ts:107-117`) fires only on `keydown Enter` inside
`searchInputSelector`. No platform defines a search-button selector; mouse-submitted
searches are lost. Backend default weight is 0.25
(`event_format.py:250`) — below `view` (0.35) despite search being the strongest
explicit-intent signal. Verified: no test asserts the 0.25 and no digest/cache
depends on it.

### D6. account_sync favorites/following caps

account_sync calls `get_all_favorites(max_folders=10, max_items_per_folder=50)`
(`account_sync.py:243-246`, defaults at `:147-148`) — folders 11+ never sync (init
uses `max_folders=200`, `cli.py:5447-5451`). `get_following(page=1, page_size=100)`
(`account_sync.py:263-266`) fetches exactly one page with no pagination loop (init
paginates, `cli.py:5477-5494`). Favorites/following endpoints have no dedicated
backoff — only the global 0.2s interval — so any raise must be budget-capped.

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 1 | X un-action capture (tap ops + consumer + backend retraction event) | **MUST** | Stops silent loss of the only reliably-detectable retraction signal |
| 2 | Pressed-state plumbing (`ActionHint.pressed`) + suppress-on-pressed | **MUST** | Stops un-likes being recorded as likes on DOM platforms wherever the DOM exposes state; fail-open elsewhere |
| 3 | Playback-accurate watch_seconds (segmented tracker) | **MUST** | watch_seconds is the highest-weight interest input; wall-clock poisons it |
| 4 | Content-page view + dwell (xhs/zhihu/reddit/X) | **MUST** | Unblinds three platforms that currently contribute near-noise |
| 5 | URL-derived search capture + weight 0.25 → 0.5 | RECOMMENDED | Cheap; recovers mouse-submitted searches and corrects an obvious mis-weight |
| 6 | account_sync favorites budget raise + following pagination | RECOMMENDED | Small, bounded; closes the folder-11+/page-2+ blind spot |
| 7 | Docs sync | **MUST** (per CLAUDE.md) | |

Dependencies: Phase 2 builds on Phase 1's backend retraction event type (shared
metadata shape). Phase 4 builds on Phase 3's segmented tracker. Phases 5 and 6 are
independent. Extension phases (1–5) and backend Phase 6 can be implemented in
parallel; the backend halves of Phases 1 and 4 (classifier rules) land with those
phases.

## Phase designs

### Phase 1 — X un-action capture

Extension (`x-graphql-tap.ts` + `content/x.ts`) — **one concrete end-to-end shape,
all four touch points updated together** (review finding: a new type otherwise dies
silently at the consumer whitelist):

1. `XEventType` union gains `"retraction"`; `GRAPHQL_OP_EVENTS` maps
   `UnfavoriteTweet` / `DeleteBookmark` / `DeleteRetweet` → `retraction`, and the
   tap payload carries `retracted_action: "like" | "favorite" | "share"`.
   (Do not map `DeleteTweet` — deleting your own tweet is not preference signal.)
2. `isXEngagement` / the `known` whitelist (`x.ts:80`) accepts `retraction`.
3. `content/x.ts` normalization builds the backend event: `event_type="feedback"`,
   `metadata.feedback_type="retraction"`, `metadata.retracted_action=<...>`,
   `metadata.signal_strength=0.2` (explicit — `build_event` respects
   caller-provided strength).
4. Tap tests for each new op + a consumer test that the normalized shape reaches
   the event sender.

Backend (`sources/event_format.py`, `soul/pipeline.py`, `soul/engine.py`):

- No new `_EVENT_TYPES` whitelist entry — retraction rides `feedback`.
- `classify_event_satisfaction` gains a rule: `feedback_type == "retraction"` →
  `neutral` with reason `"retraction"` (invariant 2 — checked *before* any generic
  feedback-negative rule).
- `default_signal_strength_for_event` gains a retraction branch returning **0.2**
  (defense for server-built or metadata-stripped events; the plain feedback default
  would otherwise apply — the extension-side 0.2 alone is not enough).
- `signals_from_events`: retraction feedback maps to a plain BEHAVIOR_EVENT, NOT
  the FEEDBACK strong-signal type (`soul/pipeline.py:100,386-391`) — no
  min_signals=1 bypass, no ENGAGEMENT path.
- **Feedback batch learning must exclude retractions** (review finding — the real
  consumer risk): `SoulEngine.process_feedback_batch_if_needed` queries persisted
  `event_type="feedback"` rows, counts them toward `feedback_batch_threshold=3`,
  and feeds them to LLM preference re-analysis. Filter
  `metadata.feedback_type == "retraction"` out of both the threshold count and the
  analysis input (`soul/engine.py:954-1012` region). Audit other feedback
  consumers: the `/api/feedback` path and `record_immediate_feedback_cognition`
  hook the API endpoint, not the events table — verify and document they are
  unaffected.

**Flush note:** `shouldFlushImmediately` treats all `feedback` as immediate-flush;
retraction inheriting that is acceptable (rare, immediate delivery is fine) —
explicitly unchanged.

### Phase 2 — Pressed-state plumbing for DOM strong signals

- `buildActionHintFromClickTarget` additionally reads
  `aria-pressed` from the attributed element (and walks one step: the attributed
  button itself only): `ActionHint` gains `pressed: boolean | null`
  (`"true"` → true, `"false"` → false, absent/other → null).
- Kernel click handling: when `inferActionType(hint)` returns a positive strong
  signal (`like` / `favorite` / `follow`) **and** `hint.pressed === true`, the action
  is a retraction (clicking an already-active control) — emit the Phase 1 retraction
  feedback event (`retracted_action=<type>`) instead of the positive event.
  `pressed === false` or `null` → emit the positive event exactly as today
  (invariant 1).
- **X double-emit guard** (review finding): on X the GraphQL tap is the
  authoritative retraction source, and the DOM click path would emit a duplicate.
  `PlatformAdapter` gains `strongSignalSource?: "dom" | "tap"` (default `"dom"`;
  twitter sets `"tap"`). On a `"tap"` platform, `pressed === true` **suppresses the
  positive event without emitting a retraction** — the tap emits the real one. The
  plumbing stays platform-generic for any future `aria-pressed`-exposing platform.
- Adapter interfaces don't change (`inferActionType` still receives the hint; the
  pressed decision lives in kernel, one place).

Acceptance: behavior unit tests for the hint extraction (string/attribute matrix);
kernel source-regex test asserting the pressed-check exists in the click path.

### Phase 3 — Playback-accurate watch_seconds

Rework `VideoDwellTracker`'s single-`startedAt` model into **segment accumulation**:

- Session state: `accumulatedMs` + `segmentStartedAt: number | null`. `beginSegment()`
  / `endSegment()` are idempotent. `flush()` reports
  `(accumulatedMs + openSegment) / 1000` as `watch_seconds` and additionally reports
  the old wall-clock value as `metadata.page_dwell_seconds` (kept for
  diagnostics/backward comparability).
- Kernel drives segments from the existing video listeners (`kernel.ts:260-265`):
  `play` → `beginSegment()`, `pause` → `endSegment()`, plus `ended` → `endSegment()`
  (add the listener). At bind time (`attachVideoListeners`), if the element is
  already playing (`!video.paused && !video.ended`), begin a segment — autoplay
  never emits `play`.
- **Playing in a background tab still accumulates** (listening to bilibili/YouTube
  audio in another tab is genuine consumption) — no visibility gating on the video
  path; the gate is purely play-state. Content-page dwell (Phase 4) is where
  visibility gating applies.
- Cap: reported `watch_seconds` is clamped to
  `max(video_duration_seconds * 1.5, 600)` when duration is known (constant) — a
  stuck `pause`-event loss can't produce absurd values.
- **Late-rendered `<video>`** (review finding): kernel attaches video listeners
  only at load and immediately post-navigation — an SPA that inserts `<video>`
  later never gets segment callbacks. Add a bounded retry: after each
  `rebindPageObservers`, if the page is a `"video"` PageType and no video was
  found, retry `attachVideoListeners` every 500ms up to 20 attempts (constants),
  cancelled on the next navigation. (Retry timer, not MutationObserver — simpler,
  bounded, and consistent with the kernel's existing setTimeout style.)
- Backend contract unchanged: `watch_seconds`' *meaning* tightens (playing time
  only), the ≥15s / ≥30% satisfaction rule (`event_format.py:73-75`) now measures
  what it always assumed it measured.

Acceptance: tracker unit tests with the injected clock — pause gaps excluded,
multiple segments accumulate, autoplay-start (begin without `play`), clamp applies,
`page_dwell_seconds` still reports wall-clock.

### Phase 4 — Content-page view + dwell

- `PlatformAdapter` gains optional `dwellPageTypes?: string[]` — pages whose dwell is
  worth measuring: bilibili/douyin/youtube `["video"]` (unchanged semantics), xhs
  `["note"]`, zhihu `["answer", "article", "question"]`, reddit `["post"]`, x
  `["status"]`.
- Kernel generalizes `enterDwellIfVideoPage` → `enterDwellIfTrackedPage`:
  `detectPageType(url)` ∈ `dwellPageTypes` starts a dwell session. Non-video sessions
  are **visibility-gated wall-clock**: segments begin on entry/`visibilitychange:
  visible` and end on `visibilitychange: hidden` (new kernel listener; the Phase 3
  segment model is reused as-is). Video pages keep play-state gating (Phase 3) —
  the session carries a `mode: "playback" | "visible"` chosen at entry by PageType
  (`"video"` → playback, else visible).
- **Hidden-tab state machine** (review finding — races must be explicit, session
  state is `{url, mode, accumulatedMs, segmentStartedAt, enteredAt}` plus the
  document's live visibility read at decision points):
  - entry while hidden (`document.hidden`) → session created with **no open
    segment**; the first `visibilitychange: visible` begins one;
  - SPA navigation while hidden → flush the old session (closing any open segment —
    normally already closed by the hidden transition), create the new session
    segment-closed;
  - `visibilitychange` events apply **only** to a `visible`-mode session (playback
    mode ignores them entirely);
  - flush closes any open segment before reporting.
  Each of these four is a required tracker/kernel test case (hidden initial load,
  hidden navigation, visible resume, mode isolation).
- On entering a tracked non-video content page, emit a `view` event (currently these
  platforms emit zero views): url, title, `metadata.content_id` from the adapter's
  existing extractor, default view weight 0.35. Dedup: same URL within the session
  doesn't re-emit (SPA re-renders).
- Dwell flush event: existing envelope, `dwell_source: "content_page_exit"`,
  `watch_seconds` = visible seconds, no `video_duration_seconds`.
- Backend: `classify_event_satisfaction` currently keys the positive rule on the
  ≥30% *ratio* which requires duration. Add a duration-less rule for
  `dwell_source == "content_page_exit"`: visible dwell ≥ 30s → `positive` /
  `"engaged_reading"`; < 5s → existing quick-exit negative; else neutral. Constants
  module-level next to the existing `_MEANINGFUL_DWELL_*` ones.

Acceptance: adapter tests for `dwellPageTypes`; tracker tests for visible-mode
segments; kernel source-regex tests for the visibilitychange wiring; backend
classifier tests for the three bands.

### Phase 5 — Search capture completeness + weight

- **URL-derived capture** (covers Enter, button clicks, suggestion clicks — the
  result URL is the ground truth): `PlatformAdapter` gains optional
  `extractSearchQuery(url: string): string | null`. On SPA/full navigation where
  `detectPageType(nextUrl) === "search"`, kernel calls it and emits a `search` event
  with the query. Extraction MUST follow each adapter's own `detectPageType` URL
  patterns, not assumed query params (review finding):
  bilibili `keyword` param; xhs `keyword` param; **douyin: path-segment forms like
  `/search/<encoded>` and `/jingxuan/search/<encoded>` (decodeURIComponent) in
  addition to any `keyword` param**; youtube `search_query`; zhihu `q`; reddit `q`;
  x `q` — and **X `/explore` classifies as `search` with no query: return null and
  emit nothing** (null-guard test required per platform for query-less search
  URLs).
- **Dedup window**: kernel keeps `{query, ts}` of the last emitted search; identical
  normalized query within 10s (constant) is skipped — the Enter-path capture
  (`kernel.ts:107-117`, kept unchanged) typically precedes the URL navigation for
  the same query.
- **Weight**: `_DEFAULT_SIGNAL_STRENGTH_BY_EVENT_TYPE["search"]` 0.25 → 0.5
  (`event_format.py:250`), between click-grade and follow-grade — explicit intent,
  but a query is broader than an engagement with a specific item. Add the missing
  default-weight assertion to `tests/test_event_format.py` (locks the new value;
  none exists today).

### Phase 6 — account_sync favorites budget + following pagination

- Favorites: `max_folders` default 10 → 200 (parity with init) **with**
  `max_total_items=500` passed through. Note (review): the API client
  `get_all_favorites` **already supports** `max_total_items` — the change is purely
  an `AccountSyncService` field + pass-through, no `bilibili/api.py` edits. The
  budget bounds worst-case requests at `1 + ceil(500/20) ≈ 26` instead of an
  unbounded 200×3. Existing per-folder `max_items_per_folder=50` kept.
- Following: replace the single `get_following(page=1, page_size=100)` call with the
  init-style pagination loop (`cli.py:5477-5494` pattern): `page_size=100`, continue
  while the page is full, hard cap `following_max_pages=5` (→ 500 follows). Existing
  `following_mids` set-diff dedup is unchanged and makes re-reading old pages
  harmless.
- **Partial-failure semantics** (review finding): if page N of the following loop
  fails, pages 1..N-1's mids are still ingested (partial import), the exception is
  recorded via `_record_stage_error` (auth-expired precedence unchanged), and
  `last_account_sync_at` stamps as today. The `following_mids` set-diff makes the
  next sync re-cover anything missed.
- Both knobs are constructor fields with defaults (existing style); assembly sites
  don't need changes. Request growth stays within invariant 6.

### Phase 7 — Documentation sync (per CLAUDE.md)

- `docs/modules/extension.md` — retraction capture, pressed-state, playback dwell,
  content-page dwell, URL-derived search.
- `docs/modules/runtime.md` — account_sync favorites budget / following pagination.
- `docs/modules/soul.md` or `memory.md` — retraction feedback semantics (neutral,
  non-strong-signal), only if those docs enumerate feedback semantics (verify).
- `docs/changelog.md` — bullet under the current version block.
- Architecture diagrams unaffected (no new modules or cross-module edges).

## Expected impact

| Lever | Effect |
| --- | --- |
| 1+2 | Withdrawn likes stop silently persisting as positive preference; retraction is recorded for future profile discounting |
| 3 | The highest-weight interest signal (watch time) stops counting paused/idle time |
| 4 | xhs/zhihu/reddit/X go from zero view+dwell to visibility-gated reading signal — the largest coverage win available without touching unstable DOM |
| 5 | Mouse-submitted searches recovered; search weighted above passive view |
| 6 | Favorites in folders 11+ and follows 101+ finally sync (bounded at 500/500) |
