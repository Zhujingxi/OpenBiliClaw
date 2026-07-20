# Pool Share Fairness Spec — 低份额来源不再被超份额来源永久饿死

**Created:** 2026-07-20
**Scope:** 候选池份额执行体系:生产端缺口计算(`runtime/refresh.py`)、入池录取(`discovery/candidate_pipeline.py`)、录取队列排序(`storage/database.py`)、温和再平衡与可观测性。
**Out of scope:** 各来源 producer 内部逻辑(bangumi/reddit/… 的分支、预算、游标)、`pool_source_shares` 配置格式、B站 trending/explore/signal 的池满时新鲜度维护路径、CLI 新命令。

## Goal

**现状故障(已在真实生产数据中确认):** 本机池子 300/300 顶满,其中 reddit 占 169(份额目标 25,超 7 倍),douyin/youtube/zhihu/twitter/bangumi 全部为 0;用户另一台机器上 B站 挤死 bangumi,表现为"初始化补入一批后 bangumi 永不再补货"。

**量化目标:**
1. 全局池满且某来源低于自身份额目标时,该来源的生产缺口 > 0(当前恒为 0)。
2. 欠份额来源的已评估候选能在坑位释放时优先入池;超份额来源仅在无欠份额供给时才继续占坑。
3. 存在欠份额等待供给时,超份额来源以每 drain tick ≤ 3 行的速率退坑,池组成单调向配置份额收敛。
4. 全局可见池总量始终 ≤ `pool_target_count`(不因本修复超发)。

**验证命令:**
- 单元:`.venv/bin/python -m pytest tests/test_refresh_source_deficit.py tests/test_candidate_pipeline_admission.py -x -q`(新增文件,名字可依仓库惯例调整)
- 复现脚本(修前 deficit=0 / 修后 >0):`/tmp/bangumi_starvation_repro.py` 场景 2
- 真实 E2E:拷贝本机真实 DB(reddit 超份额饿死态)到隔离项目根,启用 bangumi,起真实 serve-api,观察 `bangumi_discovery_runs` 新增行与池组成收敛(验收人执行,见 Plan)。

## Design invariants (MUST hold in every phase)

1. **全局封顶:** 任何路径入池后,`count_pool_candidates()` ≤ `pool_target_count`。验证面:admission 单元测试 + E2E 后查询。
2. **生产缺口以自身份额为准:** `_source_requested_count(s)` 当 `available(s) < target(s)` 时必须 > 0(受 raw headroom 钳制,不再受全局满额钳制)。验证面:deficit 单元测试(全局 300/300、bangumi 12/50 → deficit=38)。
3. **入池份额优先:** 单次 admission 中,欠份额来源的行先于超份额来源;超份额来源的行仅当"全局池低于目标且没有欠份额行可录"时才入池(可用性兜底优先于纯净度)。验证面:admission 两轮单元测试。
4. **质量不回退(仓库铁律):** 再平衡只退超份额来源中 `relevance_score` 最低、`last_scored_at` 最老的行,每 tick ≤ 3 行,退坑写入可追溯的 `pool_status`;绝不退欠份额或恰好在份额内的来源。验证面:rebalance 单元测试断言选择顺序与上限。
5. **向后兼容:** `candidate_pipeline` 未注入份额策略(现有测试、OpenClaw one-shot 桥)时,admission 行为与现状逐字节一致。验证面:现有全量 pytest 通过,无需改旧测试语义。
6. **可观测:** 每源 `available/target/deficit` 摘要在数值变化时打 INFO(带节流),tick 层"缺口为零跳过"不再完全静默。验证面:日志单元测试或 caplog 断言。

## Current diagnosis

### D1. 生产端缺口被全局满额清零(confirmed)

`runtime/refresh.py:3187` `requested_by_available = max(0, min(available_deficit, global_available_deficit))`——全局池达标时 `global_available_deficit=0`,任何欠份额来源的缺口都被 min 清零,`_tick_bangumi_producer`(`refresh.py:2034`)在 `deficit <= 0` 处静默 return,producer 从不被调用。确定性复现:全局 300/300 + bangumi 12/50 → deficit=0,producer 零调用。

### D2. 入池端不感知份额(confirmed)

`discovery/candidate_pipeline.py:866` `_admit_until_full` 仅以 `self._pool_full()`(全局)为闸,逐行录取 FIFO 队列(`storage/database.py:2979`,`ORDER BY evaluated_at ASC`),对候选所属来源与该来源是否超份额零感知。积压充足的来源(本机为 reddit,用户机为 B站)在 60 秒 drain tick 里抢占一切释放的坑位。

