# 认知画像流水线 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-17-cognitive-profile-pipeline-spec.md`](./2026-07-17-cognitive-profile-pipeline-spec.md)
> **Status:** r5 — codex 第四轮 4 findings 已修订,待第五轮 review
> **Execution order:** Wave A(Task 0→1→2→3)→ Wave B(Task 4→5)→ Wave C(Task 6→7)→ Wave D(Task 8→9,RECOMMENDED)。每 Wave 最后完成其文档子集方可交付。A 独立;B 依赖 A;C 依赖 A(部分依赖 B:疑惑直接结算出口,B 未上则直写);D 依赖 A。
> **Tech:** Python 3.11+/仓库 `.venv/bin/python`;测试 `PYTHONPATH=$PWD/src <venv>/python -m pytest <file> -q`(worktree 根,PYTHONPATH 指 worktree src);`ruff format/check src/ tests/`;`mypy src/`(strict)。无扩展侧改动。

**Invariants that MUST hold — re-read before each task:**

- Prompt-cache:被触碰 builder 全部常量化 system + sort_keys + 入 invariance 清单(含现存不合规的 dialogue-insight builder)。
- 回放不变性(作用域收窄,R4-3):偏好渲染、对话 prompt(≤窗口)、`analyze()` 路径 awareness 逐字节一致;`cognition_cycle` 切新 builder 属有意变更走 A/B 对照;基线快照先行;`posture_gate_mode=off` 与现状逐字节一致。
- 门控只拦深层(dialogue 深层 candidates / pipeline VALUES+CORE / soul rebuild 三面),shadow 异步旁路零延迟+快照隔离;enforce save-time 三条件(最早有效判定 ≥14 天 且 近 14 天 ≥10 条 且 近 7 天 ≥1 条,force 逃生门);异常→downgrade。
- 台账只追加、best-effort 观察者(WARNING 不阻断);覆盖=枚举写点清单内 100% 挂钩。
- 收编不迁移;结算身份=自然键(speculation:domain / insight:内容 hash8 / confusion:id),白名单=当轮注入清单。
- 结算单一所有权:probe/avoidance_probe/confusion scope 归 durable 侧效应;settles 只处理 scope="chat"。
- 对话学习单 worker 队列;worker 自持生命周期不入 cancel_all 注册表;热重载 pause-drain、失败回滚 resume。
- 疑惑不写画像;clarifying 并发=1 由 partial unique index 保证;冷却 72h 持久化。
- 阈值带校准注释;LLM 输出白名单/clamp+WARNING;解析失败保守化。
- 单用户全量注入;新 caller 注册 usage recorder。
- 状态 JSON 写入 tmp+rename 原子;due-check+watermark 在单飞锁内。

---

## Wave A — 台账 + 觉察证据链 + 对话线(Phase 0、1)

### Task 0: `profile_update_ledger` 表 + DAO + 8 写点挂钩 + CLI

**Files:** 修改 `src/openbiliclaw/storage/database.py`(建表+DAO)、`src/openbiliclaw/soul/engine.py`(挂钩:preference 覆写 855-885 / soul 重建 900-920 / dislike purge 887-898 / feedback batch 写入 / init 建像 307 附近 / cognition sync)、`src/openbiliclaw/soul/layer_updaters.py`(pipeline 各层持久化,184 附近)、`src/openbiliclaw/soul/speculator.py`(promote/confirm/reject)、12h 整理(压缩/归档/revert,实现时定位)、`src/openbiliclaw/cli.py`(`ledger` 子命令);新增 `tests/test_profile_ledger.py`。

**Interfaces:** Consumes: 各写点 before/after 摘要 + source refs。Produces: **动作结束后一次 INSERT**,行含 `outcome(success|failed)`(r3/R2-6,不做 attempted 预写);挂钩 try/except WARNING;CLI 表格输出(--line/--days 过滤)。

**Steps:**

