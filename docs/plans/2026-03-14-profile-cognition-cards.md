# Profile Cognition Cards Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade popup profile cognition updates into expandable cards with paginated history, so the popup shows what changed, how it affects the portrait, why the system thinks so, and lets the user keep scrolling through older cognition updates.

**Architecture:** Extend the cognition update data model in the soul layer to emit structured fields (`summary`, `impact`, `reasoning`, `evidence`, `source`, `created_at`), expose them through `/api/profile-summary` with cursor-based pagination metadata, and render them as expandable cards with incremental history loading in the popup profile tab. Keep backward compatibility for existing stored updates that only have `summary`.

**Tech Stack:** Python 3.14, FastAPI, vanilla JS popup UI, Node test runner, pytest

---

### Task 1: Define the paginated cognition card contract

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**

Add API tests that expect `/api/profile-summary` to:

- return `recent_cognition_updates` as structured items with `summary`, `impact`, `reasoning`, `evidence`, `source`, and `created_at`
- default to the first 3 cognition updates
- return `has_more_cognition_updates` and `next_cognition_cursor`
- accept `cursor` and `limit` query params for later pages
- still accept legacy memory entries with only `summary`

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_api_app.py -q`
Expected: FAIL because the endpoint currently hard-caps the response and exposes no pagination metadata.

**Step 3: Write minimal implementation**

Add pagination fields to the profile summary response model and update `profile_summary()` to:

- map stored memory entries into the structured card form
- sort them deterministically
- slice by `cursor` / `limit`
- emit `has_more_cognition_updates` and `next_cognition_cursor`
- keep graceful fallback for legacy records

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_api_app.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: paginate profile cognition history"
```

### Task 2: Generate structured cognition cards in the soul layer

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Test: `tests/test_soul_engine.py`

**Step 1: Write the failing test**

Add focused tests for:

- immediate `comment` feedback cognition card
- immediate `dislike` feedback cognition card
- immediate dialogue cognition card
- batch refresh cognition card

Each test should assert `summary`, `impact`, `reasoning`, and `evidence` contents, not just presence.

**Step 2: Run test to verify it fails**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_soul_engine.py -q`
Expected: FAIL because cognition updates only store `summary`.

**Step 3: Write minimal implementation**

Extend the update builders in `SoulEngine` so both immediate and batch cognition paths emit the new fields with conservative wording and source-specific evidence.

**Step 4: Run test to verify it passes**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_soul_engine.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/engine.py tests/test_soul_engine.py
git commit -m "feat: enrich cognition updates with impact and reasoning"
```

### Task 3: Normalize structured and legacy cognition cards plus pagination state in popup helpers

**Files:**
- Modify: `extension/popup/popup-helpers.js`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**

Add helper tests covering:

- structured cognition card normalization
- legacy string/summary-only fallback
- empty optional fields being omitted cleanly
- history pagination metadata normalization
- next-page state transitions for idle/loading/error/done

**Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern "cognition"`
Expected: FAIL because popup helpers only accept `list[str]`.

**Step 3: Write minimal implementation**

Add popup helpers that normalize cognition cards into one stable frontend shape:

- `summary`
- `impact`
- `reasoning`
- `evidence`
- `source`
- `created_at`
- `expandable`

And one stable cognition history state shape:

- `items`
- `hasMore`
- `nextCursor`
- `loadingMore`
- `loadMoreError`

**Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern "cognition"`
Expected: PASS

**Step 5: Commit**

```bash
git add extension/popup/popup-helpers.js extension/tests/popup-helpers.test.ts
git commit -m "feat: normalize popup cognition history state"
```

### Task 4: Render expandable cognition cards with incremental history loading in the popup profile tab

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-api.js`
- Modify: `extension/popup/popup.html`
- Test: `extension/tests/popup-helpers.test.ts`
- Test: `extension/tests/popup-copy.test.ts`

**Step 1: Write the failing test**

Add tests that describe the expected popup behavior:

- cognition cards render collapsed by default
- clicking one expands its detail sections
- opening a new card collapses the previous one
- legacy summary-only card still renders without expand details
- initial profile load only renders the first page
- scrolling near the bottom triggers the next page load
- the footer “加载更多” button can fetch the next page if auto-load does not trigger
- loading/error/no-more states render the right copy

**Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL because the popup still renders a fixed cognition list and has no history loading controls.

**Step 3: Write minimal implementation**

Replace the fixed cognition list rendering with expandable card rendering plus pagination controls, update popup fetch calls to pass `cursor` / `limit`, and add the corresponding markup/CSS hooks in `popup.html`.

**Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS

**Step 5: Commit**

```bash
git add extension/popup/popup.js extension/popup/popup-api.js extension/popup/popup.html extension/tests/popup-helpers.test.ts extension/tests/popup-copy.test.ts
git commit -m "feat: render paginated profile cognition history"
```

### Task 5: Update docs and run final verification

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document the new cognition card structure, API/UI behavior, and legacy fallback behavior.

**Step 2: Run backend verification**

Run: `/Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_api_app.py tests/test_soul_engine.py tests/test_memory_manager.py -q`
Expected: PASS

**Step 3: Run extension verification**

Run: `npm test`
Expected: PASS

Run: `npm run build`
Expected: PASS

**Step 4: Commit**

```bash
git add docs/modules/soul.md docs/modules/extension.md docs/changelog.md
git commit -m "docs: record expandable profile cognition cards"
```
