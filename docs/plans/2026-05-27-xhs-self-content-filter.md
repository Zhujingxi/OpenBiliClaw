# XHS Self Content Filter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent Xiaohongshu notes authored by the logged-in user from entering, remaining in, or being served from the recommendation pool.

**Architecture:** Keep the ingest-time self filter, add a DB-level serve/readiness guard keyed by the persisted XHS nickname, and purge already-pooled self-authored rows whenever `self_info` first arrives or changes. Runtime/recommendation layers own `discovery_runtime_state` lookup and pass the nickname into storage methods; `Database` stays a pure storage boundary.

**Tech Stack:** Python 3.12, SQLite, FastAPI, pytest, TypeScript extension helpers, `node --test --experimental-strip-types`.

---

Implements: `docs/plans/2026-05-27-xhs-self-content-filter-spec.md`

## Task 1: Database Self-Author Guard For Pool Reads

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_storage.py`

**Step 1: Write failing storage tests**

Add focused tests near the existing `get_pool_candidates` tests.

Required cases:

```python
def test_pool_candidates_exclude_self_authored_xhs_rows_by_up_name_and_author_name() -> None:
    # Seed ready xhs rows:
    # - one with up_name="屎屎"
    # - one with author_name="屎屎"
    # - one with another author
    # - one bilibili row whose up_name also equals "屎屎"
    # Assert get_pool_candidates(..., xhs_self_nickname="屎屎")
    # returns the other xhs row and the bilibili row only.
```

```python
def test_pool_count_and_readiness_exclude_self_authored_xhs_rows() -> None:
    # Same fixture shape, then assert:
    # count_pool_candidates(xhs_self_nickname="屎屎") == 2
    # count_pool_readiness(xhs_self_nickname="屎屎")["available"] == 2
```

```python
def test_pool_self_author_guard_noops_when_nickname_empty() -> None:
    # Seed a ready xhs row with up_name="屎屎".
    # Assert empty nickname keeps existing behavior and returns/counts the row.
```

Also cover at least one background query:

```python
def test_pool_backlog_queries_skip_self_authored_xhs_rows() -> None:
    # Seed unclassified/copy-less/delight-less xhs rows where author_name matches.
    # Assert get_pool_candidates_needing_evaluation(..., xhs_self_nickname="屎屎")
    # and get_pool_candidates_needing_copy(..., xhs_self_nickname="屎屎")
    # do not return them.
```

Use the existing `_seed_visible(...)` helper where possible so rows pass the current readiness gates.

**Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_storage.py::TestDatabase -v
```

Expected: new tests fail because storage methods do not accept `xhs_self_nickname` or do not filter by it yet.

**Step 3: Add a shared SQL guard helper**

In `src/openbiliclaw/storage/database.py`, add a small helper near other pool helpers:

```python
def _xhs_self_author_guard_sql(table_alias: str = "content_cache") -> str:
    prefix = f"{table_alias}." if table_alias else ""
    return (
        "AND ("
        "? = '' "
        f"OR COALESCE({prefix}source_platform, '') != 'xiaohongshu' "
        "OR ("
        f"LOWER(COALESCE({prefix}up_name, '')) != LOWER(?) "
        f"AND LOWER(COALESCE({prefix}author_name, '')) != LOWER(?)"
        ")"
        ")"
    )
```

Use a helper for params:

```python
def _xhs_self_author_guard_params(xhs_self_nickname: str | None) -> tuple[str, str, str]:
    nickname = str(xhs_self_nickname or "").strip()
    return (nickname, nickname, nickname)
```

**Step 4: Thread the guard through DB methods**

Add `xhs_self_nickname: str = ""` keyword parameters to:

- `get_pool_candidates(...)`
- `count_pool_candidates(...)`
- `count_pool_readiness(...)`
- `get_pool_candidates_needing_evaluation(...)`
- `get_pool_candidates_needing_copy(...)`
- `get_pool_candidates_needing_delight_score(...)`

Insert `_xhs_self_author_guard_sql()` in each relevant `WHERE` clause and append `_xhs_self_author_guard_params(xhs_self_nickname)` to query parameters in the same order as the SQL placeholders.

For `count_pool_readiness()`, apply the guard to both raw and pending cursors, and call:

