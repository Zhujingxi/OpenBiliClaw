# 统一兴趣更新线 Spec — 反馈批合并进 pipeline 快线，兴趣层只剩一条事件驱动路径

**Created:** 2026-07-27（用户决策「兴趣更新合成一条路，不要拆成两个」）
**Scope:** `soul/pipeline.py`、`soul/layer_updaters.py`、`soul/engine.py`（反馈批）、`api/app.py`（/api/feedback 侧效应）、`runtime/feedback_scheduler.py`、`cli.py` 反馈命令、`integrations/openclaw/operations.py`、相关测试与文档。
**Out of scope:** 对话线（`dialogue_preference_overwrite`，用户直接输入，独立成线是设计而非债务）；init 一次性建像；疑惑重放（`confusion_replay_preference`）；account_sync 的 legacy `analyze_events` 兜底（仅在 pipeline 不可用时触发，保留）；深层线归一的任何边界（VALUES/CORE 封死、门控接入点）。

## Goal

**现状成本**：兴趣层有两条事件驱动写入路径——

1. **快线**（认知流水线分支）：浏览器事件 → 分层缓冲（INTEREST 3 条信号 / 最短 600s）→ `_update_interest` → `PreferenceAnalyzer.analyze_events`。
2. **反馈批**（2026-03-09 `fcbde4a2` 遗产）：feedback 事件游标 + 阈值 3 → `_process_feedback_batch_if_needed_locked` → 同一个 `analyze_events` 的另一次全量调用。

两条线输入互斥（不双计），但维护面翻倍：两套触发状态（缓冲 vs `last_processed_feedback_event_id` 游标）、两套 retraction 处理（折价 vs 排除）、两套台账写点、三个外部调用方各自记得调哪条。每次改兴趣语义（本 session 的认知语境、topic 生命周期）都要问一遍「另一条线要不要同步」——本 session 已经踩过一次：认知语境只进了快线，反馈批刻意不传，这类「刻意」每多一条线就多一处要背的例外。

**量化目标**：
- 兴趣层的事件驱动写入收敛为 `_update_interest` 单点（对话/init/疑惑重放除外）；`grep -rn "feedback_preference_overwrite" src/` 归零。
- 反馈响应性不退化：第 3 条反馈落地后，无需等待 INTEREST 缓冲的 600s 最短间隔即完成偏好重算（特征测试断言）。
- 质量门：同一组真实反馈事件，旧批线 vs 统一线各跑一次真实 LLM——新增 `disliked_topics` 集合 ⊇ 旧路径产出，且现有兴趣 top-10 按名字的 Jaccard ≥ 0.8。
- 验证命令：`pytest tests/test_pipeline_advanced.py tests/test_soul_engine.py tests/test_feedback_scheduler.py -q`；A/B 用 `scripts/`（Phase 2 交付）脚本对真实库跑。

## Design invariants (MUST hold in every phase)

1. **反馈优先级**：含 FEEDBACK 信号的 INTEREST 缓冲在信号数达 `feedback_batch_threshold`（默认 3，config 键复用）时**立即**消费，绕过 `min_interval_seconds`；普通事件维持 3 条/600s 不变。验证：`tests/test_pipeline_advanced.py` 新增特征测试，断言 flush 在无时间推进下发生。
2. **深层纪律不变**：feedback 触发的整份重建仍走接入点③门控（trigger=`feedback_batch`，写点 `feedback_soul_rebuild` 保留）；pipeline 对 VALUES/CORE 仍是 no-op + WARNING。验证：`tests/test_posture_gate.py` 现有矩阵 + 新增「统一线重建仍过门」测试。
3. **用户信号不丢**：迁移时旧游标（`last_processed_feedback_event_id`）之后尚未消费的 feedback 事件一条不丢地进入缓冲；重复迁移幂等。验证：迁移测试构造「游标落后 N 条」库，断言 N 条全部入缓冲且二次启动不重复。
4. **Retraction 语义收敛且不放大**：旧批线**排除** retraction，统一线走 pipeline 既有折价（`signal_strength≤0.2` + `retracted` 标记）。A/B 必须证明折价后的 retraction 不产生新增 dislike 或兴趣权重上调（排除→折价是有意变更，过质量铁律记录）。
5. **台账连续性**：每次 feedback 触发的消费记 `pipeline_layer_update` 且 `source="feedback"`；`feedback_preference_overwrite` 写点退役需同步 soul.md 写点清单与 changelog；`openbiliclaw ledger` 查询老写点仍能显示历史行（只停写不删读）。
6. **质量门先于退役**：Phase 2 的 A/B 门不过，旧批线不删（config 开关回退）。

