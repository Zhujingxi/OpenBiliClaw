# 统一兴趣更新线 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-27-unified-interest-line-spec.md`](./2026-07-27-unified-interest-line-spec.md)
> **Status:** r1 草案，待用户批准后执行
> **Execution order:** Task 1 → 2 → 3 → 4（Wave A）→ Task 5（Wave B 质量门）→ Task 6 → 7（Wave B 退役）→ Task 8（Wave C 清理，可选）
> **Tech:** 主仓 `.venv/bin/python`（3.12）；`env -u OPENBILICLAW_PROJECT_ROOT PYTHONPATH=src pytest <files> -q -p no:randomly`；`ruff format` / `ruff check src tests` / `mypy src/openbiliclaw`

**Invariants that MUST hold — re-read before each task:**

- 含 FEEDBACK 信号的 INTEREST 缓冲达 `feedback_batch_threshold`（默认 3）立即消费，绕过 600s 最短间隔；普通事件节奏不变。
- feedback 触发的整份重建仍过接入点③门控；pipeline 对 VALUES/CORE 仍 no-op。
- 迁移时旧游标之后的 feedback 事件一条不丢，重复迁移幂等。
- Retraction 从「排除」变「折价」是有意变更，必须由 A/B 门 3 证明不产生新增 dislike / 权重上调。
- `feedback_preference_overwrite` 只停写不删读；A/B 门不过不退役（config 开关可回退）。
- 每处修复配一条改前红、改后绿的测试；突变脚本必须 assert 替换真的发生。

### Task 1: 批线契约特征测试（Phase 0）

**Files:** 测试 `tests/test_soul_engine.py`（新增 `TestFeedbackBatchContract`）。

**Interfaces:** Consumes: 现有 `process_feedback_batch_if_needed`。Produces: 统一线的验收契约（每条标注「继承」或「有意变更」）。

**Steps:**

- [ ] 写 6 条特征测试：阈值触发/不触发、retraction 排除但推进游标、dislike 归档、显著变化门控重建（shadow/enforce 两态）、held-replay 消费、游标幂等。
- [ ] 运行 `pytest tests/test_soul_engine.py -q -p no:randomly -k FeedbackBatchContract`，确认全绿（特征测试钉现状，不应先红）。
- [ ] 对每条契约做突变（注掉对应分支，assert 替换发生）确认打红后恢复。
- [ ] 跑 `ruff format/check` + `mypy src/openbiliclaw/soul/engine.py`。

**Acceptance:**

- 数值门：6/6 通过且 6 处突变各至少打红 1 条。
- 复现：`pytest tests/test_soul_engine.py -q -k FeedbackBatchContract`。

### Task 2: 反馈信号入管线（Phase 1 前半）

**Files:** 改 `src/openbiliclaw/api/app.py`（feedback 端点侧效应）、`src/openbiliclaw/soul/pipeline.py`（优先级 flush）；测试 `tests/test_pipeline_advanced.py`、`tests/test_api_app.py`。

**Interfaces:** Consumes: `signal_from_feedback`（现零调用者）、`feedback_batch_threshold` config。Produces: FEEDBACK 信号进 INTEREST/SURFACE 缓冲 + 优先级 ready 规则。

**Steps:**

- [ ] 写失败测试：buffer 含 3 条 FEEDBACK 信号、时间不推进 → `is_ready` 为真；2 条 → 假；3 条普通事件不足 600s → 假。
- [ ] 确认 FAIL 后实现 `LayerBuffer.is_ready` 优先级分支（阈值透传自 config，校准注释指向旧批线默认）。
- [ ] 写失败测试：POST /api/feedback 后 pipeline 收到 1 条 FEEDBACK 信号（spy pipeline）。
- [ ] 实现端点侧效应 `signal_from_feedback → pipeline.ingest`；重跑至 PASS。
- [ ] 回归：`pytest tests/test_pipeline_advanced.py tests/test_api_app.py -q -k "feedback or buffer"` + lint/type。

**Acceptance:**

- 数值门：优先级 flush 在墙钟 0 推进下发生（测试用注入时钟断言，非 sleep）。
- 复现：`pytest tests/test_pipeline_advanced.py -q -k feedback_priority`。

### Task 3: 消费侧批线特权搬迁（Phase 1 后半）

**Files:** 改 `src/openbiliclaw/soul/layer_updaters.py`（`_update_interest`）、`src/openbiliclaw/soul/engine.py`（暴露归档/重建/held-replay 钩子）；测试 `tests/test_pipeline_advanced.py`。

**Interfaces:** Consumes: 本批信号中的 FEEDBACK 标记。Produces: dislike 归档、含反馈时显著变化 → `_gate_soul_rebuild(trigger=feedback_batch)`、批后 `replay_held_updates`、台账 `pipeline_layer_update(source="feedback")`。

**Steps:**

- [ ] 写失败测试：含 FEEDBACK 的批产生新 dislike → `archived_interests` 收到归档；不含 FEEDBACK 的批不触发归档路径。
- [ ] 写失败测试：含 FEEDBACK 且显著变化 → 门控被调用（trigger=feedback_batch）；enforce downgrade → 放弃重建且缓冲不丢。
- [ ] 实现消费侧分支；重跑至 PASS。
- [ ] 把 Task 1 的契约测试指向统一线跑一遍：除 retraction 条目（有意变更）外全部通过。
- [ ] lint/type + 突变（去掉 source="feedback" 台账、去掉归档分支）打红。

