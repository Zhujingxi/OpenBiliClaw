# Event Acquisition Fixes — Implementation Plan

> **Spec:** [`2026-07-05-event-acquisition-fixes-spec.md`](./2026-07-05-event-acquisition-fixes-spec.md)
> **Status:** Final r2 — 2026-07-05. r1 reviewed adversarially by Codex (verdict
> REVISE, 8 findings); r2 addresses all of them: awaited (non-debounced) storage
> writes + init gate for the classic-SW bundle, source-aware dedup
> (`exclude_source="account_sync"`), explicit Task 3 fallback matrix,
> tweet-ID-normalized X dedup, `SOURCE_TWITTER` platform value, first-sync seeding
> from persisted events, `x_auth.resolve_x_cookie` reuse, corrected frontend paths.
> Implement task-by-task, TDD style; do not start a
> task before the previous one's tests are green (except Task 1, which is TypeScript and
> parallel-safe against Tasks 2–5).
> **Execution order:** Task 1 (extension, independent) ∥ Task 2 → Task 5 (5 depends on
> 2's dedup helper); Task 3 and Task 4 independent, any order. Task 6 (docs) last.
> **Tech (Python):** 3.11+, pytest (`asyncio_mode=auto`), Ruff, MyPy strict, 100-char
> lines. Interpreter is `.venv/bin/python` (plain `python`/`python3` has no deps).
> Run per task: `.venv/bin/python -m pytest <touched test files> -q`, then
> `.venv/bin/python -m ruff check` / `ruff format --check` on touched files, then
> `.venv/bin/python -m mypy src/openbiliclaw/`.
> **Tech (extension):** `cd extension && npm run typecheck && npm run test`
> (node --test).

**Invariants that MUST hold (from Spec — re-read before each task):**
- Strong signals are never silently dropped; only bounded, logged eviction.
- No `events`-table schema migration; dedup is account_sync-side filtering.
- Pre-profile bootstrap (`analyze_events` + `_auto_bootstrap_soul_profile`) unchanged.
- Unauthenticated early-return keeps skipping the `last_account_sync_at` stamp.
- `shouldFlushImmediately` semantics unchanged; `BUFFER_MAX_SIZE` still bounds memory.
- X sync: read-only, 200/200 limits, tweet-ID set dedup, first-sync seeding **from
  persisted events** (a naive silent full seed swallows post-init likes),
  `source_platform="twitter"`.

---

### Task 1: Service-worker buffer persistence (extension)

**Files:** Modify `extension/src/background/buffer.ts`,
`extension/src/background/service-worker.ts`;
Test: extension test suite (find the existing buffer/service-worker tests via
`ls extension/tests/` or `grep -rl "buffer" extension/ --include="*.test.*"`; follow
their chrome-API stubbing pattern).

**Steps:**
1. Failing tests with a stubbed `chrome.storage.local` (in-memory Map with async
   get/set/remove):
   - enqueue **awaits** the mirror write under `obc_event_buffer` (assert the set
     resolved before enqueue's promise does); a strong-signal enqueue's mirror write
     completes **before** any network stub call starts.
   - simulated SW restart: pre-populate storage, re-init the module → restored events
     are at the front of the buffer, storage key cleared.
   - **race gate:** an enqueue issued before the restore promise resolves is not lost
     and not overwritten by the restore (both events present afterwards).
   - successful flush → mirror rewritten (awaited) from post-flush buffer state.
   - `not_initialized` flush response → batch moves to `obc_parked_events`;
     cap 500 (FIFO eviction) and 48h TTL enforced on read; next successful flush
     drains parked events oldest-first into the buffer and deletes the key.
   - backend down for many cycles → combined buffer never exceeds `BUFFER_MAX_SIZE`;
     evictions logged.
   - `storage.set` rejection → logged, memory buffer unaffected.
2. Implement in `buffer.ts`: `persistBuffer(): Promise<void>` (awaited write-through —
   **no debouncing**: a pending `setTimeout` dies with the SW, which is the exact
   failure mode being fixed), `restoreBuffer()` exposed as a module-level
   `bufferReady: Promise<void>`, `parkEvents(events)` / `drainParkedEvents()` with
   module constants `PARKED_MAX = 500`, `PARKED_TTL_MS = 48 * 3600_000`. Keep the
   module's existing export style.
3. Wire into `service-worker.ts`: the SW bundle is **classic (iife), no top-level
   await** (`manifest.json:28-30` has no `"type": "module"`) — every entry point that
   touches the buffer (runtime message handlers, alarm flush) starts with
   `await bufferReady`. Replace the `not_initialized` drop
   (`service-worker.ts:464-474`) with `parkEvents`; after each successful flush, call
   `drainParkedEvents()` then await `persistBuffer()`.
4. `cd extension && npm run typecheck && npm run test && npm run build`.

**Note:** do not touch `shouldFlushImmediately` or `BUFFER_FLUSH_INTERVAL`; do not
change the manifest/bundle format (init-gate approach avoids the need).

### Task 2: Cross-source dedup in account_sync

**Files:** Modify `src/openbiliclaw/storage/database.py`,
`src/openbiliclaw/runtime/account_sync.py`;
Test `tests/test_database.py` (verify name via `ls tests/ | grep -i database`),
`tests/test_account_sync.py`

**Steps:**
1. Failing tests:
   - `Database.recent_event_urls(["view"], within_hours=48)` returns the URL set of
     view events newer than the window and excludes older ones and other types;
     empty-url rows excluded; respects `limit`.
   - `exclude_source="account_sync"` drops rows whose `metadata.source ==
     "account_sync"` and keeps extension rows (metadata without that source).
   - account_sync: insert an extension-style `view` event for bvid X into the DB →
     `sync_now` with a history payload containing X and Y emits only Y's event; the
     watermark still advances over X (dedup must not stall the cursor).
   - **self-suppression guard:** a prior account_sync-emitted `view` for bvid Z inside
     the window does NOT suppress a new history item for Z (re-watch seen only by the
     history API must still flow — the filter passes `exclude_source="account_sync"`).
   - same for `favorite` (bvid key) and `follow` (mid key from
     `space.bilibili.com/<mid>` URLs).
   - an event older than 48h does **not** suppress (re-watch preserved).
   - dedup counts logged at INFO (caplog assertion).
2. Add `recent_event_urls(event_types, *, within_hours, exclude_source=None, limit=2000)`
   to `Database` as a thin wrapper over `query_events` (`database.py:995`) — pass
   `event_types`, `start_time=now-within_hours`, `limit`; parse each row's `metadata`
   JSON and skip rows where `metadata.get("source") == exclude_source`; collect
   non-empty `url` values into a set.
3. In `account_sync.py`: module constant `_CROSS_SOURCE_DEDUP_WINDOW_HOURS = 48`;
   helper `_bvid_from_url` / `_mid_from_url` (or reuse existing extractors if present —
   grep first). Apply the filter in `sync_now` after `_filter_new_history` /
   `_filter_favorite_folders` / following-diff, before `_history_events` etc.
   The DB handle: account_sync currently reaches the DB only via `memory_manager` —
   check what `propagate_event` uses; inject the `Database` (or a
   `recent_event_urls` callable) through the constructor with a `None`-safe default so
   existing tests/stubs keep working, and wire it at both assembly sites
   (`api/runtime_context.py:825-831`, `integrations/openclaw/bootstrap.py:228-233`).
4. Run targeted tests + lint + mypy.

### Task 3: account_sync → profile pipeline (when ready)

**Files:** Modify `src/openbiliclaw/runtime/account_sync.py`;
Test `tests/test_account_sync.py`

**Steps:**
1. Failing tests (stub soul_engine) — cover the spec's full fallback matrix:
   - `is_profile_ready() → True` and a `pipeline.ingest_batch` present: `sync_now`
     calls `ingest_batch` with one signal per event and does **not** call
     `analyze_events`; `_auto_bootstrap_soul_profile` not triggered.
   - ready True, pipeline attribute missing → WARN + `analyze_events` called
     (fallback), no bootstrap.
   - ready True, `ingest_batch` raising → WARN/exception log + `analyze_events`
     called (fallback), no bootstrap, sync does not crash.
   - `is_profile_ready() → False`: legacy path — `analyze_events` called, bootstrap
     attempted (existing tests likely cover; extend, don't duplicate).
   - `is_profile_ready` attribute missing, and separately raising → treated as not
     ready → legacy path (conservative).
   - in every branch, `propagate_event` persistence already happened (events never
     lost to a profile-path failure).
2. Implement: after the `propagate_event` loop (`account_sync.py:196-200`), branch per
   the spec matrix (ready-detection pattern already at `:224-232`). Ready →
   `from openbiliclaw.soul.pipeline import signals_from_events` (local import, matching
   app.py's style) + `await pipeline.ingest_batch(signals)` via defensive getattr;
   on any pipeline failure fall back to `analyze_events`. Not ready / unknown →
   current `analyze_events` + `_auto_bootstrap_soul_profile` unchanged.
3. Extend the `SoulEngineProtocol` stub (`account_sync.py:55`) minimally (add optional
   `pipeline` / `is_profile_ready` members) so mypy strict passes.
4. Run targeted tests + lint + mypy.

### Task 4: Surface sync failures

**Files:** Modify `src/openbiliclaw/runtime/account_sync.py`,
`src/openbiliclaw/api/models.py`, `src/openbiliclaw/api/app.py` (only if the
runtime-status payload assembly needs the new key),
`src/openbiliclaw/web/desktop/assets/js/app.js`,
`src/openbiliclaw/web/desktop/assets/css/app.css`;
Test `tests/test_account_sync.py`, `tests/test_api_app.py`, plus a new
`tests/test_desktop_web_sync_status.py` following `tests/test_desktop_web_pool_status.py`

**Steps:**
1. Failing tests:
   - each fetch-stage exception in `sync_now` produces a `logger.warning` (caplog).
   - `BilibiliAuthExpiredError` from any stage → state `last_sync_error_kind ==
     "auth_expired"`; generic exception → `"error"`; clean sync → `""`.
   - `get_runtime_status()` exposes `last_account_sync_error_kind`.
   - `RuntimeStatusResponse` accepts/serializes the new optional field (default `""`).
   - JS test: `app.js` references `last_account_sync_error` and
     `last_account_sync_error_kind`, and contains the auth-expired copy string
     (string-presence assertions per the desktop-web test convention).
2. Backend: import `BilibiliAuthExpiredError` in account_sync; wrap the three stages'
   `except Exception as exc` to also record kind (auth-expired check first). Add the
   field to `RuntimeStatusResponse` (`api/models.py:169-170`) and thread it through
   the `/api/runtime-status` payload (`api/app.py:4054-4071`) — check whether the
   payload is built from `get_runtime_status()` directly (then it's automatic) or
   field-by-field.
3. Frontend: grep `app.js` for where `/api/runtime-status` (or the settings/status
   panel near `accountSyncInterval`, `app.js:4085`) is populated. Render:
   - `auth_expired` → prominent chip “B 站登录已失效，账号同步已停止 — 请重新登录”;
   - other non-empty error → muted chip with error text + `last_account_sync_at`;
   - empty → render nothing (healthy installs see zero change).
   Reuse existing chip/badge CSS classes if present; add minimal new CSS otherwise.
4. Run targeted tests + lint + mypy; manually note in the PR that a backend restart is
   required for the new field (serve-api stale-routes gotcha).

### Task 5: X scheduled incremental sync

**Files:** Modify `src/openbiliclaw/runtime/account_sync.py`,
`src/openbiliclaw/api/runtime_context.py`, `src/openbiliclaw/integrations/openclaw/bootstrap.py`;
Test `tests/test_account_sync.py`

**Steps:**
1. Cookie resolution: use the existing
   `openbiliclaw.sources.x_auth.resolve_x_cookie` (`x_auth.py:54`) — no new helper,
   no cli.py extraction.
2. Failing tests (stub x_client with `.likes/.bookmarks`):
   - no x_client (None) → zero X calls, bilibili behavior byte-identical.
   - **first sync, empty state sets, events table already holds init-persisted X
     likes** (URLs in `https://x.com/<handle>/status/<id>` form): state seeds from
     the persisted tweet IDs; a fetched like whose ID is among them emits nothing; a
     fetched like whose ID is NOT among them (liked after init) **emits an event** —
     the naive silent-full-seed behavior is explicitly asserted against.
   - second sync with one new like → exactly one event: `event_type="like"`,
     `metadata.source_platform="twitter"` (`SOURCE_TWITTER`,
     `event_format.py:207` — never `"x"`), `metadata.source="account_sync"`;
     propagated and fed to the (Task 3) pipeline branch.
   - bookmarks map to `event_type="favorite"`.
   - ID-set cap: 2001 seen IDs → oldest trimmed to 2000 in saved state.
   - x fetch raising → error recorded (+ Task 4 kind/log), bilibili events unaffected.
   - Task 2 cross-source dedup keys on **tweet ID, not URL**: an extension-reported
     like at `https://x.com/i/status/123` within the window suppresses the
     account_sync event for `https://x.com/someuser/status/123` (extract the trailing
     `/status/<id>` segment on both sides).
3. Implement: optional `x_client` constructor param (duck-typed, default None); fetch
   block after the following-sync block inside `sync_now` (same `_is_due` cycle);
   dedup via state sets of normalized tweet IDs mirroring `following_mids`
   (`account_sync.py:463-471`), first-sync seeding from persisted X event URLs
   (`recent_event_urls(["like", "favorite"], ...)` filtered to x.com, no window — use a
   large `within_hours` or a dedicated unwindowed variant, since init may be months
   old); events via `build_event` (`sources/event_format.py:345`) with tweet URL,
   title from tweet text (truncate ~120 chars), default signal strengths.
4. Wire `x_client` at both assembly sites, constructed only when `resolve_x_cookie`
   returns a cookie; reuse `XClient(cookie=...)` exactly as init does.
5. Run targeted tests + lint + mypy.

### Task 6: Documentation sync (mandatory, per CLAUDE.md)

**Files:** `docs/modules/extension.md`, `docs/modules/runtime.md`,
`docs/modules/storage.md`, `docs/changelog.md`; conditionally `docs/architecture.md`,
`docs/spec.md`, `README.md`, `README_EN.md`

**Steps:**
1. `extension.md`: buffer persistence (storage keys, cap/TTL constants), parking
   behavior for `not_initialized`.
2. `runtime.md`: account_sync — cross-source dedup window (48h), pipeline-path
   unification (ready vs bootstrap), error kinds, X sync (cadence, limits, seeding).
3. `storage.md`: `recent_event_urls` in the public-API section.
4. Check `docs/architecture.md` / `docs/spec.md` §3 / README diagrams: if account_sync
   is depicted as bilibili-only or absent, add the X scheduled-sync edge; if the
   diagrams don't show account_sync at all, skip (do not invent new diagram scope).
5. `docs/changelog.md` bullet under the current version block, e.g.
   `fix: event acquisition reliability — extension buffer survives service-worker
   recycling (chrome.storage persistence + not_initialized parking); account_sync
   dedups against extension-observed events, flows through the profile pipeline, and
   surfaces sync errors (incl. auth-expired) in the desktop web UI; X likes/bookmarks
   gain 6h server-side incremental sync. Backend restart required for the new
   runtime-status field.`
6. Full suites once at the end: `.venv/bin/python -m pytest -q` + ruff + mypy, and
   `cd extension && npm run build && npm run test`.

---

## Verification after merge

1. Kill the extension SW from `chrome://serviceworker-internals` right after browsing
   activity → events appear at the backend on next wake (`/api/events` log).
2. Watch a video with the extension active, force `sync_now` within the window →
   `events` table has exactly one `view` row for that bvid.
3. Expire/clear the bilibili cookie, wait one sync cycle → desktop web shows the
   auth-expired chip; re-login clears it on the next clean sync.
4. Like a tweet with the browser closed → it appears as an X `like` event after the
   next 6h cycle.

## Explicitly out of scope

- like/unlike disambiguation, watch_seconds pause-awareness, xhs/zhihu collector depth,
  search re-weighting, comment-text capture, bilibili folder/page cap raises (spec's
  out-of-scope list).
- Any `events`-table schema change.
- Prompt or soul-pipeline threshold changes.