### D3. 节奏不对称放大饿死(confirmed)

drain/admission 每 60s 一次(`refresh.py:1421` 附近 loop 注释),而 bangumi producer 有 60 分钟最小间隔(`bangumi_producer.py:71`);即使全局出现瞬时缺口,低频 producer 也抢不到窗口。修复 D1+D2 后该不对称不再致命(欠份额供给可在 raw/evaluated 层等待,入池时优先),无需改节奏。

### D4. 真实生产数据佐证(confirmed)

本机 `data/openbiliclaw.db`:available = {reddit: 169, bilibili: 124, xiaohongshu: 7},合计恰 300;8 来源配置下 reddit 目标 25。饿死态在真实环境自然形成,可直接用作 E2E 基线数据。

### D5. B站池满时仍生产(confirmed, kept as-is)

`refresh.py:2097` 起:池 ≥ 目标时 trending/explore/signal 计划照常生成,产出进 raw/evaluated 积压。这是刻意的新鲜度维护;修复后其危害被入池份额闸约束(积压不能再越份额占坑),故不改动。

### D6. Producer 内部还有一道全局 pool_full 闸(confirmed by real E2E,Phase 1–3 解不开)

真实 serve-api 日志:`bangumi producer skip: reason=pool_full`。tick 层 deficit 修复已生效(producer 被调度),但每个 producer 的 `produce_if_due` 内部还有一道**全局** pool 闸 `_candidate_pool_full()`(`bangumi_producer.py:94` 附近,调 `candidate_pipeline.pool_full()` = 全局可见池是否达标)。这形成鸡生蛋死结:bangumi 因全局池满不能生产 → 永远没有 bangumi 的 `evaluated` 供给 → Phase 3 rebalance 的触发条件「欠份额来源有 `evaluated` 供给等待」不满足 → 不退坑 → 池永远 300/300 → bangumi 永远 pool_full。波及 `bilibili/reddit/zhihu/youtube/bangumi/douyin` 六个 producer(其中 reddit 的旧闸引用了不存在的 `is_candidate_pool_full`,实际已是 no-op;`xhs`/`x` 为 fetch-only,自查确认无此内部闸)。

### D7. Phase 3/4 挂载点在生产装配下是死代码(confirmed by real E2E)

`_rebalance_pool_shares()` 与 `_log_source_deficit_summary()` 挂在 `_drain_discovery_candidates_and_precompute`(经 `_loop_candidate_eval` 驱动)。但生产 serve-api 装配(`api/runtime_context.py:1144` 起)注入 `CandidateEvalCoordinator`,`refresh.py:1455-1459` 用 `coordinator.run_forever()` **替换** `_loop_candidate_eval()`,于是那个方法永远不被调用——Phase 3/4 在生产装配下是死代码。E2E 实机佐证:bangumi 已真实拉取 29 条入原料池(Task 1/6 生效)、xiaohongshu 有 4 条 evaluated 等待且欠份额(7/25)、池 300/300 顶满——完全满足退坑条件,却零退坑、零份额摘要日志。修复须把再平衡与摘要移到**两种装配都必经的收敛点**;两装配互斥(`coordinator.run_forever()` XOR `_loop_candidate_eval()`,且 `drain_discovery_candidates_once` 与 refresh 内联 drain 在 coordinator 存在时都提前 notify 返回),故单轮至多一次再平衡。

### D8. 无份额来源的存量行永久挤占份额(confirmed by final E2E)

`_rebalance_pool_shares` 挑超额来源时只遍历 `target_counts.items()`。本机 E2E:配置只有 bangumi+reddit 参与份额(各 150 目标),但池里躺着 bilibili 141 行、xiaohongshu 7 行——这两个来源已被用户禁用、不在 `target_counts` 里,于是这 148 行「无主占坑者」永远不被选为超额来源、永远不可回收;reddit 超额仅 2,bangumi 的 150 缺口无坑可腾。真实后果:用户禁用某来源后,其存量行会永久挤占其他来源的份额。修复:挑超额来源时把「出现在 `available_by_source` 但不在 `target_counts` 的来源族」按 target=0 处理(全额计为超额、可退);available key 先经 `source_family` 归族(禁用 bilibili 可能以四策略名出现),再算超额。排序与「挑超额最多的单一来源、每 tick ≤3、最低分最老先退」等不变量全部保持。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 1 | 生产端缺口改为自身份额口径 | **MUST** | 不修则欠份额来源永不生产,后续修复无货可入 |
| 2 | 入池份额感知(两轮录取 + 队列欠份额优先排序) | **MUST** | 不修则 Phase 1 的产出永远排在超份额积压后面 |
| 3 | 温和再平衡退坑(每 tick ≤3) | RECOMMENDED | 不修则收敛只能等自然消耗,超份额 144 行可能数周不退 |
| 4 | 可观测性(变化才打的每源缺口摘要) | RECOMMENDED | 本次排查因三处静默跳过而代价高昂 |

