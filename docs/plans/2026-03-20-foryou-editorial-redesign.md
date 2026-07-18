# For You Editorial Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refresh the extension side panel `For You` area so the recommendation flow feels more editorial, with clearer hierarchy and stronger content-first presentation.

**Architecture:** Keep the existing popup data flow and runtime wiring unchanged. Implement the redesign entirely in `extension/popup/popup.html` by updating the recommendation header/card markup and CSS, then lock the new structure with focused Node tests and update extension docs/changelog.

**Tech Stack:** HTML, CSS, vanilla JS popup runtime, Node built-in test runner

---

### Task 1: Lock the new recommendation layout in tests

**Files:**
- Modify: `extension/tests/popup-layout.test.ts`
- Test: `extension/tests/popup-layout.test.ts`

**Step 1: Write the failing test**

Add assertions for:

- a lighter recommendation header intro structure
- a dedicated summary row for pool status chips
- a recommendation card layout that uses explicit vertical sections for meta, title/copy, and actions

**Step 2: Run test to verify it fails**

Run: `npm test -- popup-layout.test.ts`
Expected: FAIL because the current popup markup/CSS still matches the older recommendation layout.

**Step 3: Write minimal implementation**

Update `extension/popup/popup.html` markup and CSS until the new assertions pass without changing popup runtime behavior.

**Step 4: Run test to verify it passes**

Run: `npm test -- popup-layout.test.ts`
Expected: PASS.

### Task 2: Implement the editorial-style recommendation UI

**Files:**
- Modify: `extension/popup/popup.html`
- Test: `extension/tests/popup-layout.test.ts`

**Step 1: Update recommendation header structure**

- tighten the header card padding and spacing
- reframe the intro copy into a lighter editorial block
- keep `换一批` visible but visually secondary to the content headline
- restyle pool status chips into a quieter summary strip

**Step 2: Update recommendation card hierarchy**

- rebalance preview grid proportions for the side panel width
- increase title priority and make description/meta more compact
- align actions into a cleaner bottom row
- reduce heavy gradients and decorative layers that compete with content

**Step 3: Verify responsive behavior**

Check the existing narrow-width media queries and adjust them so the new hierarchy holds on small side panel widths.

**Step 4: Run focused tests**

Run: `npm test -- popup-layout.test.ts popup-copy.test.ts popup-helpers.test.ts`
Expected: PASS.

### Task 3: Update required docs for extension UI changes

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update module documentation**

Document the refreshed `For You` visual hierarchy in the extension module doc so the recommendation tab description matches the shipped UI.

**Step 2: Update changelog**

Add one concise entry under the current milestone describing the editorial refresh for the recommendation panel.

**Step 3: Verify docs references**

Confirm the wording does not imply backend or API changes.

### Task 4: Verify the extension build remains healthy

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/tests/popup-layout.test.ts`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Run targeted tests**

Run: `npm test -- popup-layout.test.ts popup-copy.test.ts popup-helpers.test.ts`
Expected: PASS.

**Step 2: Run typecheck/build**

Run: `npm run typecheck`
Expected: PASS.

Run: `npm run build`
Expected: PASS and `extension/dist/` rebuilt successfully.
