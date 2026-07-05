# Event Acquisition Fixes Spec — reliability & completeness of the raw signal layer

**Created:** 2026-07-05
**Scope:** extension service-worker event buffering, cross-source event dedup for
account_sync, account_sync → profile-pipeline path unification, sync-error surfacing
in the desktop web UI, X server-side scheduled incremental sync.
**Out of scope:** new per-platform DOM collectors (xhs/zhihu depth), like/unlike state
disambiguation, watch_seconds pause-awareness, search signal re-weighting, comment-text
capture, bilibili favorites folder-cap / following page-cap raises. These are real gaps
(see the 2026-07-05 event-acquisition audit) but each has its own quality/DOM-stability
trade-off and is deferred to separate specs.

## Goal

The event-acquisition audit found the raw data layer loses events (MV3 service-worker
kill wipes the in-memory buffer), double-counts events (extension `view` + account_sync
history `view` for the same bvid, no cross-source dedup), lets pulled data bypass all
profile stability machinery (account_sync calls `analyze_events` directly instead of the
onion-layer pipeline), fails silently (cookie-expired sync stamps success; the
`last_account_sync_error` field exists end-to-end in the API but is rendered nowhere),
and covers only bilibili with server-side incremental sync even though X already has a
working server-side cookie fetch path from init. Target outcomes:

- No systematic event loss from service-worker recycling: buffered events survive an SW
  kill and are delivered on the next wake.
- One user action → at most one profile-weighted event, regardless of how many sources
  observed it.
- account_sync increments flow through the same `ProfileUpdatePipeline` thresholds /
  diff-protection as live extension events — no privileged side door into preference.
- A broken bilibili sync (expired cookie, API error) is visible in the desktop web UI
  within one poll cycle, with a distinguishable "re-login needed" state.
- X likes/bookmarks keep flowing on the same 6h cadence as bilibili without the browser
  being open.

## Design invariants (MUST hold in every phase)

1. **Never lose a strong signal silently.** like/favorite/feedback/follow events either
   reach the backend or remain persisted client-side; only bounded, logged eviction of
   the oldest events is allowed.
2. **Dedup is source-aware filtering, not a DB uniqueness constraint.** The `events`
   table stays append-only with no schema migration. A user genuinely re-watching a
   video via the extension is two legitimate events; only the *same observation arriving
   via a second source* is a duplicate. Dedup therefore lives in account_sync (the
   second observer), filtering against what the events table already recorded.
3. **Pre-profile bootstrap behavior is preserved.** Before `is_profile_ready()`,
   account_sync keeps its current `analyze_events` + `_auto_bootstrap_soul_profile`
   flow (that is what builds the first profile on cookie-only installs). The pipeline
   path applies only after a profile exists — mirroring how `/api/events` already gates
   `_ingest_profile_update_events` (`api/app.py:1410-1416`).
4. **The 6h sync stamp semantics stay monotonic.** `last_account_sync_at` is only
   written when a sync actually ran to completion (even with partial errors); the
   existing unauthenticated early-return that skips the stamp (`account_sync.py:122-127`)
   must survive, or a cookie race re-locks sync for 6h.
5. **Extension changes must not regress flush latency for strong signals.**
   `shouldFlushImmediately` semantics (`buffer.ts:56-66`) are unchanged; persistence is
   additive, not a replacement for immediate delivery.
6. **X sync is read-only and rate-respectful**: reuse `XClient.likes/bookmarks` with the
   same limits as init (200/200), one fetch pair per 6h cycle, no new endpoints, no
   pagination loops.

## Current diagnosis

### D1. MV3 service worker buffer is memory-only

`eventBuffer` is a module-level array in the service worker
(`extension/src/background/service-worker.ts:82`), `BUFFER_MAX_SIZE = 50`,
`BUFFER_FLUSH_INTERVAL = 30_000` (`service-worker.ts:83-84`). Chrome recycles idle MV3
SWs after ~30s — the same order as the flush interval — and there is no
`chrome.storage` persistence anywhere in the buffering path. Failed flushes
`unshift` back into the same memory array (`service-worker.ts:459-482`). SW death =
buffer gone. Additionally, when the backend reports `not_initialized`, events are
consumed and dropped by design (`service-worker.ts:464-474`) — acceptable for history
(init re-fetches it) but browsing-behavior events (dwell/click/scroll) are
unrecoverable.

