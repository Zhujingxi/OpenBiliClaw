# Probe Defer — Implementation Plan

> **Spec:** [`2026-07-05-probe-defer-spec.md`](./2026-07-05-probe-defer-spec.md)
> **Status:** Draft r1 — 2026-07-05, pending codex review loop.
> **Execution order:** Task 1 → 2 → 3 → 4 → 5 → 6; Task 7 (E2E acceptance) after all green.
> Tasks 1–2 are pure speculator-layer and independent of 3; 3 can be done before or after 1–2
> but MUST land before 4 (the API chat branches depend on the new label).
> **Tech:** Python 3.11+, pytest (`asyncio_mode=auto`), Ruff, MyPy strict, 100-char lines.
> Interpreter is `.venv/bin/python` (plain `python`/`python3` has no deps).
> Per task: `.venv/bin/python -m pytest <touched test files> -q`, then
> `.venv/bin/python -m ruff check` / `ruff format --check` on touched files, then
> `.venv/bin/python -m mypy src/openbiliclaw/`.

**Invariants that MUST hold (from Spec §Invariants — re-read before each task):**
- deferred is invisible on every read path until revival; revival only in tick/force_tick,
  **ordered after `promote_ready`** in `_prepare`.
- `defer_count` never resets; 3rd defer = TTL-style exhaustion (status=rejected + 30-day cooldown,
  `response="defer_exhausted"`), re-guessable after cooldown — NOT a durable user-reject.
- `"defer"`/`"defer_exhausted"` never enter `HANDLED_PROBE_FEEDBACK_RESPONSES` (explicit `reject`
  still does).
- A threshold-ready-at-defer item does not silently auto-promote on revival: clamp
  `confirmation_count` to `threshold - 1` on revival.
- confirm / reject / plain-neutral / TTL paths byte-identical to today.
- `normalize_probe_feedback_history` extended to preserve `deferred_until` + `defer_count`.
- Prompt-cache convention for the migrated sentiment builder; invariance test updated.
- **Concurrent-session hygiene:** the working tree is shared with other sessions. Only touch the
  files listed per task; when committing, stage the explicit file list for THIS feature only.

---

### Task 1: Interest speculator defer core

**Files:** `src/openbiliclaw/soul/speculator.py`; tests in `tests/test_speculator.py`

**Steps:**
1. Failing tests first:
   - `SpeculativeInterest` round-trips `deferred_at`/`deferred_until`/`defer_count`; a dict
     *without* those keys loads with defaults (old state files).
   - `user_defer_speculation("X")` on an active item → `outcome="deferred"`, status `deferred`,
     `deferred_until ≈ now + 7d`, `defer_count == 1`; item absent from
     `get_active_speculations()`.
   - Second defer (after simulated revival) → 14d window, `defer_count == 2`.
   - Third defer → `outcome="exhausted"`, status `rejected`, a `CooldownEntry` for the domain,
     no `deferred` remnant; the feedback response recorded is `defer_exhausted` and it is NOT in
     `HANDLED_PROBE_FEEDBACK_RESPONSES` (so a re-generation after the cooldown lapses can re-guess
     the domain — assert re-guessable, contrasting with an explicit `reject` which stays blocked).
   - Defer on unknown/non-active domain → `outcome="not_found"`, state unchanged.
   - Revival: item with `deferred_until` in the past → after `tick()`'s maintenance pass it is
     `active` again, `created_at` reset to now, `deferred_at`/`deferred_until` cleared,
     `defer_count` preserved; with `deferred_until` in the future it stays `deferred`.
   - **Revive-after-promote ordering:** a `deferred` item with `confirmation_count >= threshold`
     and `deferred_until` in the past → after a full `tick()` it is `active` (revived), NOT
     `promoted`, and its `confirmation_count` is clamped to `threshold - 1`. (Build the item so
     it would promote if revive ran before `promote_ready`; the test fails if ordering is wrong.)
   - `expire_stale` on a long-past-TTL `deferred` item → untouched (no cooldown, no reject).
   - Generation dedup: a `deferred` domain is filtered from new candidates (drive whichever
     selection helper consumes `existing_domains`); novelty guard contains the deferred domain's
     terms.
   - Feedback-history round-trip: an entry carrying `deferred_until` (str) + `defer_count` (int)
     survives `append_probe_feedback_history` → `normalize_probe_feedback_history` (currently
     dropped by the string-only whitelist).
