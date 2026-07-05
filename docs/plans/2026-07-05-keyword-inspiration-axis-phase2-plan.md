# Keyword Inspiration Axis Library — Phase 2 Implementation Plan

> **Spec:** [`2026-07-05-keyword-inspiration-axis-phase2-spec.md`](./2026-07-05-keyword-inspiration-axis-phase2-spec.md)
> **Status:** Draft — 2026-07-05. Executes after Phase 1 is committed on
> `feature/discovery-inspiration-mvp` (Task 0).
> **Executor:** implementation agent (Opus 4.8 subagent), task-by-task; Claude verifies each
> increment; TDD throughout.

**Goal:** Land the yield-learning loop, persisted axis lifecycle, config collapse (13→4), and
pipeline extraction — with zero change to Phase 1's call-count/coverage/fallback invariants.

**Tech Stack:** Python 3.11+, SQLite via `openbiliclaw.storage.database.Database`, pytest
(`asyncio_mode=auto`), Ruff 100-char, MyPy strict. Interpreter: `.venv/bin/python` (worktree-local).
Test style: inline hand-written fake data; no new fixtures beyond the shared `db` handle.

**Invariants that MUST hold (from Spec):**
- Backfill/lifecycle/config/extraction add ZERO LLM calls; Phase 1's "≤1 call per stage,
  0 in grounding" tests pass unmodified.
- Backfill is a trailing-window RECOMPUTE with SET semantics — idempotent by construction,
  no watermarks.
- Smoothing constant 0.3 stays equal to `exploration_prior` (unused axis score == prior).
- Preview never triggers backfill or lifecycle transitions (production-only, throttled 6h).
- `retired` axes never return to selection and are not resurrected by upsert.
- Part D is a pure move: existing tests pass with zero assertion edits.
- `medium` breadth derives values item-identical to Phase 1 defaults (regression table).

---

### Task 0: Commit Phase 1 (Claude, not the agent)

Commit all current worktree changes on `feature/discovery-inspiration-mvp` (Phase 1 accepted
2026-07-05) so Phase 2 diffs are reviewable against a clean base. No push.

### Task 1: axis_id attribution + backfill DAO

**Files:** `src/openbiliclaw/discovery/inspiration.py`, `src/openbiliclaw/storage/database.py`;
Tests `tests/test_discovery_inspiration.py`

**Steps:**
1. Failing tests: `MaterializeCandidate` accepts optional `axis_id`; `_realized_from_materialize`
   writes `axis_id` into metadata (given → verbatim; missing → derived via
   `derive_inspiration_axis_id(source_interest, axis_label)`); Task-6 parser maps
   `axis_id_or_label` to a real `axis_id` when it matches an existing axis; deterministic fill
   carries the library axis's id.
2. Schema migration: `discovery_inspiration_axis` gains `yield_backfilled_at TEXT` via the
   existing tolerant `ALTER TABLE ... ADD COLUMN` pattern (test: fresh db has it; pre-existing
   db without it gets it on open).
3. Failing tests for `backfill_inspiration_axis_yield(*, window_days=30, now)` (new DAO):
   aggregates inspiration-cohort `discovery_keywords` rows in the window by metadata `axis_id`
   (fallback: derive from `source_interest` + `axis_label` when `axis_id` absent — covers
   Phase 1 rows), computes `uses` (claimed/used rows) and `admissions` (SUM(yield_count)),
   then SETs `admissions`, `yield_score = (admissions + 0.3) / (uses + 1.0)`,
   `yield_backfilled_at = now` on matching axes. Axes with zero window rows get
   score reset to the smoothed floor (0.3/1.0) — SET semantics everywhere.
   Idempotency test: run twice, dump table, byte-identical. Spec AC1–AC3.
4. Delight feasibility spike (time-boxed): inspect `get_keyword_cohort_stats` internals for a
   per-axis mean_delight join. If cheap, apply the `clamp(0.5 + mean_delight, 0.5, 1.5)`
   multiplier with tests; if not, record "deferred to Phase 3" in the report and skip — do NOT
   build new attribution infrastructure for it.
5. Gate: `.venv/bin/python -m pytest tests/test_discovery_inspiration.py tests/test_storage.py -q`
   + ruff check/format + `mypy src/openbiliclaw/`.

### Task 2: lifecycle transitions (stale / retired / purge)

**Files:** `src/openbiliclaw/storage/database.py`; Tests `tests/test_discovery_inspiration.py`

**Steps:**
1. Failing tests for `apply_inspiration_axis_lifecycle(*, now)` (new DAO, called right after
   backfill in the same tick): (a) `time_sensitive=1` past `freshness_ttl_days` → persisted
   `status='stale'`; (b) active axes with `use_count >= 5` and post-backfill
   `yield_score < 0.08` → `status='retired'`; (c) stale/retired rows with
   `last_refreshed_at` older than 90 days → physically DELETEd. All thresholds module-level
   constants; `now` injected.
2. Failing test: `upsert_inspiration_axes` merging into a `retired` row updates evidence but
   does NOT flip status back to active (no resurrection). `stale` rows MAY be revived by fresh
   upsert (deliberate: a topic can come back) — assert that too.
3. Returns a transition summary dict (staled/retired/purged counts) for telemetry.
4. Targeted gate.

### Task 3: production-tick wiring + ordering regression

**Files:** `src/openbiliclaw/runtime/keyword_planner.py`; Tests `tests/test_keyword_planner.py`

**Steps:**
1. Failing tests: production stage (regular AND shared) runs backfill+lifecycle before ② when
   `MAX(yield_backfilled_at)` is older than 6h (constant `_AXIS_BACKFILL_MIN_INTERVAL_HOURS=6`);
   second stage within 6h skips (assert via transition-summary telemetry / call spy);
   preview NEVER triggers either regardless of staleness. Spec AC4.