### D2. No cross-source dedup between extension and account_sync

`insert_event` is a plain INSERT (`storage/database.py:579`), and account_sync's
watermark (`_filter_new_history`, `runtime/account_sync.py:278-308`) only dedups
against *its own previous pulls*. A video watched with the extension active produces an
extension `view` (signal 0.35) and, within 6h, a second account_sync `view` for the
same bvid. Both rows land in `events`, both feed the profile, and both inflate the
refresh loop's pending-signal count (`refresh.py:1674-1680`). Same story for
`favorite` (signal 1.0 — double-counted at full weight) and `follow`.

### D3. account_sync bypasses the profile pipeline

`sync_now` calls `soul_engine.analyze_events(events)` directly
(`account_sync.py:199` → `engine.py:264`), which re-runs the preference analyzer and
overwrites the preference layer — skipping `ProfileUpdatePipeline`'s buffering,
per-layer thresholds (`soul/pipeline.py:304`), and ROLE/VALUES/CORE diff-protection.
Live extension events go through `pipeline.ingest_batch`
(`api/app.py:3529` → `:1402-1439`). Two paths, different power levels, for the same
kind of data.

### D4. Sync failures are invisible

All three fetch blocks in `sync_now` swallow exceptions into an `errors` list with no
log call (`account_sync.py:155-156, 174-175, 193-194`). An expired cookie passes
`is_authenticated` (cookie-presence check only, `bilibili/api.py:252-255`), then
`BilibiliAuthExpiredError` from `_get_json` (`api.py:356-363`) is swallowed too — the
sync stamps `last_account_sync_at` and looks healthy. `last_account_sync_error` is
plumbed through `get_runtime_status` (`account_sync.py:211-217`) and
`RuntimeStatusResponse` (`api/models.py:170`) but **no frontend code reads it**
(grep of `web/desktop/assets/js/app.js` finds only the interval setting).

### D5. X has server-side fetch capability but no scheduled sync

Init fetches X likes+bookmarks server-side via cookie
(`_fetch_x_init_data`, `cli.py:5501`, `XClient.likes/bookmarks`,
`sources/x_client.py:157,167`, limits 200/200). No runtime service reuses it; after
init, X data flows only while the extension is open. `XClient` has no cursor/since
parameters — incremental dedup must be set-based (seen tweet IDs), like the existing
favorites/following signature approach in account_sync.

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 1 | SW buffer persistence (chrome.storage.local write-through + restore + not_initialized parking) | **MUST** | Stops systematic cross-platform event loss; everything downstream starves without it |
| 2 | Cross-source dedup in account_sync (events-table lookback filter) | **MUST** | Stops double-weighting of the highest-signal events (favorite 1.0) and pending-signal inflation |
| 3 | account_sync → `pipeline.ingest_batch` when profile ready | **MUST** | Closes the side door around all profile stability machinery |
| 4 | Sync error surfacing: logging + error kind + desktop web status chip | **MUST** | Converts "silently stale profile" into a user-visible, actionable state |
| 5 | X scheduled incremental sync inside AccountSyncService | RECOMMENDED | Cheapest coverage win — server-side fetch code already exists, only scheduling + set-dedup is new |
| 6 | Docs sync (extension.md, runtime.md, storage.md, changelog) | **MUST** (per CLAUDE.md) | Interfaces and data flow change |

Dependencies: Phase 2 must land **before or together with** Phase 5 (X events must be
born deduped — the same lookback helper applies). Phase 3 is independent of 1–2.
Phase 4 is independent of all. Phases 1 (TypeScript) and 2–5 (Python) touch disjoint
codebases and can be implemented in parallel.

## Phase designs

### Phase 1 — Service-worker buffer persistence

