# 候选补货空转冷却 Spec — 源枯竭时补货循环从 ~9000 次/时降到 ≤10 次/时

**Created:** 2026-07-16
**Scope:** 候选评估协调器(`runtime/candidate_eval.py`)、刷新控制器空计划诊断路径(`runtime/refresh.py`)、协调器状态载荷(`status_payload`)。
**Out of scope:** 内容源自身的可用性修复(YouTube 被代理拒连、xhs producer throttle 属网络/源侧问题,另行处理);`refresh_if_needed` 的计划构建逻辑与水位线阈值(`_DISCOVERY_REPLENISH_LOW_WATERMARK_RATIO`)不动;补货慢的候选杠杆(升级冷却共享等,见 pool-replenishment 既有分析)不在本期;扩展/桌面 UI 新增"源不可用"提示面(仅后端 status 字段就绪,四端 UI 展示显式排除,依 CLAUDE.md 第 5 条)。

## Goal

**现状成本(用户报告 + 日志实证)**:桌面端更新到 v0.3.171 后笔记本风扇狂转、发热明显,退出桌面端即恢复——后端进程在"内容池低于目标 + 所有源无法供货"状态下以 **2–3 Hz** 热轮询补货。2026-07-16 单日日志:`refresh plan empty` **6742 条**、`pool maintenance skipped unchanged fingerprint` **6733 条**(两者 1:1,对应约 6740 次 `refresh_if_needed` 调用,峰值每分钟 313 条日志);更新前(07-01 及以前)同指标每天 ≤884 条。每次空转调用含多次 SQLite count 查询 + 池就绪读取 + 全源诊断统计,持续占满一个核。

**量化目标**:

1. 源枯竭稳态下,协调器补货请求 ≤ **10 次/模拟小时**(现约 9000 次/时)。
2. `refresh plan empty` 全量诊断(含 3 组 DB 统计)在指纹未变化时 ≤ **12 次/时**(每 300s 至多 1 次)。
3. 外部唤醒(手动刷新 / 配置变更 / 事件摄入)后 **1 个循环迭代内**发出补货探测——响应性不回退。

**验证命令**:

```bash
.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q      # 冷却阶梯 + 唤醒响应
.venv/bin/python -m pytest tests/test_refresh.py tests/test_refresh_runtime.py -q  # 空计划诊断节流
.venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m mypy src/
```

上线后实机验证:`grep -c 'refresh plan empty' <日志>` 单日 < 300(回到回归前水平);任务管理器/活动监视器中 serve-api 空闲 CPU < 5%。

## Design invariants (MUST hold in every phase)

