# Event Capture Depth — Implementation Plan

> **Spec:** [`2026-07-05-event-capture-depth-spec.md`](./2026-07-05-event-capture-depth-spec.md)
> **Status:** Final r2 — 2026-07-05. r1 reviewed adversarially by Codex (verdict
> REVISE, 9 findings); r2 addresses all: concrete end-to-end X retraction shape,
> retraction excluded from feedback batch learning + explicit 0.2 default strength,
> X tap-authoritative suppress-only pressed handling, douyin path-segment /
> X-`/explore` null-guard search extraction, late-video attach retry,
> hidden-tab dwell state machine, `max_total_items` is pass-through-only,
> following partial-failure semantics. Implement task-by-task, TDD style.
> **Execution order:** Extension chain Task 1 → 2 → 3 → 4 → 5 (each builds on the
> previous; 5 only needs 4's nav hook refactor) ∥ Backend Task 6 (independent).
> Backend halves of Tasks 1/4/5 (classifier + weight) are inside those tasks and
> touch only `sources/event_format.py` + `soul/pipeline.py` + tests — disjoint from
> Task 6's `account_sync.py`, so the two agents never collide on files.
> Task 7 (docs) last.
> **Tech (Python):** `.venv/bin/python` interpreter (plain python has no deps);
> pytest -q on touched files, ruff check + format --check, mypy strict per task.
> If running from the worktree, `PYTHONPATH=src` (editable install points at the
> main checkout).
> **Tech (extension):** `cd extension && npm run typecheck && npm run test`
> (node --test; keep the pure-logic-in-adapters / source-regex-for-kernel split —
> no jsdom).

**Invariants (from Spec — re-read before each task):**
- Fail open: absent/ambiguous state → today's behavior; never suppress on uncertainty.
- Retraction is `neutral`, never `negative`, never a strong-signal bypass event.
- X GraphQL tap stays observe-only.
- Dwell events keep the `type:"click"` + `watch_seconds`/`dwell_source` envelope.
- bilibili sync request growth bounded by constants (`max_total_items`, page cap).
- Search dedup: normalized-identical query within 10s emits once.

---

### Task 1: X un-action capture (extension + backend retraction event)

**Files:** `extension/src/main/x-graphql-tap.ts`, `extension/src/content/x.ts`,
`extension/tests/x-graphql-tap.test.ts`;
`src/openbiliclaw/sources/event_format.py`, `src/openbiliclaw/soul/pipeline.py`,
`tests/test_event_format.py`, `tests/test_soul_pipeline.py` (verify name:
`ls tests/ | grep -i pipeline`)

**Steps:**
1. Extension failing tests: `classifyXResponseUrl` maps `UnfavoriteTweet` /
   `DeleteBookmark` / `DeleteRetweet` URLs to a retraction classification with
   `retracted_action` = `like` / `favorite` / `share`; `DeleteTweet` still → null;
   existing 6 mappings unchanged.
2. Extend all four extension touch points together: `XEventType` (add
   `"retraction"`), `GRAPHQL_OP_EVENTS` (three Un*/Delete* ops with
   `retracted_action`), `isXEngagement`/`known` whitelist (`x.ts:80`), and the
   `x.ts` normalization → `event_type="feedback"`,
   `metadata.feedback_type="retraction"`, `metadata.retracted_action=<...>`,
   `metadata.signal_strength=0.2`. Consumer test: normalized shape reaches the
   event sender.
3. Backend failing tests:
   - `classify_event_satisfaction` on feedback with `feedback_type="retraction"` →
     `("neutral", "retraction")`, checked **before** any feedback-negative rule
     (add a dislike-feedback regression case).
   - `default_signal_strength_for_event("feedback", {"feedback_type": "retraction"})`
     → 0.2 (new branch; plain feedback default unchanged).
   - `signals_from_events`: retraction feedback maps to a plain BEHAVIOR_EVENT,
     NOT the FEEDBACK strong-signal type (`soul/pipeline.py:100,386-391`, branch on
     `feedback_type`).
   - **`process_feedback_batch_if_needed` excludes retractions** (`soul/engine.py:954-1012`):
     3 retraction rows alone do NOT reach `feedback_batch_threshold`; a mix of
     2 dislikes + 2 retractions counts 2 and the analysis input contains no
     retraction rows.
4. Implement the backend rules; verify `/api/feedback`-path consumers
   (`record_immediate_feedback_cognition`, exploration buffer) are endpoint-hooked
   and unaffected — note the finding in the test file if so. Run targeted tests +
   ruff + mypy.
5. `cd extension && npm run typecheck && npm run test`.

### Task 2: Pressed-state plumbing (`ActionHint.pressed`)

**Files:** `extension/src/shared/behavior.ts`, `extension/src/shared/types.ts`,
`extension/src/content/kernel.ts`;
Tests: `extension/tests/behavior.test.ts` (verify name), `extension/tests/kernel.test.ts`

**Steps:**
1. Failing tests: `buildActionHintFromClickTarget` returns `pressed: true` for an
   attributed element with `aria-pressed="true"`, `false` for `"false"`, `null` when
   absent or any other value. (These tests may construct minimal element stubs the
   way existing behavior tests do — check the current stubbing approach first.)
2. Extend `ActionHint` in `types.ts` (`pressed: boolean | null`), populate in
   `behavior.ts:45-56` from the **attributed** element only.
3. Kernel click path: when the inferred action ∈ {`like`, `favorite`, `follow`} and
   `hint.pressed === true`:
   - platform `strongSignalSource === "tap"` (new optional adapter field; twitter
     sets it) → **suppress only** (no event — the GraphQL tap emits the
     authoritative retraction; prevents double-emit);
   - otherwise → emit the Task 1 retraction feedback event (`retracted_action` =
     the inferred action) instead of the positive event.
   `false`/`null` → unchanged. Adapter test for the twitter field; kernel
   source-regex test asserting both the pressed check and the tap-suppress branch
   exist in the click path (existing `kernel.test.ts` style).
4. typecheck + test.

### Task 3: Playback-accurate watch_seconds (segmented tracker)

**Files:** `extension/src/content/video-dwell-tracker.ts`,
`extension/src/content/kernel.ts`;
Tests: `extension/tests/video-dwell-tracker.test.ts`, `extension/tests/kernel.test.ts`

**Steps:**
1. Failing tracker tests (injected clock, existing style):
   - enter → beginSegment → advance 10s → endSegment → advance 20s (paused) →
     flush reports `watch_seconds=10`, `page_dwell_seconds=30`.
   - multiple begin/end cycles accumulate; double-begin / double-end are idempotent.
   - flush with an open segment includes it.
   - autoplay: enter + beginSegment without any prior end works from t=0.
   - clamp: with `video_duration_seconds=100`, reported watch_seconds ≤ 150; with
     unknown duration, ≤ 600 only when the raw value exceeds it (constant
     `_WATCH_SECONDS_FALLBACK_CAP = 600`; keep raw below that untouched).
2. Rework `DwellSession` to `accumulatedMs` + `segmentStartedAt` (+ keep
   `enteredAt` for `page_dwell_seconds`); add `beginSegment()` / `endSegment()`;
   `flush` emits both fields.
3. Kernel: `play` → beginSegment, `pause`/`ended` → endSegment (add the `ended`
   listener in `attachVideoListeners`); at bind time begin a segment if
   `!video.paused && !video.ended`. Source-regex tests for the three wirings.
4. **Late-rendered video retry**: after `rebindPageObservers` (and initial load),
   if `detectPageType === "video"` and no `<video>` matched, retry
   `attachVideoListeners` every `_VIDEO_ATTACH_RETRY_MS = 500` up to
   `_VIDEO_ATTACH_MAX_RETRIES = 20`, cancelling the timer on the next navigation.
   Source-regex tests for the retry loop + cancellation.
5. typecheck + test.

### Task 4: Content-page view + dwell (xhs/zhihu/reddit/X)

**Files:** `extension/src/shared/types.ts`, `extension/src/shared/platforms/*.ts`,
`extension/src/content/kernel.ts`, `extension/src/content/video-dwell-tracker.ts`;
`src/openbiliclaw/sources/event_format.py`;
Tests: adapter tests per platform, tracker + kernel tests,
`tests/test_event_format.py`

**Steps:**
1. Failing adapter tests: `dwellPageTypes` — bilibili/douyin/youtube `["video"]`,
   xiaohongshu `["note"]`, zhihu `["answer","article","question"]`, reddit
   `["post"]`, twitter `["status"]`.
2. Tracker: session gains `mode: "playback" | "visible"` (chosen at `enter`);
   in `visible` mode segments are driven by `beginSegment`/`endSegment` exactly the
   same way (the mode field only documents intent + selects `dwell_source`:
   `video_page_exit` vs `content_page_exit`, and suppresses
   `video_duration_seconds`). Failing tests for mode selection + flush metadata,
   **plus the four hidden-tab state-machine cases from the spec**:
   - entry while hidden → no open segment until the visible transition;
   - navigation while hidden → old session flushed with segments closed, new
     session starts segment-closed;
   - visible resume → segment reopens and accumulates;
   - mode isolation → visibility transitions never touch a `playback` session.
3. Kernel:
   - `enterDwellIfVideoPage` → `enterDwellIfTrackedPage` using
     `adapter.dwellPageTypes ?? ["video"]`; mode = `"playback"` iff PageType is
     `"video"`.
   - New `visibilitychange` listener: `hidden` → endSegment, `visible` →
     beginSegment — **only when the active session is `visible` mode** (playback
     mode is gated by play state alone, per spec).
   - `visible`-mode entry begins a segment immediately **only when
     `!document.hidden`** (hidden entry stays segment-closed per the state
     machine).
   - On entering a tracked non-video page, emit a `view` event with
     `metadata.content_id` from the adapter's existing extractor
     (`extractXxxContentId` / note-id / post-id — reuse `buildEventMetadata` where
     it already injects the id); dedup same-URL re-entry within the session.
   - Source-regex tests: visibilitychange wiring, generalized entry function.
4. Backend failing tests: `classify_event_satisfaction` for
   `dwell_source == "content_page_exit"` (no duration): 30s+ →
   `("positive", "engaged_reading")`; <5s → existing quick-exit negative; between →
   neutral. Constants `_CONTENT_DWELL_POSITIVE_MIN_SECONDS = 30` beside the
   existing `_MEANINGFUL_DWELL_*` block. Existing video-dwell classification
   unchanged (regression cases).
5. typecheck + test (extension); pytest + ruff + mypy (backend).

### Task 5: URL-derived search + weight correction

**Files:** `extension/src/shared/types.ts`, `extension/src/shared/platforms/*.ts`,
`extension/src/content/kernel.ts`;
`src/openbiliclaw/sources/event_format.py`;
Tests: adapter tests, kernel test, `tests/test_event_format.py`

**Steps:**
1. Failing adapter tests: `extractSearchQuery(url)` per platform, driven by each
   adapter's OWN `detectPageType` search URL patterns (read them first):
   bilibili `search.bilibili.com/...?keyword=`; xhs `/search_result...?keyword=`;
   **douyin path-segment forms `/search/<encoded>` and
   `/jingxuan/search/<encoded>` (decodeURIComponent) plus `keyword` param if the
   adapter classifies it**; youtube `?search_query=`; zhihu `?q=`; reddit
   `/search?q=`; x `/search?q=`. URL-decodes; returns null on missing/empty —
   **explicit null-guard test for query-less search pages (X `/explore`, douyin
   `/search/` bare) emitting nothing**.
2. Kernel: in the navigation callback (both history-patch and popstate paths, and
   initial load), when `detectPageType(nextUrl) === "search"` and
   `adapter.extractSearchQuery` yields a query, emit `createEvent("search",
   {query})` — behind a dedup guard: normalized (trim/lower) query identical to the
   last emitted search within `_SEARCH_DEDUP_WINDOW_MS = 10_000` → skip. The
   Enter-path (`observeSearch`) also records into the same guard. Source-regex test
   for the nav-hook emission + a pure-function test if the dedup guard is extracted
   (extract it — testable without DOM).
3. Backend: `"search"` 0.25 → 0.5 in `_DEFAULT_SIGNAL_STRENGTH_BY_EVENT_TYPE`
   (`event_format.py:250`); add the missing default-weight assertions (search=0.5,
   plus lock a couple of neighbors) to `tests/test_event_format.py`.
4. typecheck + test; pytest + ruff + mypy.

### Task 6: account_sync favorites budget + following pagination (backend, parallel-safe)

**Files:** `src/openbiliclaw/runtime/account_sync.py`;
Test `tests/test_account_sync.py`

**Steps:**
1. Failing tests (stub bilibili client records call args):
   - `sync_now` calls `get_all_favorites(max_folders=200, max_items_per_folder=50,
     max_total_items=500)`.
   - following: client stub returns a full page (100) twice then a short page →
     three `get_following` calls with page=1,2,3, all mids ingested; a full page ×5
     → stops at `following_max_pages=5`; first page short → one call (existing
     behavior preserved).
   - existing `following_mids` diff dedup still applies across the paginated union.
2. Implement: field defaults `max_folders=200`, `max_total_items=500` (service
   field + pass-through ONLY — `get_all_favorites` already accepts the kwarg, no
   `bilibili/api.py` edits), `following_max_pages=5`; pagination loop mirroring
   `cli.py:5477-5494` (stop on short page or page cap).
   **Partial-failure test**: page-2 fetch raising → page-1 mids still ingested,
   `_record_stage_error` records the error (auth-expired precedence regression
   case), `last_account_sync_at` still stamped.
3. Targeted tests + ruff + mypy. (This file was just modified by the previous
   batch — rebase mentally on its current state, e.g. `_record_stage_error`,
   `_dedup_cross_source` already exist.)

### Task 7: Documentation sync

**Files:** `docs/modules/extension.md`, `docs/modules/runtime.md`,
`docs/changelog.md`; conditionally `docs/modules/soul.md` / `memory.md`

**Steps:**
1. extension.md: retraction capture (X ops + pressed-state), playback-gated
   watch_seconds (+ `page_dwell_seconds`), content-page view/dwell table
   (platform → tracked PageTypes), URL-derived search capture.
2. runtime.md: account_sync favorites budget (200 folders / 500 items) + following
   pagination (≤5 pages).
3. soul.md / memory.md: only if they enumerate feedback-event semantics — add the
   retraction rule (neutral, non-strong-signal). Verify with grep first.
4. changelog.md: bullet under the current version block (note the search-weight
   change 0.25→0.5 as a behavior change).
5. Full suites once: backend pytest -q + ruff + mypy; extension typecheck + test +
   build.

---

## Verification after merge

1. X: unlike a tweet → `/api/events` receives feedback/retraction (grep daemon log
   or query events table).
2. bilibili: pause a video for 2 minutes mid-watch → the flushed `watch_seconds`
   excludes the pause; `page_dwell_seconds` includes it.
3. xhs: read a note ~1 min, switch tab, come back, leave → `view` + dwell events
   with visible-time-only `watch_seconds`.
4. Search via mouse click on the search button (bilibili) → search event appears
   exactly once.
5. account_sync cycle: a favorite in folder #15 and a follow at position 150 both
   produce events.

## Explicitly out of scope

- xhs DOM like/collect/comment capture; comment text; 弹幕.
- Profile-side discounting of retracted likes (recorded only).
- Any change to `_EVENT_TYPES` whitelist or the events table schema.