```python
"available": self.count_pool_candidates(xhs_self_nickname=xhs_self_nickname)
```

Do not change default behavior when `xhs_self_nickname` is empty.

**Step 5: Run storage tests**

Run:

```bash
pytest tests/test_storage.py -v
```

Expected: all storage tests pass.

**Step 6: Commit**

```bash
git add src/openbiliclaw/storage/database.py tests/test_storage.py
git commit -m "fix: exclude self-authored xhs rows from pool reads"
```

## Task 2: Purge Existing Self-Authored Rows On Self Info Arrival

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_xhs_ingest.py`

**Step 1: Write failing API tests**

Add tests near the existing XHS ingest self-info tests.

```python
def test_purge_self_authored_pool_items_matches_author_name(
    xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
) -> None:
    # Seed a fresh xhs row with up_name="" and author_name="屎屎".
    # Persist self_info through an observed-urls request.
    # Assert the seeded row pool_status becomes "suppressed".
```

```python
def test_self_info_change_triggers_immediate_purge(
    xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
) -> None:
    # Seed a self-authored xhs row before self_info exists.
    # POST /api/sources/xhs/observed-urls with self_info and one harmless URL.
    # Assert the existing row is suppressed in the same request lifecycle.
```

**Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_api_xhs_ingest.py::TestXhsObservedUrls -v
```

Expected: author_name-only rows are not suppressed; immediate purge does not fire on first self_info persistence.

**Step 3: Expand purge SQL**

In `_purge_self_authored_pool_items(...)`, change the final predicate to:

```sql
AND (
  LOWER(COALESCE(up_name, '')) = LOWER(?)
  OR LOWER(COALESCE(author_name, '')) = LOWER(?)
)
```

Pass `(nickname, nickname)`.

**Step 4: Trigger purge when persisted self_info changes**

In `_persist_xhs_self_info(...)`, after saving changed state, call:

```python
suppressed = _purge_self_authored_pool_items(ctx.database, self_info)
if suppressed:
    logger.info(
        "xhs self_info purge: suppressed %d self-authored pool item(s) (nickname=%r)",
        suppressed,
        self_info.get("nickname", ""),
    )
```

Keep the existing idempotent early return when `existing == self_info`.

**Step 5: Run API tests**

Run:

```bash
pytest tests/test_api_xhs_ingest.py -v
```

Expected: pass.

**Step 6: Commit**

```bash
git add src/openbiliclaw/api/app.py tests/test_api_xhs_ingest.py
git commit -m "fix: purge self-authored xhs rows when self info arrives"
```

## Task 3: Wire Self Nickname Into Recommendation And Runtime Counts

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_recommendation_engine.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing recommendation test**

Add a test using a fake database that records keyword arguments:

```python
def test_recommendation_engine_passes_xhs_self_nickname_to_pool_queries() -> None:
    # Build RecommendationEngine(..., xhs_self_info_provider=lambda: {"nickname": "屎屎"})
    # Call _load_pool_candidates(limit=10) or serve() with a minimal profile fixture.
    # Assert fake_db.get_pool_candidates saw xhs_self_nickname="屎屎".
```

Add a second test for empty/invalid provider data:

```python
def test_recommendation_engine_self_nickname_provider_failure_noops(caplog) -> None:
    # Provider raises RuntimeError.
    # Assert get_pool_candidates receives xhs_self_nickname="" and no exception escapes.
```

**Step 2: Add provider support to RecommendationEngine**

Update the constructor and import `Callable` from `typing`:

```python
def __init__(
    ...,
    xhs_self_info_provider: Callable[[], dict[str, object] | None] | None = None,
) -> None:
    ...
    self._xhs_self_info_provider = xhs_self_info_provider
```

Add:

```python
def _xhs_self_nickname(self) -> str:
    if self._xhs_self_info_provider is None:
        return ""
    try:
        info = self._xhs_self_info_provider() or {}
    except Exception:
        logger.exception("Failed to load xhs self_info for pool guard")
        return ""
    if not isinstance(info, dict):
        return ""
    return str(info.get("nickname", "") or "").strip()
```

Pass it to:

