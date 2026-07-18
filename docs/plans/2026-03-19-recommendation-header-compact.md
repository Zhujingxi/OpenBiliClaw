# Recommendation Header Compact Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Compress the popup recommendation header into a tighter two-layer content-first header so more recommendation cards are visible above the fold, while keeping three status items and the reshuffle action readable.

**Architecture:** Keep the recommend-tab data flow and runtime event handling intact, but restyle the header into a compact top row plus a chip row. The helper copy for pool status will be shortened to fit chip-sized presentation, and popup rendering will keep using the same status IDs and runtime priority rules.

**Tech Stack:** Inline popup HTML/CSS, vanilla JavaScript, Node test runner, Markdown docs

---

### Task 1: Lock the compact header structure with failing layout/copy tests

**Files:**
- Modify: `extension/tests/popup-layout.test.ts`
- Modify: `extension/tests/popup-copy.test.ts`

**Step 1: Write the failing tests**

Assert that the recommend header now uses:

- a compact title row instead of a large intro block
- a chip row instead of a three-column status grid
- no large status-card min-height treatment

**Step 2: Run test to verify it fails**

Run: `cd extension && node --test --experimental-strip-types tests/popup-layout.test.ts tests/popup-copy.test.ts`

Expected: FAIL because the current markup and CSS still describe a larger card-style header.

**Step 3: Write minimal implementation**

Update the header markup/CSS in `extension/popup/popup.html` to:

- tighten top-row spacing
- inline the kicker with the title
- replace the grid/card status section with compact chips

**Step 4: Run test to verify it passes**

Run: `cd extension && node --test --experimental-strip-types tests/popup-layout.test.ts tests/popup-copy.test.ts`

Expected: PASS

### Task 2: Lock compact chip copy with failing helper tests

**Files:**
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `extension/popup/popup-helpers.js`

**Step 1: Write the failing tests**

Update helper expectations so pool status copy is shorter and chip-friendly:

- `还有 N 条可换`
- `刚补进 N 条`
- `这会儿先不补货`
- 更短的实时内容本体

Keep runtime/loading priority behavior unchanged.

**Step 2: Run test to verify it fails**

Run: `cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts`

Expected: FAIL because current helper copy is still panel-length.

**Step 3: Write minimal implementation**

Shorten `getPoolStatusSummary()` output and keep `getDisplayedPoolStatusSummary()` runtime override intact.

**Step 4: Run test to verify it passes**

Run: `cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts`

Expected: PASS

### Task 3: Preserve rendering behavior while making chips readable

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup.html`

**Step 1: Keep runtime rendering intact**

Ensure the popup still renders available/replenished/topics into the same DOM IDs, but with chip-friendly layout and optional tooltip/title attributes for long content.

**Step 2: Run focused verification**

Run: `cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts tests/popup-layout.test.ts tests/popup-copy.test.ts`

Expected: PASS

### Task 4: Update required docs

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document that the recommendation header was compressed into a two-layer content-first header with compact status chips.

**Step 2: Verify docs scope**

Run: `git diff -- docs/modules/extension.md docs/changelog.md`

Expected: Only recommendation-header notes.

### Task 5: Final verification

**Files:**
- Verify only

**Step 1: Build and run focused tests**

Run: `cd extension && npm run build && node --test --experimental-strip-types tests/popup-helpers.test.ts tests/popup-layout.test.ts tests/popup-copy.test.ts tests/popup-api.test.ts tests/popup-stream.test.ts`

Expected: PASS

**Step 2: Browser verification**

Open the popup in a browser and confirm:

- the header is visibly shorter
- the first recommendation card appears higher on the screen
- chips remain readable in the popup width
- `换一批` still works without layout breakage

**Step 3: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/popup/popup-helpers.js extension/tests/popup-layout.test.ts extension/tests/popup-copy.test.ts extension/tests/popup-helpers.test.ts docs/modules/extension.md docs/changelog.md docs/plans/2026-03-19-recommendation-header-compact-design.md docs/plans/2026-03-19-recommendation-header-compact.md
git commit -m "feat: compact popup recommendation header"
```
