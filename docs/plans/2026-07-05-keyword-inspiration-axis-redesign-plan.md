# Keyword Inspiration Axis Redesign — Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: use superpowers:executing-plans to implement task-by-task.
> **Spec:** [`2026-07-05-keyword-inspiration-axis-redesign-spec.md`](./2026-07-05-keyword-inspiration-axis-redesign-spec.md)
> **Status:** Reviewed r4 — 2026-07-05, hardened through 5-round Codex adversarial review
> (R1–R4 findings applied, R5 VERDICT: APPROVE). Phase 1 only
> (see Spec §Phasing). Yield backfill / config collapse / pipeline extraction are Phase 2 and
> out of scope here.

**Goal:** Ship the "1 LLM call + deterministic assembly + accumulating axis library" skeleton so a
preview run demonstrably shows: LLM `caller=` count ≤ 1 per regular/shared stage (0 in grounding),
no `platform_style_mismatch` rejections, every selected interest covered on ≥N platforms and
spanning ≥2 distinct axes (via over-generation + coverage-first selection + deterministic fill;
shortfalls surfaced as `coverage_shortfall` telemetry, never silent), and the axis table
upserted+reused across two rounds (`--persist-axes`).

**Architecture:** TDD, smallest durable pieces first — schema → DAO → pure selection/assembly
helpers → deterministic grounding probes → single merged LLM call → wiring → telemetry/docs.
Everything stays behind `inspiration_search_enabled` (default off). No behavior change for callers
when the flag is off.

**Tech Stack:** Python 3.11+, SQLite via `openbiliclaw.storage.database.Database`, pytest
(`asyncio_mode=auto`), Ruff, MyPy strict. Run tests with `.venv/bin/python -m pytest` (see repo
memory: interpreter is `.venv/bin/python`).

**Invariants that MUST hold (from Spec):**
- ④ is the ONLY LLM call in a regular/shared inspiration stage; grounding (③) issues ZERO LLM
  calls (brainstorm precursor deleted). On ④ failure the stage degrades deterministically —
  no repair call, no retry call — so per-stage LLM count is always ≤ 1. The fallback is a
  two-level ladder: axes exist → `[interest_label × example_terms]` templates; axis library
  empty (cold start) → interest-only queries (always producible for script-compatible
  platforms; script-incompatible platforms emit `coverage_shortfall(reason=script_mismatch)`;
  axis coverage emits `coverage_shortfall(missing_axes)`). Fallback candidates pass the same
  hard gates as everything else.
- Both `_run_inspiration_stage` AND `_run_shared_inspiration_stage` are rewritten; neither keeps
  the brainstorm/curate/repair flow.
- ④ has hard input caps (≤4 interests, ≤6 axes/interest, ≤24 evidence rows, target-platform-only
  guides) and `max_tokens=8192`; truncated JSON is salvaged by complete-object prefix.
- Style is a soft ranking score, never a hard reject. Hard gates = dedup / url / length / script only.
- Coverage (interest×axis×platform) is coverage-first-selection + deterministic fill in the pure
  assembler; fills are platform/script-aware and pass the SAME hard gates (no garbage fills for
  cross-script platforms — those emit `coverage_shortfall(reason=script_mismatch)` instead);
  unfillable slots always emit `coverage_shortfall` telemetry.
- preview and production call the SAME `materialize_platform_keywords` symbol; preview never
  writes keyword rows, and writes axes only under `--persist-axes`. Preview axis upserts do NOT
  bump `use_count`/`last_used_at` ("usage" is a production-only semantic — otherwise round 1
  would trip the axis-saturation 鉴权 signal). Together with 鉴权 reading only
  `selection_scope='production'` ledger rows, back-to-back preview runs select stable interests
  (two-round acceptance is not self-defeating).
- Axis library is bounded: per-interest active cap (16) enforced at upsert; ordering uses
  `freshness × max(yield_score, prior=0.3)` so Phase-1 zero-yield rows still rank meaningfully.
- Prompt-cache convention (CLAUDE.md): static system prompt, per-call vars in user, deterministic JSON.

---

### Task 1: `discovery_inspiration_axis` schema

**Files:** Modify `src/openbiliclaw/storage/database.py`; Test `tests/test_discovery_inspiration.py`

**Steps:**
1. Failing test: a fresh `Database` creates table `discovery_inspiration_axis` with the Spec columns
   and index `idx_discovery_inspiration_axis_interest`.
2. Add `CREATE TABLE IF NOT EXISTS discovery_inspiration_axis (...)` + index in
   `_ensure_discovery_keywords_table()` (alongside the existing `discovery_inspiration_*` tables).
3. Column set per Spec §Data Model (include `yield_score`/`admissions` as placeholder-only in Phase 1).
4. `.venv/bin/python -m pytest tests/test_discovery_inspiration.py -q`.