**Storage choice: `chrome.storage.local`** (not `.session`): survives SW kill *and*
browser restart; 10MB default quota dwarfs our worst case (~50 events × ~1KB). Keys:
`obc_event_buffer` (main buffer mirror), `obc_parked_events` (not_initialized parking).

MV3 lifecycle constraint (from review): the SW is bundled as classic (no
`"type": "module"` in `manifest.json:28-30`), so **no top-level await** — restore runs
behind an async init gate. Debounced writes are rejected outright: a `setTimeout`
pending when the SW dies loses its write, which is exactly the failure mode being fixed.

Mechanics (all in `background/buffer.ts` + `service-worker.ts`):

1. **Awaited write-through mirror**: every buffer mutation (enqueue, successful-flush
   trim, re-buffer) performs an **awaited**
   `chrome.storage.local.set({obc_event_buffer: eventBuffer})` before the mutation is
   considered complete — in particular, a strong-signal enqueue awaits the mirror
   *before* the network flush starts, so a like is on disk even if the SW dies
   mid-flush. No debouncing. Event rates make this cheap: scroll/hover are already
   throttled (600/800ms) and the payload is ≤50 small events. Failed `storage.set` is
   logged and ignored (memory buffer remains authoritative).
2. **Restore behind an init gate**: a module-level `bufferReady: Promise<void>` kicks
   off at first import — reads `obc_event_buffer`, prepends persisted events, clears
   the key. Every entry point that touches the buffer (message handlers, alarm flush)
   awaits `bufferReady` first, so an event arriving before restore completes cannot be
   overwritten by the restore.
3. **Successful flush clears the mirror** for the delivered slice (rewrite the mirror
   from the post-flush buffer state — same debounced writer).
4. **not_initialized parking**: instead of dropping, move the batch to
   `obc_parked_events` (cap 500 events, FIFO eviction, entries older than 48h dropped
   on read — both constants module-level). When a later flush succeeds (backend
   initialized), drain the parking lot into the front of the buffer in original order,
   then delete the key. History-shaped duplication is harmless: init's own backfill and
   Phase 2's dedup absorb overlaps; behavior events (dwell/click) are exactly what we
   are saving.
5. **Eviction stays bounded**: combined in-memory + mirrored buffer still respects
   `BUFFER_MAX_SIZE` (oldest dropped, as today, `buffer.ts:50-52`) — persistence must
   not turn the buffer into an unbounded queue when the backend is down for days.

Acceptance: unit tests (node --test, matching `extension/npm run test` conventions) with
a stubbed `chrome.storage.local`: enqueue → mirror written; simulated SW restart
(re-import module with storage populated) → events restored and flushed; not_initialized
response → events parked, later success → parked events delivered oldest-first; cap and
TTL enforced.

### Phase 2 — Cross-source dedup in account_sync

New DB helper `Database.recent_event_urls(event_types: list[str], *, within_hours: int,
exclude_source: str | None = None, limit: int = 2000) -> set[str]` — thin wrapper over
the existing `query_events` (`database.py:995`) returning the non-empty `url` values
(events store full URLs; bvid extraction happens caller-side to keep the helper
generic). `exclude_source` drops rows whose `metadata.source` equals the given value
(JSON parsed per row in Python — limit 2000 keeps that cheap). account_sync always
passes `exclude_source="account_sync"`: **its own prior rows must never suppress** —
otherwise a re-watch observed only via the history API (TV/app playback, no extension)
within the window would be wrongly deduped against the previous sync's own event.

In `AccountSyncService.sync_now`, after the existing watermark filtering and before
event construction, drop items whose identity key already appears in the events table:

- **history** (`view`): key = bvid extracted from the history item; compare against
  bvids parsed from `recent_event_urls(["view"], within_hours=48)`. 48h > 2× the sync
  interval (6h wall + one full missed cycle) while comfortably shorter than typical
  organic re-watch gaps; constant `_CROSS_SOURCE_DEDUP_WINDOW_HOURS = 48`, module-level.