依赖:Phase 2 依赖 Phase 1 提供供给;Phase 3 依赖 Phase 2 的份额判定函数;Phase 4 独立。
**Wave A(一次出货):** Phase 1 + 2 + 4。**Wave B:** Phase 3(可独立评估质量影响后合入;本次一并实现,验收时单独观察)。

## Phase designs

### Phase 1 — 生产端缺口自身份额口径

`_source_requested_count`(`refresh.py:3156`)删除对 `global_available_deficit` 的 min 钳制,改为:

```
requested_by_available = available_deficit          # max(0, target(s) - available(s))
if requested_by_available <= 0: return 0
# raw headroom 钳制逻辑保持原样(含"trimming guard 非硬停"注释语义)
```

`current_global_available` 的读取与 `_update_llm_inventory_state` 调用保留(库存观测仍需要)。安全论证:总产出受"自身份额缺口 + per-source raw ceiling"双重约束;admission 全局封顶不变,可见池不会超发。受影响调用方(`_build_source_replenishment_plan`、`keyword_planner_real_deficit`、全部 `_tick_*_producer`)同步获得公平口径——B站自身也适用,对称无特例。

测试:全局满 + 欠份额 → deficit>0;全局满 + 份额内 → deficit=0;raw headroom 钳制回归。

### Phase 2 — 入池份额感知

1. `ContinuousRefreshController` 向 `candidate_pipeline` 注入份额策略(构造时传 callback 或在 drain 调用处传参,实现者依现有装配代码选侵入最小者;`None` 时旧行为,invariant 5)。策略提供 `{source_family: target}` 与每源当前 available(单次 admission 开始时快照一次,admission 过程中本地增量维护,不重复查库)。
2. `_admit_until_full` 两轮:第一轮跳过"该源 available ≥ target"的行(保持 `evaluated` 状态原地等待,不 reject);第一轮结束若全局仍 < target 且不存在可录的欠份额行,第二轮允许超份额行填满全局(可用性兜底)。
3. `get_evaluated_discovery_candidates_for_admission`(`database.py:2979`)加可选参 `preferred_source_platforms: Sequence[str]`,排序改 `CASE WHEN source_platform IN (…) THEN 0 ELSE 1 END, evaluated_at ASC, …`,pipeline 以欠份额来源清单传入——防止 FIFO 窗口内全是超份额行而欠份额行取不进来。缺省不传时排序与现状一致。
4. 来源归族必须复用 `_pool_source_family` 口径(bilibili 四策略归 bilibili 族),不得按裸 `source_platform` 判定。

测试:两轮语义、族归并、preferred 排序、未注入策略时逐字节旧行为。

### Phase 3 — 温和再平衡退坑

drain tick 内、admission 之前新增 `_rebalance_pool_shares()`:条件 =(全局池 ≥ 目标)且(存在欠份额来源有 `evaluated` 供给等待)。动作 = 选超额最多的一个来源,将其池内 `relevance_score ASC, last_scored_at ASC` 的前 N 行(N = min(3, 该源超额数, 欠份额等待供给数))`pool_status` 置为 `'stale'`(复用既有状态,不新增枚举);同 tick 的 admission 即用释放坑位录取欠份额行。来源降至 target 即停。

测试:选择顺序(最低分先退)、每 tick 上限、无欠份额供给时不退坑、退坑后全局仍 ≤ 目标。

### Phase 4 — 可观测性

1. controller 持有上次每源 `(available, target, deficit)` 快照,数值变化时打一条 INFO 摘要(单行,全部来源),沿用 `_log_empty_refresh_plan_diagnostics` 的节流风格;不变化不打。
2. rebalance 每次退坑打 INFO:来源、行数、腾给了谁。
3. admission 第一轮跳过的超份额行数进 debug 日志。

### Phase 5 — Producer 内部 pool_full 闸份额感知(E2E 发现,见 D6)

