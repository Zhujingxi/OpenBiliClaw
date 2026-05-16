# Awareness Analyzer Resilience Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop the awareness pass from failing every cognition cycle, restore prompt-cache hit rate on the awareness call, and ensure a single bad LLM response does not silently disable the awareness window for the next 12 hours.

**Architecture:** Three localized changes — parser tolerance in `awareness_analyzer`, prompt-cache regression coverage for the already stable-first `prompts.build_awareness_prompt`, and a retry-with-state-preservation policy in `cognition_cycle._run_awareness`. No public API changes; no migrations.

**Tech Stack:** Python (existing soul/llm modules), pytest, ruff, mypy strict.

---

### Task 1: Tolerate Singular-Note and Expanded Wrapper Keys

**Files:**
- Modify: `src/openbiliclaw/soul/awareness_analyzer.py`
- Test: `tests/test_awareness_analyzer.py`

**Step 1: Write failing tests**

Add tests for `_coerce_note_list` proving:
- A dict with a single note shape (`{"date": "...", "observation": "...", "trend": "...", "emotion_guess": "..."}`) is wrapped into a one-element list.
- A dict under a new wrapper key (`observations`, `recent_observations`, `latest`, `latest_observations`) returns the inner list.
- A dict wrapping a single note dict under a known wrapper key (e.g. `{"notes": {"date": "...", "observation": "..."}}`) is wrapped into a one-element list.
- Garbage shapes still return `None` (scalar, empty dict, dict with only unrelated keys).
- A fixture in `tests/fixtures/awareness_singular_note.json` is consumed end-to-end via `_parse_response` and returns one `AwarenessNote`.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_awareness_analyzer.py -q
```

Expected: new cases fail because `_coerce_note_list` only handles the existing wrapper-key set.

**Step 3: Implement parser tolerance**

In `awareness_analyzer.py`:
- Extend `_AWARENESS_WRAPPED_ARRAY_KEYS` with `"observations"`, `"recent_observations"`, `"latest"`, `"latest_observations"`.
- Add a private `_NOTE_SHAPE_KEYS = frozenset({"date", "observation", "trend", "emotion_guess"})`. The set is used as documentation of the schema and for the parse test fixture; the runtime check only needs `observation`.
- Add a helper `_looks_like_single_note(value: object) -> bool` that returns `True` iff `value` is a dict that contains the key `"observation"` (the only field whose absence makes the note worthless — everything else is recoverable with sensible defaults in `_build_note`).
- Update `_coerce_note_list`:
  - If `value` is a list, return as today.
  - If `value` is a dict, scan wrapper keys; if a wrapped value is itself a dict that looks like a single note, return `[wrapped_value]` instead of giving up.
  - If no wrapper key matched but `value` itself looks like a single note, return `[value]`.
  - Otherwise return `None`.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/awareness_analyzer.py tests/test_awareness_analyzer.py tests/fixtures/awareness_singular_note.json
git commit -m "fix(soul): awareness parser tolerates singular-note and expanded wrapper keys"
```

---

### Task 2: Lock Awareness Prompt Cache Hygiene

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py`
- Test: `tests/test_llm_prompts.py`

**Step 1: Audit current prompt shape**

Inspect `src/openbiliclaw/llm/prompts.py`. The current implementation should already:
- Define `_AWARENESS_SYSTEM_PROMPT` as a module-level constant.
- Emit user blocks in the order `<soul_profile>`, `<preference_summary>`, `<recent_events>`.
- Use `sort_keys=True` for the JSON dumps. Keep it on the events block too; it only stabilizes dict key order inside each event and does not reorder the event list.

**Step 2: Write or tighten regression tests**

Add tests proving:
- `build_awareness_prompt(...)` returns a system message that is byte-equal to `_AWARENESS_SYSTEM_PROMPT`.
- The builder remains in `_builder_test_inputs()` used by `test_prompt_builder_system_messages_are_call_invariant`, with two distinct event/preference/profile inputs.
- `test_build_awareness_prompt_user_block_is_stable_first` asserts:
  - The user message text starts with `<soul_profile>`.
  - `<preference_summary>` appears before `<recent_events>`.
  - `<recent_events>` is the final block.
- `test_build_awareness_prompt_serialization_is_deterministic` asserts that calling the builder twice with the same semantic payload but differently ordered dict keys produces the same user-message bytes. This validates `sort_keys=True` on profile, preference, and event object keys.

**Step 3: Run tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_llm_prompts.py -q
```

