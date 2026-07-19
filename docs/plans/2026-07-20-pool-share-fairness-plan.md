# Pool Share Fairness — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-20-pool-share-fairness-spec.md`](./2026-07-20-pool-share-fairness-spec.md)
> **Status:** rev 1, awaiting execution
> **Execution order:** Task 1 → 2 → 3 → 4 → 5(严格顺序;Task 2 依赖 Task 1 的口径,Task 3 依赖 Task 2 的份额判定)
> **Tech:** Python 3.12,主仓 venv `.venv/bin/python`;worktree 内跑测试须设 `PYTHONPATH=<worktree>/src` 与 `OPENBILICLAW_PROJECT_ROOT=<worktree>`。测试 `.venv/bin/python -m pytest <file> -x -q`;lint `.venv/bin/python -m ruff check src/ tests/`;format `.venv/bin/python -m ruff format src/ tests/`;类型 `.venv/bin/python -m mypy src/`。

**Invariants that MUST hold — re-read before each task:**

- 全局封顶:任何入池路径完成后可见池 ≤ `pool_target_count`。
- 生产缺口自身份额口径:`available(s) < target(s)` ⇒ `_source_requested_count(s) > 0`(仅受 raw headroom 钳制)。
- 入池份额优先:欠份额行先录;超份额行仅在全局低于目标且无欠份额可录时兜底。
- 质量不回退:退坑只动超份额来源的最低分最老行,每 tick ≤ 3;不新增 `pool_status` 枚举,复用 `'stale'`。
- 向后兼容:pipeline 未注入份额策略时 admission 行为与现状逐字节一致;`get_evaluated_discovery_candidates_for_admission` 缺省排序不变。
- 来源归族一律用 `_pool_source_family` 口径。

### Task 1: 生产端缺口改为自身份额口径

**Files:** modify `src/openbiliclaw/runtime/refresh.py`(`_source_requested_count`);add `tests/test_refresh_source_deficit.py`。

**Interfaces:** Consumes: 每源 available/raw 计数、share targets。Produces: `_source_deficit` / `keyword_planner_real_deficit` / `_build_source_replenishment_plan` 共用的新口径。

**Steps:**

- [ ] Write one focused failing test:全局 available=300(构成 {bilibili:288, bangumi:12},shares {bilibili:5, bangumi:1},target 300)时 `_source_deficit("bangumi") == 38` 且 `_tick_bangumi_producer` 调用 producer。
- [ ] Run `.venv/bin/python -m pytest tests/test_refresh_source_deficit.py -x -q` and confirm FAIL for the intended missing behavior.
- [ ] Add the minimal implementation:删除 `global_available_deficit` 的 min 钳制;保留 `current_global_available` 读取与 `_update_llm_inventory_state`;raw headroom 逻辑与注释语义原样保留。
- [ ] Rerun focused test and confirm PASS with no warnings.
- [ ] Run `.venv/bin/python -m pytest tests/ -x -q -k "deficit or replenish or refresh_plan or keyword_planner"`、ruff、mypy;修复受新口径影响的既有测试**断言值**(若某测试专门断言"全局满则缺口为零"的旧语义,更新为新语义并在测试 docstring 注明 spec 依据;不得为凑绿弱化断言)。

**Acceptance:**

- Numeric gate:复现场景(300/300, bangumi 12/50)deficit 由 0 → 38;份额内场景 deficit 保持 0;raw headroom 钳制场景结果与现状一致。
- Reproduce with `.venv/bin/python /tmp/bangumi_starvation_repro.py`(验收人持有;场景 2 应显示 producer_called=True)。

### Task 2: 入池份额感知(两轮录取 + 队列欠份额优先)

**Files:** modify `src/openbiliclaw/discovery/candidate_pipeline.py`(`_admit_until_full`、`admit_evaluated`、`_admit_evaluated_candidates`、装配点)、`src/openbiliclaw/storage/database.py`(`get_evaluated_discovery_candidates_for_admission` 加 `preferred_source_platforms` 可选参)、`src/openbiliclaw/api/runtime_context.py` 与 `runtime/refresh.py`(注入份额策略);add `tests/test_candidate_pipeline_admission.py`。

**Interfaces:** Consumes: `_pool_source_family`、`count_pool_available_candidates_by_source`、controller 的 `_source_target_counts()`。Produces: pipeline 的份额感知 admission;`None` 策略 = 旧行为。

**Steps:**