2. Implement:
   - Dataclass fields + `to_dict`/`from_dict` (defaults as in spec).
   - Module constants `PROBE_DEFER_DAYS = (7, 14)`, `PROBE_MAX_DEFERS = 3`, and a small
     `DeferResult` dataclass (`outcome`, `deferred_until`, `defer_count`).
   - `Speculator.user_defer_speculation(domain)` next to `user_reject_speculation`
     (`speculator.py:1123`), including the exhaustion branch. Exhaustion reuses the
     `expire_stale` mutation shape (status→`rejected` + append a 30-day `CooldownEntry`), NOT the
     API-layer handled-feedback recording — the `defer_exhausted` feedback response is written by
     the API layer (Task 4) and stays out of the handled set.
   - Pure helper `revive_deferred(state, now) -> tuple[list[SpeculativeInterest], SpeculativeState]`
     next to `expire_stale` (`speculator.py:592`); on revival set `status="active"`,
     `created_at=now`, clear `deferred_at`/`deferred_until`, keep `defer_count`, and clamp
     `confirmation_count = min(confirmation_count, confirmation_threshold - 1)`. Call it inside the
     `_prepare` closure of BOTH `tick()` (`:849`) and `force_tick()` (`:919`), **positioned after
     the `promote_ready` call** so revived items are never promoted in the same pass.
   - Audit and extend status sets: `grep -n 'status in {' src/openbiliclaw/soul/speculator.py`
     — add `"deferred"` to the `existing_domains` dedup allowlist (`:806`) and any other site
     where a deferred item must count as "occupied domain" but not as "active". (The
     `{s.domain for s in state.active}` sites at `:1037`/`:1242` already cover all statuses — add a
     test asserting that rather than changing them.)
   - Extend `normalize_probe_feedback_history` (`speculator.py:348-386`): preserve `deferred_until`
     as a string field, and add `defer_count` with int handling (the current loop is
     `_string_field`-only — add a small int-coercion branch). Do NOT touch any existing whitelisted
     field.
3. `.venv/bin/python -m pytest tests/test_speculator.py -q` + lint + mypy.

### Task 2: Avoidance speculator defer (symmetric)

**Files:** `src/openbiliclaw/soul/avoidance_speculator.py`; tests in
`tests/test_avoidance_speculator.py`

**Steps:**
1. Mirror Task 1's tests for `SpeculativeAvoidance` / `user_defer_avoidance` / revival, including
   the `confirmation_count` clamp edge. **Ordering is stricter than interest:** avoidance
   `_prepare` runs `expire_stale_avoidances` → `promote_ready_avoidances` →
   `compact_redundant_active_avoidances` (`:1027`, `:1091`), and compaction can reject an active
   item. `revive_deferred` must be the **LAST** step (after compaction), else a freshly-revived
   avoidance can be compacted/rejected in the same tick. **Add a test proving a revived avoidance
   is NOT compacted in the same tick** (construct a revived item that would be seen as redundant by
   `compact_redundant_active_avoidances` if it had been present before the compaction step).
   Note avoidance `ttl_days` defaults to **3** (`:148`) — revived avoidance items get a fresh
   3-day window; assert that.
2. Implement symmetrically; share the ladder semantics (constants may be imported from
   `speculator.py` or duplicated — follow whichever pattern the two modules already use for
   shared shapes; they currently duplicate, so duplicate). **Add `"deferred"` to the
   `existing_domains` status-allowlist at `avoidance_speculator.py:919`** (the exact symmetric
   site to interest `:806`). The `{item.domain for item in state.active}` site at `:1228` already
   covers all statuses — assert in a test rather than changing it. Extend the avoidance feedback
   normalizer if it has its own whitelist (`grep -n 'normalize_.*feedback\|whitelist' ` in the
   avoidance module); if it reuses `speculator.py`'s normalizer, Task 1's extension already covers
   it — confirm which and note it. **Add the symmetric handled-set assertion:** avoidance has its
   own `HANDLED_AVOIDANCE_RESPONSES` (`avoidance_speculator.py:37`, consumed by the novelty guard
   at `:350`); assert neither `"defer"` nor `"defer_exhausted"` is in it, mirroring Task 4's
   interest-side assertion.