## Current diagnosis

### D1. 两条线在同一层各自全量重算

- 快线：`soul/pipeline.py:317-321`（INTEREST 3 条/600s 阈值）→ `soul/layer_updaters.py:255+` `_update_interest` → `analyze_events(events, existing_preference, awareness/insights)`。
- 反馈批：`soul/engine.py:2537+` `_process_feedback_batch_if_needed_locked`——游标读 `event_types=["feedback"]`（2541-2547）、阈值 `feedback_batch_threshold`（config.py:476，默认 3）、retraction 排除（2552-2554）、`_compact_feedback_event_for_analysis` + 分片（2568-2571）、dislike diff + `_archive_disliked_topics`（2583-2597）、写点 `feedback_preference_overwrite`（2629）、显著变化 → 门控重建（2604+，P2 已补门）。
- 两者殊途同归于同一个 `PreferenceAnalyzer.analyze_events` + 同一个偏好层 + 同一套 topic 生命周期 overlay——合并的收敛点现成。

### D2. 反馈事件今天根本不进 pipeline

`/api/feedback`（api/app.py:9542-9583）只做：`propagate_event`（落账本）→ 探索缓冲 → 即时认知 → `_schedule_post_feedback_tasks`（2097-2099，调度批线）。`_ingest_profile_update_events`（2101）的调用方（2249/5697/11209/12057/12312）不含反馈端点。**合并 = 把反馈接进 pipeline，再把批线的特权（优先级、dislike 归档、重建触发）搬进消费侧，然后退役批线。**

### D3. 接口早就留好了，零调用者

`soul/pipeline.py:415+` `signal_from_feedback()` 产 `SignalType.FEEDBACK`——路由表（259-264）已定 INTEREST+SURFACE，`_STRONG_SIGNAL_TYPES`（358-361）已收录。**该函数当前零调用者**（`grep -rn signal_from_feedback src/` 仅定义处）。深线归一时 FEEDBACK 路由已收窄到 interest+surface（不会碰深层），与不变量 2 天然一致。

### D4. 批线的三个外部调用方

- `runtime/feedback_scheduler.py:61`（debounce 循环，`_schedule_post_feedback_tasks` 触发）
- `cli.py:10650`（CLI 反馈命令，无 daemon 场景）
- `integrations/openclaw/operations.py:238`（OpenClaw 适配）

三者都通过 `process_feedback_batch_if_needed` 这个方法名耦合——保留方法名做 shim 可以让三处零改动。

### D5. 输入形态差异（质量风险所在）

批线喂 `_compact_feedback_event_for_analysis`（裁剪过的 feedback 事件）+ 分片；快线喂信号还原的完整事件 + 认知语境（本 session 新增）。统一后反馈走快线形态——prompt 输入变化，按质量铁律（quality-first：改模型输入过回放门 + 主观质量新旧对照）必须 A/B。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | 特征测试钉死批线现有语义（游标/阈值/retraction 排除/dislike 归档/门控重建/held-replay 消费） | **MUST** | 合并的验收契约；没有它「行为不变」不可证 |
| 1 | 反馈进快线：/api/feedback → `signal_from_feedback` → 优先级 flush；消费侧补 dislike 归档 + 含反馈时的显著变化门控重建 + held-replay 钩子 | **MUST** | 主体改动 |
| 2 | 真实 LLM A/B 质量门（同一反馈集，旧 vs 新） | **MUST** | 不变量 6：门不过不退役 |
| 3 | 退役批线：`process_feedback_batch_if_needed` 变 shim（触发 flush）、游标一次性迁移、写点清单/文档收口 | **MUST** | 目标本体 |
| 4 | 删除死代码（`_compact_feedback_event_for_analysis`、批线私有路径）与 config 开关 | RECOMMENDED | 清理，可独立停在 Phase 3 |

依赖：0 → 1 → 2 → 3 → 4 严格串行。**Wave A** = Phase 0+1，**Wave B** = Phase 2+3，**Wave C** = Phase 4。Wave A 可独立合入（新路径默认关、批线照旧）；工作可安全停在任何 Wave 边界。

## Phase designs

### Phase 0 — 特征测试（契约先行）

