# 深层线归一 Spec — 深层画像的事件驱动影响统一经「假设 → 门控下 soul 重建」

**Created:** 2026-07-22(r4,codex 三轮 F7 修订;用户决策「合并流程 2/3,深层以流程 3 为准」)
**Scope:** `soul/pipeline.py`、`soul/layer_updaters.py`、`soul/engine.py`、`soul/cognition_cycle.py`、`soul/posture_gate.py`(GateDecision 错误分类,r4/F7)、`soul/confusion.py`(文档措辞)、相关测试与文档。基于分支 `feat/cognitive-profile-pipeline`(未合 main,零 shadow 数据——最低成本窗口)。
**Out of scope:** 对话深层自述路径(接入点①,保留);逐假设结构化深层写入(r1 方案,已废弃——见设计要点);insight/speculation 存储迁移;INTEREST/SURFACE/ROLE 快线。

## Goal

r2 修正后的现状诊断(对齐 codex 全仓核查):

深层画像(VALUES/CORE 层与 soul 层)的**事件驱动直写路径共三条**:
- **P1** pipeline VALUES/CORE 阈值消费(`layer_updaters.py:74,95` 接入点②)——实际可达性有限(`pipeline.py:398,401,241`:多数事件路由为 BEHAVIOR/ENGAGEMENT,不入 VALUES/CORE;r1 高估了此路径流量),但机制存在且绕过假设-验证。
- **P2** 反馈批显著变化时的 soul 直接重建(`engine.py:1285,1306`)——**未过任何门控接入点**(r1 漏诊,codex F1)。
- **P3** 对话深层 candidates(接入点①,已门控,保留)。

另:VALUES/CORE 的 layer updater 仍全局注册(`layer_updaters.py:796-797`),`update_layer()` 可被直接调用(F3);疑惑机制从不直写画像(`confusion.py:14`,其 resolved 出口产物是假设/兴趣重放,r1 spec 把它列为深层入口是失实,F11)。

**目标**:深层画像的事件驱动影响收敛为唯一模式——**「假设(验证 confirmed)→ 攒批去抖 → 门控下 soul 重建」**;P1 退役、P2 补门控、VALUES/CORE updater 封死。

## Design invariants (MUST hold)

1. **深层影响唯一模式**:事件驱动的深层变更只能经由 soul 重建(接入点③)或对话深层 candidates(接入点①),两者均过态势门控。重建的触发源:(a) confirmed 假设攒批(新增,见 4);(b) 反馈批显著变化(P2 补接③);(c) 对话路径既有触发(已接③)。pipeline 不再消费 VALUES/CORE;`update_layer()` 对这两层直接调用抛错或 no-op + WARNING(代码级封死,F3)。
2. **深层证据不丢(F4)**:退役含一次性迁移——读取持久化 buffer 中的 VALUES/CORE 信号,**确定性转换为 awareness note**(payload 摘要 + source="pipeline_migration",不经 LLM),写台账迁移行;迁移后清空对应 buffer 键。此后深层证据在 events 表 + 觉察提炼自然流动。
3. **重建即幂等与反悔语义**:soul 重建全量再生成。**重建输入的假设过滤(r3/F1)**:只纳入 `validated=True 且 confidence>=0.75` 的假设——过滤在重建输入组装处实施(现状 `engine.py:1159/1300` 全量传 `_load_insights()`、prompt 不过滤,须改),rejected/未验证假设不可见。**反悔触发(r3/F2)**:假设被 reject 的状态迁移**同样置 rebuild_pending**——已写入 soul 的旧结论靠下一次重建(过滤后不含它)挤出,不会无限期残留。重复确认无副作用(幂等天然)。
4. **rebuild_pending 状态机(r3/F3 完整化)**:confirm 与 reject 的状态迁移(均由 `update_from_feedback` 单点拥有,对话 settles 只经该函数)置 `rebuild_pending {set_at, trigger_refs}`(持久化于 soul 状态存储,重启可恢复);去抖 `_DEEP_REBUILD_DEBOUNCE_HOURS = 6`(校准:介于对话节奏与 12h 循环之间,首轮重校)后由 12h 循环或下一次对话学习触发门控重建。**清标语义**:重建 accept 完成 → 清标;门控 downgrade/reject → 清标 + 记 `last_gate_refusal`(本批放弃;**新的 confirm/reject 迁移会重新置标**,即"新证据重开",无无限重试);LLM/解析异常 → 保留 pending,`retry_count+1`、下轮再试,`retry_count>=2` 后清标 + WARNING(有界)。**错误可区分(r4/F7)**:`GateDecision` 增加 `is_error: bool` 分类字段——enforce 下异常仍表现为保守 downgrade 行为,但 `is_error=True` 让重建调用方走「保留 pending 重试」分支而非清标;shadow 路径与既有 `shadow_error` 语义对齐;接入点①既有调用方行为不变(不消费该字段)。带测试:异常 → is_error=True 且 pending 保留;真实 downgrade → is_error=False 且清标。
5. **回放与回归**:INTEREST/SURFACE/ROLE 路径逐字节不变;P1/P2 相关既有测试(`test_signal_channel_eval.py:300,476,492`、`test_soul_engine.py:1655` 等,F12)改写为反向断言(不再直写/必过门控);`posture_gate_mode=off` 下重建直写(off 语义一致)。
6. 台账(重建行含触发源与纳入的 confirmed 假设列表)、prompt-cache、阈值出处、单用户注入等既有不变量沿用。台账仍为 best-effort 观察者——本设计不再依赖它做幂等回执(F5 消解)。

