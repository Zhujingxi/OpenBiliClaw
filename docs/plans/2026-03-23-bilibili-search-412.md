# Bilibili Search 412 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce Bilibili search `412 Precondition Failed` errors by switching `search()` to the browser-aligned WBI search endpoint and degrading handled `412` responses into an empty search result.

**Architecture:** Keep the fix local to `BilibiliAPIClient.search()`. Extend the client’s internal GET helper to support per-request headers, add WBI key fetching and signing helpers inside the Bilibili API client, then use TDD to lock in WBI search behavior and the `412` soft-failure branch without changing unrelated endpoints.

**Tech Stack:** Python, httpx, pytest

---

### Task 1: Lock in WBI search request context

**Files:**
- Modify: `tests/test_bilibili_api.py`
- Modify: `src/openbiliclaw/bilibili/api.py`

**Step 1: Write the failing test**

Add a test that calls `BilibiliAPIClient.search()` and asserts the outgoing request:
- targets `/x/web-interface/wbi/search/type`
- includes signed search params plus `web_location`
- includes a search-page `Referer`

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_bilibili_api.py -k search_referer -v`
Expected: FAIL because the client still calls the old unsigned search endpoint.

**Step 3: Write minimal implementation**

Update the API client so `_get_json()` can accept per-request headers, then add WBI key loading and param signing so `search()` uses the signed WBI endpoint with search-page headers.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_bilibili_api.py -k search_referer -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_bilibili_api.py src/openbiliclaw/bilibili/api.py
git commit -m "fix: sign bilibili search requests"
```

### Task 2: Lock in graceful 412 degradation

**Files:**
- Modify: `tests/test_bilibili_api.py`
- Modify: `src/openbiliclaw/bilibili/api.py`

**Step 1: Write the failing test**

Add a test that simulates an HTTP `412` from `/x/web-interface/search/type` and asserts `search()` returns `[]` instead of raising.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_bilibili_api.py -k search_returns_empty_on_412 -v`
Expected: FAIL because the current implementation raises `BilibiliAPIError`.

**Step 3: Write minimal implementation**

Handle `httpx.HTTPStatusError` with status `412` inside `search()` and return an empty list after logging a warning.

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_bilibili_api.py -k search_returns_empty_on_412 -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_bilibili_api.py src/openbiliclaw/bilibili/api.py
git commit -m "fix: degrade bilibili search 412 failures"
```

### Task 3: Update docs and run verification

**Files:**
- Modify: `docs/modules/bilibili.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document that search requests now use the WBI search endpoint with a search-page referer and that handled `412` responses degrade to empty results.

**Step 2: Run focused verification**

Run: `./.venv/bin/pytest tests/test_bilibili_api.py -v`
Expected: PASS

Run: `./.venv/bin/ruff check src/openbiliclaw/bilibili/api.py tests/test_bilibili_api.py`
Expected: PASS

Run: `git diff --check -- src/openbiliclaw/bilibili/api.py tests/test_bilibili_api.py docs/modules/bilibili.md docs/changelog.md`
Expected: no output

**Step 3: Restart backend and observe logs**

Run:

```bash
pkill -f "openbiliclaw start" || true
nohup ./.venv/bin/openbiliclaw start > logs/backend-restart.log 2>&1 &
```

Then trigger a refresh and inspect whether search `412` tracebacks are reduced or replaced by the new warning line.

**Step 4: Commit**

```bash
git add src/openbiliclaw/bilibili/api.py tests/test_bilibili_api.py docs/modules/bilibili.md docs/changelog.md
git commit -m "fix: harden bilibili search requests"
```
