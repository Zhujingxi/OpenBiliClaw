# 认知画像流水线 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-17-cognitive-profile-pipeline-spec.md`](./2026-07-17-cognitive-profile-pipeline-spec.md)
> **Status:** r1 — 待 codex 对抗 review
> **Execution order:** Wave A(Task 0→1→2)→ Wave B(Task 3→4)→ Wave C(Task 5→6)→ Wave D(Task 7→8,RECOMMENDED 可独立决策是否执行)。每 Wave 最后一步完成其文档子集,Wave 才算可交付。Wave A 可独立交付;B 依赖 A(台账);C 依赖 A、部分依赖 B(疑惑 resolved 出口送门控,若 B 未上则该出口保持直写);D 依赖 A。
> **Tech:** Python 3.11+/仓库 `.venv/bin/python`(勿用裸 `python`);测试 `PYTHONPATH=$PWD/src <venv>/python -m pytest <file> -q`(**必须在 worktree 根,PYTHONPATH 指 worktree src**);lint `ruff format/check src/ tests/`;类型 `mypy src/`(strict)。本 plan 无扩展侧改动。

**Invariants that MUST hold — re-read before each task:**

- Prompt-cache 静态性:system prompt 只能是模块级常量;调用不变性测试全程绿;新 builder(posture gate)加入清单。
- 回放不变性:不含新语义对象的输入,偏好分析渲染与对话 prompt 字节不变;基线快照先行并单独提交。
- 门控只拦深层、shadow 先行 ≥14 天;topic 快线永不过门控。
- 台账只追加;写入失败 WARNING 不阻断主流程。
- 收编不迁移:speculation 与 insight hypothesis 存储/状态机不动,统一发生在台账与结算挂钩层。
- 疑惑不写画像;打扰预算(并发 ≤1、冷却 72h、defer 语义复用探针)。
- 阈值带校准注释;LLM 结构化输出白名单/clamp + WARNING(pitfall #4);解析失败保守化(门控→downgrade,settles→丢弃)。
- 单用户全量注入,不建向量检索;新 LLM caller 可被 `cost --by caller` 观察。

---

## Wave A — 台账 + 对话线三补丁(Phase 0、1)

### Task 0: `profile_update_ledger` 表 + DAO + 写入点挂钩

**Files:** 修改 `src/openbiliclaw/storage/database.py`(建表 + `append_profile_ledger()` + `list_profile_ledger()`);修改 `src/openbiliclaw/soul/engine.py`(preference 覆写 `engine.py:855-885`、soul 重建 `engine.py:900-920`、dislike purge `engine.py:887-898` 三处挂钩)、`src/openbiliclaw/soul/speculator.py`(promote/confirm/reject 挂钩)、12h 画像整理归档点(实现时定位 consolidation 写入函数);新增 `tests/test_profile_ledger.py`。

**Interfaces:** Consumes: 各写入点的 before/after 摘要 + source refs。Produces: 只追加台账行(spec Phase 0 schema);挂钩异常 WARNING 不阻断(不变量:台账是观察者)。

**Steps:**

- [ ] Write one focused failing test:`append_profile_ledger` 落行 + `list_profile_ledger` 按 line/days 过滤。
- [ ] Run `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_profile_ledger.py -q` and confirm FAIL。
- [ ] 建表(initialize 幂等迁移,对齐既有新表先例)+ DAO 最小实现 → PASS。
- [ ] Failing tests(逐挂钩点):preference 覆写 / soul 重建 / dislike purge / speculation promote+confirm+reject / 整理归档 → 各动作后台账行存在且 `source_refs` 非空、`diff` 截断 ≤2000 字符。
- [ ] 逐点挂钩实现 → PASS;挂钩内 try/except WARNING 用例(台账写失败,主流程照常)。
- [ ] Run `pytest tests/test_soul_engine.py tests/test_database.py -q` 回归 + ruff + mypy。

**Acceptance:**

- Numeric gate:D5 清单 6 个写入点挂钩用例全过;主流程零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_profile_ledger.py tests/test_soul_engine.py -q`; record in PR。

### Task 1: 对话窗口上限 + chat_turns 回灌

**Files:** 修改 `src/openbiliclaw/soul/dialogue.py`(`_history_to_messages` 截断、启动回灌)、`src/openbiliclaw/api/runtime_context.py` 或 dialogue 构造点(注入 database 以回灌);新增 `tests/test_dialogue_context.py`。

**Interfaces:** Consumes: `DIALOGUE_WINDOW_TURNS = 20`(校准注释:典型侧边栏会话上界,单轮 ~80 tokens → 窗口 ≲1.6k;首轮真实数据后重校)。Produces: prompt 历史 ≤20 轮;进程启动从 `chat_turns` 回灌最近 20 轮(session 各自回灌,只取 status=completed 行,按 created_at 逆序取后正序拼)。

**Steps:**

- [ ] **先做基线**:改动前对 ≤20 轮会话生成对话 prompt 快照,存测试常量,单独提交(回放不变性)。
- [ ] Failing test:25 轮历史 → prompt 只含最近 20 轮;≤20 轮 → 与基线字节一致。
- [ ] Confirm FAIL → 实现截断 → PASS。
- [ ] Failing test:构造 chat_turns 行 → 新建 SocraticDialogue 回灌出等价 `_history`;pending/failed 行不回灌。
- [ ] Confirm FAIL → 实现回灌 → PASS。
- [ ] Run `pytest tests/test_api_app.py -q`(chat 端点回归)+ ruff + mypy。

**Acceptance:**

- Numeric gate:截断/字节不变/回灌/脏行过滤 4 用例全过;chat 端点零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_dialogue_context.py tests/test_api_app.py -q`。

### Task 2: 提取器活跃清单注入 + `settles` 结算通道 + Wave A 文档 gate

**Files:** 修改 `src/openbiliclaw/soul/dialogue_insight_analyzer.py`(输入追加活跃清单)、`src/openbiliclaw/llm/prompts.py`(提取 prompt 的 user 段扩展 + settles 输出契约;system 段静态)、`src/openbiliclaw/soul/engine.py`(`learn_from_dialogue` 处理 settles:speculation → `user_confirm/reject_speculation`;insight → `update_from_feedback`;结算进台账);测试并入 `tests/test_soul_engine.py` + `tests/test_llm_prompts.py` 清单核对。

**Interfaces:** Consumes: speculation 活跃项 + insight 待验证项(全量注入 user prompt,单用户 ≤10+N 条)。Produces: candidates 原有 schema + 可选 `settles[]`;白名单校验(kind/verdict 枚举、未知 id 丢弃 + WARNING)。

**Steps:**

- [ ] Failing test:提取 prompt 的 user 段包含活跃清单;system 段与常量一致(调用不变性)。
- [ ] Confirm FAIL → 实现注入 → PASS。
- [ ] Failing tests:settles 三类结算(speculation confirm / insight feedback / 未知 id 丢弃+WARNING);结算行进台账。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] 回放不变性:无活跃清单(空列表)且 LLM 未返回 settles 时,`learn_from_dialogue` 行为与现状一致(既有测试全绿证明)。
- [ ] Run `pytest tests/test_soul_engine.py tests/test_llm_prompts.py -q` + ruff + mypy。
- [ ] **Wave A 文档 gate**:`docs/modules/soul.md`(台账+对话补丁)、`docs/modules/storage.md`(ledger 表)、`docs/changelog.md`。