## 设计要点

- **P1 退役**:`signals_from_events` 不再路由 VALUES/CORE 入缓冲;接入点②逻辑移除;updater 注册表摘除这两层并在 `update_layer()` 入口防御。
- **迁移落地细节(r3/F5)**:迁移必须在把 VALUES/CORE 移出 `_BUFFERED_LAYERS` **之前**读取持久化 buffer 旧键原文(`pipeline.py:520,537` 移除后旧键会被忽略——读取逻辑独立于常量);产物 note 以内容前缀 `[migration:pipeline-deep]` 表达 provenance(AwarenessNote 无 source 字段,不加字段),`source_event_ids` 尽力回填(payload 无 id 则空);提交顺序:①写 notes(内容 hash 去重,重复可容忍)→ ②同一持久化写内记 migration marker + 清空 buffer 旧键;崩溃重跑以 marker 为幂等判据,marker 在则跳过。
- **P2 补门控 + 快照泛化(r3/F4)**:`engine.py:1285,1306` 的反馈批重建接入门控③。现状接入点③快照硬编码 dialogue 触发且只含兴趣 diff(`engine.py:1995-2000`)——泛化为 `{trigger: dialogue|feedback_batch|confirmed_hypotheses, write_point, 旧 soul 摘要, 触发上下文(对话 candidates / confirmed 假设列表 / 反馈批摘要)}`,三个触发源各自携带正确来源与非空判定语境;台账 verdict 行区分 trigger。
- **重建输入**:soul 重建的现有输入(preference/awareness/insight 层)已含 validated 假设——实现时核对 `_profile_builder.build` 的输入面,确认 confirmed 假设可见;不可见则把 validated 假设列表并入其输入(最小改动)。
- **疑惑措辞修正(F11)**:疑惑 resolved 的"直接结算"出口产物统一为**假设**(高置信,走本 spec 的 confirmed→重建通道)或兴趣重放(既有),spec/文档不再称其为独立深层入口;`layer_updaters.py:201` 的 downgrade-转-假设函数不复用于新通道(F10,新通道无逐假设 downgrade)。
- **文档**:soul.md 深层线描述更新(唯一模式)、architecture/spec 图 pipeline 深层节点改注、changelog;2026-07-17 spec 加指针注记。

## 验收门

- 反向断言:VALUES/CORE 信号批 → 无缓冲消费、无接入点②调用、`update_layer(VALUES|CORE)` 防御触发;迁移一次性转换 + 台账行 + buffer 清空。
- P2:反馈批显著变化 → 门控③调用(shadow 记录/enforce 可拦/off 直写);既有直写断言改写。
- 触发纪律:confirm → pending 标记持久化 → 去抖窗口内不重建 → 窗口后触发门控重建 → 台账含假设列表;重复 confirm 幂等;reject 后重建不含该假设;downgrade 本次放弃不重试。
- 全量:`pytest tests/test_pipeline_advanced.py tests/test_signal_channel_eval.py tests/test_posture_gate.py tests/test_soul_engine.py tests/test_confusion_lifecycle.py tests/test_cognition_cycle.py -q` 全绿(r3/F6:含 pending 重启恢复、并发状态保存、12h 触发用例);mypy/ruff 干净。
