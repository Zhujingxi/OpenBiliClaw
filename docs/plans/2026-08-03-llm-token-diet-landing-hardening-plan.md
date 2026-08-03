# LLM Token Diet Landing Hardening — Implementation Plan

> **Spec:** [`2026-08-03-llm-token-diet-landing-hardening-spec.md`](./2026-08-03-llm-token-diet-landing-hardening-spec.md)
> **Owner:** root agent（规格、集成、验收）
> **Implementation:** bounded multi-agent tasks with non-overlapping file ownership
> **Status:** implementation complete; acceptance runs from the resulting clean commit

## 0. Working rules

- 所有实现基于 rebase 后的当前 `main`，rebase 前创建可恢复备份 ref。
- 子 agent 不改自己任务边界以外的文件；共享测试文件必须提前声明 owner。
- 每个任务先补失败测试，再改实现，再跑 focused tests。
- 主 agent 审查每个 diff，不把子 agent 的“完成”当成验收结果。
- 不调用已失效的 cc review。
- 不提交真实 config、Cookie、API key、profile 正文或 DB。

## Task 1 — Freeze landing contract

**Owner:** root
**Files:** this spec/plan

- [x] 记录 replay、cache、reason、rebase 与文档阻塞项。
- [x] 定义 effective-profile、embedding、route、body-cap 否决条件和 artifact contract。
- [x] 定义自动化、真实 replay 和 runtime/E2E 三层验收。

**Gate:** spec 和 plan 在任何生产代码修改前存在并可引用。

## Task 2 — Rebase current main safely

**Owner:** root

1. 记录 branch HEAD、merge-base、ahead/behind、dirty state。
2. 创建 `backup/perf-llm-token-diet-pre-rebase-20260803`。
3. `git rebase main`，逐文件语义化解决冲突。
4. 特别核对 config defaults、route docs、current-main tests 与 token-diet knobs。
5. 运行冲突敏感 focused tests：config、OpenClaw、embedding、memory、recommendation。

**Gate:** worktree clean、无 conflict markers、`git diff --check` pass；备份 ref 可恢复。

**Result:** semantic rebase completed on `main@1d9c2a4e`; backup ref
`backup/perf-llm-token-diet-pre-rebase-20260803` preserves the pre-rebase head. Integration found
and repaired a serializer-move regression: topic-lifecycle filtering remains owned by
`soul/profile_views.py`, while `_utils` is only a compatibility re-export.

## Task 3 — Replay production-equivalence hardening

**Owner:** `luna_max_replay` sub-agent
**Primary files:**

- `scripts/run_profile_diet_ab.py`
- `tests/test_profile_diet_ab.py`

Implementation checklist:

- [x] effective profile loader applies overrides and active speculations；
- [x] strict embedding audit detects exception/empty/nonfinite/dimension mismatch；
- [x] embedding cache lifetime covers the whole run and closes in `finally`；
- [x] per logical run call attribution and route-equivalence validation；
- [x] 临时恢复 faithful `body-cap` legacy-vs-production arm 并完成真实对照；
- [x] 真实 gate 否决 200+100 后删除该正式 arm，并把生产正文路径完整回滚；
- [x] artifact records blocking reasons, recall/route/embedding audit without private payloads；
- [x] unit tests cover every failure and valid zero-tail/zero-similarity case；
- [x] mirror topic lifecycle, production 30-item claim grouping and `mixed` context；reject
      production `eval_prefilter_mode=enforce` because controlled replay intentionally uses `off`；
- [x] extract and test final blocking-reason aggregation so every failed sub-audit blocks landing。
- [x] retry only recovered transient provider rate limits after cooldown, restoring chunk evaluation
      state and retaining failed attempts in route audit；classification stops at the normalized
      provider-limit boundary, retry budgets reset per chunk, and 402/billing errors remain fatal。

**Focused gate:**

```bash
.venv/bin/python -m pytest tests/test_profile_diet_ab.py -q
```

## Task 4 — Evaluation cache input closure

**Owner:** `luna_max_cache` sub-agent
**Primary files:**

- `src/openbiliclaw/discovery/engine.py`
- `tests/test_discovery_engine.py`

Implementation checklist:

- [x] deterministic prompt-visible content digest includes effective source context；
- [x] embedding/recall namespace participates in single and batch cache keys；
- [x] normal cache entries are written only after complete recall or explicit no-recall mode；
- [x] transient/partial recall failure cannot poison the normal cache；
- [x] same content/profile/negative inputs still hit LRU without repeated LLM work；
- [x] changed body/metrics/context/model namespace invalidates；
- [x] heterogeneous outer prompt metadata and actual vision attempts bypass normal per-item cache；
- [x] raw cache hits reapply franchise/style caps with cold/warm-stable caller grouping, including
      enforce-prefilter boundary compression；empty metadata clears stale object state；
- [x] existing legacy tuple compatibility and 4096-entry LRU behavior remain green。

**Focused gate:**

```bash
.venv/bin/python -m pytest tests/test_discovery_engine.py -q
```

## Task 5 — Runtime reason normalization

**Owner:** `luna_max_reason` sub-agent
**Primary files:**

- `src/openbiliclaw/discovery/engine.py` only after Task 4 owner coordination
- reason-specific tests in `tests/test_discovery_engine.py`

Because Task 4 and Task 5 share the same files, Task 5 initially prepares a small patch/design in
an isolated commit or waits for Task 4. The root agent decides integration order; agents must not
edit the same file concurrently.