**Acceptance:**

- Numeric gate:注入 + 三类结算 + 防御 + 不变性用例全过;`test_soul_engine.py` 既有用例零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_soul_engine.py tests/test_llm_prompts.py tests/test_profile_ledger.py -q`。

---

## Wave B — 疑惑机制(Phase 2)

### Task 3: `confusions` 表 + 生命周期 + 两个产生源

**Files:** 修改 `src/openbiliclaw/storage/database.py`(confusions 表 + DAO)、新增 `src/openbiliclaw/soul/confusion.py`(状态机:open→clarifying→resolved/dismissed/expired;TTL 扫描并入 12h 循环)、修改 `src/openbiliclaw/llm/prompts.py`(awareness prompt 静态扩展:输出 `confusion_candidates` ≤2 条契约)、`src/openbiliclaw/soul/cognition_cycle.py`(候选落库)、`src/openbiliclaw/soul/speculator.py`(僵局降格钩子:连续 2 个 TTL 未确认且正反混合 → 生成疑惑;speculation 本体照常 expire);新增 `tests/test_confusion_lifecycle.py`。

**Interfaces:** Consumes: awareness 输出候选(白名单校验:interpretations ≤4、summary 非空,越界丢弃+WARNING)、speculation 僵局信号。Produces: confusions 行(TTL 14 天,校准注释);状态跃迁进台账。

**Steps:**

- [ ] Failing test:DAO 全生命周期(创建/状态跃迁/TTL 过期扫描/台账行)。
- [ ] Confirm FAIL → 表 + 状态机实现 → PASS。
- [ ] Failing test:awareness 输出含合法/越界 confusion_candidates → 合法落库、越界丢弃+WARNING;prompt system 段调用不变。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Failing test:speculation 僵局(2 TTL + 混合信号)→ 疑惑生成、speculation 正常 expire、台账两行。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Run `pytest tests/test_soul_engine.py tests/test_llm_prompts.py -q` 回归 + ruff + mypy。

**Acceptance:**

- Numeric gate:生命周期 5 态 + 两产生源 + 防御用例全过;awareness 既有输出路径零回归(回放不变性:无矛盾输入时 awareness 输出与现状一致)。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_confusion_lifecycle.py tests/test_soul_engine.py -q`。