- [ ] Write one focused failing test:DAO 落行/过滤。
- [ ] Run `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_profile_ledger.py -q` and confirm FAIL。
- [ ] 建表(幂等迁移)+ DAO → PASS。
- [ ] Failing tests:**8 个写点逐一**(spec D5 清单)→ 动作后台账行存在(outcome=success)、source_refs 非空、diff ≤2000 字符;写点动作抛异常 → outcome=failed 行;台账写失败 WARNING 主流程照常。
- [ ] 逐点挂钩 → PASS;实现中发现清单外写点 → 补挂钩 + 更新 spec/soul.md 清单(记录进 PR)。
- [ ] CLI 子命令 + 输出测试。
- [ ] Run `pytest tests/test_soul_engine.py tests/test_database.py tests/test_cli.py -q` 回归 + ruff + mypy。

**Acceptance:**

- Numeric gate:8 写点 + WARNING + CLI ≥10 用例;零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_profile_ledger.py tests/test_soul_engine.py -q`; record in PR。

### Task 1: AwarenessNote 证据链(id + source_event_ids)

**Files:** 修改 `src/openbiliclaw/soul/profile.py`(AwarenessNote 增 `note_id`/`source_event_ids`,兼容读:旧数据默认空)、`src/openbiliclaw/soul/awareness_analyzer.py`(解析时归属事件 id;归属不可得整批近似挂载并标注)、`src/openbiliclaw/soul/cognition_cycle.py`(传入本轮 cursor 消费的事件 ids);测试并入 `tests/test_soul_engine.py` 或新增 `tests/test_awareness_evidence.py`。

**Steps:**

- [ ] Failing test:awareness 输出 note 带 note_id 与 source_event_ids;旧格式数据加载不炸(兼容)。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] 回放不变性:prompt 输入不变(只是解析侧增强),awareness prompt 字节不变断言。
- [ ] Run `pytest tests/test_soul_engine.py -q` + ruff + mypy。

**Acceptance:**

- Numeric gate:id/回溯/兼容 3 用例;awareness 既有用例零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_awareness_evidence.py tests/test_soul_engine.py -q`。

### Task 2: 对话学习串行队列 + scope/turn_id 透传

**Files:** 新增 `src/openbiliclaw/soul/dialogue_learn_queue.py`(单 worker asyncio 队列,**worker 自持生命周期、不入 cancel_all 注册表,r5/R4-2**;显式 pause/resume/shutdown;drain:stop-accepting → `queue.join()` → 停 worker);修改 `src/openbiliclaw/soul/dialogue.py`(投递替代 `asyncio.create_task`,`dialogue.py:133`;透传 scope/turn_id,默认 "chat")、`src/openbiliclaw/api/runtime_context.py`(`:506` cancel_all **之前** pause-drain 本队列;构建成功停旧启新)、`src/openbiliclaw/api/app.py`(durable 路径传 scope/turn_id;`:4005` shutdown 钩子;**热重载失败回滚分支 resume 旧队列**);新增 `tests/test_dialogue_learn_queue.py`。

**Steps:**

- [ ] Failing test:并发投递 5 个学习任务 → 严格串行执行(执行顺序断言 + 无交错)。
- [ ] Confirm FAIL → 队列实现 → PASS。
- [ ] Failing test:drain 时序;热重载成功(pause-drain → 就绪 → 停旧启新,无交错);热重载失败(构建抛异常 → 回滚分支 resume 旧队列 → 后续投递正常);**cancel_all 执行后队列 worker 仍存活可 resume(r5/R4-2)**;uvicorn shutdown 钩子触发 drain。
- [ ] Failing test:scope/turn_id 透传到 learn 调用参数。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Run `pytest tests/test_soul_engine.py tests/test_api_app.py -q` 回归 + ruff + mypy。

**Acceptance:**

