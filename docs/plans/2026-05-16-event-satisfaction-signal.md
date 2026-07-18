# Event Satisfaction Signal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Attach a deterministic `inferred_satisfaction` field to every behavior event at ingestion time, capture the dwell data needed to compute it, and give the preference analyzer a config-gated filter so it can ignore events the user clearly didn't enjoy. Default-off so the rollout is safe; flipping the flag is the actual loop-breaking change.

**Architecture:** New classifier helper in `sources/event_format.py`; storage schema adds two columns and owns classification centrally; extension collector kernel emits dwell; preference analyzer reads through a new `soul/event_filters.py` helper guarded by a dataclass config flag passed in through `SoulEngine`. No prompt-builder changes — this is data-quality work, not LLM tuning.

**Tech Stack:** Python 3.11 (sources, storage, soul, api), SQLite migration, TypeScript (extension content script + service worker), pytest, node --test, ruff, mypy strict.

---

### Task 1: Add the Classifier Helper

**Files:**
- Modify: `src/openbiliclaw/sources/event_format.py`
- Test: `tests/test_event_satisfaction.py`

**Step 1: Write failing tests**

Add tests proving:
- `classify_event_satisfaction({"event_type": "like", ...})` → `("positive", "explicit_engagement")`.
- Same for `coin`, `favorite`, `comment`.
- Feedback with `metadata.feedback_type == "dislike"` or `metadata.reaction == "thumbs_down"` → `("negative", "explicit_negative")`.
- Feedback with `metadata.feedback_type in {"like", "comment"}` or `metadata.reaction == "thumbs_up"` → `("positive", "explicit_engagement")`.
- A `click` with `watch_seconds=18, video_duration_seconds=60` → `("positive", "meaningful_dwell")`.
- A `click` with `watch_seconds=2, video_duration_seconds=600` → `("negative", "quick_exit")`.
- A `click` with `watch_seconds=10, video_duration_seconds=600` → `("neutral", "shallow_view")`.
- A `click` with no `watch_seconds` field → `("unknown", "missing_dwell")`.
- `snapshot` / `scroll` / `hover` / `search` events → `("neutral", "passive_browse")`.
- An unrecognized event_type → `("unknown", "fallback")` and does **not** raise.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_event_satisfaction.py -q
```

Expected: fail because `classify_event_satisfaction` does not exist.

**Step 3: Implement the classifier**

In `event_format.py`:
- Add a module-level rule table that captures the categories above; keep it short and readable.
- Add `classify_event_satisfaction(event: dict) -> tuple[Literal["positive","neutral","negative","unknown"], str]` that walks the rule table and returns the first matching `(category, reason)`.
- Add thresholds as module-level constants: `_MEANINGFUL_DWELL_MIN_SECONDS = 15`, `_MEANINGFUL_DWELL_MIN_RATIO = 0.3`, `_QUICK_EXIT_MAX_SECONDS = 5`.
- Read dwell fields from either top-level keys or `metadata`: `watch_seconds`, `video_duration_seconds`, and the existing extension video `duration` key as a fallback.
- Treat recommendation click-throughs as `event_type == "click"` plus `metadata.source == "recommendation_click"`; do not introduce a new DB event type in this task.
- The function must never raise; on a TypeError reading the payload, return `("unknown", "fallback")` after a `logger.debug`.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/sources/event_format.py tests/test_event_satisfaction.py
git commit -m "feat(sources): classify_event_satisfaction with deterministic rule table"
```

---

### Task 2: Extend Storage Schema and Persist Classification

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_storage.py`
- Test: `tests/test_memory_manager.py`

**Step 1: Write failing tests**

Add tests proving:
- After first boot on a fresh database, the events table has columns `inferred_satisfaction TEXT` and `satisfaction_reason TEXT`.
- A pre-existing database without those columns is migrated additively on next boot; existing rows have `NULL` in both columns.
- Inserting an event via the existing persistence helper writes the classifier output to the new columns.
- `MemoryManager.propagate_event(...)` persists through that single classification path.
- `query_events(..., satisfaction_modes=None)` returns all rows when `modes is None`; with `modes={"positive"}` returns only positive rows; with `modes={"positive", "unknown"}` includes rows whose `inferred_satisfaction` is `NULL` or `"unknown"`.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_storage.py tests/test_memory_manager.py -q
```

Expected: fail because the columns and query parameter do not exist.

**Step 3: Implement schema + persistence + query**

- In `database.py`:
  - Add the two columns to the events DDL and add `_ensure_event_satisfaction_columns()` to `initialize()` using `PRAGMA table_info(events)` + additive `ALTER TABLE`.
  - In `insert_event`, construct a full event dict from `event_type` + kwargs (`url`, `title`, `context`, `metadata`) and call `classify_event_satisfaction(event)` exactly once. Write `inferred_satisfaction` and `satisfaction_reason` alongside the existing event fields.
  - Extend `query_events(...)` with `satisfaction_modes: frozenset[str] | None = None`. When set, append an `IN (...)` clause; if `"unknown"` is included, also include `inferred_satisfaction IS NULL`.