### Task 4: 澄清三路 + resolved 三出口 + topic 冻结反压 + Wave B 文档 gate

**Files:** 修改 `src/openbiliclaw/api/app.py`(durable chat scope 白名单加 `"confusion"`,`app.py:6454`;成功侧效应结算到解读,`app.py:6491` 模式)、`src/openbiliclaw/soul/confusion.py`(ask 调度:并发 ≤1、冷却 72h、defer 语义;resolved 三出口)、`src/openbiliclaw/soul/engine.py` 或 preference 写入点(冻结集过滤:open/clarifying 疑惑的 related_topics 的新增/上调被搁置 + 台账;resolved/expired 解冻)、`src/openbiliclaw/soul/dialogue_insight_analyzer.py`(Task 2 的活跃清单加入 open 疑惑;settles 支持 confusion);测试并入 `tests/test_confusion_lifecycle.py` + `tests/test_api_app.py`。

**Interfaces:** Consumes: 疑惑 ask 回复(经分类结算到某解读)。Produces: 转正假设(insight hypothesis 初始置信 = 解读置信)/ 直接结算(候选更新送 Phase 3 门控,门控未上线时直写 + 台账;相关觉察/事件盖折扣标记,复用 retraction metadata patch 机制)/ dismissed(evidence 盖「已澄清-无信息」)。

**Steps:**

- [ ] Failing test:scope="confusion" 的 durable turn → 回复结算到解读 → resolved;defer 回复 → 冷却。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Failing test:并发 ≤1(两个 open 疑惑只有一个能进 ask)、72h 冷却。
- [ ] Failing test:三出口各一(转正/直接结算+事件折扣/dismissed),台账链完整。
- [ ] Failing test(反压):open 疑惑关联 topic → 偏好分析对该 topic 的新增被搁置 + 台账;resolved 后放行。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Run `pytest tests/test_api_app.py tests/test_preference_analyzer.py -q` 回归 + ruff + mypy。
- [ ] **Wave B 文档 gate**:`docs/modules/soul.md`(疑惑机制)、`docs/modules/storage.md`(confusions 表)、`docs/changelog.md`。

**Acceptance:**

- Numeric gate:澄清/预算/三出口/反压 ≥8 用例全过;偏好分析既有路径零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_confusion_lifecycle.py tests/test_api_app.py -q`。

---

## Wave C — 态势门控与分流(Phase 3)

### Task 5: `build_posture_gate_prompt` + 门控执行体(shadow 默认)

**Files:** 修改 `src/openbiliclaw/llm/prompts.py`(新 builder,static system:三判定判据 + 「冲突不是错误是新假设」;user:候选 + core memory + 台账 30 天深层摘要;加入 invariance 测试清单)、新增 `src/openbiliclaw/soul/posture_gate.py`(执行体:LLM 调用、verdict 白名单、解析失败→downgrade、caller 注册)、`src/openbiliclaw/config.py`(`[soul] posture_gate_mode: shadow|enforce|off`,默认 shadow;save 时白名单校验)、新增 `tests/test_posture_gate.py`。

**Steps:**

- [ ] Failing test:builder system 段调用不变;user 段含三要素。
- [ ] Confirm FAIL → builder → PASS(并加入 `_builder_test_inputs()` 清单)。
- [ ] Failing tests:三 verdict 路径、解析失败→downgrade、shadow 模式判定照跑但放行、off 模式跳过、全部进台账(shadow_ 前缀 verdict)。
- [ ] Confirm FAIL → 执行体 → PASS。
- [ ] Run `pytest tests/test_llm_prompts.py tests/test_config.py -q` + ruff + mypy。

**Acceptance:**

- Numeric gate:3 verdict + shadow/off + 保守化 + 台账 ≥7 用例;prompt invariance 全绿。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_posture_gate.py tests/test_llm_prompts.py -q`。

### Task 6: 深层/表层分流接线 + Wave C 文档 gate

**Files:** 修改 `src/openbiliclaw/soul/engine.py`:`learn_from_dialogue` 合格 candidates 按 kind 分流(interest/dislike → 现路径;goal/value/state → 门控);soul 重建触发(`engine.py:900-920`)改道门控;downgrade → 转 insight hypothesis(置信 = 候选 confidence × 0.6,校准注释);测试并入 `tests/test_posture_gate.py` + `tests/test_soul_engine.py`。

**Steps:**