3. Tests + lint + mypy.

### Task 3: Sentiment prompt migration + `neutral_deferred` label

**Files:** `src/openbiliclaw/llm/prompts.py`, `src/openbiliclaw/api/app.py`
(`_llm_judge_sentiment` `:4835`, `_keyword_judge_sentiment` `:4803`,
`_classify_probe_sentiment` `:4789`); tests in `tests/test_llm_prompts.py`,
`tests/test_api_app.py`

**Steps:**
1. Failing tests:
   - New builder registered in `test_llm_prompts.py::_builder_test_inputs()` — system message
     byte-identical across two distinct (domain, message) inputs.
   - Keyword classification via the API layer (monkeypatched/absent LLM): 「先放着吧」→
     `neutral_deferred`; 「不确定」→ `neutral`(unchanged); 「不想再看看这个了」→ `negative`
     (deferred terms must not shadow negatives); existing positive/negative fixtures unchanged.
2. Implement:
   - `llm/prompts.py`: module constant `_PROBE_SENTIMENT_SYSTEM_PROMPT` (current inline text +
     `neutral_deferred` as a 5th label with 判断标准 line 「用户主动要求先放一放：『暂时忽略』
     『先放着』『稍后再看』」; plain `neutral` keeps 「态度不明确」) and builder
     `build_probe_sentiment_prompt(domain, user_message)` following the module's existing
     builder shape; ALL variables in the user message.
   - `_llm_judge_sentiment`: consume the builder, `max_tokens` 8 → 16, accepted-word set gains
     `neutral_deferred` only.
   - `_keyword_judge_sentiment`: add `deferred_terms` (spec D6 list), checked **after**
     `negative_terms`, before the positive sets return... order: negative → deferred →
     strong_positive → weak_positive → neutral. (Negative first per spec; deferred before
     positives because 「先放着」 contains no positive term anyway — keep the diff minimal and
     the tests authoritative.)
   - `_classify_probe_sentiment`: accepted LLM set gains `neutral_deferred`; keyword result
     `neutral_deferred` propagates with classifier `"keyword"`; everything else unchanged.
3. Tests + lint + mypy.

### Task 4: API endpoints + chat branches

**Files:** `src/openbiliclaw/api/app.py`; tests in `tests/test_api_app.py`