```python
self._database.get_pool_candidates(limit=limit, xhs_self_nickname=self._xhs_self_nickname())
self._database.get_pool_candidates_needing_copy(limit=limit, xhs_self_nickname=self._xhs_self_nickname())
self._database.get_pool_candidates_needing_evaluation(..., xhs_self_nickname=self._xhs_self_nickname())
self._database.get_pool_candidates_needing_delight_score(..., xhs_self_nickname=self._xhs_self_nickname())
```

Also use it inside `_pool_readiness_counts()`:

```python
counts = readiness_fn(xhs_self_nickname=self._xhs_self_nickname())
```

If existing fake DBs do not accept the keyword, update those fakes rather than adding broad `try TypeError` production fallback.

**Step 3: Wire provider in RuntimeContext and CLI**

In `RuntimeContext._rebuild_components(...)`, define a closure before constructing `RecommendationEngine`:

```python
def _xhs_self_info_provider() -> dict[str, object] | None:
    state = self.memory_manager.load_discovery_runtime_state()
    info = state.get("xhs_self_info")
    return info if isinstance(info, dict) else None
```

Pass it as `xhs_self_info_provider=_xhs_self_info_provider`.

In `src/openbiliclaw/cli.py::_build_recommendation_engine()`, reuse the `memory` object already built and pass the same style of provider.

**Step 4: Wire runtime controller direct counts**

In `src/openbiliclaw/runtime/refresh.py`, add:

```python
def _xhs_self_nickname(self) -> str:
    try:
        state = self.memory_manager.load_discovery_runtime_state()
    except Exception:
        logger.exception("Failed to load xhs self_info for runtime pool counts")
        return ""
    info = state.get("xhs_self_info")
    if not isinstance(info, dict):
        return ""
    return str(info.get("nickname", "") or "").strip()
```

Replace direct calls that mean servable inventory:

```python
self.database.count_pool_candidates()
self.database.count_pool_readiness()
```

with:

```python
self.database.count_pool_candidates(xhs_self_nickname=self._xhs_self_nickname())
self.database.count_pool_readiness(xhs_self_nickname=self._xhs_self_nickname())
```

Update test fake databases in `tests/test_refresh_runtime.py` to accept the keyword where needed.

**Step 5: Run targeted tests**

Run:

```bash
pytest tests/test_recommendation_engine.py tests/test_refresh_runtime.py tests/test_cli.py -v
```

Expected: pass.

**Step 6: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/cli.py tests/test_recommendation_engine.py tests/test_refresh_runtime.py tests/test_cli.py
git commit -m "fix: pass xhs self info to pool serving guards"
```

## Task 4: Extension Author Metadata Hardening

**Files:**
- Modify: `extension/src/content/xhs/passive.ts`
- Modify: `extension/src/content/xhs/task-executor.ts` if needed
- Test: `extension/tests/xhs-passive.test.ts`
- Test: `extension/tests/xhs-task-executor.test.ts`

**Step 1: Write failing extension tests**

Add DOM fixtures where author text is not under the current selectors:

```ts
test("extractNoteMetadataFromAnchor reads author from alternate xhs card selectors", () => {
  // Build a card with title + anchor + author text in a currently-missed selector.
  // Assert metadata.author === "屎屎".
});
```

Also test that empty author still keeps the note:

```ts
test("extractNoteMetadataFromAnchor preserves note when author is absent", () => {
  // Title exists, author does not.
  // Assert metadata is not null and metadata.author === "".
});
```

**Step 2: Run tests and verify failure**

Run:

```bash
node --test --experimental-strip-types extension/tests/xhs-passive.test.ts extension/tests/xhs-task-executor.test.ts
```

Expected: alternate-author fixture fails before selector hardening.

**Step 3: Improve author extraction conservatively**

Update `extractNoteMetadataFromAnchor(...)` to check a wider but bounded selector set, for example:

```ts
const authorEl = card.querySelector(
  [
    ".author-wrapper .name",
    ".author .name",
    ".user-name",
    "[class*='author'] .name",
    "[class*='author'] [class*='name']",
    "[class*='user'] [class*='name']",
    ".nickname",
  ].join(", "),
);
```

Do not infer author from title/body text. If no author element is present, keep `author=""` and rely on backend guards.

**Step 4: Run extension tests**

Run:

```bash
node --test --experimental-strip-types extension/tests/xhs-passive.test.ts extension/tests/xhs-task-executor.test.ts
```

Expected: pass.

**Step 5: Commit**

```bash
git add extension/src/content/xhs/passive.ts extension/src/content/xhs/task-executor.ts extension/tests/xhs-passive.test.ts extension/tests/xhs-task-executor.test.ts
git commit -m "fix(extension): improve xhs author metadata extraction"
```

## Task 5: Optional Author ID Persistence

Do this only after P0 is green and if extension payloads can reliably carry `author_id`.

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `extension/src/content/xhs/passive.ts`
- Modify: `extension/src/content/xhs/bootstrap.ts`
- Test: `tests/test_storage.py`
- Test: `tests/test_api_xhs_ingest.py`
- Test: `extension/tests/xhs-passive.test.ts`

**Step 1: Write failing schema and ingest tests**

```python
def test_content_cache_has_author_id_column() -> None:
    # Initialize Database and PRAGMA table_info(content_cache).
    # Assert author_id exists.