### Task 2: `AxisRow` dataclass + axis DAO (bounded, prior-ranked)

**Files:** Modify `src/openbiliclaw/discovery/inspiration.py`, `src/openbiliclaw/storage/database.py`;
Test `tests/test_discovery_inspiration.py`

**Steps:**
1. Failing tests for `axis_id` derivation: stable hash of `interest_label + normalize(axis_label)`
   where normalize = NFKC + casefold + strip whitespace/punctuation; two rewordings that normalize
   identically map to the same `axis_id`.
2. Failing tests for `upsert_inspiration_axes(list[AxisRow], *, bump_usage=True)`: insert;
   conflict on `axis_id` merges (`use_count += 1` and `last_used_at` only when `bump_usage=True`,
   always updates `last_refreshed_at`, unions `evidence_refs`); `bump_usage=False` (preview)
   leaves `use_count`/`last_used_at` untouched; after upsert, if an interest has > 16 active
   axes, lowest-ranked overflow rows get `status='stale'`.
3. Failing tests for `list_inspiration_axes(interest_labels, *, limit, now)`: returns only
   `status='active'`; drops `time_sensitive` rows past `freshness_ttl_days` relative to `now`;
   orders by `freshness × max(yield_score, 0.3)` (freshness = decay on `last_refreshed_at`) with
   tie-breaks `last_refreshed_at` desc → `use_count` asc → `axis_kind` rotation — assert ordering
   is meaningful when ALL rows have `yield_score=0` (Phase-1 reality).
4. Add frozen `AxisRow` dataclass to `inspiration.py`; implement both DAO methods.
5. `now` is passed in (never wall-clock inside helpers) to keep tests deterministic.
6. Run targeted tests.

### Task 3: Interest selection + 鉴权 (deterministic)

**Files:** Modify `src/openbiliclaw/runtime/keyword_planner.py`; Test `tests/test_keyword_planner.py`

**Steps:**
1. Failing tests: given a selection ledger where interest X was picked in the last K rounds and its
   active axes are all recently used (saturated), `_selected_inspiration_interests` downweights/skips
   X in favor of a lower-weight-but-fresh interest.
2. Extend `_selected_inspiration_interests` to read `discovery_interest_selection_ledger` frequency
   (**counting only `selection_scope='production'` rows** — preview selections must not cool down
   future selection; add a test that preview-scope rows leave selection unchanged) + axis
   saturation (via `list_inspiration_axes`) as a deterministic 鉴权 penalty.
3. Cap selected interests at 4 (Spec §预算与失败处理; current default is 6).
4. No LLM. Keep the existing deterministic ranked/diversity window core (the current helper slices
   a ranked window — it is NOT a random sampler; do not introduce randomness, tests expect stable
   ordering).
5. Run targeted tests.

### Task 4: Deterministic grounding probe builder (replaces LLM brainstorm)

**Files:** Modify `src/openbiliclaw/discovery/inspiration.py` (pure builder),
`src/openbiliclaw/runtime/keyword_planner.py`; Test `tests/test_discovery_inspiration.py`,
`tests/test_keyword_planner.py`

**Steps:**
1. Failing tests for pure `build_grounding_probes(selected_interests, axes, pooled_terms, *, limit)`:
   emits `interest_label`, `interest_label + axis_label`, `interest_label + example_term`
   combinations ordered by axis rank; caps at `limit`; dedups; empty axis library still yields
   plain interest-label probes (cold start).
2. Wire `_ground_inspiration_branches` (or a thin successor) to consume these probes instead of
   LLM brainstorm branches. Retrieval provider, TTL cache, and history pooling stay unchanged.
3. Delete `_brainstorm_inspiration_branches` and `_fallback_brainstorm_branches` call sites from
   the inspiration paths (actual dead-code removal lands with Task 7's rewiring; this task makes
   the probe path exist and be the one used).
4. Failing test: grounding for a stage issues ZERO LLM calls (LLM stub call count == 0 during ③).
5. Run targeted tests.

### Task 5: `materialize_platform_keywords` pure function (coverage-first + deterministic fill)

**Files:** Create logic in `src/openbiliclaw/discovery/inspiration.py` (pure); Test
`tests/test_discovery_inspiration.py`

**Steps:**
1. Define input `MaterializeCandidate` (interest, axis_label, platform, core_concept, decoration,
   recency_sensitivity, origin) and `AllocationTarget` (platforms, min_axes) dataclasses.