- [ ] Failing test:goal 类 candidate 在 enforce 下被 reject → preference 未变、台账有 reject 行;shadow 下照写 + shadow 行。
- [ ] Failing test:interest 类 candidate 不经门控(门控 LLM 零调用断言)。
- [ ] Failing test:downgrade → insight hypothesis 生成(置信折算)+ 台账。
- [ ] Confirm FAIL → 分流实现 → PASS。
- [ ] **回放不变性**:posture_gate_mode=off 时 `learn_from_dialogue` 全行为与现状一致(既有测试全绿)。
- [ ] Run `pytest tests/test_soul_engine.py -q` 回归 + ruff + mypy。
- [ ] **Wave C 文档 gate**:`docs/modules/soul.md`(门控+分流)、`docs/modules/config.md`(posture_gate_mode)、`docs/architecture.md` + `docs/spec.md` + README 双语图(Soul 三线+门控节点,无条件)、`docs/changelog.md`。

**Acceptance:**

- Numeric gate:分流矩阵(kind × mode)≥6 用例;off 模式零差异;既有 soul 测试零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_posture_gate.py tests/test_soul_engine.py -q`。

---

## Wave D — topic 生命周期 + 提炼节奏(Phase 4、5,RECOMMENDED)

### Task 7: topic 状态机(试用/衰减/归档/复燃/细分提议)

**Files:** 修改 `src/openbiliclaw/memory/manager.py` 或 preference 层存储(interests 叠加 state/evidence_count/last_evidence_at/parent_topic 元数据,兼容旧数据默认 active)、`src/openbiliclaw/soul/engine.py`(新 topic 入域 trial;证据计数更新)、12h 整理(衰减/归档扫描 + 细分提议,提议 shadow 记台账不执行,首轮观察后再启用执行)、dislike purge 改「归档+避雷」;新增 `tests/test_topic_lifecycle.py`。

**Steps:**

- [ ] Failing tests:trial 入域(证据 ≥5 或 7 天持续 → active,校准注释)/ 30 天无证据 → decaying(×0.5)/ 再 30 天 → archived / archived 遇证据 → active 复燃 / 旧数据兼容默认 active / 跃迁台账。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Failing test:细分提议(子类占比 ≥60%)→ 只记台账不执行(shadow)。
- [ ] Run `pytest tests/test_preference_analyzer.py tests/test_soul_engine.py -q` 回归 + ruff + mypy;**实现时核对**:trial topic 与 explore-cluster-cap / 推荐池的对接点,只标记不改推荐逻辑(推荐侧消费 trial 标记留后续)。

**Acceptance:**

- Numeric gate:状态机 6 跃迁 + 兼容 + 细分 shadow 用例全过;偏好层读写零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_topic_lifecycle.py -q`。

### Task 8: 觉察提炼触发扩展 + Wave D 文档 gate + 总核对

**Files:** 修改 `src/openbiliclaw/soul/cognition_cycle.py`(事件量 ≥30 / 强信号插队触发,单飞锁)、awareness 存储加 `source_event_ids`;`docs/modules/soul.md` + `docs/modules/cli.md`(ledger 命令若在本 Wave 实现则此处,否则 Wave A)+ `docs/changelog.md`;对照 spec Documentation obligations 总核对写入 PR。

**Steps:**

- [ ] Failing tests:30 条触发 / 强信号插队 / 单飞不重入 / source_event_ids 回溯。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] 回放不变性:同批事件的 awareness prompt 输出路径与现状一致。
- [ ] 文档总核对,结果进 PR。

**Acceptance:**

- Numeric gate:触发 3 态 + 回溯用例全过;文档义务逐项勾对。

## Verification after merge

- **shadow 观察(核心)**:门控 enforce 前 ≥14 天,每周查台账:`SELECT gate_verdict, COUNT(*) FROM profile_update_ledger WHERE gate_verdict LIKE 'shadow_%' GROUP BY 1`——shadow_reject 率 >30% 说明门控过严或候选质量差,人工抽查 reason 后再定 enforce;疑惑周产生量 >5 说明认知失调检测过敏,收紧 awareness 候选条数。
- **成本观察**:`openbiliclaw cost --by caller` 新增 caller(posture_gate、confusion_ask)的日调用量与费用,7 天均值超预期(门控 >10 次/天)人工排查。
- **打扰观察**:疑惑 ask 频率 ≤1 次/72h 由测试保证;线上一周内用户 defer/忽略率 >2/3 则延长冷却。
- 回滚:各 Wave 独立提交,revert 对应 Wave 提交即可;门控/细分默认 shadow,`posture_gate_mode=off` 一键停用。Owner:white。

## Explicitly out of scope

- 对话历史语义检索(v2,窗口+回灌观察后决定);判别式多解读探针(v2)。
- speculation / insight hypothesis 存储合并迁移(收编不迁移)。
- 推荐侧消费 trial/decaying 标记(本版只产状态,消费留后续)。
- popup/桌面/移动 Web 的台账 UI(CLI 先行)。
