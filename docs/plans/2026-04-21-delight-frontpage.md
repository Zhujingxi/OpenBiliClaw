# Delight Frontpage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Promote delight recommendations into a persistent first-screen module in the extension `recommend` tab, while keeping messages and system notifications as secondary entry points.

**Architecture:** Keep the backend protocol unchanged and implement `P0` entirely in the extension. Add a pure helper layer for delight normalization/state/deep-link handling, expose `/api/delight/pending` in popup API glue, update the popup runtime to hydrate and render an active delight card, and make notification clicks open a delight-aware extension URL.

**Tech Stack:** HTML, CSS, vanilla JS popup runtime, extension service worker, Node built-in test runner

---

### Task 1: Lock delight API and helper behavior with failing tests

**Files:**
- Modify: `extension/tests/popup-api.test.ts`
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `extension/tests/notifications.test.ts`
- Test: `extension/tests/popup-api.test.ts`
- Test: `extension/tests/popup-helpers.test.ts`
- Test: `extension/tests/notifications.test.ts`

**Step 1: Write the failing tests**

Add tests for:

- `fetchPendingDelight()` calling `GET /api/delight/pending`
- delight helper normalization for pending/runtime payloads
- delight visibility and stable handled-state copy
- extension URLs carrying `delight=<bvid>`

**Step 2: Run the focused tests to verify RED**

Run: `npm test -- popup-api.test.ts popup-helpers.test.ts notifications.test.ts`
Expected: FAIL because the delight API helper and deep-link-aware helpers do not exist yet.

**Step 3: Write the minimal implementation**

Implement the new popup API function and helper exports only until the new tests pass.

**Step 4: Run the focused tests to verify GREEN**

Run: `npm test -- popup-api.test.ts popup-helpers.test.ts notifications.test.ts`
Expected: PASS.

### Task 2: Lock the recommend-tab delight slot in layout tests

**Files:**
- Modify: `extension/tests/popup-layout.test.ts`
- Test: `extension/tests/popup-layout.test.ts`

**Step 1: Write the failing test**

Add assertions for:

- `viewRecommend` containing a dedicated `delightSlot`
- delight card markup exposing cover, hook, reason, response area, and four action buttons
- delight slot staying separate from `recommendationList`

**Step 2: Run the focused layout test to verify RED**

Run: `npm test -- popup-layout.test.ts`
Expected: FAIL on the new delight-slot assertions. Two unrelated existing popup-layout failures may still remain as baseline noise.

**Step 3: Write the minimal markup/CSS**

Update `extension/popup/popup.html` to include the new slot and supporting styles without yet wiring runtime behavior.

**Step 4: Run the focused layout test to verify GREEN for the new assertions**

Run: `npm test -- popup-layout.test.ts`
Expected: New delight assertions PASS; the two pre-existing card-layout failures may remain until separately fixed.

### Task 3: Implement popup delight hydration and state flow

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-helpers.js`
- Modify: `extension/popup/popup-api.js`
- Test: `extension/tests/popup-api.test.ts`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Extend popup state**

Add:

- `activeDelight`
- `delightHighlightBvid`
- `dismissedDelightBvids`

Keep the model intentionally small; do not add a full historical inbox in this pass.

**Step 2: Hydrate delight state**

Trigger `fetchPendingDelight()`:

- during popup init
- after backend reconnect
- after `init_completed`
- after `config_reloaded`

Merge pending/runtime delight by `bvid` and ignore items dismissed with `稍后看`.

**Step 3: Render the delight slot**

Implement a dedicated renderer that:

- shows the delight card when an active item exists
- highlights the card if URL/query state targets that `bvid`
- keeps handled states visible instead of immediately removing the card
- falls back cleanly when no active delight exists

**Step 4: Implement actions**

Wire:

- `看看` → open content + mark local state as viewed
- `不感兴趣` → call delight respond endpoint if available, otherwise keep local handled state only
- `聊一聊` → show inline composer/result state tied to the active delight
- `稍后看` → hide current frontpage card without sending negative feedback

**Step 5: Run focused tests**

Run: `npm test -- popup-api.test.ts popup-helpers.test.ts`
Expected: PASS.

### Task 4: Implement delight-aware notification landing

**Files:**
- Modify: `extension/src/background/notifications.ts`
- Modify: `extension/src/background/service-worker.ts`
- Test: `extension/tests/notifications.test.ts`

**Step 1: Extend extension URL builder**

Allow `buildExtensionUiUrl()` / `openExtensionUi()` to accept an optional delight bvid and encode it into the popup URL.

**Step 2: Update notification click handling**

When a delight notification is clicked, open `recommend` with the delight deep-link parameter instead of a generic tab open.

**Step 3: Run focused tests**

Run: `npm test -- notifications.test.ts`
Expected: PASS.

### Task 5: Update docs and run final targeted verification

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/plans/2026-04-21-delight-frontpage-design.md`
- Modify: `docs/plans/2026-04-21-delight-frontpage.md`
- Test: `extension/tests/popup-api.test.ts`
- Test: `extension/tests/popup-helpers.test.ts`
- Test: `extension/tests/notifications.test.ts`
- Test: `extension/tests/popup-layout.test.ts`

**Step 1: Update extension docs**

Document the new recommend-tab delight slot and the secondary role of notifications.

**Step 2: Update changelog**

Add one concise entry describing the new delight frontpage behavior.

**Step 3: Run final targeted verification**

Run: `npm test -- popup-api.test.ts popup-helpers.test.ts notifications.test.ts popup-layout.test.ts`
Expected: PASS on new delight coverage. Note the two unrelated existing popup-layout failures if they still remain.

Run: `npm run typecheck`
Expected: PASS.
