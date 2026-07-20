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

### Task 6: Producer 内部 pool_full 闸份额感知(真实 E2E 发现,见 spec D6 / Phase 5)

**Files:** add `src/openbiliclaw/runtime/pool_gate.py`(共享助手);modify `src/openbiliclaw/discovery/candidate_pipeline.py`(`pool_full_for_source`)、`src/openbiliclaw/runtime/{bilibili,douyin,youtube,zhihu,bangumi,reddit}_producer.py`(内部闸改调助手);add tests(`tests/test_candidate_pipeline_admission.py` 的 `pool_full_for_source` 用例 + `tests/test_bangumi_producer.py` 的欠份额 producer 用例)。

**Interfaces:** Consumes: `_share_targets`、`_available_by_family`、`_pool_full`、`_source_family`。Produces: `pipeline.pool_full_for_source(family)`;共享 `candidate_pool_full_for_source(pipeline, family, *, logger, label)`(pipeline 缺方法 → 回退全局 `pool_full()` → `False`)。

**Steps:**

- [x] 先写失败测试:①全局满 + 欠份额 → `pool_full_for_source` False;②全局满 + 已达份额 → True;③未注入策略 → 等同 `pool_full()`;④全局未满 → 恒 False;⑤bangumi `produce_if_due` 在「全局满 + bangumi 欠份额」时不再 `reason=pool_full`。
- [x] 确认 FAIL(4 个 pipeline 断言 + 1 个 producer 断言)。
- [x] 实现:pipeline `pool_full_for_source`;`runtime/pool_gate.py` 共享助手;六个 producer 内部闸改调助手并传各自 family(bilibili 族归并;reddit 旧的 `is_candidate_pool_full` 死闸一并替换)。`xhs`/`x` 自查确认无内部闸,不改。
- [x] 确认 PASS(pipeline 4 + 六个 producer 既有回归 + bangumi 新用例);ruff、mypy、全量 pytest。

**Acceptance:**

- Numeric gate:`pool_full_for_source("bangumi")` 在 300/300 + bangumi 0/50 → **False**;`("reddit")` 在 169/25 → **True**;未注入策略时 == `pool_full()`;全局未满恒 False。bangumi `produce_if_due` 在全局满 + 欠份额时 `reason != "pool_full"`。
- 既有断言更新:无(六个 producer 的 `_Pipeline`/`_FakeCandidatePipeline` 桩只有 `pool_full()`/无任何池方法,经 getattr 回退逐字节保持旧行为;bangumi 桩新增可选 `pool_full_for_source` 仅供新用例,旧 `full=True` 用例仍 skip pool_full)。

### Task 7: 再平衡/摘要挂到两装配共同的收敛点(真实 E2E 发现,见 spec D7 / Phase 6）