2. Failing table-driven tests (NO LLM), asserting:
   a. hard gates drop only dedup/url/over-length/wrong-script;
   b. NO candidate is dropped for style;
   c. selection is coverage-first: per platform, chosen keywords cover the allocation's platforms
      and span ≥ `min_axes` distinct axes per interest whenever the pool allows;
   d. same-interest two-slot picks come from different axes (no near-duplicate axis);
   e. when the pool is too thin, missing slots are filled deterministically from
      `[interest_label + axis example_terms]` templates with `origin=deterministic_fill`;
      fills are platform/script-aware and must pass the SAME hard gates — a Chinese-only
      interest on `youtube`/`reddit` yields `coverage_shortfall(reason=script_mismatch)`,
      never a garbage fill (explicit test for Chinese interests on English-script platforms);
   f. when not even axes exist for a slot, telemetry records
      `coverage_shortfall(interest, missing_axes, missing_platforms)` — never silent;
   g. degenerate cases covered: thin pool, single-axis pool, empty candidate list;
   h. assembled query text uses `core_concept`; `decoration` appended only within token budget;
      `recency_sensitivity=high` never injects a literal year into text;
   i. returns telemetry: per-interest axis-coverage count + soft-score distribution + hard-gate
      rejects + `deterministic_fill` count + `coverage_shortfall` detail.
3. Implement `platform_style_score(keyword, platform) -> float` (soft) and delete the hard
   `_platform_style_rejection_reason` path from the realize/consume flow.
4. Implement allocation as greedy over (interest×axis×platform) maximizing coverage first, then
   soft score.
5. Run targeted tests.

### Task 6: Single merged LLM call ④ (axes + keywords, budgeted, salvageable)

**Files:** Modify `src/openbiliclaw/llm/prompts.py`, `src/openbiliclaw/runtime/keyword_planner.py`;
Test `tests/test_llm_prompts.py`, `tests/test_keyword_planner.py`

**Steps:**
1. Add `build_inspiration_axis_keyword_prompt` with a module-level static
   `_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT`; all per-call vars (profile_digest, platform_guides,
   selected_interests, existing_axes, fresh_evidence, allocation_targets) in the user message,
   serialized `ensure_ascii=False, indent=2, sort_keys=True`. Prompt requires: keywords span
   ≥ `min_axes` axes per interest; ≥2 candidates per allocation slot (over-generation); reuse
   existing `axis_id`/`axis_label` verbatim for semantically-same axes; separate
   `core_concept`/`decoration`/`recency_sensitivity`; no literal years in `core_concept`.
2. Add the builder to `test_prompt_builder_system_messages_are_call_invariant`'s
   `_builder_test_inputs()` (system prompt byte-identical across two distinct inputs).
3. Enforce input caps before the call (≤4 interests, ≤6 axes/interest, ≤24 evidence rows,
   target-platform-only guides) with telemetry for truncation; call with `max_tokens=8192`,
   `caller="discovery.keyword_inspiration"`; parse `{axes[], keywords[]}` into `AxisRow` +
   `MaterializeCandidate`.
4. Tolerant parser: on truncated JSON, salvage the longest valid prefix of complete objects per
   array, set `parse_salvaged=true` + dropped count. On empty/unparseable output, return the
   failure marker (`llm_call_failed=true`) — NO retry, NO repair call.
5. Tests with a stubbed LLM: (a) fixed valid payload → expected candidates, exactly one LLM
   invocation; (b) truncated payload → salvaged prefix, still one invocation; (c) garbage/empty →
   failure marker, still exactly one invocation (no retry).

### Task 7: Wire ④⑤⑥ into BOTH stages + unify preview/prod

**Files:** Modify `src/openbiliclaw/runtime/keyword_planner.py`; Test `tests/test_keyword_planner.py`

**Steps:**
1. Rewrite `_run_inspiration_stage` regular path: ① select+鉴权 → ② `list_inspiration_axes` →
   ③ deterministic probes → ground (provider unchanged) → ④ single call →
   ⑤ `materialize_platform_keywords` → ⑥ `upsert_inspiration_axes`.
2. Rewrite `_run_shared_inspiration_stage` (regular+explore merged path, `keyword_planner.py:1456`)
   onto the same ①–⑥ skeleton — it must not keep its own brainstorm/curate flow.
3. On ④ failure marker: assemble candidates deterministically via the two-level ladder — axes
   exist → `[interest_label × existing-axis example_terms]` templates; axis library empty →
   interest-only queries + `coverage_shortfall(missing_axes)` — and continue through ⑤⑥
   (Spec §预算与失败处理). Fallback candidates go through the same hard gates: script-incompatible
   platforms get `coverage_shortfall(reason=script_mismatch)`, never a garbage query. Tests:
   (i) cold-start double failure (empty axis library + garbage LLM output) still yields
   candidates for script-compatible platforms with zero extra LLM calls; (ii) Chinese-only
   interest in the same scenario yields script_mismatch shortfall for youtube/reddit, not
   garbage.
4. Rewrite `preview_inspiration_keywords` to call the SAME ⑤ pure function (delete the duplicated
   consume/repair/backfill loop). Preview keeps `persist=False` for keyword rows and grounding;
   axis upsert (⑥) runs in preview only when `persist_axes=True` is passed down.
