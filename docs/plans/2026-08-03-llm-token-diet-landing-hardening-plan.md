# LLM Token Diet Landing Hardening — Implementation Plan

> **Spec:** [`2026-08-03-llm-token-diet-landing-hardening-spec.md`](./2026-08-03-llm-token-diet-landing-hardening-spec.md)  
> **Owner:** root agent（规格、集成、验收）  
> **Implementation:** bounded multi-agent tasks with non-overlapping file ownership  
> **Status:** in progress

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
- [x] 定义 effective-profile、embedding、route、body-cap 和 artifact contract。
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

## Task 3 — Replay production-equivalence hardening

**Owner:** `luna_max_replay` sub-agent  
**Primary files:**

- `scripts/run_profile_diet_ab.py`
- `tests/test_profile_diet_ab.py`

Implementation checklist:

- [ ] effective profile loader applies overrides and active speculations；
- [ ] strict embedding audit detects exception/empty/nonfinite/dimension mismatch；
- [ ] embedding cache lifetime covers the whole run and closes in `finally`；
- [ ] per logical run call attribution and route-equivalence validation；
- [ ] re-enable faithful `body-cap` legacy-vs-production arm；
- [ ] fail body-cap gate when zero candidate is actually affected；
- [ ] artifact records blocking reasons, recall/route/embedding audit without private payloads；
- [ ] unit tests cover every failure and valid zero-tail/zero-similarity case。

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

- [ ] deterministic prompt-visible content digest includes effective source context；
- [ ] embedding/recall namespace participates in single and batch cache keys；
- [ ] normal cache entries are written only after complete recall or explicit no-recall mode；
- [ ] transient/partial recall failure cannot poison the normal cache；
- [ ] same content/profile/negative inputs still hit LRU without repeated LLM work；
- [ ] changed body/metrics/context/model namespace invalidates；
- [ ] existing legacy tuple compatibility and 4096-entry LRU behavior remain green。

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

- [ ] add one pure `normalize_evaluation_reason(score, raw_reason)` helper；
- [ ] single and batch paths normalize before object/cache persistence；
- [ ] `<0.5` always empty；`>=0.5` at most 30 code points；
- [ ] missing empty accepted, non-string continues malformed retry/error path；
- [ ] prompt text and admission semantics unchanged。

**Focused gate:** reason prompt + single/batch/cache/persistence tests.

## Task 6 — Main-agent integration and documentation

**Owner:** root

- [ ] review agent diffs against spec invariants；
- [ ] resolve overlap without dropping tests；
- [ ] update `docs/modules/discovery.md` retry/cache/reason/body-cap truth；
- [ ] update `docs/modules/recommendation.md` body cap to one value；
- [ ] update `docs/modules/llm.md`, `docs/modules/config.md`, changelog and architecture/data-flow
      notes where ownership changed；
- [ ] mark superseded historical gate claims explicitly；
- [ ] update this plan with actual commands/results。

**Gate:** documentation contains no contradictory retry/body-cap/replay claims found by targeted `rg`.

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

## Task 8 — Replay and end-to-end acceptance

**Owner:** root

1. Validate DB/config prerequisites without printing secrets。
2. Run compact, body-cap and reason-diet commands from Spec §6B。
3. Validate each JSON artifact structurally and recompute key metrics independently from raw scores。
4. Run deterministic candidate-pipeline E2E cases from Spec §6C。
5. Smoke `openbiliclaw config-show` and relevant API config serialization。
6. Record exact commit, commands, pass counts, skips, artifact paths/digests and unresolved environmental
   limitations。

**Final gate:** no code/test/docs/replay blocker remains. If a required real-data or provider prerequisite
is unavailable, the branch is reported blocked rather than described as release-ready.

## Task 9 — Landing handoff

**Owner:** root

- [ ] concise change summary；
- [ ] explicit review findings and how each was closed；
- [ ] test/replay evidence；
- [ ] remaining rollout observation and rollback points；
- [ ] confirm no secrets/artifacts intended to stay local were committed。