对现有批线写 `tests/test_soul_engine.py::TestFeedbackBatchContract`：
- 3 条反馈触发、2 条不触发；retraction 不计数也不进分析但推进游标；
- 新增 dislike 归档进 `archived_interests`；显著变化走门控（shadow 放行 / enforce downgrade 放弃重建）；
- held-replay 在批后消费；游标推进幂等。
每条测试标注「统一线必须继承」或「统一线有意变更（retraction 排除→折价）」。验收门：全部通过且突变（去掉对应分支）各打红。

### Phase 1 — 反馈进快线

- `/api/feedback` 成功侧效应追加：`signal_from_feedback(feedback_type, title, note)` → `pipeline.ingest(signal)`（不动既有 `propagate_event`）。
- `LayerBuffer.is_ready` 增加优先级规则：buffer 内 FEEDBACK 信号数 ≥ `feedback_batch_threshold` → ready（无视 `min_interval_seconds`）。阈值从 config 透传，校准注释指向旧批线默认。
- `_update_interest` 消费侧、仅当本批含 FEEDBACK 信号时：dislike diff → `_archive_disliked_topics`；`_preference_changed_significantly` → `_gate_soul_rebuild(trigger=feedback_batch)`（写点 `feedback_soul_rebuild` 不变）；批后调 `replay_held_updates`（best-effort，与现批线相同）。
- 台账：该次 `pipeline_layer_update` 行 `source="feedback"`。
- 错误行为：LLM 失败沿用快线现有回退（缓冲保留，下轮重试）——比批线「本轮丢弃」更保守，记为有意变更。
- 测试：优先级 flush 特征测试；Phase 0 契约测试改跑统一线（预期除 retraction 条目外全绿）。

### Phase 2 — 真实 LLM A/B 质量门

`scripts/run_unified_interest_ab.py`：取真实库最近 N≥8 条 feedback 事件，旧批线路径与统一线路径各跑一次（隔离项目根，互不落库），比较：
- **门 1**：新增 `disliked_topics` 集合 ⊇ 旧路径产出（负反馈语义不丢）；
- **门 2**：现有兴趣 top-10（按名字）Jaccard ≥ 0.8；
- **门 3**：retraction 样本（如无真实样本则注入合成）不产生新增 dislike / 权重上调。
门值、样本量、基线 commit、观测值记入 PR。任一门不过 → 停在 Wave A，排查后重跑。

### Phase 3 — 退役与迁移

- `process_feedback_batch_if_needed` → shim：读旧游标后的未消费 feedback 事件（一次性迁移进缓冲，写迁移标记幂等），然后触发 pipeline flush；返回形状兼容三个调用方。
- `FeedbackBatchScheduler` 保留（debounce 触发 shim），`cli.py:10650`、openclaw 零改动。
- `feedback_preference_overwrite` 停写；soul.md 写点清单标注「已退役，历史行仍可查」；changelog 记录。
- 回退开关：`scheduler.unified_interest_line`（bool，默认 true），false 时 shim 直接走旧批线实现（Phase 4 前旧代码不删）。

### Phase 4 — 清理

A/B 门过 + 一个发布周期无回退后：删旧批线实现、`_compact_feedback_event_for_analysis`、回退开关；config.md 同步。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 1 | 反馈事件从「独立游标批」变为「优先级信号」；兴趣写入路径 2→1（事件驱动侧） |
| Phase 1 | 反馈重算首次获得认知语境（批线此前刻意不传的例外消失） |
| Phase 3 | 触发状态从两套（缓冲+游标）收敛为一套；外部调用方零改动 |
| Phase 4 | `engine.py` 反馈批实现 ~120 行退役 |

## Documentation obligations

- `docs/modules/soul.md`：写点清单（`feedback_preference_overwrite` 退役标注、`pipeline_layer_update` source=feedback 语义）、实现表新行。
- `docs/modules/runtime.md`：FeedbackBatchScheduler 语义变更（debounce 触发 flush shim）。
- `docs/modules/config.md`：`feedback_batch_threshold` 语义迁移 + `unified_interest_line` 开关（Phase 3 加、Phase 4 删）。
- `docs/changelog.md`：每 Wave 一条。
- `docs/architecture.md` + `docs/spec.md` §3 + README 双语架构图：兴趣更新线合并（数据流变化，无条件触发架构图义务）。
- `docs/modules/cli.md`：反馈命令行为说明（若输出文案变化）。
