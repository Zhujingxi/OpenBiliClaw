# Delight Positive Feedback Visibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make surprise recommendation cards stay visible after positive actions and disappear only after negative or explicit dismiss actions.

**Architecture:** Keep the existing delight response API and three clients. Change consumption semantics so `like`, `view`, saved toggles, and chat state update the current card without marking it consumed; keep `dislike`, `reject`, `dismiss`, and close actions as immediate removal paths.

**Tech Stack:** FastAPI backend, vanilla JavaScript desktop web, mobile web modules, browser extension popup, Python pytest, Node test runner.

---

### Task 1: Backend Consumption Semantics

**Files:**
- Modify: `tests/test_api_app.py`
- Modify: `src/openbiliclaw/api/app.py`

**Steps:**
1. Change the existing delight like test so it asserts `mark_delight_notified` is not called.
2. Add coverage for `view` not consuming the delight candidate.
3. Keep dislike and dismiss assertions consuming the delight.
4. Run the focused API tests and confirm the new positive-action tests fail before implementation.
5. Remove `mark_delight_consumed()` from `like` and keep `view` non-consuming.

### Task 2: Mobile Web Delight Tray

**Files:**
- Modify: `tests/test_mobile_web_view_models.py`
- Modify: `src/openbiliclaw/web/js/view-models.js`
- Modify: `src/openbiliclaw/web/js/views/recommend.js`

**Steps:**
1. Update `getDelightActionState` tests so `view` and `like` are not permanent, while `reject` stays permanent.
2. Run the focused view-model test and confirm it fails.
3. Change action metadata and local removal logic so only permanent negative actions remove from `activeDelights`.
4. Change stream handling so `delight.disliked` removes but `delight.liked` only updates status.

### Task 3: Desktop Web And Extension Popup

**Files:**
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `extension/tests/web-watch-later.test.ts` or a focused existing static test if better
- Modify: `extension/popup/popup.js`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`

**Steps:**
1. Add source-level regression checks that popup like/view handlers do not call queue-removal helpers, and dislike still does.
2. Add a desktop source-level regression check that `respondDelight` removes only dislike/dismiss responses.
3. Run the focused Node tests and confirm they fail.
4. Keep popup like/view in the queue with local state and hint updates; keep dislike/dismiss removal.
5. Keep desktop like/view in `state.delights`; remove only dislike/dismiss.

### Task 4: Verification

Run:
- `pytest tests/test_api_app.py tests/test_mobile_web_view_models.py -q`
- `cd extension && npm test -- popup-helpers.test.ts web-watch-later.test.ts`
- `ruff check src/ tests/`
