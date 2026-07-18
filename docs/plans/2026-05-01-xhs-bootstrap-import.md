# XHS Bootstrap Import Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Import Xiaohongshu saved, liked, and Xiaohongshu-page browsing-history signals during initialization so the first profile is built from both Bilibili and Xiaohongshu.

**Architecture:** Extend the existing `xhs_tasks` bridge with a `bootstrap_profile` task. The extension opens an inactive Xiaohongshu tab, follows the current logged-in user's profile URL, extracts notes from Xiaohongshu-rendered profile state, then the backend converts those results into normal event-layer payloads consumed by `SoulEngine.analyze_events()` and `build_initial_profile()`.

**Tech Stack:** Python/FastAPI/SQLite/Typer, TypeScript Chrome extension MV3, node:test, pytest.

---

### Task 1: Backend Event Conversion

**Files:**
- Modify: `src/openbiliclaw/sources/xhs_tasks.py`
- Test: `tests/test_xhs_tasks.py`

**Step 1: Write failing tests**

Add tests for a helper that converts bootstrap notes to events:

```python
def test_xhs_bootstrap_notes_to_events_maps_scopes() -> None:
    events = xhs_bootstrap_notes_to_events([
        {"scope": "saved", "title": "收藏笔记", "url": "https://www.xiaohongshu.com/explore/a", "note_id": "a"},
        {"scope": "liked", "title": "点赞笔记", "url": "https://www.xiaohongshu.com/explore/b", "note_id": "b"},
        {"scope": "xhs_history", "title": "看过笔记", "url": "https://www.xiaohongshu.com/explore/c", "note_id": "c"},
    ])
    assert [event["event_type"] for event in events] == ["favorite", "like", "view"]
    assert all(event["metadata"]["source_platform"] == "xiaohongshu" for event in events)
```

**Step 2: Verify red**

Run: `uv run pytest tests/test_xhs_tasks.py::test_xhs_bootstrap_notes_to_events_maps_scopes -q`

Expected: FAIL because `xhs_bootstrap_notes_to_events` does not exist.

**Step 3: Implement helper**

Add a small pure helper to `xhs_tasks.py`:

- map `saved -> favorite`, `liked -> like`, `xhs_history -> view`
- include `source_platform`, `note_id`, `xsec_token`, `author`, `cover_url`, `import_source`, `signal_strength`
- skip notes with no title and no URL

**Step 4: Verify green**

Run: `uv run pytest tests/test_xhs_tasks.py -q`

Expected: PASS.

### Task 2: Backend Task Result Ingestion

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_xhs_ingest.py`

**Step 1: Write failing test**

Add a test posting `/api/sources/xhs/task-result` with `status=ok`, task type `bootstrap_profile`, and scoped `notes`. It should mark the task completed, cache notes when metadata is present, and propagate converted events into memory.

**Step 2: Verify red**

Run: `uv run pytest tests/test_api_xhs_ingest.py::test_xhs_bootstrap_task_result_records_events -q`

Expected: FAIL because task result currently only stores URLs/notes for content cache.

**Step 3: Implement ingestion**

In `xhs_task_result`:

- look up the task by id before completing it
- if task type is `bootstrap_profile`, convert `payload["notes"]` with the new helper
- call `ctx.memory_manager.propagate_event()` for each converted event
- do not call `soul_engine.analyze_events()` here; init will analyze the full combined batch

**Step 4: Verify green**

Run: `uv run pytest tests/test_api_xhs_ingest.py tests/test_xhs_tasks.py -q`

Expected: PASS.

### Task 3: Extension Bootstrap Extraction

**Files:**
- Modify: `extension/src/content/xhs/task-executor.ts`
- Test: `extension/tests/xhs-task-executor.test.ts`

**Step 1: Write failing tests**

Add pure tests for extracting scoped notes from mocked Xiaohongshu state:

```ts
test("extractBootstrapNotesFromState maps saved liked and history groups", () => {
  const notes = extractBootstrapNotesFromState({
    user: { notes: { _rawValue: [[published], [saved], [liked]] } },
    history: { notes: { _rawValue: [history] } },
  });
  assert.equal(notes.find((n) => n.title === "saved")?.scope, "saved");
  assert.equal(notes.find((n) => n.title === "liked")?.scope, "liked");
  assert.equal(notes.find((n) => n.title === "history")?.scope, "xhs_history");
});
```

**Step 2: Verify red**

Run: `cd extension && node --test --experimental-strip-types tests/xhs-task-executor.test.ts`

Expected: FAIL because extraction helpers do not exist.

**Step 3: Implement pure helpers**

Add exported helpers:

- `extractBootstrapNotesFromState(state, scopes?)`
- `extractOwnProfileUrlFromDocument(doc, baseUrl)`
- `extractOwnProfileUrlFromState(state, baseUrl)`
- `executeBootstrapTaskInPage()`

Keep DOM parsing best-effort and cap each scope.

**Step 4: Verify green**

Run: `cd extension && node --test --experimental-strip-types tests/xhs-task-executor.test.ts`

Expected: PASS.

### Task 4: Extension Dispatcher Task Type

**Files:**
- Modify: `extension/src/background/xhs-task-dispatcher.ts`
- Test: `extension/tests/xhs-task-dispatcher.test.ts`

**Step 1: Write failing tests**

Add tests that `isValidTask()` accepts `bootstrap_profile` and `buildTaskUrl()` routes it to `https://www.xiaohongshu.com/explore`.

**Step 2: Verify red**

Run: `cd extension && node --test --experimental-strip-types tests/xhs-task-dispatcher.test.ts`

Expected: FAIL because `bootstrap_profile` is not valid.

**Step 3: Implement dispatcher support**

Extend `XhsTask` type and helpers:

- allow `type: "bootstrap_profile"`
- include optional `scopes`, `max_items_per_scope`, `max_scroll_rounds`
- route first to `https://www.xiaohongshu.com/explore`, then follow a content-script supplied profile URL for `bootstrap_profile`

**Step 4: Verify green**

Run: `cd extension && node --test --experimental-strip-types tests/xhs-task-dispatcher.test.ts`

Expected: PASS.

### Task 5: Init Orchestration

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/sources/xhs_tasks.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing tests**

Add CLI tests proving init includes XHS bootstrap events when a fake bootstrap importer returns scoped notes, and continues when it returns no notes or errors.

**Step 2: Verify red**

Run: `uv run pytest tests/test_cli.py::test_init_includes_xhs_bootstrap_events -q`

Expected: FAIL because init never imports XHS bootstrap signals.

**Step 3: Implement orchestration**

Add a helper used by `init()`:

- enqueue `bootstrap_profile` with scopes `saved`, `liked`, `xhs_history`
- wait briefly for the extension result through the task table
- convert result notes into events
- append XHS summary rows into `combined_history`

**Step 4: Verify green**

Run: `uv run pytest tests/test_cli.py::test_init_includes_xhs_bootstrap_events tests/test_xhs_tasks.py -q`

Expected: PASS.

### Task 6: Documentation

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document:

- `bootstrap_profile` XHS task
- init now attempts XHS saved/liked/page-history best-effort import
- XHS browsing history means Xiaohongshu page-derived history, not Chrome history

**Step 2: Verify docs and formatting**

Run:

```bash
uv run ruff check src/ tests/
cd extension && npm run typecheck
```

Expected: PASS for touched areas; pre-existing unrelated baseline failures remain documented.