- Numeric gate:串行/drain/透传 ≥5 用例;chat 端点零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_dialogue_learn_queue.py -q`。

### Task 3: 窗口+回灌(限定范围)+ 活跃清单注入 + settles + builder 合规化 + Wave A 文档 gate

**Files:** 修改 `src/openbiliclaw/soul/dialogue.py`(截断 `DIALOGUE_WINDOW_TURNS=20`;popup/scope='chat'/completed 限定回灌,注入 database)、`src/openbiliclaw/soul/dialogue_insight_analyzer.py`(活跃清单注入:speculation domain 键 + insight hash8 键 + open 疑惑 id——Wave B 前疑惑清单为空列表)、`src/openbiliclaw/llm/prompts.py`(dialogue-insight builder 常量化 system + sort_keys + settles 输出契约;入 invariance 清单)、`src/openbiliclaw/soul/engine.py`(learn 处理 settles:白名单=注入清单、scope!="chat" 跳过、结算调既有函数、台账含 turn_id);测试 `tests/test_dialogue_context.py` + 并入 `tests/test_soul_engine.py`。

**Steps:**

- [ ] **基线先行**(单独提交):≤20 轮对话 prompt 字节快照;dialogue-insight 提取 prompt 快照(builder 合规化是行为等价重构,对照用)。
- [ ] Failing tests:25 轮→截断 20;≤20 轮→字节同基线;回灌仅 popup+chat+completed(CLI 构造无 DB 不回灌、probe scope 行排除、pending/failed 排除)。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Failing tests:注入清单含三类自然键;**hash8 稳定性(r3/R2-8):SHA-256+NFC+strip+空白折叠的 canonicalization 对等价文本产出同键、对不同文本稳定;清单内碰撞 → 扩展 hex16,仍碰撞跳过+WARNING**;settles 结算 speculation(domain)/insight(hash8)/未知键丢弃+WARNING/非 chat scope 跳过;结算台账行含 turn_id;同一 turn 重复 settles 幂等(状态不劣化断言)。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] builder invariance 清单核对(新旧 builder 全绿)。
- [ ] Run `pytest tests/test_soul_engine.py tests/test_llm_prompts.py tests/test_api_app.py -q` + ruff + mypy。
- [ ] **Wave A 文档 gate**:`docs/modules/soul.md`(台账+写点清单+对话补丁)、`docs/modules/storage.md`(ledger)、`docs/modules/cli.md`(ledger 命令)、`docs/changelog.md`。

**Acceptance:**

- Numeric gate:窗口/回灌限定 ≥5 + settles ≥6 + invariance 全绿;`test_soul_engine.py` 零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_dialogue_context.py tests/test_soul_engine.py tests/test_llm_prompts.py -q`。

---

## Wave B — 疑惑机制(Phase 2)

### Task 4: confusions 表(DB 约束)+ 生命周期 + 两产生源

**Files:** 修改 `src/openbiliclaw/storage/database.py`(confusions 表 + partial unique index `WHERE status='clarifying'` + DAO 含原子 claim:`UPDATE ... SET status='clarifying' WHERE id=? AND NOT EXISTS(clarifying)` 语义)、新增 `src/openbiliclaw/soul/confusion.py`(状态机 + TTL 扫描并入 12h 循环)、修改 `src/openbiliclaw/soul/awareness_analyzer.py`(r3/R2-5:**保留 `analyze()` 完全不变**,新增 `analyze_with_confusions() -> (notes, confusion_candidates)`;仅 cognition_cycle 切换新 API,`engine.py:1253` 等旧调用方零改动)、`src/openbiliclaw/llm/prompts.py`(r4/R3-2:**新增独立 builder `build_awareness_with_confusions_prompt`**,静态 system + 入 invariance 清单;既有 `build_awareness_prompt` 一字不动)、`src/openbiliclaw/soul/cognition_cycle.py`(切换到 analyze_with_confusions + 新 builder;候选落库;**新旧 awareness 输出 A/B 对照记录进 PR**——有意行为变更过质量铁律)、`src/openbiliclaw/soul/speculator.py`(expire 钩子:`0 < confirmation_count < threshold` → 生成疑惑,现存字段可判定);新增 `tests/test_confusion_lifecycle.py`。

**Steps:**