- In `memory/manager.py`:
  - Add the same optional `satisfaction_modes` parameter to `MemoryManager.query_events(...)` and pass it through to `Database.query_events(...)`.
  - Do not add unsupported event types like `dismiss`, `hide`, `not_interested`, `browse`, or `history_sync` in this minimum version.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/memory/manager.py tests/test_storage.py tests/test_memory_manager.py
git commit -m "feat(storage): persist inferred_satisfaction on events with backward-compatible migration"
```

---

### Task 3: Preserve Dwell Fields in Ingest Paths

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py` (`/api/events` ingest endpoint + `/api/recommendation-click` handler)
- Test: `tests/test_api_app.py`

**Step 1: Write failing tests**

Add tests proving:
- POSTing a `click` event to `/api/events` with top-level or metadata `watch_seconds=2, video_duration_seconds=120` passes those fields through to `memory_manager.propagate_event(...)` metadata.
- POSTing a `like` event still passes through unchanged; classification is asserted in storage/memory tests, not duplicated in the API test.
- Calling `/api/recommendation-click` with `watch_seconds` and `video_duration_seconds` fields includes both fields in the persisted click event metadata.
- Calling `/api/recommendation-click` without dwell fields still persists the click event and lets storage classify it as `unknown`.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_api_app.py -q
```

Expected: fail because the API models / handlers do not yet preserve the new dwell fields.

**Step 3: Implement ingest wiring**

- In `api/models.py`:
  - Add optional `watch_seconds: float | None = None` and `video_duration_seconds: float | None = None` to `BehaviorEventIn`.
  - Add the same optional fields to `RecommendationClickIn`.
- In `/api/events`:
  - Fold non-`None` top-level dwell fields into `metadata` before calling `build_event(...)`.
  - Preserve existing `item.metadata` values when the extension already sends dwell inside metadata.
- In `/api/recommendation-click`:
  - Add non-`None` dwell fields to the click event metadata together with `source = "recommendation_click"`.
  - Do not call `classify_event_satisfaction` here; storage is the single classification owner from Task 2.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat(api): preserve dwell fields on event ingest"
```

---

### Task 4: Capture Dwell in the Extension

**Files:**
- Modify: `extension/src/content/kernel.ts`
- Modify: `extension/src/shared/types.ts`
- Modify: `extension/src/background/service-worker.ts`
- Test: `extension/tests/collector-helpers.test.ts` (or a new focused `extension/tests/dwell-collector.test.ts`)

**Step 1: Write failing tests**

Add tests proving:
- On a simulated Bilibili video-page session followed by a route-change away after 18 seconds, the kernel emits one final `click` dwell event with `metadata.watch_seconds = 18`.
- On a simulated quick-exit (route change after 2 seconds), the emitted event carries `metadata.watch_seconds = 2`.
- The emitted payload includes `metadata.video_duration_seconds` when it can be read from the `<video>` element; omits or leaves it null when unknown.
- `service-worker.ts` forwards the event without stripping the new metadata fields.

**Step 2: Run tests to verify they fail**

Run:

```bash
cd extension && npm run test
```

Expected: fail because the collector does not yet track dwell.

**Step 3: Implement dwell capture**

- In `kernel.ts`:
  - Add a small video-session tracker keyed by the current URL/content id. On video-page entry, record `dwellStartedAt = performance.now()`.
  - Before `currentUrl` is changed in `pushState` / `replaceState` / `popstate`, flush the previous page's dwell event.
  - Also flush on `pagehide` so closing the tab records quick exits.
  - Emit the dwell summary as a `click` event for the previous video URL with metadata:
    - `watch_seconds`
    - `video_duration_seconds`
    - `dwell_source = "video_page_exit"`
    - platform metadata from `adapter.buildEventMetadata(previousUrl)`
  - Keep existing immediate click / view / pause / seek events unchanged.
- In `types.ts`: widen `BehaviorEvent.metadata` usage only if TypeScript tests require a named type; otherwise no structural change is needed because metadata already accepts `Record<string, unknown>`.
- In `service-worker.ts`: no transformation should be necessary; add/keep tests proving it forwards metadata verbatim.

**Step 4: Run tests to verify pass**

Run the same npm test command and confirm pass. Then run `npm run typecheck` and confirm clean.

**Step 5: Commit**

```bash
git add extension/src/content/kernel.ts extension/src/shared/types.ts extension/src/background/service-worker.ts extension/tests/
git commit -m "feat(extension): collect watch_seconds and video_duration_seconds on video pages"
```

---

### Task 5: Add the Consumer-Side Filter Behind a Config Flag

**Files:**
- Create: `src/openbiliclaw/soul/event_filters.py`
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `config.example.toml`
- Test: `tests/test_event_filters.py`
- Test: `tests/test_preference_analyzer.py`
- Test: `tests/test_config.py`
- Test: `tests/test_soul_engine.py`

