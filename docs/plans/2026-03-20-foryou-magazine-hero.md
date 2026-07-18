# For You Magazine Hero Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade the `For You` recommendation flow so the first recommendation reads like a magazine-style hero card while the rest remain compact secondary cards.

**Architecture:** Keep popup runtime behavior unchanged and implement the redesign with CSS-first differentiation on the first recommendation card plus any minimal class/markup support needed in `popup.js`. Validate the new hierarchy with focused layout tests and refresh extension docs.

**Tech Stack:** HTML, CSS, vanilla JS popup renderer, Node built-in test runner

---

### Task 1: Lock the hero-card layout in tests

**Files:**
- Modify: `extension/tests/popup-layout.test.ts`
- Modify: `extension/tests/popup-copy.test.ts`

**Step 1: Write the failing test**

Add assertions that:

- the first recommendation card has dedicated hero selectors
- the hero preview uses a single-column cover-first layout
- the header still has no descriptive note copy

**Step 2: Run test to verify it fails**

Run: `npm test -- popup-layout.test.ts popup-copy.test.ts`
Expected: FAIL because the first card still follows the same compact layout as the rest.

**Step 3: Write minimal implementation**

Add the CSS and minimal renderer hook needed for the first recommendation card to expose the hero treatment.

**Step 4: Run test to verify it passes**

Run: `npm test -- popup-layout.test.ts popup-copy.test.ts`
Expected: PASS.

### Task 2: Implement the magazine hero recommendation card

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`

**Step 1: Add first-card differentiation**

- mark the first rendered recommendation card as the hero card
- keep the rest of the cards unchanged structurally

**Step 2: Restyle the hero card**

- expand the cover area
- strengthen headline typography
- compress meta/tags into eyebrow-style elements
- align the action row like an editorial footer

**Step 3: Preserve responsive behavior**

Ensure the hero card still collapses cleanly for narrow side panel widths without horizontal overflow.

### Task 3: Refresh docs

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update extension module docs**

Describe that the recommendation flow now promotes the first item into a hero-style cover card.

**Step 2: Update changelog**

Add one short entry describing the new magazine-style hero card treatment.

### Task 4: Verify the extension package

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Modify: `extension/tests/popup-layout.test.ts`
- Modify: `extension/tests/popup-copy.test.ts`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Run targeted tests**

Run: `npm test -- popup-layout.test.ts popup-copy.test.ts`
Expected: PASS.

**Step 2: Run full extension checks**

Run: `npm test`
Expected: PASS.

Run: `npm run typecheck`
Expected: PASS.

Run: `npm run build`
Expected: PASS.