- [ ] Failing test:DAO 全生命周期 + **跨连接并发 claim**(两个独立 SQLite 连接并发置 clarifying → 恰一成功)。
- [ ] Confirm FAIL → 表 + index + 状态机 → PASS。
- [ ] Failing test:`analyze()` 与 `build_awareness_prompt` 字节不变(旧调用方零回归);`analyze_with_confusions` 用独立新 builder(invariance 清单含之),无候选时 notes 解析路径与 `analyze()` 等价;合法候选落库、越界丢弃+WARNING。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Failing test:speculation 部分确认 expire → 疑惑生成(speculation 照常 expire)+ 台账两行;零确认 expire → 不生成。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Run `pytest tests/test_soul_engine.py tests/test_llm_prompts.py -q` 回归 + ruff + mypy。

**Acceptance:**

- Numeric gate:生命周期 5 态 + 并发 claim + 两产生源 + 兼容 ≥9 用例;awareness 零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_confusion_lifecycle.py tests/test_soul_engine.py -q`。

### Task 5: 澄清三路 + 三出口 + 冻结(chokepoint + held)+ Wave B 文档 gate

**Files:** 修改 `src/openbiliclaw/api/app.py`(durable scope 白名单加 "confusion"——实现时定位实际 scope 校验点;成功侧效应结算到解读)、`src/openbiliclaw/soul/confusion.py`(ask 调度:claim + 72h 冷却持久化 + defer;三出口)、preference 持久化 chokepoint(实现时确认 `layer_updaters.py:184` flat persist 与 `engine.py:855-885` 的收敛点;**不可收敛则两处都挂并清单化**——冻结集过滤:冻结 topic 新增/上调 → 写入 `confusions.held_updates` 搁置 + 台账;resolved 重放合并 / dismissed·expired 丢弃;trial 态 related topic 回滚补偿——Wave D 前记台账不回滚)、`src/openbiliclaw/soul/dialogue_insight_analyzer.py`(活跃清单加 open 疑惑;settles 支持 confusion id);测试并入 `tests/test_confusion_lifecycle.py` + `tests/test_api_app.py`。

**Steps:**

- [ ] Failing test:scope="confusion" durable turn → 回复结算到解读 → resolved;defer → 冷却持久化(重启后仍在冷却)。
- [ ] Failing test:并发 claim 恰一(复用 Task 4 index)+ 72h 冷却拒绝二问。
- [ ] Failing test:三出口(转正 insight / 直接结算+事件折扣标记 / dismissed),台账链完整。
- [ ] Failing test(冻结):open 疑惑关联 topic → 新增被搁置进 held_updates(每项稳定 id + 状态 held)+ 台账;已有权重不动。
- [ ] Failing test(重放状态机,r3/R2-2/R2-3):resolved-真实兴趣型 → held→replaying(持久化)→ 并入下次偏好分析输入(rebase 语义,LLM 以当前画像重评估)→ 成功后置 applied 并清除;resolved-代理行为/误读型 → 直接 discarded(不重放);expired/dismissed → discarded。
- [ ] Failing test(崩溃与幂等,r5/R4-1):置 replaying 与记 `replay_submitted_at+batch_id` 回执同一 SQLite 事务;**记回执后、applied 前崩溃 → 恢复置 applied_unverified+WARNING 且不重复提交**(宁漏勿双计);无回执防御分支 → 重试,`replay_attempts` 达 2 → discarded+WARNING;重复 resolve → 已 applied/applied_unverified/discarded 跳过。台账行带 held_id 仅作观察,回执不依赖台账。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Run `pytest tests/test_api_app.py tests/test_preference_analyzer.py tests/test_soul_engine.py -q` 回归 + ruff + mypy。
- [ ] **Wave B 文档 gate**:`docs/modules/soul.md`、`docs/modules/storage.md`(confusions)、`docs/changelog.md`。

**Acceptance:**

- Numeric gate:澄清/预算/出口/冻结-held-重放 ≥10 用例;偏好路径零回归(无疑惑时冻结过滤零差异断言)。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_confusion_lifecycle.py tests/test_api_app.py -q`。

---

## Wave C — 态势门控(Phase 3)

### Task 6: gate builder + 执行体(异步 shadow / enforce 校验 / off 逐字节)

