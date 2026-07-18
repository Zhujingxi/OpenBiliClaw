# Awareness Analyzer Resilience Design

## Goal

Stop the awareness pass from failing every cognition cycle, and recover the prompt-cache hit rate so each successful call is not a ~¥0.65 cold-prefix spend. Awareness is the meta-supervision layer that lets the soul engine notice "user preference is drifting toward passive-engagement content"; while it is failing, every downstream nudge (negative-signal filtering, dislike anchoring) has nothing to act on.

## Current Gaps

- A 6-hour log capture from a live MiMo-backed install shows **569 consecutive `Awareness analyzer failed during cognition cycle`** errors, all raising `AwarenessGenerationError: LLM awareness response must be a JSON array.` (`src/openbiliclaw/soul/awareness_analyzer.py:118`).
- `_coerce_note_list` already handles a fixed set of wrapper keys (`results / items / notes / awareness_notes / awareness / data / output / list / array`), but MiMo's reasoning output for this prompt typically returns either a singular note (`{"date": "...", "observation": "..."}`) or a dict under an unmatched key like `observations` / `recent_observations`. Neither shape is recoverable today, so the analyzer hard-fails and rolls back the whole tick.
- The awareness call sends ~36k input tokens with **cache_hit = 512 / 36213 (1 %)** per log line. The current tree already has the desired prompt-cache shape in `build_awareness_prompt` (`soul_profile → preference_summary → recent_events`) and a static `_AWARENESS_SYSTEM_PROMPT`; this plan should preserve that shape and add/keep regression coverage rather than rewriting the builder. If a live install still shows 1% cache hit, first verify it is running the current prompt builder.
- `cognition_cycle._run_awareness` raises straight through to `run_if_due`, which catches with `logger.exception` and continues. A single bad LLM response therefore takes out the awareness pass for an entire 12-hour throttle window even though the next call would likely succeed.
- There is no test that asserts the analyzer survives the response shapes MiMo and other reasoning models actually emit; `tests/test_awareness_analyzer.py` only covers the happy-path JSON array.

## Chosen Approach

Make the parser **strictly more tolerant** without weakening output validation, **preserve the existing prompt-cache convention with regression tests**, and **make failures isolated** so one bad response does not blank the awareness window.

Two behavior changes plus one prompt-cache guardrail, in one phase:

1. **Parser tolerance.** Extend `_coerce_note_list` to:
   - Recognize the singular-note case: if `value` is a dict whose keys overlap the note schema (`date`, `observation`, `trend`, `emotion_guess`) and it does not match any wrapper key, wrap it in `[value]`.
   - Expand the wrapper-key list to include `observations`, `recent_observations`, `latest`, `latest_observations`.
   - When the wrapped value is itself a dict (not a list) that looks like a single note, wrap that too.
   - All other shapes still raise `AwarenessGenerationError` so genuine garbage is not silently absorbed.

2. **Prompt cache guardrail.** In `build_awareness_prompt`:
   - Keep the existing user-message order `soul_profile → preference_summary → recent_events`. Profile/preference change at most once per profile rebuild; events change every call.
   - Keep `sort_keys=True` on all JSON blocks, including the events block. This does not reorder the event list itself; it only stabilizes each event object's key order and improves cache determinism.
   - Keep returning the module-level `_AWARENESS_SYSTEM_PROMPT` as-is. The task here is to add/retain regression tests so future edits do not put volatile events before stable profile data again.

3. **Failure isolation.** In `cognition_cycle._run_awareness`:
   - Wrap the analyzer call in a single retry with exponential backoff (1 retry, +2s). MiMo 502s and transient JSON-shape glitches will clear on a re-call.
   - On retry exhaustion, log at `WARNING` (not `ERROR.exception`) and return 0 added notes, leave `state["last_awareness_at"]` **unchanged** so the next tick re-attempts instead of waiting 12 hours.

## Data Flow

1. `cognition_cycle.run_if_due()` decides awareness is due.
2. `_run_awareness()` reads events + preference + soul_profile from memory.
3. `awareness_analyzer.analyze()` calls `build_awareness_prompt()` with the existing stable→variable user-message ordering.
4. The LLM responds; `_parse_response` runs `parse_llm_json_tolerant` then `_coerce_note_list` (now accepts singular-note, expanded wrapper keys, and dict-wrapped-singular shapes).
5. On success: notes are merged and persisted as before.
6. On `AwarenessGenerationError`: one retry; if still failing, log warning and leave `last_awareness_at` untouched so the next tick retries naturally.

## Error Handling

- Parse failures that are not absorbed by the expanded tolerance still raise `AwarenessGenerationError` so we do not silently invent notes.
- A retried-and-still-failing run does not advance `last_awareness_at`, so the throttle does not punish the user with a 12-hour gap after a single transient failure.
- Existing handling in `run_if_due` (catch exception, append to `result.errors`, continue) is preserved as the outer safety net.

## Testing

- Unit tests for `_coerce_note_list` covering: list, dict with each known wrapper key, dict with new keys (`observations`, `recent_observations`), singular-note dict, dict-wrapped-singular, scalar (still raises), empty dict (still raises).
- Keep the `test_prompt_builder_system_messages_are_call_invariant` entry for `build_awareness_prompt`.
- Add/keep a `test_build_awareness_prompt_user_block_is_stable_first` assertion that the user message starts with `<soul_profile>`, puts `<preference_summary>` before `<recent_events>`, and ends with the events block.
- Add a serialization determinism test that differently ordered dict keys produce byte-identical user messages. This should pass with the current `sort_keys=True` usage.
- A cognition-cycle test that simulates an analyzer raising `AwarenessGenerationError` and asserts `last_awareness_at` is unchanged after the failed retry, so the next tick will retry.
- A fixture-based regression test that feeds a real captured MiMo response (singular-note dict) through `_parse_response` and asserts it succeeds.

## Documentation

Update `docs/modules/soul.md` (awareness analyzer's tolerance contract + retry policy), `docs/modules/llm.md` (document that `build_awareness_prompt` is guarded by the stable-first prompt-cache convention), and `docs/changelog.md` (`fix(soul): awareness parser tolerates more JSON shapes; retry preserves cycle schedule on transient failure`).