**Files:** modify `src/openbiliclaw/runtime/refresh.py`(新增 `run_pool_share_maintenance()`,legacy drain 改调它)、`src/openbiliclaw/runtime/candidate_eval.py`(新增 `pre_admit_hook` 参数 + `_run_pre_admit_hook`,`run_forever` 每 tick admission 前调用）、`src/openbiliclaw/api/runtime_context.py`（`getattr` 守卫注入 hook）；add tests（`tests/test_candidate_eval_coordinator.py` 的 hook 顺序用例 + `tests/test_refresh_source_deficit.py` 的 `run_pool_share_maintenance` 用例）。

**Interfaces:** Consumes: `controller._rebalance_pool_shares`/`_log_source_deficit_summary`（已在 Task 3/4 落地）。Produces: `controller.run_pool_share_maintenance()`（两装配唯一入口）;`CandidateEvalCoordinator(pre_admit_hook=…)`。

**Steps:**

- [x] 先写失败测试:①coordinator 每 tick 在 `_admit_evaluated` 之前调 `pre_admit_hook`（顺序断言 `["hook","admit"]`）；②`run_pool_share_maintenance()` 顺序调 rebalance→summary。
- [x] 确认 FAIL（`pre_admit_hook` 非法参数 + `run_pool_share_maintenance` 不存在）。
- [x] 实现:controller `run_pool_share_maintenance()`（吞异常）；legacy drain 两处 `with suppress` 合并为调它一次；coordinator `pre_admit_hook` 参数 + `_run_pre_admit_hook`（admission 前、吞异常）；runtime_context 以 `getattr` 守卫注入 `controller.run_pool_share_maintenance`。
- [x] 确认 PASS;全量 pytest + ruff + mypy。

**防重复执行依据:** 三个 `_drain_discovery_candidates_and_precompute` 调用点在 coordinator 存在时都不生效——`_loop_candidate_eval`（唯一周期驱动）仅在 `candidate_eval_coordinator is None` 时被 `run_forever` 调度（`refresh.py:1455-1459` XOR）；`drain_discovery_candidates_once` 与 refresh 内联 drain 在 `coordinator.notify` 可调用时都提前 return。故 coordinator 装配下退坑只经 hook，legacy 装配下只经 drain,单轮至多一次。

**Acceptance:**

- Numeric gate:coordinator 一轮的事件序列首二项 == `["hook","admit"]`;`run_pool_share_maintenance` 调用序列 == `["rebalance","summary"]`；六个 producer 与既有 coordinator 回归全绿。
- 既有断言更新:无（新增参数带默认值 `None`,既有 coordinator 构造/用例不受影响；bootstrap 装配测试的 `FakeRuntimeController` 缺 `run_pool_share_maintenance`,经 `getattr` 守卫回退为不注入 hook）。

### Task 8: 无份额来源存量行可回收(真实 E2E 发现,见 spec D8 / Phase 7)

**Files:** modify `src/openbiliclaw/runtime/refresh.py`(`_rebalance_pool_shares` 超额候选集 + `source_family` 归族 import）；add tests（`tests/test_refresh_source_deficit.py`）。

**Interfaces:** Consumes: `available_by_source`、`target_counts`、`_platform_source_count`、`source_family`。Produces: 退坑候选集含缺席 `target_counts` 的族(target=0)。

**Steps:**

- [x] 先写失败测试:①available={bilibili:141, reddit:152, xiaohongshu:7}、targets={bangumi:150, reddit:150}、bangumi evaluated≥3 → 退 bilibili 3 行(不是 reddit 2 行)；②bilibili 以 {search:100, explore:41} 出现时归族后仍退 "bilibili" 3 行。
- [x] 确认 FAIL（旧逻辑只挑 reddit、退 2 行）。
- [x] 实现:候选族 = `set(target_counts)` ∪ {`source_family(k,k)` for k in available_by_source}；缺席 target 的族 target=0 计超额;排序/上限/最低分口径不变。
- [x] 确认 PASS;既有 rebalance 测试(仅在册来源)全绿；全量 pytest + ruff + mypy。

**Acceptance:**

- Numeric gate:D8 复现场景单 tick `demote_calls == [("bilibili", 3)]`;strategy-name 变体同样 `[("bilibili", 3)]`;既有 `test_rebalance_*`(reddit 超额场景)结果不变。
- 既有断言更新:无(新增候选族只增不减；仅在册来源的既有场景候选族不变,选择结果一致)。

## Verification after merge

验收人(主会话)在隔离项目根执行真实 E2E:拷贝本机真实 DB(reddit 169 超份额饿死态)→ 启用 bangumi(真实 bgm.tv 匿名 API,走 custom 代理)+ 商汤 LLM → 起真实 serve-api → 观察 ≥3 个 drain tick:①`bangumi_discovery_runs` 出现新行;②池组成收敛(reddit 净减、bangumi 净增);③全局池恒 ≤300;④日志出现每源缺口摘要与退坑记录。回滚触发条件:全局池超发、退坑波及非超份额来源、或全量测试回归失败——revert 整个 feature 分支。

## Explicitly out of scope

- 各 producer 内部节奏/预算调整(60 分钟间隔等)。
- B站池满时 trending/explore/signal 生产路径。
- `pool_source_shares` 配置格式或新 CLI。
- 池行 TTL/老化等更大的轮换机制。