Expected: tests pass or fail only on missing regression assertions. If the source already satisfies the contract, do not rewrite the prompt builder.

**Step 4: Commit**

```bash
git add src/openbiliclaw/llm/prompts.py tests/test_llm_prompts.py
git commit -m "test(llm): lock awareness prompt stable-first cache shape"
```

---

### Task 3: Preserve Awareness Schedule on Transient Failure

**Files:**
- Modify: `src/openbiliclaw/soul/cognition_cycle.py`
- Test: `tests/test_cognition_cycle.py`

**Step 1: Write failing tests**

Add tests proving:
- When `awareness_analyzer.analyze` raises `AwarenessGenerationError` on the first call but succeeds on the second, `_run_awareness` returns the added count from the second call and `last_awareness_at` is set to `now`.
- When both calls raise `AwarenessGenerationError`, `run_if_due` records the error, logs at `WARNING` (not `ERROR`), and `state["last_awareness_at"]` is **not** updated, so a subsequent `run_if_due` call after one minute will re-attempt awareness instead of waiting the full throttle.
- The retry adds at most one extra LLM call per cycle (assert via a counting fake analyzer).

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run --extra dev python -m pytest tests/test_cognition_cycle.py -q
```

Expected: the retry and state-preservation tests fail.

**Step 3: Implement retry policy**

In `cognition_cycle.py`:
- In `_run_awareness`, wrap the `await self._awareness_analyzer.analyze(...)` call in a small inline retry: try once, on `AwarenessGenerationError` await `asyncio.sleep(2)` and try again, then re-raise on second failure.
- In `run_if_due`'s awareness branch:
  - Catch `AwarenessGenerationError` separately from generic `Exception`.
  - On `AwarenessGenerationError`: `logger.warning("Awareness analyzer failed twice; will retry next tick: %s", exc)`, append to `result.errors`, and **do not** set `state["last_awareness_at"]`.
  - Generic `Exception` retains today's behavior (`logger.exception` + skip schedule update).
- No change to insight handling.

**Step 4: Run tests to verify pass**

Run the same pytest command and confirm pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/cognition_cycle.py tests/test_cognition_cycle.py
git commit -m "fix(soul): single retry + preserve schedule when awareness fails transiently"
```

---

### Task 4: Docs + Changelog

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/llm.md`
- Modify: `docs/changelog.md`

**Step 1: Update soul module doc**

Update the awareness analyzer section in `docs/modules/soul.md`:
- Document the expanded parser tolerance contract (singular-note, expanded wrapper keys, dict-wrapped-singular).
- Document the cognition-cycle retry policy and the rule that `last_awareness_at` is not advanced on a still-failing run.

**Step 2: Update llm module doc**

In `docs/modules/llm.md`, in the prompt-builder table / public API section, note that `build_awareness_prompt` now follows the stable-first user-message ordering and that its system message is extracted into `_AWARENESS_SYSTEM_PROMPT`.

**Step 3: Update changelog**

Add a bullet under the current version block in `docs/changelog.md`:
- `fix(soul): awareness parser handles singular-note and more wrapper keys; prompt-cache shape is regression-locked; single retry preserves cycle schedule on transient failure`.

**Step 4: Commit**

```bash
git add docs/modules/soul.md docs/modules/llm.md docs/changelog.md
git commit -m "docs: awareness resilience changes"
```

---

## Verification

Run the full Python test suite once at the end:

```bash
uv run --extra dev python -m pytest -q
```

And confirm in a live install (or via mocked replay) that:
- A captured MiMo singular-note response is parsed instead of raising.
- The next cognition cycle after a forced failure re-attempts within one tick rather than 12 hours.
- `openbiliclaw cost --by caller` shows `soul.awareness` cache hit rate climb from ~1 % to well above its previous floor on the second and subsequent calls.
