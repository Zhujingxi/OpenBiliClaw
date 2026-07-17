# 认知画像流水线 Spec — 三线更新、疑惑/假设生命周期、态势门控与台账

**Created:** 2026-07-17
**Scope:** 画像认知层(`soul/`:engine、pipeline、dialogue、speculator、cognition_cycle、dialogue_insight_analyzer)、存储(`storage/database.py` 新表)、prompt(`llm/prompts.py`)、API(`api/app.py` 对话/洞察端点)。全部为后端内部改动;四端 UI 仅在最后阶段暴露台账查询(CLI 优先,其余端显式排除至后续版本——CLAUDE.md 四端契约在此声明)。
**Out of scope:** 事件采集侧(2026-07-16 spec 已完成);多用户化;对话历史语义检索(v2,先用窗口+回灌观察);speculation 兴趣推测的存储迁移(保留原系统,仅打通台账与门控);画像历史回溯重算。

## Goal

事件与对话已经采集得足够好(PR #85/#121),但**事件→画像的更新流是散装的**:深层特质与表层兴趣混在同一条 LLM 分析里、一次分析即可覆写画像;对话提取候选够阈值直接改偏好、无一致性校验;系统"看不懂"的行为没有表达形式,只能硬猜或污染 topic;画像为什么变成这样无法回溯。

目标架构(与用户三轮讨论定稿):**三条更新线 + 觉察/疑惑/假设三类认知对象 + 态势门控 + 统一台账**。基调是**收编为主、新建为辅**:真正全新的只有疑惑对象、态势门控、台账三样,其余是给现役机制(偏好分析、awareness/insight、speculation、learn_from_dialogue)立规矩。

量化目标与验证命令:

- 深层画像(soul 层 values/life_stage/deep_needs 等)的每次写入都能在台账中回溯到「假设 → 觉察 → 事件」证据链;台账覆盖率 100%(所有画像层写入路径均挂钩)。验证:`pytest tests/test_profile_ledger.py -q`。
- 态势门控 shadow 先行:上线后 14 天内只记录判定不拦截,shadow 期结束以真实判定分布决定 enforce 阈值(质量铁律:过滤 shadow 先行)。验证:`pytest tests/test_posture_gate.py -q` + 台账 shadow 判定查询。
- 对话窗口有上限且重启可恢复;无 retraction/疑惑等新语义时,既有对话回复与偏好分析的 prompt 渲染字节不变(回放不变性)。验证:`pytest tests/test_dialogue_context.py tests/test_llm_prompts.py -q`。
- 疑惑机制端到端:矛盾行为 → 疑惑对象 → 关联 topic 冻结 → 询问 → resolved 三出口。验证:`pytest tests/test_confusion_lifecycle.py -q`。

## Design invariants (MUST hold in every phase)

1. **Prompt-cache 静态性**:所有 prompt 改动的 system 段保持模块级常量;`tests/test_llm_prompts.py::test_prompt_builder_system_messages_are_call_invariant` 全程绿;新 builder 加入该测试清单。
2. **回放不变性(作用域=既有渲染路径)**:不含新语义对象(疑惑/门控/台账标注)的输入,偏好分析事件渲染与对话 prompt 的输出与改动前字节一致;基线快照在实现前生成并单独提交(2026-07-16 spec 同款方法)。
3. **门控只拦深层,shadow 先行**:态势门控仅作用于深层画像写入(soul 层与 preference 层的 goal/value/state 类变更);topic/interest 快线永不过门控。门控 enforce 前必须有 ≥14 天 shadow 期,shadow 判定全量进台账。
4. **台账只追加**:`profile_update_ledger` 只 INSERT,不 UPDATE/DELETE;画像回滚以新的补偿行表达。
5. **收编不迁移**:speculation(`soul/speculator.py`)与 insight hypothesis(`engine.update_from_feedback`)的存储与状态机保持原样;新对象(疑惑)新建表;统一发生在**台账与门控挂钩层**,不做数据迁移(控制爆炸半径;后续版本再评估合并)。
6. **疑惑不写画像**:疑惑对象自身永不修改画像层;它只能产出下游对象(假设/结算动作/觉察折扣标记)。
7. **打扰预算**:疑惑主动询问全局并发 ≤1、单疑惑冷却 ≥72h、复用探针「暂时忽略」defer 状态机语义;询问必须走既有苏格拉底口吻通道。
8. **阈值有出处**:所有新常量(提炼触发阈值、疑惑 TTL、投票增量、窗口轮数、半衰期)带校准注释并标注「首轮真实数据后重校」;LLM 输出经白名单/clamp 校验(CLAUDE.md pitfall #3/#4)。
9. **单用户成本模型**:上下文一律全量注入(core memory、活跃假设/疑惑清单),不建向量检索;新增 LLM 调用点(门控、提炼扩展)必须记录 caller 供 `openbiliclaw cost --by caller` 观察。

## Current diagnosis

### D1. 深层与表层混在同一条分析里,一次 LLM 分析即可覆写画像

- 增量管线:事件 → `ProfileUpdatePipeline.ingest_batch`(`soul/pipeline.py`)按层缓冲,阈值消费 `_update_layer`(`pipeline.py:839-862`)→ `layer_updaters._update_interest`(`soul/layer_updaters.py:149-176`)→ `preference_analyzer.analyze_events` 直接更新偏好层。表层兴趣与深层线索共用此路径,无分级。
- 对话路径更重:每轮 `respond()` 后台触发 `learn_from_dialogue`(`soul/dialogue.py:110-133`、`soul/engine.py:805`):LLM 提取 candidates(kind ∈ interest/dislike/goal/value/state,`llm/prompts.py:928-959`)→ 合并去重(`engine.py:1309`)→ `confidence>=0.8 OR occurrences>=2` 即合格(`engine.py:1359-1364`)→ 作为 `dialogue_insight` 事件喂 `analyze_events` **直接覆写 preference 层**(`engine.py:855-885`),显著变化时整份重建 soul(`engine.py:900-920`)。goal/value/state 这类深层判断与 interest 同门槛同通道,无一致性校验。
- 后果:一次误读(如代理行为、猎奇)即可写入深层画像,且无法回溯依据。

### D2. 对话链路的三处结构问题

- **窗口无上限**:`SocraticDialogue._history` 全量拼进 prompt(`soul/dialogue.py:215-223`,`self._history[:-1]` 无截断),长会话 prompt 无限增长;进程重启即丢(`dialogue.py:65,211`)。
- **持久化与上下文脱钩**:`chat_turns` 表(`storage/database.py:856-872`)只服务 durable turn 的 UI 轮询(`api/app.py:6466`),从不回灌对话上下文,也不被任何分析检索。
- **提取器看不见假设**:`DialogueInsightAnalyzer.extract`(`soul/dialogue_insight_analyzer.py:50`)输入只有 core memory + 当轮对话(`dialogue_insight_analyzer.py:61`);speculation 确认只挂在 durable chat 的 probe scope(`api/app.py:6491-6580`,情感分类 → `speculator.user_confirm/reject/defer`),**普通聊天无法结算任何假设**——用户在闲聊中亲口给出的答案(「那个事儿定了」)与等待中的推测/疑惑对不上号。
- core memory 每轮注入已含 `recent_awareness` 与 `active_insights`(`memory/manager.py:670-675`、`llm/service.py:392,686-691`)——行为背景与部分假设**已在上下文里**,缺的只是 speculation 清单与结算通道。

### D3. 系统缺少「看不懂」的表达形式

- awareness(12h 认知循环,`soul/cognition_cycle.py:247` 重读 events)产出观察;insight/speculation 产出**假设**(insight hypothesis:`engine.update_from_feedback`,confirm≥0.75/reject≤0.35,`engine.py:701`;speculation:确认阈值 3、TTL 3d、max_active 5,`config.py:448+`,探针与苏格拉底 probe scope 是其验证器)。
- 但当行为**无法解释**(与画像矛盾、突变、多解读打架)时,系统没有对象可建:要么硬造一个低置信假设去"验证"(验证个不存在的候选答案),要么什么都不做任由 topic 快线被污染(美妆案例)。「问用户」目前只服务于假设确认(probe scope),不服务于认知失调。

### D4. topic 域没有显式生命周期

- 兴趣以偏好层权重存在,由 LLM 偏好分析浮动;speculation 推测转正受 `max_primary_interests=15` 约束(`soul/speculator.py:956`);dislike 触发 purge(`engine.py:887-898`);12h 画像整理压缩 like 证据。
- 但这些是散落的隐式行为:无试用期(新 topic 一次分析即全权重入域)、无自动衰减语义(实现时核对偏好分析是否事实性衰减)、无细分操作(泛摄影→胶片摄影靠 LLM 自觉)、无归档/复燃(被挤出即消失)。状态跃迁不留痕。

### D5. 画像变更不可回溯

- 画像层写入点分散:preference 覆写(`engine.py:855-885`)、soul 重建(`engine.py:900-920`)、speculation promote(`speculator.py:956`)、dislike purge、12h 整理归档——均无统一变更记录。retraction 台账思想(2026-07-16 spec)只覆盖了事件标注,未覆盖画像层。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | 统一台账(所有画像写入点挂钩) | **MUST** | 一切后续机制的审计底座;独立可交付 |
| 1 | 对话线三补丁(窗口上限+回灌、假设清单注入、结算通道) | **MUST** | 含无上限 prompt 的事实 bug;改动小收益大 |
| 2 | 疑惑对象与生命周期(产生源、澄清三路、topic 冻结反压) | **MUST** | 架构核心新增;防画像污染的第一道闸 |
| 3 | 态势门控(shadow 先行)+ 深层/表层分流 | **MUST** | 深层画像写入的一致性闸门 |
| 4 | topic 生命周期状态机(试用/衰减/细分/归档/复燃) | RECOMMENDED | 结构化包装,错误代价低,可后置 |
| 5 | 觉察提炼节奏改造(12h→事件量触发+强信号插队) | RECOMMENDED | 现 12h 循环可先凑合;提速是优化非纠错 |

**Wave A**(可独立交付):Phase 0 + 1。**Wave B**:Phase 2。**Wave C**:Phase 3。**Wave D**:Phase 4 + 5。每 Wave 完成其文档子集后方可交付(2026-07-16 spec 同款逐 Wave gate)。

## Phase designs

### Phase 0 — 统一台账 `profile_update_ledger`

**表结构**(新表,只追加,不变量 4):
```
id INTEGER PK | ts | line TEXT(topic_fast|deep|dialogue|consolidation|speculation)
change_kind TEXT(interest_update|soul_rebuild|speculation_promote|dislike_purge|
                 gate_decision|confusion_transition|belief_transition|archive)
source_refs TEXT(JSON:事件 ids/觉察 ids/假设 id/疑惑 id/对话 turn_id)
gate_verdict TEXT(''|accept|downgrade|reject|shadow_accept|shadow_downgrade|shadow_reject)
diff TEXT(JSON:before/after 摘要,截断 2000 字符) | reason TEXT
```
**挂钩点**(D5 清单逐一挂):preference 覆写、soul 重建、speculation promote/confirm/reject、dislike purge、12h 整理归档、(后续 Phase 的)疑惑/信念状态跃迁、门控判定。写入失败 WARNING 不阻断主流程(台账是观察者不是参与者)。
**查询入口**:CLI `openbiliclaw ledger [--line] [--days]`(四端契约:CLI 先行,popup/桌面/移动 Web 显式排除至后续版本)。
**验收门**:每个挂钩点一个单测(动作发生 → 台账行存在且 source_refs 非空);`pytest tests/test_profile_ledger.py -q`。

### Phase 1 — 对话线三补丁

1. **窗口上限 + 回灌**:`_history` 截断至最近 `DIALOGUE_WINDOW_TURNS = 20` 轮(校准:20 轮 ≈ 典型侧边栏会话上界,单轮均 ~80 tokens,窗口 ≲1.6k tokens;首轮真实数据后重校);进程启动时从 `chat_turns` 回灌最近 20 轮(`session='popup'` 与 `'cli'` 各自回灌)。窗口截断只影响超长会话——**回放不变性作用域**:≤20 轮会话的 prompt 字节不变(不变量 2)。
2. **提取器看得见假设/疑惑**:`DialogueInsightAnalyzer.extract` 输入追加「活跃清单」段——speculation 活跃项(≤5+5)+ insight hypotheses 待验证项 + (Phase 2 后)open 疑惑,全量注入 user prompt(不变量 9,不检索);输出 schema 增加可选字段 `settles: [{kind: speculation|insight|confusion, id, verdict: support|contradict|answer, note}]`,白名单校验 + 未知 id 丢弃 + WARNING(不变量 8)。
3. **结算通道**:`learn_from_dialogue` 处理 `settles`——speculation → 复用 `user_confirm_speculation`/`user_reject_speculation`(probe scope 现有函数,`api/app.py:6521-6527` 同款);insight → 复用 `update_from_feedback`;confusion →(Phase 2 的 resolved 入口)。结算动作进台账。
**验收门**:窗口/回灌/字节不变各一测;settles 三类结算 + 未知 id 防御各一测;`pytest tests/test_dialogue_context.py tests/test_soul_engine.py -q`。

### Phase 2 — 疑惑对象与生命周期

**新表 `confusions`**:
```
id | created_at | status(open|clarifying|resolved|dismissed|expired)
summary TEXT | interpretations TEXT(JSON,≤4 个候选解读)
evidence_refs TEXT(JSON:觉察/事件 ids) | related_topics TEXT(JSON)
clarify_mode(''|ask|probe|wait) | asked_at | resolved_at | resolution TEXT
ttl_days INT(默认 14,校准:两个 speculation TTL 周期,首轮后重校)
```
**产生源**(v1 两个,其余后续):
- **认知失调检测**:12h 认知循环(awareness 分析)prompt 增加静态指令段——观察与 core memory 轮廓矛盾/突变/多解读时,输出 `confusion_candidates`(结构化,≤2 条/轮,白名单校验)。这是对现有 `build_awareness_prompt` 的扩展,system prompt 保持常量(不变量 1)。
- **假设僵局降格**:speculation 连续 2 个 TTL 周期未达确认阈值且期间有正反混合信号 → 降格生成疑惑(speculation 本体照常 expire,不迁移,不变量 5)。
**澄清三路**:
- **ask**:复用 durable chat probe scope 模式新增 `scope="confusion"`(`api/app.py:6454` 白名单扩展):苏格拉底口吻提问,回复经情感/内容分类结算到对应解读;打扰预算按不变量 7(并发 ≤1,冷却 72h,defer 复用探针忽略状态机语义)。
- **probe**(判别探针):v1 仅在解读可映射到现有探针域时复用 interest probe 机制,不新建探针类型(标注:判别式多解读探针为 v2)。
- **wait**:默认路径,TTL 到期 expired。
**resolved 三出口**:转正为假设(insight hypothesis 或 speculation,带初始置信)/ 直接结算(生成候选更新送门控 + 相关觉察/事件盖折扣标记,复用 retraction 的 metadata patch 机制)/ dismissed(evidence_refs 盖「已澄清-无信息」标记)。
**topic 冻结反压**:open/clarifying 疑惑的 `related_topics` 进入冻结集——偏好分析结果中对冻结 topic 的**新增/权重上调被搁置**(写入前过滤,搁置动作进台账;已有权重不动;resolved/expired 自动解冻)。快线抢跑几小时可接受(讨论定稿:快线先跑、认知层慢半拍纠偏)。
**验收门**:生命周期状态机全路径测试、冻结反压测试、打扰预算测试;`pytest tests/test_confusion_lifecycle.py -q`。

### Phase 3 — 态势门控 + 深层/表层分流

**分流**:`learn_from_dialogue` 的合格 candidates 与 `preference_analyzer` 的输出按 kind 分流——interest/dislike 类照走现有偏好层路径(快线,不过门控);goal/value/state 类与 soul 重建触发**改道门控**。
**门控**:新 builder `build_posture_gate_prompt`(静态 system prompt:门控职责、三种判定的判据、「冲突不是错误是新假设」原则;user prompt:候选变更 + core memory 全量 + 台账最近 30 天深层变更摘要)。输出 `{verdict: accept|downgrade|reject, reason}`,白名单校验,解析失败按 downgrade(保守)。
- **accept** → 执行原写入 + 台账。
- **downgrade** → 不写入;转为 insight hypothesis(置信度按候选 confidence × 0.6,校准注释)继续观察;台账记录。
- **reject** → 不写入,台账记录理由。
**shadow 模式(不变量 3)**:配置 `[soul] posture_gate_mode = "shadow"`(默认)——判定照跑、照进台账,但**全部放行写入**;14 天后人工查台账判定分布(reject 率、downgrade 命中率)再切 `"enforce"`。config 字段进 `docs/modules/config.md`。
**验收门**:三判定路径 + shadow 放行 + 解析失败保守化测试;门控 caller 出现在 cost 统计;`pytest tests/test_posture_gate.py -q`。

### Phase 4 — topic 生命周期状态机(RECOMMENDED)

偏好层 interests 增加状态元数据(不改既有权重语义,叠加字段):`state(trial|active|decaying|archived)`、`evidence_count`、`last_evidence_at`、`parent_topic`。
- **试用期**:偏好分析新增 topic 首次入域为 trial(参与推荐小流量——实现为 explore 池标记,实现时核对 explore-cluster-cap 的对接点);累计证据 ≥5 或 7 天内持续出现 → active(校准注释)。
- **衰减/归档**:`last_evidence_at` 超 30 天(校准:两个月度兴趣周期的一半,首轮后重校)→ decaying(权重×0.5);再 30 天 → archived(不参与推荐,不删除);archived topic 再遇证据 → 直接 active(复燃,免试用)。衰减扫描并入 12h 画像整理。
- **细分**:12h 整理时 LLM 检查大权重 topic 的证据分布,子类占比 ≥60% 时提议分裂(母 topic 降权、子 topic 继承证据),提议过台账、按 shadow→enforce 节奏启用。
- 所有状态跃迁进台账。dislike purge 改为「归档 + 避雷」而非删除。
**验收门**:状态机全跃迁测试 + 复燃测试;`pytest tests/test_topic_lifecycle.py -q`。

### Phase 5 — 觉察提炼节奏(RECOMMENDED)

12h 认知循环保留为兜底;新增触发:未提炼事件累计 ≥30 条(校准:重度用户约半天量)或出现强信号事件(comment_text 非空 / 显式反馈)→ 提前触发一次 awareness 提炼(单飞锁防重入,复用 cognition cycle 的执行体)。觉察(awareness 观察)已有存储沿用,新增 `source_event_ids` 引用字段(觉察→事件可回溯,台账链条的中间环)。
**验收门**:触发条件测试 + 单飞测试;回放不变性(觉察 prompt 对同批事件输出路径不变)。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 0 | 画像层写入台账覆盖 0% → 100%;任意深层特质可答「为什么」 |
| Phase 1 | 对话 prompt 上界从无限 → ~1.6k tokens 窗口;重启不再失忆;普通聊天可结算假设(现状只有 probe scope 能) |
| Phase 2 | 「看不懂」从硬猜/污染 → 显式疑惑对象;美妆类误判在 topic 入域前被冻结 |
| Phase 3 | 深层画像写入从「一次分析即覆写」→ 一致性门控(shadow 数据先行) |
| Phase 4 | topic 进出从隐式浮动 → 显式状态机,误入 topic 自动衰减归档 |
| Phase 5 | 觉察时延从固定 12h → 事件量/强信号自适应 |

## Documentation obligations

- `docs/modules/soul.md` — 三线架构、疑惑/假设生命周期、门控、台账(实现表+公开 API)。
- `docs/modules/storage.md` — `profile_update_ledger`、`confusions` 表与 DAO。
- `docs/modules/config.md` — `posture_gate_mode` 等新 config 字段。
- `docs/modules/cli.md` — `openbiliclaw ledger` 命令。
- `docs/architecture.md` + `docs/spec.md` §3 + README 双语架构图 — Soul 层内部结构变化(三线 + 门控节点),无条件同步。
- `docs/changelog.md` — 逐 Wave 条目。
- 隐私政策:不触发(无新采集面,纯内部处理)。