**Acceptance:**

- 数值门：契约测试 5/6 在统一线通过（retraction 条目按有意变更改写后 6/6）。
- 复现：`pytest tests/test_pipeline_advanced.py tests/test_soul_engine.py -q -k "FeedbackBatchContract or feedback_priority"`。

### Task 4: Wave A 全量回归

**Files:** 无新改动。

**Steps:**

- [ ] `env -u OPENBILICLAW_PROJECT_ROOT PYTHONPATH=src pytest tests/ -q -p no:randomly` 全量。
- [ ] 对照基线（当前 6308 passed / 42 skipped + 已知墙钟 flake）确认无新增失败。

**Acceptance:** 全量与基线差异仅限已知 flake；否则回退本 Wave。

### Task 5: 真实 LLM A/B 质量门（Phase 2）

**Files:** 加 `scripts/run_unified_interest_ab.py`。

**Interfaces:** Consumes: 真实库（隔离项目根副本）最近 ≥8 条 feedback 事件 + SenseTime 真实 LLM。Produces: 三道门的观测值。

**Steps:**

- [ ] 实现脚本：同一反馈集，旧批线路径与统一线路径各跑一次（互不落库），输出三道门指标。
- [ ] 在隔离根跑真实 A/B，记录：门 1 dislike 超集、门 2 top-10 Jaccard ≥ 0.8、门 3 retraction 无放大。
- [ ] 观测值 + 基线 commit 写进 PR 描述与本 plan 状态行。

**Acceptance:**

- 数值门：三门全过（Jaccard ≥ 0.8 样本 ≥8 条反馈）；任一不过 → 停在 Wave A 排查，禁止进 Task 6。
- 复现：`OPENBILICLAW_PROJECT_ROOT=<隔离根> python scripts/run_unified_interest_ab.py`。

### Task 6: 游标迁移 + shim（Phase 3 前半）

**Files:** 改 `src/openbiliclaw/soul/engine.py`（shim + 迁移）、`src/openbiliclaw/config.py`（`unified_interest_line` 开关）；测试 `tests/test_soul_engine.py`、`tests/test_config.py`。

**Interfaces:** Consumes: 旧 `last_processed_feedback_event_id` 游标。Produces: 兼容三个调用方（scheduler/cli/openclaw）的 shim；一次性幂等迁移。

**Steps:**

- [ ] 写失败测试：游标落后 3 条的库 → shim 首跑把 3 条全部入缓冲并触发 flush；二次启动零重复。
- [ ] 写失败测试：`unified_interest_line=false` → shim 走旧批线实现（行为逐字节回归 Task 1 契约）。
- [ ] 实现 shim + 迁移标记；重跑至 PASS。
- [ ] 三个调用方（`feedback_scheduler.py:61`、`cli.py:10650`、`operations.py:238`）零改动验证：现有测试全绿。

**Acceptance:**

- 数值门：迁移测试「N 条全入、二跑零重复」；开关回退契约 6/6。
- 复现：`pytest tests/test_soul_engine.py tests/test_config.py -q -k "unified or migration"`。

### Task 7: 写点退役 + 文档收口（Phase 3 后半）

**Files:** 改 `docs/modules/soul.md`（写点清单）、`docs/modules/runtime.md`、`docs/modules/config.md`、`docs/changelog.md`、`docs/architecture.md`、`docs/spec.md`、`README.md`/`README_EN.md` 架构图。

**Steps:**

- [ ] `feedback_preference_overwrite` 停写；`openbiliclaw ledger` 查询老写点仍显示历史行（测试断言）。
- [ ] 全部文档义务逐项落实（见 spec Documentation obligations）。
- [ ] 全量回归。

**Acceptance:** `grep -rn "feedback_preference_overwrite" src/` 仅剩 ledger 读路径与迁移注释；文档 checklist 全勾。

### Task 8: 清理（Phase 4，RECOMMENDED，可独立停止）

**Files:** 删旧批线实现、`_compact_feedback_event_for_analysis`、回退开关；同步 config.md。

**Steps:**

- [ ] 确认 A/B 门已过且一个发布周期无回退。
- [ ] 删除 + 全量回归 + 文档同步。

**Acceptance:** `engine.py` 反馈批私有实现归零；全量与基线一致。

## Verification after merge

统一线随下一版发布后，观察 7 天：`openbiliclaw cost --by caller` 的 `soul.preference.chunk` 调用次数不应显著上升（反馈重算从独立批并入快线，总调用应持平或略降）；`openbiliclaw ledger` 中 `pipeline_layer_update(source=feedback)` 出现且 `feedback_preference_overwrite` 停增。回退触发条件：用户反馈后兴趣/避雷不更新，或 A/B 门 3 类 retraction 放大在真实数据出现 → `unified_interest_line=false` 一键回旧线。负责人：项目唯一维护者。

## Explicitly out of scope

- 对话线、init、疑惑重放的偏好写入（独立成线是设计）。
- account_sync 的 legacy `analyze_events` 兜底路径。
- 深层线归一的任何边界调整。
- `feedback_batch_threshold` 的数值重校准（沿用 3，语义迁移不改值）。