**Steps:**
1. Failing tests (fake speculator objects, as existing probe tests do):
   - `POST /api/interest-probes/respond {response:"defer"}` → 200 `{action:"deferred",
     deferred_until, defer_count}`; speculator's defer method called once; feedback history entry
     `response="defer"` with metadata captured pre-transition; `interest.deferred` event
     published; cognition recorded.
   - Exhausted variant → `{action:"defer_exhausted"}` + `interest.rejected` event; feedback entry
     `response="defer_exhausted"` and an assertion that `"defer_exhausted"` is NOT in
     `HANDLED_PROBE_FEEDBACK_RESPONSES` (guards the TTL-style contract).
   - Feedback entry for the deferred outcome carries `deferred_until` and `defer_count` and they
     survive into runtime state (depends on Task 1's normalizer extension).
   - `not_found` → `{ok: false}`.
   - Same trio for `/api/avoidance-probes/respond` (`state_key="avoidance_probe_feedback_history"`).
   - Validation: `response="bogus"` 422 message now lists `defer`.
   - Chat: sentiment stub returning `neutral_deferred` → defer method called, summary copy
     「先放一放」, plain `neutral` chat behaves exactly as before (regression assertion on the
     existing neutral fixture).
2. Implement per spec §API surface: defer branch in both respond endpoints (mirroring the reject
   branch's structure and metadata ordering), `neutral_deferred` branch at all four
   sentiment-branch sites (`:5078`, `:5129`, `:5457`, `:5682` — durable + synchronous, interest +
   avoidance), summary/cognition/event copy from spec.
3. Tests + lint + mypy.

### Task 5: Frontend (desktop web + mobile web view-models)

**Files:** `src/openbiliclaw/web/desktop/assets/js/app.js`,
`src/openbiliclaw/web/desktop/assets/css/app.css`, `src/openbiliclaw/web/js/view-models.js`;
tests: `tests/test_mobile_web_view_models.py`, new `tests/test_desktop_web_probe_defer.py`
(follow the `tests/test_desktop_web_*.py` source-inspection pattern)

**Steps:**
1. Failing tests: view-models action lists contain `defer` at index 1 for both probe kinds;
   desktop app.js source contains `data-probe="defer"` in the message-card renderer and
   `data-spec-response="defer"` in the profile row; resolved/toast copy strings for `deferred`
   and `defer_exhausted` present; css contains the `is-neutral` rules. **Event-handling guard:**
   assert `handleRuntimeEvent`'s profile-refresh branch (~`app.js:4920`) does NOT list
   `interest.deferred` / `avoidance.deferred` (defer must not trigger a profile refresh — spec
   §Frontend WS handling).
2. Implement per spec §Frontend (three surfaces + CSS; response-`action`-keyed copy; never
   「已忽略」-as-permanent wording). Do NOT add deferred events to the profile-refresh set; the
   unconditional `applyRuntimeStatus` at the top of `handleRuntimeEvent` already surfaces the
   deferred live-summary message.
3. Tests + lint (ruff not applicable to JS — ensure `node --test` if any JS tests exist for
   view-models; the Python source-inspection tests are the gate).

### Task 6: Docs (with an explicit architecture audit — AGENTS.md/CLAUDE.md compliance)

**Files (definite):** `docs/modules/soul.md` (speculator state machine — add `deferred` + ladder
+ defer/revive/exhaust transitions), `docs/modules/api.md` (probe respond endpoints — new `defer`
response value + `deferred`/`defer_exhausted` outcomes + `interest.deferred`/`avoidance.deferred`
events, if the endpoint table exists there), `docs/changelog.md` (one bullet under the CURRENT
version block — this is NOT a release; no new `## vX.Y.Z` header).

**Steps:**
1. Update the module docs' state-machine description + endpoint contract; changelog bullet
   crediting PR #82's author (15515151) for the product idea.
2. **Architecture-doc audit (do not blanket-skip — AGENTS.md:31-39/54-57 requires it whenever
   interfaces / data flow / cross-module interactions change).** This change adds a persisted
   state value, a new API response value, new WS events, and new frontend actions. Explicitly
   open and check each of: `docs/architecture.md` (text layers + module roles), `docs/spec.md` §3
   ASCII diagram, `README.md` + `README_EN.md` top-of-page diagrams. Expected finding: no diagram
   edit needed — this adds a *state within* the existing soul→api→web probe path, not a new
   module/adapter/dependency edge or a new cross-module data-flow arrow. **Record that conclusion
   in the PR description** (audited, no diagram surface changed) rather than silently omitting it.
   If the audit surprises us (e.g. probe events are enumerated in a diagram), update accordingly.
3. No CLI / config docs (no CLI command or `config.toml` field added — constants only, spec D8).

### Task 7: End-to-end acceptance (real environment)

Per spec §Verification. Sequence:
1. Restart `serve-api` (stale-routes gotcha: routes are fixed at process start).
2. Live desktop web: defer a probe → honest copy → hard reload → gone; inspect
   `data/memory/speculative_state.json`.
3. Time-travel: edit `deferred_until` into the past → restart daemon → probe reappears
   (`force_tick` revival), `created_at` fresh.
4. Defer ×3 → cooldown entry, `defer_exhausted` in feedback history.
5. Chat 「先放着吧」 → deferred state via `neutral_deferred` (check classifier field in history).
6. Avoidance probe spot-check (defer + reload).
7. Record results in the PR/commit description; on failure, fix before commit.