**Files:** 修改 `src/openbiliclaw/llm/prompts.py`(`build_posture_gate_prompt`:静态 system + sort_keys,入 invariance 清单)、新增 `src/openbiliclaw/soul/posture_gate.py`(执行体:verdict 白名单、解析失败→downgrade、caller 注册;shadow=异步旁路任务进注册表、enforce=同步、off=旁路)、`src/openbiliclaw/config.py`(`posture_gate_mode: shadow|enforce|off` 默认 shadow;`posture_gate_force_enforce: false`;save-time 校验 r4/R3-3 三条件:enforce 且非 force 需「最早有效判定距今 ≥14 天 **且** 近 14 天有效判定 ≥10 条 **且** 近 7 天 ≥1 条」否则 blocking 拒绝)、新增 `tests/test_posture_gate.py`。

**Steps:**

- [ ] Failing test:builder invariance + user 段三要素(候选/core memory/台账 30 天摘要)。
- [ ] Confirm FAIL → builder → PASS。
- [ ] Failing tests:三 verdict;解析失败→downgrade;**shadow 异步零延迟 + 快照隔离**(写入完成时判定未跑;commit boundary 捕获不可变快照 {before, after, source_refs, gate_id},判定任务只消费快照——判定前对活状态再做一次写入,断言判定输入不受污染,r3/R2-4;判定事后落台账 shadow_* 行;LLM 异常→shadow_error 行);enforce 同步拦截;off 完全旁路(门控 LLM 零调用断言)。
- [ ] Failing test:save-time 五态(r4/R3-3)——首日刷满 10 条但最早判定不足 14 天 → 拒;有效判定 <10 拒;仅 shadow_error 拒;近 7 天无判定拒;三条件齐 → 放行;force=true 无条件放行。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Run `pytest tests/test_llm_prompts.py tests/test_config.py tests/test_api_config_guards.py -q` + ruff + mypy。

**Acceptance:**

- Numeric gate:模式语义 ≥8 用例(含异步零延迟与 save-time);invariance 全绿。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_posture_gate.py tests/test_llm_prompts.py -q`。

### Task 7: 三接入点接线 + Wave C 文档 gate

**Files:** 修改 `src/openbiliclaw/soul/engine.py`(①dialogue candidates 按 kind 分流:interest/dislike 现路径、goal/value/state 门控、downgrade 置信=confidence×0.6 转 insight;③soul rebuild:diff 摘要过门控,downgrade→放弃本次 rebuild+台账)、`src/openbiliclaw/soul/layer_updaters.py` 或 pipeline 消费点(②VALUES/CORE 层 updater 写入前过门控,downgrade 固定置信 0.5 转 insight;ROLE 不过,校准注释);测试并入 `tests/test_posture_gate.py` + `tests/test_soul_engine.py` + `tests/test_pipeline_advanced.py`。

**Steps:**

- [ ] Failing tests(接入点①):goal 类 enforce-reject → preference 未变+台账;shadow → 照写+shadow 行;interest 类 → 门控零调用。
- [ ] Failing tests(接入点②):VALUES 层消费 enforce-downgrade → 层未写+insight 生成(0.5)+台账;INTEREST/ROLE 层 → 零调用。
- [ ] Failing tests(接入点③):rebuild enforce-downgrade → 放弃+台账;shadow → 照常 rebuild+shadow 行。
- [ ] Confirm FAIL → 接线 → PASS。
- [ ] **回放门**:off 模式下 `learn_from_dialogue` + pipeline 全行为与现状逐字节一致(既有测试全绿 + off 断言)。
- [ ] Run `pytest tests/test_soul_engine.py tests/test_pipeline_advanced.py -q` 回归 + ruff + mypy。
- [ ] **Wave C 文档 gate**:`docs/modules/soul.md`(门控三面)、`docs/modules/config.md`(三字段)、`docs/architecture.md`+`docs/spec.md`+README 双语图(无条件)、`docs/changelog.md`。

**Acceptance:**

- Numeric gate:接入点×模式矩阵 ≥9 用例;off 零差异;既有 soul/pipeline 测试零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_posture_gate.py tests/test_soul_engine.py tests/test_pipeline_advanced.py -q`。

---