- [ ] Write focused failing tests:①欠份额行先于队列更靠前的超份额行入池;②全局未满且无欠份额供给时超份额行兜底入池;③策略未注入时行为与现状一致(用现有测试夹具对拍);④B站四策略行归 bilibili 族。
- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_pipeline_admission.py -x -q` and confirm FAIL for the intended missing behavior.
- [ ] Add the minimal implementation:admission 开始时快照每源 available,过程中本地增量;第一轮跳过超份额行(保持 evaluated,不 reject);第二轮兜底;DB 查询 `CASE WHEN source_platform IN (…) THEN 0 ELSE 1 END` 前置排序,缺省不传时 SQL 与现状一致。
- [ ] Rerun focused tests and confirm PASS with no warnings.
- [ ] Run `.venv/bin/python -m pytest tests/ -x -q -k "admission or candidate_pipeline or drain"`、ruff、mypy。

**Acceptance:**

- Numeric gate:构造 evaluated 队列 [超份额×5, 欠份额×2] + 池 298/300 → 入池的恰为 2 条欠份额行;策略为 None 时同一夹具入池前 2 条 FIFO 行。
- Reproduce with `.venv/bin/python -m pytest tests/test_candidate_pipeline_admission.py -q`;record the result in the PR.

### Task 3: 温和再平衡退坑

**Files:** modify `src/openbiliclaw/runtime/refresh.py`(drain tick 内 admission 前调用 `_rebalance_pool_shares()`)、`src/openbiliclaw/storage/database.py`(如需:按源取最低分 fresh 行并置 `'stale'` 的接口);add tests(并入 `tests/test_refresh_source_deficit.py` 或独立文件)。

**Interfaces:** Consumes: 每源 available/target、欠份额来源的 evaluated 等待计数、`content_cache` 行。Produces: 每 tick ≤3 行的超份额退坑。

**Steps:**

- [ ] Write focused failing tests:①有欠份额等待供给且全局满 → 超额最多来源退 min(3, 超额, 等待供给) 行,选择顺序 `relevance_score ASC, last_scored_at ASC`;②无欠份额等待供给 → 零退坑;③来源到 target 即停。
- [ ] Run focused test command and confirm FAIL for the intended missing behavior.
- [ ] Add the minimal implementation(退坑写 `pool_status='stale'`,INFO 日志:来源/行数/受益源)。
- [ ] Rerun focused tests and confirm PASS with no warnings.
- [ ] Run `.venv/bin/python -m pytest tests/ -q`(全量)、ruff、mypy。

**Acceptance:**

- Numeric gate:模拟 {reddit:169} 超额 + bangumi evaluated 等待 ≥3 → 单 tick 恰退 3 行且全为 reddit 最低分;连续 tick 收敛期间全局池始终 ≤ 300。
- Reproduce with focused pytest command;record the result in the PR.

### Task 4: 可观测性

**Files:** modify `src/openbiliclaw/runtime/refresh.py`;tests 并入既有新测试文件(caplog)。

**Steps:**

- [ ] Write focused failing test:每源 (available, target, deficit) 快照变化时打一条含全部来源的 INFO 单行,连续两次相同快照只打一次。
- [ ] Run focused test command and confirm FAIL.
- [ ] Add the minimal implementation(沿用 `_log_empty_refresh_plan_diagnostics` 节流风格;admission 跳过计数进 debug)。
- [ ] Rerun focused test and confirm PASS with no warnings.
- [ ] Run ruff、mypy、全量 pytest。

**Acceptance:**

- Numeric gate:caplog 断言快照变化打 1 条、不变打 0 条。

### Task 5: 文档同步

**Files:** modify `docs/modules/` 中调度/发现相应文档(执行时核对实际文件名)、`docs/changelog.md`(当前版本块加 bullet)。

**Steps:**

- [ ] 更新模块文档的份额执行语义(生产口径、两轮录取、再平衡、日志)。
- [ ] changelog bullet:一句话描述 bug 与修复。
- [ ] 核对无架构图/config 文档触发(spec 文档义务节)。

**Acceptance:** 文档描述与最终代码行为一致;pre-merge checklist 可勾选。

## Verification after merge

验收人(主会话)在隔离项目根执行真实 E2E:拷贝本机真实 DB(reddit 169 超份额饿死态)→ 启用 bangumi(真实 bgm.tv 匿名 API,走 custom 代理)+ 商汤 LLM → 起真实 serve-api → 观察 ≥3 个 drain tick:①`bangumi_discovery_runs` 出现新行;②池组成收敛(reddit 净减、bangumi 净增);③全局池恒 ≤300;④日志出现每源缺口摘要与退坑记录。回滚触发条件:全局池超发、退坑波及非超份额来源、或全量测试回归失败——revert 整个 feature 分支。

## Explicitly out of scope

- 各 producer 内部节奏/预算调整(60 分钟间隔等)。
- B站池满时 trending/explore/signal 生产路径。
- `pool_source_shares` 配置格式或新 CLI。
- 池行 TTL/老化等更大的轮换机制。