- **favorites** (`favorite`): key = bvid, against `recent_event_urls(["favorite"], within_hours=48)`.
  (The existing `favorite_bvids` state-set continues to handle self-dedup across syncs;
  this adds the extension-observed case.)
- **following** (`follow`): key = UP mid (from the follow event URL, `space.bilibili.com/<mid>`),
  against `recent_event_urls(["follow"], within_hours=48)`.

Dropped counts are logged at INFO (`account_sync: deduped N history / M favorite / K follow
events already observed by extension`). Re-watch semantics are explicitly preserved: a
bvid re-watched *after* the window still produces a new `view` (that is a genuine signal).

Acceptance: unit tests with an in-memory DB — an extension-inserted `view` for bvid X
suppresses the account_sync history event for X inside the window; outside the window it
does not; a favorite observed only by account_sync still flows.

### Phase 3 — Unify account_sync into the profile pipeline

In `sync_now` (`account_sync.py:196-200`), replace the unconditional
`await self.soul_engine.analyze_events(events)` with an explicit fallback matrix
(default is always the current behavior — degrade to legacy, never to nothing):

| Condition | Action |
| --- | --- |
| `is_profile_ready` callable, returns True, pipeline present, `ingest_batch` succeeds | pipeline only (no `analyze_events`, no bootstrap) |
| ready True, but pipeline missing / `ingest_batch` absent / raises | WARN log + fall back to `analyze_events` (no bootstrap — profile exists) |
| ready False | legacy: `analyze_events` + `_auto_bootstrap_soul_profile` |
| `is_profile_ready` missing or raises | treated as **not ready** → legacy path (conservative) |

`propagate_event` persistence stays unconditional and first, exactly as today. The
pipeline handle is `soul_engine.pipeline` with the same defensive getattr/fallback
pattern as `_ingest_profile_update_events` (`api/app.py:1418-1436`) — account_sync
must not hard-depend on pipeline internals (tests stub SoulEngine with a Protocol,
`account_sync.py:55`; extend the protocol accordingly).

Effect: pulled `view` events (0.35) buffer into SURFACE/INTEREST like any other weak
signal; pulled `favorite` (1.0) rides the ENGAGEMENT_EVENT path
(`pipeline.py:100,386-391`). No more whole-preference-layer rewrites every 6h.

Acceptance: unit tests — with a ready profile, `analyze_events` is **not** called and
`ingest_batch` receives one signal per event; with no profile, legacy path + bootstrap
still fire. Existing `tests/test_account_sync.py` bootstrap tests keep passing.

### Phase 4 — Make sync failures visible

Backend (`runtime/account_sync.py`):

1. Each swallowed exception gains a `logger.warning("account sync: <stage> failed: %s", exc)`.
2. Classify the error kind into state: new key `last_sync_error_kind` with values
   `"" | "auth_expired" | "error"`. `BilibiliAuthExpiredError` (import from
   `bilibili/api.py`) → `auth_expired`; anything else → `error`; cleared to `""` on a
   fully clean sync. Expose it via `get_runtime_status()` and a new optional field
   `last_account_sync_error_kind: str = ""` on `RuntimeStatusResponse`
   (`api/models.py:169-170`) — additive, backward compatible.

Frontend (`src/openbiliclaw/web/desktop/assets/js/app.js` + `assets/css/app.css`): in
whichever existing panel
renders `/api/runtime-status` data (locate by grepping the fetch of `runtime-status`;
if no panel consumes it yet, attach to the settings/status area that already hosts
`accountSyncInterval`, `app.js:4085`), render a status line when
`last_account_sync_error` is non-empty:

- `auth_expired` → prominent chip: “B 站登录已失效，账号同步已停止 — 请重新登录” (CN
  primary, matching surrounding copy conventions).
- other errors → muted chip with the error text and `last_account_sync_at`.
- empty error → no new UI (zero visual change for healthy installs).

Note the serve-api stale-routes gotcha: JS is served live but routes are fixed at
process start — the new `last_sync_error_kind` field requires a backend restart to
appear; call this out in the changelog entry.