## Wave D — topic 状态机 + 提炼节奏(Phase 4、5,RECOMMENDED)

### Task 8: topic 状态机 + archived 序列化排除(开关+对照)

**Files:** 修改 `src/openbiliclaw/soul/profile.py`(interests 状态字段持久化+兼容读,254 附近序列化)、`src/openbiliclaw/soul/engine.py`(trial 入域/证据计数)、12h 整理(衰减/归档/复燃/细分 shadow 提议)、dislike purge 改归档+避雷、`src/openbiliclaw/discovery/strategies/_utils.py`(`build_profile_summary` 排除 archived,受 `topic_lifecycle_serialization` 开关,默认 off)、`src/openbiliclaw/config.py`(开关);新增 `tests/test_topic_lifecycle.py`。

**Steps:**

- [ ] Failing tests:trial(证据≥5 或 7 天持续→active)/30 天→decaying(×0.5)/再 30 天→archived/复燃/旧数据默认 active/跃迁台账/细分提议 shadow。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Failing test(最小消费):开关 off → `build_profile_summary` 字节不变(回放门);on → archived 排除。
- [ ] Confirm FAIL → 实现 → PASS;**质量铁律记录**:开关 on 的新旧序列化 A/B 对照样例写入 PR,线上启用前人工比对推荐质量。
- [ ] Run `pytest tests/test_preference_analyzer.py tests/test_soul_engine.py tests/test_discovery_engine.py -q` 回归 + ruff + mypy。

**Acceptance:**

- Numeric gate:状态机 6 跃迁 + 兼容 + 开关两态 ≥9 用例;off 字节不变。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_topic_lifecycle.py -q`。

### Task 9: 觉察提炼触发扩展 + 状态原子化 + Wave D 文档 gate + 总核对

**Files:** 修改 `src/openbiliclaw/soul/cognition_cycle.py`(≥30 事件/强信号插队触发;state JSON tmp+rename 原子写;due-check+watermark 消费入单飞锁;异常不前进 watermark);文档:`docs/modules/soul.md` + `docs/modules/config.md`(若新增触发阈值配置)+ `docs/changelog.md`;对照 spec Documentation obligations 总核对进 PR。

**Steps:**

- [ ] Failing tests:30 条触发/强信号插队/并发触发恰一执行/异常后 watermark 未前进(重做)/原子写(写入中断不损坏 state)。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] 回放不变性:同批事件 awareness prompt 输出路径不变。
- [ ] 文档总核对(含 Wave A 的 cli.md 复查),结果进 PR。

**Acceptance:**

- Numeric gate:触发/单飞/原子 ≥6 用例;文档义务逐项勾对。

## Verification after merge

- **shadow 观察**:enforce 前 ≥14 天(save-time 强制),每周查台账 `SELECT gate_verdict, COUNT(*) FROM profile_update_ledger WHERE gate_verdict LIKE 'shadow_%' GROUP BY 1`;shadow_reject 率 >30% 人工抽查 reason 再定 enforce;shadow_error 率 >10% 排查 provider。
- **成本观察**:`cost --by caller` 新 caller(posture_gate/confusion)日调用 >10 次人工排查(单用户预期:门控 ~2-5 次/天、疑惑 ask ≤1 次/3 天)。
- **打扰观察**:疑惑 defer/忽略率一周 >2/3 → 冷却延长至 7 天。
- **对话学习队列**:日志确认 drain 在重启/热重载时执行;队列深度 >10 告警(学习跟不上说明 LLM 慢,人工看)。
- 回滚:各 Wave 独立提交 revert;门控/细分/序列化排除默认 shadow/off,一键停用。Owner:white。

## Explicitly out of scope

- 对话历史语义检索(v2);判别式多解读探针(v2);"正反混合"僵局判据(v2,需 speculation 存储扩展)。
- speculation / insight 存储合并迁移。
- trial topic 推荐侧小流量消费(本版仅状态;archived 序列化排除是唯一最小消费,开关默认 off)。
- popup/桌面/移动 Web 台账 UI(CLI 先行);跨进程 lease(单 daemon)。