2. Failing ordering test (end-to-end through `list_inspiration_axes`): seed keyword history so
   axis X (yield) > axis Z (unused, ==prior) > axis Y (used, zero admissions); include the
   freshness-crossover case (older-but-yielding X outranks fresher zero-yield W). Spec AC2.
3. Stage telemetry gains `axis_backfill` block (ran/skipped, staled/retired/purged counts).
4. Verify Phase 1 LLM-count tests pass unmodified (Spec AC8).
5. Targeted gate.

### Task 4: config collapse (13 → 4)

**Files:** `src/openbiliclaw/config.py`, `config.example.toml`; Tests `tests/test_config.py`,
`tests/test_keyword_planner.py`

**Steps:**
1. Failing tests: new `inspiration_breadth: str = "medium"` field validating
   `low|medium|high`; derivation function returns the Spec Part C table per tier;
   **medium == Phase 1 defaults, item by item (table-driven)**; invalid tier → config error.
2. Remove the 9 collapsed fields from `DiscoveryConfig` (confirm
   `inspiration_max_expansions_per_seed` is already dead post-Phase-1 → delete outright);
   consumers in `keyword_planner.py` read derived values instead. Align with the codebase's
   existing unknown-key behavior; add an explicit startup WARNING listing removed
   `inspiration_*` keys found in the raw config ("已移除,请改用 inspiration_breadth"),
   values ignored, no fail-fast.
3. Update `config.example.toml` (4 keys + tier comment). Docs land in Task 7.
4. Full `tests/test_config.py` + planner tests gate.

### Task 5: `InspirationKeywordPipeline` extraction

**Files:** Create `src/openbiliclaw/runtime/inspiration_pipeline.py`; Modify
`src/openbiliclaw/runtime/keyword_planner.py`; Tests `tests/test_inspiration_pipeline.py` (new)

**Steps:**
1. Move the ①–⑥ orchestration (interest selection glue, axis fetch, probe build, grounding
   orchestration, single-call invocation, materialize glue, upsert/backfill tick) into
   `InspirationKeywordPipeline` with injected deps (db, llm, inspiration provider, discovery
   config view, clock callable).
2. `KeywordPlanner` keeps its public/仍被测试引用的 API surface and delegates. **Zero behavior
   change: run the full existing planner/inspiration test files WITHOUT editing a single
   assertion — they must pass before and after.** (Mechanical import/monkeypatch-path updates
   in tests are allowed ONLY if a test patched a private planner attribute that physically
   moved; list every such edit in the report.)
3. New direct pipeline unit tests with fakes (happy path, ④-failure fallback path, preview
   flags) — thin, the deep coverage stays in existing files.
4. Record moved-line count in the report. Targeted gate + `mypy src/openbiliclaw/`.

### Task 6 (OPTIONAL — droppable without affecting A–D): embedding near-dup axis merge

**Files:** `src/openbiliclaw/discovery/inspiration.py`, `src/openbiliclaw/storage/database.py`;
Tests `tests/test_discovery_inspiration.py`

**Steps:**
1. Failing tests with a fake embedding service: new axis with cosine ≥ 0.92 against an active
   same-interest axis merges into it (evidence union, no new row); below threshold → new row.
2. Degradation contract: embedding service raising/timeout → silent fallback to Phase 1
   string-normalization behavior, `axis_embedding_degraded=true` in telemetry, stage never
   blocked. Spec AC9.
3. Wire into `upsert_inspiration_axes` behind availability check; reuse `llm/embedding.py` +
   `embedding_cache`. Targeted gate.

### Task 7: docs + full gate + live acceptance

**Files:** `docs/modules/storage.md`, `docs/modules/discovery.md`, `docs/modules/config.md`,
`docs/changelog.md`

**Steps:**
1. storage.md: backfill/lifecycle DAO + new column + thresholds. discovery.md: learning loop
   (recompute semantics, throttle, preview isolation), lifecycle states diagram
   (active→stale/retired→purged), pipeline module. config.md: the 4 keys + breadth table +
   removed-keys warning behavior.
2. changelog.md: bullet under current version.
3. Full gate: `.venv/bin/python -m pytest tests/test_discovery_inspiration.py
   tests/test_keyword_planner.py tests/test_inspiration_pipeline.py tests/test_config.py
   tests/test_storage.py tests/test_cli.py tests/test_llm_prompts.py -q` ;
   `ruff check src/ tests/` ; `mypy src/`.
4. Live acceptance (Claude): seed fake keyword history into the smoke db → production
   `run_once` → axis scores move + telemetry shows backfill; two `--persist-axes` previews →
   zero backfill side effects; `medium` breadth smoke run behaves identically to pre-collapse
   config. Spec AC10.

---

## Sequencing & risk

- Task 1→2→3 are the learning loop (each independently testable); Task 4 and Task 5 are
  independent of them and of each other; Task 6 is last and droppable.
- Task 5 is the highest-regression-risk (mass code motion) — do it AFTER 1–4 are green so the
  move carries the finished Phase 2 logic; its "zero assertion edits" rule is the safety net.
- Rollback: same policy as Phase 1 — feature branch only, no compat shims, version downgrade
  is the runtime rollback; nothing merges until Task 7's live acceptance passes.

## Out of scope (Phase 3+)

- Delight multiplier if the feasibility spike says the join isn't cheap.
- Keyword-level embedding near-dup in the materialize hard gate (only axis-level merge is in
  Task 6).
- Cross-interest axis transfer ("维修DIY" learned on Switch informing Steam Deck) — not designed.