`candidate_pipeline` 新增 `pool_full_for_source(source_family)`:注入份额策略且该源低于自身份额 → 返回 `False`(即使全局满,两轮 admission + rebalance 会腾坑);该源已达/超份额或未传 family → 沿用全局 `pool_full()`;未注入策略 → 完全等同 `pool_full()`(不变量 5)。六个 producer 的内部闸 `_candidate_pool_full()` 改经共享助手 `runtime/pool_gate.candidate_pool_full_for_source(pipeline, family, …)` 调用它,并传入各自的 `_pool_source_family` 口径 family(bilibili 族归并);pipeline 缺该方法时保守回退全局 `pool_full()`,再退 `False`。这打破 D6 死结:bangumi 欠份额时即使全局满也能生产,产出 `evaluated` 供给,rebalance 才有触发条件。

测试:全局满 + 欠份额 → `pool_full_for_source` False、`produce_if_due` 不再 skip pool_full;全局满 + 已达份额 → True;未注入策略 → 等同 `pool_full()`;全局未满 → 恒 False。

### Phase 6 — 再平衡/摘要挂到两装配共同的收敛点(修 D7)

新增 controller 入口 `run_pool_share_maintenance()`(顺序调 `_rebalance_pool_shares()` + `_log_source_deficit_summary()`,吞异常不打断 eval loop)。legacy drain 的两处 `with suppress` 合并为调它一次;`CandidateEvalCoordinator` 新增 `pre_admit_hook` 参数,`run_forever` 每 tick 在 `_admit_evaluated` 之前调一次;`runtime_context` 以 `getattr` 守卫把 `controller.run_pool_share_maintenance` 注入 hook(测试替身缺该方法时不注入)。两装配互斥 → 单轮至多一次再平衡;hook 在 admission 前跑 → 退坑腾的坑同 tick 被欠份额供给填上。语义(触发条件、每 tick ≤3、只动最超份额来源最低分最老行、INFO 日志)与 Phase 3/4 完全一致。

测试:coordinator 每 tick 在 admission 前调 hook(顺序断言);`run_pool_share_maintenance` 顺序调 rebalance→summary;legacy(无 coordinator)路径回归不变。

### Phase 7 — 无份额来源存量行可回收(修 D8)

`_rebalance_pool_shares` 挑超额来源的候选集从「`target_counts` 的族」扩为「`target_counts` 的族 ∪ `available_by_source` 各 key 经 `source_family` 归族后的族」;缺席 `target_counts` 的族按 target=0 计算超额(全额可退)。选择仍是「超额最多的单一来源」,`min(3, overage, fillable)`、最低分最老先退、INFO 日志、`fillable` 仍只按在册欠份额来源的 evaluated 等待量计——全部不变。

测试:available={bilibili:141, reddit:152, xiaohongshu:7}、targets={bangumi:150, reddit:150}、bangumi evaluated≥3 → 单 tick 退 bilibili(超额最大)3 行;bilibili 以四策略名出现时归族后同样命中;既有(仅在册来源)场景不变。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 1 | 饿死复现场景 deficit 0 → 38;bangumi producer 恢复小时级调度 |
| Phase 2 | 坑位释放后欠份额行优先入池;超份额来源停止净增长 |
| Phase 3 | 本机 reddit 169→25 的收敛从"数周自然消耗"变为 ≤3 行/分钟的可控速率(理论 <1 小时,实际受欠份额供给节奏限制) |
| Phase 4 | 下次同类问题可从日志直接读出每源缺口,免于本次的三层静默排查 |
| Phase 5 | 解除 producer 内部全局闸死结:欠份额来源即使全局满也能生产 `evaluated` 供给,Phase 3 rebalance 得以触发,`reason=pool_full` 不再冤枉欠份额来源 |
| Phase 6 | Phase 3/4 不再是生产装配下的死代码:coordinator 每 tick 触发退坑与份额摘要,E2E 中 300/300 顶满 + xhs 7/25 欠份额 + evaluated 等待的场景真正退坑 |
| Phase 7 | 禁用来源的存量行(不在份额表里)不再永久占坑:按 target=0 计为超额可退,bangumi 等欠份额来源终于有坑可腾 |

## Documentation obligations

- `docs/modules/discovery.md`(或调度所在模块文档,实现者核对 `docs/modules/` 实际文件):份额执行语义(生产口径、入池两轮、再平衡)更新。
- `docs/changelog.md`:当前版本块新增 bullet。
- 架构图:无新模块/新依赖块,不触发。
- config 文档:`pool_source_shares` 字段未变,不触发;若模块文档中描述过旧语义则同步修正。