```

```python
def test_cache_xhs_notes_persists_author_id() -> None:
    # POST xhs note with author_id.
    # Assert content_cache.author_id equals payload author_id.
```

```python
def test_pool_guard_excludes_xhs_rows_by_author_id() -> None:
    # Seed xhs row author_id="uid-self" and different nickname.
    # Assert get_pool_candidates(..., xhs_self_user_id="uid-self") excludes it.
```

**Step 2: Add schema column**

In `_ensure_schema_columns()`, add:

```python
"author_id": "TEXT DEFAULT ''",
```

Extend `cache_content(...)` insert/update paths to persist `author_id`.

**Step 3: Extend DB guard**

Add `xhs_self_user_id: str = ""` alongside `xhs_self_nickname` and update SQL to exclude when:

```sql
LOWER(COALESCE(author_id, '')) = LOWER(?)
```

Keep nickname matching because older rows will not have `author_id`.

**Step 4: Extend backend and extension payloads**

In `_cache_xhs_notes(...)`, read:

```python
author_id = str(note.get("author_id", "") or "").strip()
```

Pass it into `database.cache_content(author_id=author_id, ...)`.

Only add extension extraction where the DOM/state exposes a stable author id. Do not invent IDs from profile URLs unless tests confirm the profile URL contains the author id for the card.

**Step 5: Run targeted tests**

Run:

```bash
pytest tests/test_storage.py tests/test_api_xhs_ingest.py -v
node --test --experimental-strip-types extension/tests/xhs-passive.test.ts
```

Expected: pass.

**Step 6: Commit**

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/api/app.py extension/src/content/xhs/passive.ts extension/src/content/xhs/bootstrap.ts tests/test_storage.py tests/test_api_xhs_ingest.py extension/tests/xhs-passive.test.ts
git commit -m "feat: persist xhs author ids for self-content filtering"
```

## Task 6: Documentation And Full Verification

**Files:**
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/architecture.md` only if implementation changes cross-module flow beyond the provider wiring described here

**Step 1: Update docs**

Document:

- recommendation pool now excludes known self-authored XHS rows at serve/count time
- XHS purge matches both `up_name` and `author_name`
- extension author extraction remains best-effort; backend is the final guard
- if Task 5 is implemented, `author_id` is now stored and used for self matching

**Step 2: Run backend checks**

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest
```

Expected: pass.

**Step 3: Run extension checks**

Run:

```bash
node --test --experimental-strip-types extension/tests/xhs-passive.test.ts extension/tests/xhs-task-executor.test.ts extension/tests/xhs-task-dispatcher.test.ts
```

Expected: pass.

**Step 4: Manual smoke**

Run the backend and extension against a logged-in XHS page:

```bash
openbiliclaw start
```

Manual checks:

- backend log shows `xhs self_info persisted` on first XHS observation
- if matching old rows exist, backend logs `xhs self_info purge: suppressed N`
- popup/mobile recommendations do not show notes whose author equals the logged-in XHS nickname
- “可换” count does not include suppressed/self-authored rows

**Step 5: Commit docs**

```bash
git add docs/modules/recommendation.md docs/modules/extension.md docs/changelog.md docs/architecture.md
git commit -m "docs: describe xhs self-content pool guard"
```
