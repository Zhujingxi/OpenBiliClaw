# Interest Probe Diversity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep speculative probes bold while preventing the active probe set and push order from collapsing into one experience axis.

**Architecture:** Extend speculative candidates with user-perceived diversity tags, oversample generation, apply a local balanced selector before candidates enter the active pool, then add lightweight experience-axis dedupe when choosing the next probe to push or return through OpenClaw.

**Tech Stack:** Python, FastAPI, pytest

---

### Task 1: Lock the diversity regression in the speculator

**Files:**
- Modify: `tests/test_speculator.py`
- Modify: `src/openbiliclaw/soul/speculator.py`
- Modify: `src/openbiliclaw/llm/prompts.py`

**Step 1: Write the failing test**

- Add a speculator regression test showing that when the model returns several heavy `knowledge` candidates plus a smaller number of `light` / non-knowledge candidates, the final active additions should still include a lighter and non-knowledge mix.
- Add a parser/defaulting test showing missing `experience_mode` / `entry_load` fields do not break generation.

**Step 2: Run test to verify it fails**

Run: `uv run --extra dev python -m pytest tests/test_speculator.py -k "diverse_active_selection or defaults_missing_experience_fields" -v`

Expected: FAIL because the current speculator directly appends generated candidates and has no diversity-aware selection.

**Step 3: Write minimal implementation**

- Extend `SpeculativeInterest` with `experience_mode` and `entry_load`.
- Expand the speculation prompt output schema to request those fields with a small fixed enum.
- Update `_generate()` to parse an oversampled batch and feed it into a local balanced selector before appending to `state.active`.
- Implement a selector that favors confidence but penalizes repeated `experience_mode` / `entry_load`, with graceful fallback when model output is sparse.

**Step 4: Run test to verify it passes**

Run the same targeted pytest command.

**Step 5: Commit**

```bash
git add tests/test_speculator.py src/openbiliclaw/soul/speculator.py src/openbiliclaw/llm/prompts.py docs/plans/2026-04-23-interest-probe-diversity-design.md docs/plans/2026-04-23-interest-probe-diversity.md
git commit -m "fix: diversify speculative probe candidates"
```

### Task 2: Lock probe ordering so push does not repeat one experience axis

**Files:**
- Modify: `tests/test_openclaw_adapter.py`
- Modify: `tests/test_api_app.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/integrations/openclaw/operations.py`

**Step 1: Write the failing test**

- Add an adapter test showing `get_next_probe()` should prefer a candidate on a different experience axis when two probes have equal confirmation pressure.
- Add a runtime/controller regression test showing probe push should skip a recently pushed candidate if it repeats the same `experience_mode` / `entry_load` and another viable option exists.

**Step 2: Run test to verify it fails**

Run: `uv run --extra dev python -m pytest tests/test_openclaw_adapter.py tests/test_api_app.py -k "probe_prefers_fresher_axis or interest_probe_skips_recent_axis_repeat" -v`

Expected: FAIL because current probe selection only sorts by `confirmation_count` and `weight`.

**Step 3: Write minimal implementation**

- Add small ranking helpers shared by runtime and OpenClaw selection.
- Persist a short probe-axis history in runtime state alongside recent probed domains.
- Prefer candidates from a different `experience_mode` / `entry_load` when available, while keeping fallback to the old behavior when diversity metadata is absent.

**Step 4: Run test to verify it passes**

Run the same targeted pytest command.

**Step 5: Commit**

```bash
git add tests/test_openclaw_adapter.py tests/test_api_app.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/integrations/openclaw/operations.py
git commit -m "fix: diversify interest probe ordering"
```

### Task 3: Verify speculative flows and update docs

**Files:**
- Modify: `tests/test_openclaw_proactive_e2e.py`
- Modify: `docs/modules/soul.md`
- Modify: `docs/changelog.md`

**Step 1: Write the failing test**

- Extend the proactive probe E2E fixture so pushed probes carry the new diversity metadata and the selected probe reflects the new balanced-selection path.

**Step 2: Run test to verify it fails**

Run: `uv run --extra dev python -m pytest tests/test_openclaw_proactive_e2e.py -v`

Expected: FAIL because fixtures and expectations do not yet include the new speculative diversity behavior.

**Step 3: Write minimal implementation**

- Update E2E fixtures and assertions for `experience_mode` / `entry_load`.
- Document the new diversity strategy and probe-selection behavior in the soul module doc and changelog.

**Step 4: Run test to verify it passes**

Run: `uv run --extra dev python -m pytest tests/test_openclaw_proactive_e2e.py -v`

**Step 5: Commit**

```bash
git add tests/test_openclaw_proactive_e2e.py docs/modules/soul.md docs/changelog.md
git commit -m "docs: describe interest probe diversity strategy"
```

### Task 4: Final verification

**Files:**
- Test: `tests/test_speculator.py`
- Test: `tests/test_openclaw_adapter.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_openclaw_proactive_e2e.py`

**Step 1: Run focused verification**

Run: `uv run --extra dev python -m pytest tests/test_speculator.py tests/test_openclaw_adapter.py tests/test_api_app.py tests/test_openclaw_proactive_e2e.py -v`

**Step 2: Run lint on touched files**

Run: `uv run --extra dev python -m ruff check src/openbiliclaw/soul/speculator.py src/openbiliclaw/llm/prompts.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/integrations/openclaw/operations.py tests/test_speculator.py tests/test_openclaw_adapter.py tests/test_api_app.py tests/test_openclaw_proactive_e2e.py`

**Step 3: Final verification**

Run both commands again after any fixups and confirm they pass cleanly.
