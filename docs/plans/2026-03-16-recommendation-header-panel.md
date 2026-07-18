# Recommendation Header Panel Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rework the popup recommendation header into a single light-weight info panel with grouped pool/runtime status cards, while keeping recommendation cards and backend behavior unchanged.

**Architecture:** The popup will keep using the existing recommendation/runtime helpers and event flow, but the recommend-tab header will be restructured into one container that owns the title, primary reshuffle action, and three semantic status cards. Loading and runtime messages will be surfaced inside the status-card area instead of an independent floating toolbar line.

**Tech Stack:** Vanilla JavaScript, inline CSS/HTML in the extension popup, Node test runner, Markdown docs

---

### Task 1: Lock the new header structure with failing layout and copy tests

**Files:**
- Modify: `extension/tests/popup-layout.test.ts`
- Modify: `extension/tests/popup-copy.test.ts`
- Modify: `extension/popup/popup.html`

**Step 1: Write the failing tests**

Update the popup layout test to assert that the recommend view now contains:

- one unified header card container for the recommendation intro area
- a top section that groups intro copy and the `refreshRecommendationsButton`
- a status grid with three semantic blocks for available / replenished / active work

Update the copy test so it still asserts the key phrases stay present, but no longer requires the old floating toolbar status line as a separate visual pattern.

**Step 2: Run tests to verify they fail**

Run: `cd extension && node --test --experimental-strip-types tests/popup-layout.test.ts tests/popup-copy.test.ts`

Expected: FAIL because the current markup still uses separate intro, toolbar, and pool-status blocks.

**Step 3: Write minimal implementation**

Restructure the recommend-tab header markup inside `popup.html` and add the new CSS blocks for:

- the unified recommendation header card
- the top row for intro and action
- the three-card status grid
- responsive wrapping and fixed-height status cards

Keep all existing recommendation-list markup untouched.

**Step 4: Run tests to verify they pass**

Run: `cd extension && node --test --experimental-strip-types tests/popup-layout.test.ts tests/popup-copy.test.ts`

Expected: PASS

### Task 2: Lock status behavior with failing helper and popup-state tests

**Files:**
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `extension/popup/popup-helpers.js`
- Modify: `extension/popup/popup.js`

**Step 1: Write the failing tests**

Add focused coverage for the recommendation header state rules:

- loading state should be representable without depending on a separate floating status line
- runtime messages should still override the default topic copy in the “now working on” card
- enough-stock and recent-replenish copy should stay unchanged

If needed, add a small test seam around the logic that maps button/runtime state to the active status-card text.

**Step 2: Run tests to verify they fail**

Run: `cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts`

Expected: FAIL because the current state handling still assumes `refreshRecommendationsStatus` is rendered independently from the pool cards.

**Step 3: Write minimal implementation**

Refactor the popup state handling so:

- `setRefreshButtonState()` still controls the button text/disabled state
- loading text is routed into the “现在在忙” status content instead of requiring a separate standalone line
- runtime stream messages continue to take priority when appropriate
- the old `refreshRecommendationsStatus` node is removed or turned into a non-layout-affecting compatibility hook

Do not change backend payloads or helper copy generation outside what the new header rendering needs.

**Step 4: Run tests to verify they pass**

Run: `cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts tests/popup-layout.test.ts tests/popup-copy.test.ts`

Expected: PASS

### Task 3: Polish the visual hierarchy without altering recommendation cards

**Files:**
- Modify: `extension/popup/popup.html`

**Step 1: Adjust spacing and emphasis**

Tune the inline CSS so the new header reads in this order:

- title
- `换一批`
- current available count
- active runtime state

Use higher-contrast text, restrained tinted backgrounds, and stable hover/focus states. Keep the recommendation cards below unchanged.

**Step 2: Run focused layout verification**

Run: `cd extension && node --test --experimental-strip-types tests/popup-layout.test.ts tests/popup-copy.test.ts`

Expected: PASS with no further markup regressions.

### Task 4: Update required docs for the popup UI change

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document that the popup recommend tab header is now a unified light-weight info panel with grouped pool/runtime status cards, and note that reshuffle progress is surfaced inside the active-status block instead of a separate toolbar line.

**Step 2: Verify docs scope**

Run: `git diff -- docs/modules/extension.md docs/changelog.md`

Expected: Only recommendation-header UI notes.

### Task 5: Run final focused verification

**Files:**
- Verify only

**Step 1: Run extension tests**

Run: `cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts tests/popup-layout.test.ts tests/popup-copy.test.ts tests/popup-api.test.ts tests/popup-stream.test.ts`

Expected: PASS

**Step 2: Manual popup verification**

Check the popup recommend tab in the browser and verify:

- the header reads as one grouped panel
- clicking `换一批` does not introduce obvious layout jump
- enough-stock, replenished, live-runtime, and error states are all readable
- the three status cards wrap cleanly in the narrow popup width

**Step 3: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/popup/popup-helpers.js extension/tests/popup-layout.test.ts extension/tests/popup-copy.test.ts extension/tests/popup-helpers.test.ts docs/modules/extension.md docs/changelog.md docs/plans/2026-03-16-recommendation-header-panel-design.md docs/plans/2026-03-16-recommendation-header-panel.md
git commit -m "feat: refine popup recommendation header panel"
```