- [x] add one pure `normalize_evaluation_reason(score, raw_reason)` helper；
- [x] single and batch paths normalize before object/cache persistence；
- [x] `<0.5` always empty；`>=0.5` at most 30 code points；
- [x] missing empty accepted, non-string continues malformed retry/error path；
- [x] scoring/admission semantics unchanged；prompt wording now labels reason as an internal
      diagnostic and states the exact Unicode limit。

**Focused gate:** reason prompt + single/batch/cache/persistence tests.

## Task 6 — Main-agent integration and documentation

**Owner:** root

- [x] review agent diffs against spec invariants；
- [x] resolve overlap without dropping tests；
- [x] update `docs/modules/discovery.md` retry/cache/reason/body-cap rejection truth；
- [x] update `docs/modules/recommendation.md` to document full-body rollback；
- [x] update `docs/modules/llm.md`, `docs/modules/config.md`, changelog and architecture/data-flow
      notes where ownership changed；
- [x] mark superseded historical gate claims explicitly；
- [x] update this plan with automated commands/results；real replay evidence remains in Task 8。

**Gate:** documentation contains no contradictory retry/body-cap-rejection/replay claims found by targeted
`rg`.

## Task 7 — Automated verification

**Owner:** root

Run in this order:

```bash
.venv/bin/ruff format --check src/ tests/ scripts/run_profile_diet_ab.py
.venv/bin/ruff check src/ tests/ scripts/run_profile_diet_ab.py
.venv/bin/mypy src/
git diff --check

.venv/bin/python -m pytest \
  tests/test_profile_diet_ab.py \
  tests/test_discovery_engine.py \
  tests/test_profile_views.py \
  tests/test_profile_views_guards.py \
  tests/test_candidate_eval_coordinator.py \
  tests/test_discovery_candidate_pipeline.py \
  tests/test_config.py \
  tests/test_api_app.py \
  tests/test_llm_service.py \
  tests/test_memory_manager.py \
  tests/test_recommendation_engine.py -q

.venv/bin/python -m pytest -q
```

If a failure also occurs on current `main`, record it as a baseline defect but do not waive it:
either incorporate the current-main fix during rebase or document an environment-only skip with evidence.

**Result (2026-08-03):**

- Ruff format check: 544 files formatted；Ruff lint: pass；MyPy: 236 source files, pass；
  `git diff --check`: pass。
- Required focused integration group after the conservative 80 / 16 correction, normalized
  rate-limit-boundary fix and full-body rollback: 1355 passed in 130.33s。
- Full repository after the same final corrections: 7035 passed, 93 environment/platform skips in
  624.06s；zero failures。
- Extension final-main compatibility: TypeScript typecheck pass；1244 Node tests pass after the
  worktree installed lockfile-pinned dev dependencies。
- The rebase exposed two current-main hygiene failures (one missing blank line and one import order);
  both were mechanically formatted so the repository-wide Ruff commands now pass without waiver。

## Task 8 — Replay and end-to-end acceptance

**Owner:** root

1. Validate DB/config prerequisites without printing secrets。
2. Run compact and reason-diet commands from Spec §6B；retain and independently validate the rejected
   body-cap diagnostic artifact without rerunning a removed production feature。
3. Validate each JSON artifact structurally and recompute key metrics independently from raw scores。
4. Run deterministic candidate-pipeline E2E cases from Spec §6C。
5. Smoke `openbiliclaw config-show` and relevant API config serialization。
6. Record exact commit, commands, pass counts, skips, artifact paths/digests and unresolved environmental
   limitations。

**Final gate:** no code/test/docs/replay blocker remains. If a required real-data or provider prerequisite
is unavailable, the branch is reported blocked rather than described as release-ready.

**Progress:** deterministic SQLite E2E now covers enqueue → 60s coalescing wait → tokenized claim →
real batch parser/runtime reason normalization → eval LRU → admission/content cache, including a warm
eval-cache replay with zero additional provider calls. Production `config-show` exits 0 and the safe
acceptance fields resolve to prefilter `shadow`, admission `0.6`, coalescing `15 / 90s`, topic lifecycle
`off`. A pre-hardening compact run passed, but it is intentionally not final evidence because a subsequent
provider 429 caused the replay-only bounded cooldown retry change. The first compact run on that hardened
commit then correctly failed the unchanged relative
quality gate at 64 / 12 (Spearman median `0.494686 < 0.570454`; admission delta median
`-0.09 < -0.07`). Root diagnosis found that this cut saved only about 11% on the current profile while
removing model-visible semantic tail, so the implementation now uses 80 interests / 16 specifics and
tail recall ranks 81..256. The full-body rollback's focused discovery/replay/recommendation group passes
304 tests；the required integration group passes 1355 tests, and the full repository passes 7035 tests
with 93 environment/platform skips. Extension TypeScript typecheck and all 1244 Node tests also pass.
The 80 / 16 compact artifact on
`11f77a64` passed its final gate, while the strict Reddit 100×3 body-cap artifact failed all three quality
dimensions (18% flip vs 8% ceiling, 0.192031 Spearman vs 0.632378 floor, -11pp admission vs -3pp floor).
Because the cap retained only 12.95% of affected body characters, all discovery/recommendation body
truncation and the formal replay arm were removed instead of tuning the gate. Compact and reason-diet are
rerun from the resulting clean commit；the rejected artifact remains diagnostic evidence only. The first
80 / 16 rerun also
exposed and closed a replay-only classifier bug where raw SDK 429 metadata overrode the adapter's
normalized transient-rate-limit decision；no quality metrics were emitted by that aborted run.

## Task 9 — Landing handoff

**Owner:** root

- [ ] concise change summary；
- [ ] explicit review findings and how each was closed；
- [ ] test/replay evidence；
- [ ] remaining rollout observation and rollback points；
- [ ] confirm no secrets/artifacts intended to stay local were committed。