5. Delete `_brainstorm_inspiration_branches`, `_fallback_brainstorm_branches`, `brainstorm.repair`,
   `keyword_inspiration.repair`, and template `backfill` from the inspiration paths.
6. Failing tests: (a) a regular stage issues exactly one `caller=discovery.keyword_inspiration`
   LLM call and zero other LLM calls (stub call count); (b) same for the shared stage; (c) the
   ④-failure path issues no additional LLM call and still yields deterministic candidates;
   (d) preview/prod produce identical keywords for identical input.
7. Run `tests/test_keyword_planner.py`.

### Task 8: Telemetry, report + `--persist-axes` CLI flag

**Files:** Modify `src/openbiliclaw/runtime/keyword_planner.py`, `src/openbiliclaw/cli.py`;
Test `tests/test_keyword_planner.py`, `tests/test_cli.py`

**Steps:**
1. Add per-interest axis-coverage + soft-score summary + `deterministic_fill` /
   `coverage_shortfall` / `parse_salvaged` / `llm_call_failed` fields to the preview report;
   ensure `rejected_reasons` can no longer contain `platform_style_mismatch`.
2. Keep `repair_applied` key for backward-compat but it is always false on the happy path (or remove
   and update the report contract test).
3. Add `--persist-axes` (default off) to `keyword-inspiration-preview`, threading `persist_axes`
   into the planner preview entrypoint. Preview-mode upserts insert axis rows / merge evidence but
   do NOT bump `use_count`/`last_used_at`. Tests: axes written vs not written per flag; preview
   upsert leaves `use_count`/`last_used_at` untouched; two consecutive previews with
   `persist_axes=True` select identical interests.
4. Run targeted tests.

### Task 9: Docs + acceptance verification

**Files:** Modify `docs/modules/discovery.md`, `docs/modules/storage.md`, `docs/modules/cli.md`,
`docs/changelog.md`

**Steps:**
1. `docs/modules/storage.md`: document `discovery_inspiration_axis` + new DAO methods + bounding
   rules (active cap, prior-ranked ordering).
2. `docs/modules/discovery.md`: replace the brainstorm→curate→repair description with the
   select→probe→ground→single-call→assemble→writeback flow; note axis library + failure
   degradation semantics.
3. `docs/modules/cli.md`: document `keyword-inspiration-preview --persist-axes`.
4. `docs/changelog.md`: bullet under current version.
5. Full gate: `.venv/bin/python -m pytest tests/test_discovery_inspiration.py
   tests/test_keyword_planner.py tests/test_llm_prompts.py tests/test_storage.py tests/test_cli.py -q`;
   `.venv/bin/python -m ruff check src/ tests/`; `.venv/bin/python -m mypy src/`.
6. Acceptance run: `openbiliclaw keyword-inspiration-preview --persist-axes ...` twice; verify
   (a) exactly one `discovery.keyword_inspiration` LLM call per round and zero grounding LLM calls,
   (b) no style rejects, (c) each interest ≥2 axes / ≥N platforms or explicit `coverage_shortfall`
   telemetry, (d) round 2 `existing_axes` non-empty (axis reuse) — round-2 interest selection is
   stable because 鉴权 ignores preview-scope ledger rows, preview axis upserts don't bump usage
   fields, and selection is deterministic ranking,
   (e) where the provider reports cache metrics, `openbiliclaw cost --by caller` shows non-zero
   cached tokens for the caller on round 2 (providers that don't report cache metrics are exempt),
   per Spec §Acceptance Criteria.

---

## Sequencing & risk

- Tasks 1→2→5 are pure/low-risk and unblock table-driven tests without any LLM. Task 4 (probe
  builder) is also pure and independent of Task 6.
- Task 6 is the only new LLM surface; Task 7 is the riskiest (rewires BOTH stages + deletes the
  brainstorm/curate/repair code) — do it only after 4, 5 and 6 are green.
- Rollback policy (Spec §Rollout): everything gated by `inspiration_search_enabled` (default off) —
  default users see zero change. For already-enabled installs (pre-alpha single user) the old path
  is deleted in the same release; there is deliberately NO compat subflag (contradicts the
  Phase-2 config collapse), so runtime rollback = version downgrade via the release channel.
  Keep the change on-branch until the Task 9 acceptance run passes.

## Out of scope (Phase 2)

- yield/delight → `axis.yield_score` backfill loop; time_sensitive decay/retire scheduling
  (Phase 1 bounds the library via the per-interest active cap instead).
- Config collapse (14 `inspiration_*` knobs → ~4).
- Extracting `InspirationKeywordPipeline` out of `KeywordPlanner`.
- Embedding-based near-dup in the hard-dedup gate (Phase 1 uses normalized-string dedup).
