# 对话确认入口 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-22-dialogue-confirmation-entry-spec.md`](./2026-07-22-dialogue-confirmation-entry-spec.md)
> **Status:** r7 — codex 第六轮 F37-F38 已修订,待第七轮 review
> **Execution order:** Wave A(Task 0→1→2→3)→ Wave B(Task 4→5)→ Wave C(Task 6)→ Wave D(Task 7)。每 Wave 完成其文档子集方可交付。
> **Tech:** Python:仓库 `.venv/bin/python` + worktree 根 + `PYTHONPATH=$PWD/src`(import 自查);测试前 `mv config.toml /tmp/dce_config_stash.toml`,结束**必须还原并 diff 确认原件**(5070 字节含 source_incremental_hours);`ruff format/check src/ tests/`;`mypy src/`(strict)。扩展:`cd extension && npm test && npm run typecheck && npm run build`。**桌面端 = `src/openbiliclaw/web/desktop/assets/js/`(session="webui"),移动端 = `src/openbiliclaw/web/js/`(本版仅只读化)**。

**Invariants that MUST hold — re-read before each task:**

- 唯一主动 UI 入口 + legacy 端点转发兼容(source=legacy_endpoint 台账)。
- 结算:仲裁 INSERT OR IGNORE;冲突分流(applied=1→already_settled;=0→接管路径,不谎报终态);claim+fencing token;event 段=同事务原子占位+INSERT(token+seg 双条件,停顿恢复必失配);其它段幂等;投影只认 applied=1;discuss 先 CAS(记 discussing_at+attempt_token)同请求建锚(建锚校验 token)、校正带 5min 宽限且清 token 拒旧请求;投影批量 patch+读取兜底。
- 一个大脑多个屏幕:单一 history 回灌全部 {chat,hypothesis,confusion} completed turn 不分 session;前端按 session 过滤显示;durable 逐请求传 session;附着=消息处理流程内先 INSERT 卡片,排序 (created_at, rowid);去重范围 **(ref, session)**(跨 session 各自产卡,投影由结算表统一)。
- 锚:≤1;解除四条;confusion 非结算解锚回 open;generation 快照失配丢弃;anchor 含 origin_turn_id(结算时 patch 来源卡片终态)与 ambiguous_count(非 ambiguous 清零)。
- confusion 结算唯一所有者=锚处理器;失败不吞:replay_queue 持久有序小队列(上限5),队头依序处理失败停原位,新轮入队尾不越序,四种锚释放清空+台账,12h 兜底。
- kind×relation 合法矩阵(见 spec);ambiguous 追问 ≤1,两次计 defer;Jaccard(NFC+中文 bigram/英文 token,停用词表)≥0.5 丢 candidates+WARNING。
- 历史轮=绝对时间戳(创建时定死,前缀字节稳定);当前时间只进 user prompt 尾段;UTC→本地转换单点可注入。
- 双轨冷却持久化(全局 12h + 同对象 72h);用户主动零冷却;角标=SW 决策表扩展,健康类优先。
- README/README_EN 图无条件同步;新常量带校准注释;LLM 输出白名单+WARNING;结算幂等+台账 turn_id。

---

## Wave A — 后端核心

### Task 0: chat_turns.payload 迁移 + 绝对时间戳渲染

**Files:** `storage/database.py`(payload 列幂等迁移 + `update_chat_turn_payload_state` CAS + **`card_settlements` 表(ref 主键/verdict/turn_id/applied)与 INSERT OR IGNORE 仲裁 DAO(r4/F22)**)、`api/models.py`(ChatTurnOut 增 payload)、`soul/dialogue.py`(历史轮携带 created_at;渲染 `[MM-DD HH:mm]` 绝对戳,UTC→本地单点转换可注入)、user prompt 尾段「当前时间」注入点;测试 `tests/test_dialogue_context.py` + `tests/test_database.py`。

**Steps:**

- [ ] **基线更新单独提交**(绝对戳为有意变更;固定轮时间断言输出字节确定,且**渲染函数不接受/不读取 now**——前缀稳定性以"函数签名无 now"结构性保证)。
- [ ] Failing tests:payload 列迁移(fresh+旧库);CAS 状态机全路径(pending→四态、discussing→终态、discussing→pending 释放,r4/F25);**card_settlements 仲裁:两独立连接并发 confirm/reject → 恰一插入成功(r4/F22)**;ChatTurnOut 带 payload;turn 列表排序 (created_at, rowid) 旧数据兼容。
- [ ] Failing tests:历史渲染绝对戳(回灌轮用 created_at 转本地;内存轮用记录时刻);「当前时间」只出现在 user prompt 尾段(前缀不含);时区转换注入测试。
- [ ] Confirm FAIL → 实现 → PASS;`pytest tests/test_llm_prompts.py -q`(invariance)+ ruff + mypy。