Acceptance: backend unit tests for kind classification + state clearing; a
`tests/test_desktop_web_*`-style test asserting the JS references the new field (this
suite already tests JS by string/DOM assertions — follow the existing pattern in
`tests/test_desktop_web_pool_status.py`).

### Phase 5 — X scheduled incremental sync

Extend `AccountSyncService` with an optional `x_client` (duck-typed: `.likes(limit=)`,
`.bookmarks(limit=)`), wired in both assembly sites (`api/runtime_context.py:825-831`,
`integrations/openclaw/bootstrap.py:228-233`). Cookie resolution: use the existing
`openbiliclaw.sources.x_auth.resolve_x_cookie` (`x_auth.py:54`) — do **not** add a
duplicate helper. When no X cookie resolves, behavior is byte-identical to today.

Inside `sync_now`, after the bilibili blocks and behind the same 6h `_is_due` gate:

1. Fetch likes (limit 200) and bookmarks (limit 200); each in its own try/except
   feeding the same `errors` list + WARN logging (Phase 4 pattern). X failures never
   block bilibili sync (they run after) and vice versa.
2. Dedup: state sets `x_like_ids` / `x_bookmark_ids` (**normalized tweet IDs**, never
   URLs) mirroring the `following_mids` approach (`account_sync.py:463-471`). **First
   sync with empty state sets is NOT a silent full seed** — init persists X events to
   the `events` table but does not write these state sets, so a naive seed would
   swallow anything liked between init and the first cycle. Instead: on empty state,
   seed from tweet IDs extracted from already-persisted X `like`/`favorite` event URLs
   in the events table, then emit only fetched IDs absent from that seeded set. Cap
   each set at 2000 IDs (keep newest) to bound state growth.
3. Events: `build_event` with `event_type="like"` (likes) / `"favorite"` (bookmarks),
   `source_platform=SOURCE_TWITTER` (the canonical `"twitter"`,
   `event_format.py:207` — **not** `"x"`, which would split source-mix accounting),
   default signal strengths (0.85 / 1.0). Cross-source dedup from Phase 2 applies, but
   X keys on **tweet ID**, not raw URL: the extension GraphQL tap emits
   `https://x.com/i/status/<id>` while server-side events use
   `https://x.com/<handle>/status/<id>` (`cli.py:856-882`) — extract the trailing
   `/status/<id>` segment for comparison.
4. Events flow through the same Phase 3 pipeline path.

State keys live in the same `account_sync_state.json`. `get_runtime_status` gains
nothing new (errors fold into the existing fields).

Acceptance: unit tests — first sync seeds silently; second sync with one new like emits
exactly one event with `source_platform="twitter"`; X fetch failure records error + WARN but
bilibili events still flow; no cookie → x code path never invoked.

## Expected impact

| Lever | Effect |
| --- | --- |
| Phase 1 | Eliminates the largest systematic event-loss mode (SW recycling ≈ every idle 30s); behavior events survive backend-down and pre-init windows |
| Phase 2 | favorite/view/follow no longer double-weighted; pending-signal counts stop inflating discovery triggers |
| Phase 3 | Pulled data obeys the same onion-layer thresholds/diff-protection as live data; preference layer stops being rewritten wholesale every 6h |
| Phase 4 | Cookie expiry becomes a visible, actionable state instead of a silently stale profile |
| Phase 5 | X keeps updating without an open browser — second platform with server-side continuity |

## Documentation obligations (per CLAUDE.md)

- `docs/modules/extension.md` — buffer persistence + parking behavior
- `docs/modules/runtime.md` — account_sync dedup window, pipeline path, error kinds, X sync
- `docs/modules/storage.md` — `recent_event_urls` helper
- `docs/modules/api-auth.md` or `docs/modules/runtime.md` — `last_account_sync_error_kind` field
- `docs/changelog.md` — bullet under the current version block
- Architecture diagrams: the X server-side sync adds a data-flow edge (X ⇄ backend
  scheduled sync) — update `docs/architecture.md` + `docs/spec.md` §3 + README diagrams
  only if they currently show account_sync as bilibili-only (verify before editing).