1. **源枯竭补货频率有界**:supply_callback 连续返回"无产出"结果时,补货请求间隔按阶梯递增(30→60→120→300→600s 封顶),任意模拟小时内补货调用 ≤10 次。验证面:`tests/test_candidate_eval_coordinator.py::test_unproductive_supply_backs_off_exponentially`(fake clock)。
2. **唤醒响应性零回归**:`notify()` 到达(任意 reason)即清除当前冷却窗口,下一迭代允许一次立即补货探测;`manual_*`/`config_*`/`startup` 额外把阶梯清零。既有快槽回填测试(`test_fast_worker_refills_under_one_second_with_sixty_second_safety_wake`)必须全程绿。验证面:新增 notify-穿透测试 + 既有回填测试。
3. **有产出即复位**:补货结果 `refreshed=True` 或任一 worker 成功入池(`last_cached>0`)都把阶梯与冷却清零——冷却只惩罚"确认无产出",绝不惩罚正常运行。验证面:阶梯复位测试。
4. **未知结果形状不受罚**:supply_callback 返回非 mapping(旧测试替身、第三方管线)按"有产出"处理,不进入冷却——宁可漏节流,不可误伤兼容调用方。验证面:legacy-shape 测试。
5. **纯状态机改动**:不新增常驻定时器 / 后台任务 / 线程;冷却完全由既有 `run_forever` 循环 + `_wait_for_activity` 超时实现。验证面:code review + `asyncio.create_task` 调用点数量不变(supply/post-commit/wake 之外无新增)。
6. **阈值有出处**:阶梯常量必须带校准注释(依 CLAUDE.md pitfall #3):30s 起步 ≈ 2× `refresh_if_needed` 空转实测耗时上界的 60 倍,兼顾源短暂抖动的快恢复;600s 封顶 < xhs producer throttle 周期与 YouTube 拒连恢复观测粒度,保证源恢复后 ≤10 分钟自愈。
7. **诊断节流不吞首次与变化**:空计划全量诊断在(a)首次出现、(b)指纹变化、(c)距上次全量 ≥300s 三种情况必须以 INFO 全量输出;节流期间输出单行 DEBUG(含被抑制次数),不允许完全静默(依 CLAUDE.md pitfall #7:可诊断优先)。验证面:诊断节流测试断言两档日志行为。

## Current diagnosis

### D1. 协调器空补货路径无任何冷却,与 supply 任务完成即唤醒构成 2–3 Hz 自激环

确认事实(全部 file:line 实证):

- `runtime/candidate_eval.py:150-155`:`run_forever` 中当"无 worker 且 `pending_eval<=0` 且库存低于目标"时无条件调用 `_request_supply("candidate_supply")`——**该路径没有任何退避**。既有退避(`_backoff_until`)只由 worker 失败(`_record_failure`,`:299-323`)和零缓存连击(`:273-285`)设置,补货返回空不触发任何一条。
- `runtime/candidate_eval.py:324-344`:`_wait_for_activity` 把 `_supply_task` 加入 `asyncio.wait(..., FIRST_COMPLETED)` 等待集——supply 任务一完成(空结果也算)循环立即醒来。
- `runtime/candidate_eval.py:359-371`:`_settle_supply_task` 只检查异常,**丢弃结果字典**——`refresh_if_needed` 明确返回的 `{"refreshed": False, "reason": "below_threshold"}` 无人消费。
- `runtime/refresh.py:2036-2058`:池低于目标 + 低于补货水位线 + `_build_source_replenishment_plan()` 为空(B站到配额、xhs throttled、YouTube 拒连)→ 记录全量诊断并返回空计划;`runtime/refresh.py:2083-2140`:`_log_empty_refresh_plan_diagnostics` 每次执行 3 组 DB 统计(readiness / source_available / source_raw)+ 全源 requested 计算。
- 环路:请求补货 → `refresh_if_needed` 约 0.3–0.5s 返回空(含 `_enforce_pool_cap_async` 指纹检查,`refresh.py:1020-1044`)→ 任务完成唤醒循环 → 条件仍成立 → 再次请求。实测 2–3 Hz,与日志密度吻合。

日志实证(附件日志 `openbiliclaw (31).log`,2026-07-16):`refresh plan empty` 6742 条与 `pool maintenance skipped unchanged` 6733 条几乎 1:1;12:00–12:40 连续时段每秒 2–3 条;当时源状态:`xhs producer skip: reason=throttled`、YouTube 全 topic 页 `WinError 10061`、B站已到平台配额(`source_available={'bilibili': 131}` vs target 120)。

**为什么是"新版回归"**:`pool maintenance skipped unchanged fingerprint` 这条 DEBUG 仅在 07-16 日志出现(旧版无此代码);更新前 `refresh plan empty` 每日 ≤884 条。假设(未逐 commit 定位、不影响方案):v0.3.169–171 引入的 candidate-eval 供给回路(`api/runtime_context.py:1068-1070` 将 `refresh_if_needed` 接为 `supply_callback`)使协调器成为新的高频调用方,而旧版仅有慢周期调度器调用。

### D2. 空计划诊断本身是重查询 + 高频 INFO,放大了空转成本与日志噪声

- `runtime/refresh.py:2083-2140`:每次空计划都跑 3 组 DB 统计与全源循环计算,仅为产出一条 INFO——在 D1 环路里每天执行 ~6700 次。
- 该函数无任何节流/去重状态;`refresh.py:1030` 的池维护指纹机制(`_last_pool_maintenance_fingerprint`)证明本文件已有同型先例可循。
- 既有测试覆盖:`tests/test_refresh.py` / `test_refresh_runtime.py` 覆盖计划构建与维护指纹,无空计划诊断频率断言(grep 确认)。

### D3. 协调器对"供给侧枯竭"零可观测,用户只能靠风扇发现

- `runtime/candidate_eval.py:178-194`:`status_payload` 暴露 backoff/error/state,但没有任何补货冷却或枯竭征兆字段;源枯竭时 state 在 `waiting_supply`/`idle` 间跳动,无法与正常低负载区分。
- 无 WARNING 级"源枯竭"日志——6700 次空转全部是 INFO/DEBUG,运维 grep ERROR/WARNING 完全看不到。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | 协调器补货结果感知冷却(阶梯退避 + notify 穿透 + 复位) | **MUST** | 直接消除 CPU 燃烧根因;单独可发布 |
| 1 | 空计划诊断节流(300s 全量 + 期间 DEBUG 折叠) | RECOMMENDED | 纵深防御:任何未来调用方再spin也不放大 DB 查询与日志;噪声降 ~100× |
| 2 | 枯竭可观测(status 字段 + 阶梯登顶 WARNING)+ 文档 | RECOMMENDED | 让下一次源枯竭在日志/状态里可见,而不是靠风扇 |

依赖:Phase 1、2 均不依赖 Phase 0 的代码,但 Phase 2 的 status 字段读取 Phase 0 的冷却状态,故排 Phase 0 之后。

**Wave A(可独立发布,修复用户报告)**= Phase 0。
**Wave B** = Phase 1 + Phase 2 + 文档门。
安全停点:Wave A 合并即解决发热;Wave B 未跟上时仅损失纵深与可观测,不损失正确性。

## Phase designs

### Phase 0 — 协调器补货结果感知冷却

**接口与状态**(全部在 `CandidateEvalCoordinator` 内,无公共 API 变更):

- 模块常量 `_SUPPLY_UNPRODUCTIVE_BACKOFF_SECONDS = (30.0, 60.0, 120.0, 300.0, 600.0)`,带不变量 6 要求的校准注释。
- 新增实例状态:`_supply_streak: int = 0`(连续无产出次数)、`_supply_cooldown_until: float = 0.0`。
- 结果分类函数 `_supply_result_is_productive(result: Any) -> bool`:
  - 非 mapping → `True`(不变量 4);
  - mapping 且 `refreshed` truthy → `True`;
  - 其余(`skipped=True`、`refreshed=False` 各 reason)→ `False`。
- `_settle_supply_task`:成功取到结果后走分类——productive 则 `_supply_streak=0`、`_supply_cooldown_until=0`;unproductive 则 `_supply_cooldown_until = now + ladder[min(streak, len-1)]`、`streak += 1`。supply 异常路径維持现有 WARNING,同样按 unproductive 记账(异常也不该高频重试)。
- `run_forever` 补货分支:`_request_supply` 前检查 `now >= _supply_cooldown_until`;冷却中置 `state="supply_cooldown"`,等待 `min(safety_wake_seconds, 剩余冷却)`。
- `notify()`:任意 reason 清 `_supply_cooldown_until=0`(允许一次立即探测,不变量 2);`_resume_notification(reason)` 为真(`startup`/`manual_*`/`config_*`)额外清 `_supply_streak=0`。
- worker 成功入池路径(`_commit_finished_workers` 中 `last_cached>0` 分支)同步清零 streak 与冷却(不变量 3)。

**错误行为**:分类函数对任何异常形状(mapping 但键类型怪异等)catch-all 按 productive 处理并 DEBUG 记录——绝不让节流逻辑自身抛错打断评估循环。

**测试**(fake clock,复用现有 `time_fn=lambda: now[0]` 惯例):

1. `test_unproductive_supply_backs_off_exponentially`:starved snapshot + 恒空 supply,推进模拟时钟 3600s,断言 supply 调用次数 ≤10 且相邻间隔非降。
2. `test_notify_pierces_supply_cooldown_once`:冷却中 `notify("candidate_commit")` → 下一迭代恰好 1 次补货;仍无产出 → 阶梯继续(不重置)。
3. `test_manual_notify_resets_supply_ladder`:登顶后 `notify("manual_refresh")` → streak 归零,下次冷却回 30s。
4. `test_productive_supply_resets_ladder`:中途返回 `{"refreshed": True}` → streak/冷却清零。
5. `test_legacy_supply_result_shape_is_not_throttled`:callback 返回 `None`/字符串 → 不进入冷却。
6. 既有全部协调器测试(尤其 `test_fast_worker_refills_under_one_second_*`、`test_three_zero_cache_batches_trigger_supply_and_backoff`)零修改通过——若 no-progress 路径(`:285` 的 `_request_supply("candidate_eval_no_progress")`)与新冷却交互冲突,以既有测试语义为准调整实现而非测试。

**Rollout**:无配置、无迁移;随下一 patch 版本发布。

**数值验收门**:模拟 3600s 内 supply 调用 ≤10 次(测试 1 断言);既有测试套件全绿。复现:`.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q`。

### Phase 1 — 空计划诊断节流

- `runtime/refresh.py` 新增实例状态 `_last_empty_plan_diag_at: datetime | None`、`_last_empty_plan_fingerprint: tuple | None`、`_suppressed_empty_plan_count: int`,模块常量 `_EMPTY_PLAN_DIAG_INTERVAL_SECONDS = 300.0`(校准注释:与阶梯封顶同数量级,保证每个枯竭事件至少 1 条全量诊断)。
- `_log_empty_refresh_plan_diagnostics` 改为先算轻量指纹(`pool_available` + 既有就绪计数,不新增查询),满足不变量 7 的 (a)(b)(c) 任一才执行 3 组重统计 + INFO;否则 `_suppressed_empty_plan_count += 1` 并输出单行 DEBUG `refresh plan empty (suppressed diagnostics, %d since last full)`。
- 全量输出时把 `suppressed=%d` 附进 INFO 行并清零计数——被折叠的次数可审计。
- 测试:同指纹连续调用 N 次 → 恰 1 次 INFO + N-1 次 DEBUG;指纹变化 → 立即 INFO;模拟时钟越过 300s → 立即 INFO(用 `_now` 注入,沿用文件内既有 `self._now()` 惯例)。

**数值验收门**:同指纹 100 次连续调用中全量诊断恰 1 次;重统计函数调用次数(mock 计数)= 全量次数。复现:`.venv/bin/python -m pytest tests/test_refresh.py -q -k empty_plan`。

### Phase 2 — 枯竭可观测 + 文档门

- `status_payload` 新增:`candidate_eval_supply_streak`、`candidate_eval_supply_cooldown_until`(沿用现有 backoff_until 的 monotonic 语义)。
- 阶梯**首次**登顶时输出一条 WARNING:`candidate supply starved: %d consecutive unproductive replenishments, cooling down %.0fs`(每个枯竭事件恰 1 条,复位后允许再次触发)——运维 grep WARNING 即可发现源枯竭。
- 测试:登顶恰 1 条 WARNING;复位后再登顶再 1 条;status 字段随冷却状态变化。
- 文档更新见下节。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 0 冷却 | 枯竭稳态补货 ~9000 次/时 → ≤10 次/时;`refresh_if_needed` 空转调用同比例下降;serve-api 空闲 CPU 从持续单核满载回到 <5%(实机验证) |
| Phase 1 节流 | 空计划重统计 DB 查询与 INFO 日志 ~6700 条/日 → ≤288 条/日(300s 粒度上界);日志文件增长显著放缓 |
| Phase 2 可观测 | 源枯竭从"零 WARNING、靠风扇发现"变为 1 条 WARNING + 2 个 status 字段可查 |

## Documentation obligations

- `docs/modules/runtime.md`:协调器新增冷却状态机、status 字段、阶梯常量与校准依据;空计划诊断节流行为。
- `docs/changelog.md`:当前版本块加 fix bullet(空补货热轮询回归 + 诊断节流 + 枯竭 WARNING)。
- 架构图:**无需更新**(无跨模块接线变化、无新依赖块,显式声明)。
- CLI / config:**无需更新**(零新增配置项,显式声明)。
- README 📌 callout:随下一次发版由 release 流程决定是否收录(用户可感知的发热修复,倾向收录 1 条)。