**Step 1: Write failing tests**

Add tests proving:
- `filter_events_by_satisfaction(events, modes={"positive"})` keeps only positive rows in original order.
- `modes={"positive", "unknown"}` keeps positive AND rows with `inferred_satisfaction in (None, "unknown")`.
- `modes=frozenset()` returns `[]`.
- `config.soul.preference.satisfaction_filter_enabled` defaults to `False`; round-trips through `load_config` / `save_config`; appears in `config.example.toml` with a comment explaining the rollout posture.
- `preference_analyzer.analyze_events` with the flag off passes the unmodified event list to the LLM (assert by capturing the prompt builder input).
- With the flag on, `analyze_events` drops negative events from the input and the resulting prompt does not contain their titles.
- `SoulEngine(..., satisfaction_filter_enabled=True)` passes the flag to its internal `PreferenceAnalyzer`.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_event_filters.py tests/test_preference_analyzer.py tests/test_config.py tests/test_soul_engine.py -q
```

Expected: fail because filter and config flag do not exist.

**Step 3: Implement filter + flag wiring**

- Create `event_filters.py` with a single function `filter_events_by_satisfaction(events, *, modes)` that handles the `None`/`"unknown"` aliasing as specified.
- Add dataclass config models in `config.py`:
  - `SoulPreferenceConfig(satisfaction_filter_enabled: bool = False)`
  - `SoulConfig(preference: SoulPreferenceConfig = field(default_factory=SoulPreferenceConfig))`
  - Add `soul: SoulConfig = field(default_factory=SoulConfig)` to root `Config`.
  - Parse `[soul.preference]` in `_build_config(...)`.
  - Render `[soul.preference]` in `_render_config_toml(...)` so `save_config` round-trips it.
- Add `[soul.preference]` to `config.example.toml` with a comment block: `# When True, preference analysis ignores events classified as quick-exit or explicit-negative. Default False; flip after one release cycle of observing inferred_satisfaction distributions.`
- Add `satisfaction_filter_enabled: bool = False` to `PreferenceAnalyzer`. If enabled, run `filter_events_by_satisfaction(events, modes=frozenset({"positive", "unknown"}))` before building prompts, including the chunked path.
- Add `satisfaction_filter_enabled: bool = False` kwarg to `SoulEngine.__init__` and pass it to `PreferenceAnalyzer`.
- In `api/runtime_context.py`, pass `new_config.soul.preference.satisfaction_filter_enabled` when constructing `SoulEngine`.
- In `cli.py`'s `_build_soul_engine`, load config and pass the same flag when constructing `SoulEngine`.

**Step 4: Run tests to verify pass**

Run the same pytest commands and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/event_filters.py src/openbiliclaw/soul/preference_analyzer.py src/openbiliclaw/soul/engine.py src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py config.example.toml tests/test_event_filters.py tests/test_preference_analyzer.py tests/test_config.py tests/test_soul_engine.py
git commit -m "feat(soul): satisfaction_filter_enabled flag gates preference analyzer on classified events"
```

---

### Task 6: Docs + Changelog

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/api.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`

**Step 1: Update module docs**

- `soul.md`: new event-filter contract and the rollout posture of `satisfaction_filter_enabled`.
- `storage.md`: schema additions and the additive migration.
- `extension.md`: collector kernel now captures dwell + duration on video pages.
- `api.md`: event payload now accepts `watch_seconds` and `video_duration_seconds`; recommendation-click handler accepts the same.
- `config.md`: document the new `[soul.preference]` flag.

**Step 2: Update architecture diagram**

In `docs/architecture.md`, `docs/spec.md` §3, `README.md`, and `README_EN.md`, add the `inferred_satisfaction` classifier as a labelled step between event ingest/storage and the preference analyzer in the data-flow diagrams.

**Step 3: Update changelog**

Add a bullet under the current version block in `docs/changelog.md`:
- `feat(soul): inferred_satisfaction signal — events classified at ingest; new config flag soul.preference.satisfaction_filter_enabled (default off) lets the preference layer ignore quick-exit / explicit-negative events`.

**Step 4: Commit**

```bash
git add docs/ README.md README_EN.md
git commit -m "docs: inferred_satisfaction signal + filter flag"
```

---

## Verification

Run the full Python suite and the extension suite:

```bash
uv run --extra dev python -m pytest -q
cd extension && npm run test && npm run typecheck
```

Then in a manual smoke (flag still off):

1. Start the backend, install the extension, open a video, watch >20 s, close. Confirm via SQLite inspection that the stored click row has `inferred_satisfaction = "positive"`.
2. Open another video, close within 2 s. Confirm `inferred_satisfaction = "negative", satisfaction_reason = "quick_exit"`.
3. Flip the config flag on, restart, and re-run a preference cycle. Confirm via the existing LLM prompt-debug log that the prompt body no longer contains the quick-exit row's title.