**Acceptance:** 迁移/CAS ≥5 + 时间戳 ≥4 用例;前缀稳定性结构断言;invariance 绿。Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_dialogue_context.py tests/test_database.py -q`。

### Task 1: 锚状态机(四条解除/代次/持久化)

**Files:** 新增 `src/openbiliclaw/soul/dialogue_anchor.py`(anchor {kind, ref, generation, established_at, unrelated_streak, **origin_turn_id, ambiguous_count**};持久化;TTL 2h 校准注释);`soul/dialogue_learn_queue.py`(payload 增 anchor 快照);台账行;新增 `tests/test_dialogue_anchor.py`。

**Steps:**

- [ ] Failing tests:建立三入口;≤1(replaced);解除四条各一;confusion 非结算解锚回 open(槽释放);重启恢复;代次失配丢弃;**锚结算 → 经 origin_turn_id patch 来源卡片终态(r3/F17)**;**ambiguous_count 累计与非 ambiguous 清零(r3/F21)**;台账断言。
- [ ] Confirm FAIL → 实现 → PASS;`pytest tests/test_confusion_lifecycle.py tests/test_dialogue_learn_queue.py -q` 回归 + ruff + mypy。

**Acceptance:** 生命周期+代次+槽释放 ≥10 用例;既有零回归。

### Task 2: 归属判断(矩阵/ambiguous/Jaccard)

**Files:** `llm/prompts.py`(提取 builder 锚段 + anchor 输出契约 {relation(六值), interpretation?, derived[]};system 静态)、`soul/dialogue_insight_analyzer.py`(注入/解析/kind×relation 矩阵白名单)、`soul/engine.py`(锚处理器六分支;Jaccard 防御函数——NFC+中文 bigram/英文小写 token+停用词表,阈值 0.5 校准注释;锚定轮跳过检索式 settles 复用既有规则);测试并入 `tests/test_dialogue_anchor.py` + `tests/test_soul_engine.py`。

**Steps:**

- [ ] Failing tests(矩阵):hypothesis 五合法分支各一 + confusion 三合法分支各一 + 越界组合(hypothesis 收到 answer)→ 按 unrelated+WARNING;ambiguous → 追问一次、第二次计 defer。
- [ ] Failing tests(防双计):归锚内容出现在 candidates → Jaccard ≥0.5 丢弃+WARNING(中文 bigram 用例 + 英文 token 用例 + 停用词不误杀用例);旁支正常走快线;锚定轮 settles 跳过。
- [ ] Failing tests(防御+回放):anchor 字段缺失/坏值 → 不结算锚保持;**无锚时提取输入输出与现状逐字节一致**。
- [ ] Confirm FAIL → 实现 → PASS;invariance 清单 + ruff + mypy。

**Acceptance:** 矩阵 9 + 防御 ≥6 用例;无锚字节门绿。

### Task 3: confusion 结算所有权收归 + Wave A 文档 gate

**Files:** `api/app.py`(durable confusion 直接 resolve/defer 移除;分类器输出并入;抛出即建锚)、`soul/confusion.py`(锚处理器结算入口统一)、`storage/database.py`(confusions 增 `replay_queue` 持久有序小队列列(JSON 上限 5),幂等迁移,r6/F34)、`soul/cognition_cycle.py`(12h 扫描:clarifying 且有晚于 receipt 的 completed 回复 → 重放归属,幂等,r3/F14);既有用例改写;文档 gate。

**Steps:**

- [ ] Failing test:confusion 抛出 → 锚建立;回复经锚处理器处理;durable 旧路径零调用断言;**持久有序重放(r6/F34):T1 失败入队 → T2 到达入队尾 → T1 再失败 T2 不越序 → T1 成功后 T2 续;四种锚释放(replaced/TTL/结算/unrelated)清空+台账 dropped;扫描兜底幂等;上限 5 逐出**。
- [ ] 改写既有单轮结算用例为锚定语义;`pytest tests/test_confusion_lifecycle.py tests/test_api_app.py -q` 全绿。
- [ ] Wave A 文档 gate。

**Acceptance:** 所有权唯一断言 + 既有用例改写后全绿。

---

## Wave B — API 层

### Task 4: 卡片 turn + action 端点(CAS)+ 进流

**Files:** `api/app.py`(scope 白名单加 "hypothesis";卡片 turn 构造含 payload;`POST /api/chat/cards/{turn_id}/action` 端点:仲裁+可接管两段提交+四 action——confirm/reject→update_from_feedback、defer→冷却持久化、**discuss→①CAS pending→discussing ②建锚,②失败回滚(r5/F30)**;回灌与 turn 列表 scope 集合扩展 {chat,hypothesis,confusion};legacy `POST /api/insights/feedback` 转发进同一结算路径+deprecated 注记+台账 source=legacy_endpoint);`soul/dialogue.py`(回灌 scope 集合);测试 `tests/test_api_app.py` + `tests/test_dialogue_context.py`。

**Steps:**

- [ ] Failing tests:卡片 turn 创建(completed+worker guard);action 四种(**冲突分流:applied=1→already_settled、=0→接管(r6/F32);claim+fencing;**event 段同事务占位+INSERT,停顿-接管-恢复交错不双写(r7/F37)**;故障注入各段;5min 超时接管**);同 ref 并发相反 action 恰一赢;投影只认 applied=1+批量 patch+读取兜底+跨 session 刷新;discuss 顺序/回滚/5min 宽限校正/**attempt_token fencing:校正后旧请求恢复建锚被拒(r7/F38)**;**跨 session open 各自产卡正向测试(r6/F36)**;legacy 转发等价。
- [ ] Failing tests(进流,r4/F26):回灌含 hypothesis/confusion scope 轮次(probe 仍排除);**跨 session 单一 history 回灌**(popup+webui 的 completed turn 全部进同一 history,时间序);前端列表按 session 过滤仅影响显示(API 过滤参数测试)。
- [ ] Confirm FAIL → 实现 → PASS;回归 + ruff + mypy。

**Acceptance:** 卡片/action/CAS/legacy/进流 ≥10 用例。

### Task 5: 待聊列表 + 双轨冷却 + SW 角标数据 + Wave B 文档 gate

**Files:** `api/app.py`(`GET /api/chat/pending-confirmations`(高优先级过滤+计数);`POST .../{ref}/open`(零冷却,当前 session 产卡片/提问+建锚);系统抛出调度:附着于用户下一条消息、全局 12h 冷却持久化于 soul 状态存储+同对象 72h);测试 `tests/test_api_app.py`;文档:API 端点、changelog。

**Steps:**

- [ ] Failing tests:列表口径 + `?count_only=1`;open 连开 3 条零冷却(**(ref,session) 去重:同端复用、跨端各自产卡,r6/F36**);系统抛出 12h 持久化;72h 叠加;附着语义((created_at,rowid) 保序、无用户消息不凭空产 turn、重启/重试不重复附着)。
- [ ] Confirm FAIL → 实现 → PASS;回归 + ruff + mypy;Wave B 文档 gate。

**Acceptance:** 双轨矩阵+附着 ≥7 用例。

---

## Wave C — 前端(Task 6)

**Files:** `extension/popup/`(卡片渲染四按钮/已结算态/依据展开/待聊入口;action 调用+乐观更新+already_settled 回滚)、`extension/src/background/badge.ts`+`service-worker.ts`(决策表扩展:pending-confirmations 信号,轮询+runtime-stream 触发刷新,健康类优先、离线/未初始化不显示)、**`src/openbiliclaw/web/desktop/assets/js/`**(桌面同款卡片/待聊/计数)、`src/openbiliclaw/web/js/views/profile.js`(移动端洞察确认按钮只读化);`extension/tests/`(卡片/角标决策表单测);Playwright 两用例(卡片 confirm→已结算态;待聊 open→卡片入流)。

**Steps:**

- [ ] Failing tests(node --test):payload→卡片 DOM;action→端点+乐观更新+already_settled 回滚;badge 决策表(健康优先/计数/离线抑制/**复用 30s alarm 不新增定时器+count_only 请求+事件去抖,r3/F20**);无 payload turn 文字降级。
- [ ] Confirm FAIL → popup 实现 → 桌面端同步(行为语义一致)→ 移动端按钮只读化 → PASS。
- [ ] `npm test && npm run typecheck && npm run build`;Playwright;Wave C 文档 gate(extension.md、changelog)。

**Acceptance:** 前端用例全过+build 干净;三端改动各自验证。

---

## Wave D — 收尾(Task 7)

**Files:** `cli.py`(`questions` 只读)、认知更新区只读迁移收尾(popup/桌面确认按钮移除)、文档总核对(**含 README/README_EN 架构图无条件同步**:对话线加"确认入口"节点)、真实端到端。

**Steps:**

- [ ] CLI questions + 测试;两端按钮移除回归。
- [ ] **真实端到端**(隔离环境 8433 式):卡片 confirm 结算一条真实假设 + 多轮澄清一条疑惑(含中途岔题回归),台账链记录进 PR。
- [ ] 文档总核对逐项进 PR;changelog。

**Acceptance:** CLI+迁移+端到端记录齐备;README 双语图已同步。

## Verification after merge

一周观察:角标频率(常态 0-3)、系统抛出 ≤2/天、defer 率(>2/3 延长冷却)、锚均轮数与解锚分布、legacy 端点调用量(降为 0 后下版删除)。回滚:各 Wave revert;关闭抛出调度即静默。

## Explicitly out of scope

移动端卡片(跟进版);系统推送;多锚;对话检索(v2);探针迁移。
