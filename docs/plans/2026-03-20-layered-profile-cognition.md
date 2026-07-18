# Layered Profile Cognition Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make soul-profile generation and popup display reflect layered user cognition instead of topic-heavy preference paraphrases.

**Architecture:** Extend `SoulProfile` with structured cognition fields, feed `awareness + insights` into `ProfileBuilder`, tighten the soul-profile prompt around cognition-style / motivation / current-phase reasoning, then expose and render the new fields in the popup profile tab.

**Tech Stack:** Python, FastAPI, Pydantic, pytest, browser extension JavaScript

---

### Task 1: Lock the new soul-profile schema with failing tests

**Files:**
- Modify: `tests/test_profile_builder.py`
- Modify: `tests/test_soul_engine.py`

**Step 1: Write the failing test**

- Add a `ProfileBuilder` test expecting JSON payloads to include:
  - `cognitive_style`
  - `motivational_drivers`
  - `current_phase`
- Add a prompt-shape test expecting the system/user prompt to mention:
  - 不要把兴趣 topic 堆成画像主体
  - awareness / insights 会被一并注入
- Add a `SoulEngine` test expecting built profiles to persist the new fields.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_profile_builder.py tests/test_soul_engine.py -k "cognitive or layered or current_phase or motivational" -v`

**Step 3: Write minimal implementation**

- Extend `SoulProfile`
- Extend `ProfileBuilder.build()`
- Update `SoulEngine` call sites

**Step 4: Run test to verify it passes**

Run the same pytest command.

**Step 5: Commit**

```bash
git add tests/test_profile_builder.py tests/test_soul_engine.py src/openbiliclaw/soul/profile.py src/openbiliclaw/soul/profile_builder.py src/openbiliclaw/soul/engine.py src/openbiliclaw/llm/prompts.py
git commit -m "feat: add layered profile cognition schema"
```

### Task 2: Expose layered profile cognition through the API

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `tests/test_api_app.py`

**Step 1: Write the failing test**

- Extend `/api/profile-summary` tests to expect:
  - `cognitive_style`
  - `motivational_drivers`
  - `current_phase`

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_api_app.py -k "profile_summary" -v`

**Step 3: Write minimal implementation**

- Add the new fields to `ProfileSummaryResponse`
- Return them from `/api/profile-summary`

**Step 4: Run test to verify it passes**

Run the same pytest command.

**Step 5: Commit**

```bash
git add tests/test_api_app.py src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py
git commit -m "feat: expose layered profile cognition in api"
```

### Task 3: Render the new cognition layers in the popup

**Files:**
- Modify: `extension/popup/popup-helpers.js`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup.html`
- Modify: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**

- Extend popup helper normalization tests to expect:
  - `cognitive_style`
  - `motivational_drivers`
  - `current_phase`

**Step 2: Run test to verify it fails**

Run: `npm test -- extension/tests/popup-helpers.test.ts`

If repo has no unified npm script, run the existing JS test command used by the extension in this repo.

**Step 3: Write minimal implementation**

- Normalize the new fields in `popup-helpers.js`
- Render two new profile groups and a current-phase summary in `popup.js`
- Add the matching DOM containers in `popup.html`

**Step 4: Run test to verify it passes**

Run the same extension test command.

**Step 5: Commit**

```bash
git add extension/popup/popup-helpers.js extension/popup/popup.js extension/popup/popup.html extension/tests/popup-helpers.test.ts
git commit -m "feat: show layered profile cognition in popup"
```

### Task 4: Final verification and docs

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

- Document new soul profile fields and popup profile-tab behavior.

**Step 2: Run verification**

Run:

```bash
./.venv/bin/pytest tests/test_profile_builder.py tests/test_soul_engine.py tests/test_api_app.py -v
./.venv/bin/ruff check src/ tests/
```

Run the popup helper test command used in this repo as well.

**Step 3: Check git diff**

Run: `git diff --stat`

**Step 4: Commit**

```bash
git add docs/modules/soul.md docs/modules/extension.md docs/changelog.md
git commit -m "docs: describe layered profile cognition"
```
