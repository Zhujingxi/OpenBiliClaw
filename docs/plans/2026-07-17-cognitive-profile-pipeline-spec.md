# 认知画像流水线 Spec — 三线更新、疑惑/假设生命周期、态势门控与台账

> **后续归一（2026-07-22）**：本 spec 的「三线更新」中，深层画像（VALUES/CORE + soul 层）的事件驱动直写已被 `docs/plans/2026-07-22-deep-line-consolidation-spec.md` 收敛为唯一模式「假设确认 → 门控下 soul 重建」——P1（管线 VALUES/CORE 直写，接入点②）退役、P2（反馈批重建）补门控。以下正文保持历史原样，接入点②与管线深层消费的现状以归一 spec 为准。

**Created:** 2026-07-17(r2,codex 第一轮 20 findings 修订)
**Scope:** 画像认知层(`soul/`:engine、pipeline、layer_updaters、dialogue、speculator、cognition_cycle、awareness_analyzer、dialogue_insight_analyzer、profile)、存储(`storage/database.py` 新表)、prompt(`llm/prompts.py`)、API(`api/app.py` 对话/洞察端点)、CLI(`cli.py` ledger 命令)。四端契约:台账查询 CLI 先行,popup/桌面/移动 Web 显式排除至后续版本。
**Out of scope:** 事件采集侧;多用户化;对话历史语义检索(v2);speculation / insight hypothesis 的存储迁移(收编不迁移);画像历史回溯重算;跨进程 lease(单 daemon 模型);trial topic 的推荐侧小流量消费(本版仅产状态,见 Phase 4 的最小消费例外)。

## Goal

事件与对话采集已完备(PR #85/#121),但**事件→画像的更新流缺乏认知纪律**:深层写入无一致性校验、对话学习与探针结算双路径无所有权、"看不懂"的行为无表达形式、画像变更不可回溯、对话上下文无限增长。

目标架构(与用户三轮讨论定稿):**三条更新线 + 觉察/疑惑/假设三类认知对象 + 态势门控 + 统一台账**。基调:**收编为主、新建为辅**——全新的只有疑惑对象、态势门控、台账;其余是给现役机制立规矩。

量化目标与验证命令:

- 画像写入的台账覆盖:**枚举写点清单内 100%**(清单见 Phase 0,含 init/pipeline/feedback batch/consolidation;清单本身进 `docs/modules/soul.md`,新增写点纳入 code review 义务)。台账为 best-effort 观察者(写失败 WARNING 不阻断),覆盖目标指"挂钩存在",非"行必然写成"。验证:`pytest tests/test_profile_ledger.py -q`。
- 态势门控 shadow 先行:shadow 判定**异步旁路**(不阻塞、不延迟原写入);enforce 切换受 save-time 校验(shadow 数据不足 14 天拒绝)。验证:`pytest tests/test_posture_gate.py -q`。
- 对话窗口上限 + popup durable 通道回灌;≤窗口会话 prompt 字节不变。验证:`pytest tests/test_dialogue_context.py tests/test_llm_prompts.py -q`。
- 疑惑端到端:认知失调 → 疑惑 → topic 冻结(防后续强化 + trial 回滚补偿)→ 询问(DB 级并发约束)→ resolved 三出口。验证:`pytest tests/test_confusion_lifecycle.py -q`。
- 对话学习串行化:后台学习任务经单 worker 队列(worker 自持生命周期,不入 cancel_all 注册表),热重载 pause-drain/失败 resume。验证:`pytest tests/test_dialogue_learn_queue.py -q`。

## Design invariants (MUST hold in every phase)

1. **Prompt-cache 静态性**:所有被触碰的 builder(含现存不合规的 dialogue-insight builder,r1 finding 20)收敛为模块级常量 system + `sort_keys=True`,并加入 `test_prompt_builder_system_messages_are_call_invariant` 清单。
2. **回放不变性(作用域=既有渲染与行为路径,r5/R4-3 收窄)**:不含新语义对象的输入,偏好分析渲染、对话 prompt(≤窗口)、**`analyze()` 路径的 awareness prompt**(`build_awareness_prompt` 一字不动)与改动前一致;`cognition_cycle` 切换新 builder 属有意变更,以 A/B 语义对照代替字节门(过质量铁律,记录 PR)。基线快照先行单独提交。`posture_gate_mode=off` 时 `learn_from_dialogue` 与 pipeline 行为与现状逐字节一致。
3. **门控只拦深层,shadow 先行且异步**:门控作用面 =(a)dialogue candidates 的 goal/value/state 类;(b)pipeline 的 VALUES/CORE 层 updater 写入;(c)soul 整份重建。topic/interest 快线(preference 兴趣域、ROLE 层)永不过门控。shadow 判定不阻塞写入(事后旁路);enforce 的 save-time blocking 校验三条件(r5/R4-4 与 Phase 3 同步):最早有效判定距今 ≥14 天 且 近 14 天有效判定 ≥10 条 且 近 7 天 ≥1 条(逃生门 `posture_gate_force_enforce`,文档注明风险)。enforce 下 LLM 异常/解析失败 → downgrade(保守)+ WARNING。
4. **台账只追加**:只 INSERT;回滚以补偿行表达;挂钩异常 WARNING 不阻断主流程。
5. **收编不迁移,身份用自然键**:speculation 与 insight hypothesis 存储/状态机不动。结算身份模型:speculation → `domain`(其存储主键);insight hypothesis → 内容 hash8(r3/R2-8 定义:SHA-256 over「NFC 规范化 + 首尾 strip + 连续空白折叠为单空格」后的 UTF-8 字节,取 hex 前 8;当轮注入清单内发生碰撞 → 碰撞项扩展至 hex16,仍碰撞则跳过注入该项 + WARNING);confusion → 表自增 id。`settles[].ref` 只接受**当轮注入清单中出现过的键**(白名单即注入清单,未见键丢弃 + WARNING)。
6. **结算单一所有权**:带 scope 前缀的 durable turn(probe/avoidance_probe/confusion)的结算**只归** durable side-effect 路径(`api/app.py` 成功侧效应);`learn_from_dialogue` 的 `settles` 通道**只处理普通 chat scope 轮次**(scope 随 learn 调用传入,非 chat 时跳过 settles)。杜绝同一回复双路径重复结算(r1 finding 2)。
7. **对话学习串行化**:`learn_from_dialogue` 后台任务改经**单 worker asyncio 队列**(进程内串行,消除相邻轮并发 read/merge/write)。**drain 时序与 worker 归属(r5/R4-2)**:队列 worker **不进入** `cancel_all` 管辖的后台任务注册表——它由队列对象自持生命周期(显式 pause/resume/shutdown 接口),避免热重载 `cancel_all`(`api/runtime_context.py:506`)误杀后无人重建。关闭时「stop-accepting → `queue.join()` → 停 worker → shutdown」(挂 `api/app.py:4005` 附近 shutdown 钩子);热重载时旧队列 pause-drain(在 cancel_all **之前**执行)→ 新 runtime 构建成功才停旧启新;构建失败走配置回滚分支时 **resume 旧队列**(worker 未被 cancel_all 波及,resume 即恢复消费;期间新投递被 pause 拒绝的落日志)。带 uvicorn 生命周期测试 + 热重载失败回滚测试 + 「cancel_all 后队列仍存活」测试(r1 finding 1)。
8. **疑惑不写画像 + DB 级打扰预算**:疑惑只产出下游对象;`status='clarifying'` 由 partial unique index 保证全局至多 1(跨连接原子);冷却 ≥72h 持久化在行内(`asked_at`);defer 语义复用探针忽略状态机。
9. **阈值有出处 + LLM 输出防御**:新常量带校准注释并标注首轮重校;结构化输出白名单/clamp + WARNING;解析失败保守化(门控→downgrade,settles/confusion_candidates→丢弃)。
10. **单用户全量注入**:不建向量检索;core memory、活跃清单(speculation ≤10 + insight + open 疑惑)全量入 user prompt;新 LLM caller(posture_gate、confusion 相关)注册 usage recorder 供 `cost --by caller` 观察。
11. **状态写入原子性**:cognition cycle 等 JSON state 写入 tmp+rename 原子化;due-check 与 watermark 消费在进程内单飞锁内完成(跨进程 lease 超范围,单 daemon 前提写入文档)。

## Current diagnosis

(r2 修正了 r1 中四处失真引用,见各条。)

### D1. 深层写入分散且无一致性门控(r2 修正:pipeline 有分层,缺的是门控)

- 增量管线**已有分层**:`soul/pipeline.py` 按 SURFACE/INTEREST/ROLE/VALUES/CORE 分层缓冲、不同阈值消费(`_DIALOGUE_INSIGHT_KIND_MAP`,`pipeline.py:283`),各层有独立 updater(`soul/layer_updaters.py`)。r1 spec 称"无分级"不实。
- 真实缺口:**任何层的阈值消费都直接写层**——VALUES/CORE 这类深层与 INTEREST 同样"攒够即写",无一致性校验;对话路径更甚:`learn_from_dialogue`(`soul/engine.py:805`)提取 candidates(kind ∈ interest/dislike/goal/value/state)→ `confidence>=0.8 OR occurrences>=2`(`engine.py:1359-1364`)→ 直接覆写 preference(`engine.py:855-885`)、显著变化整份重建 soul(`engine.py:900-920`)。
- 深层写入面共三处(门控必须全覆盖,r1 finding 10):dialogue candidates 深层类、pipeline VALUES/CORE updater、soul 重建。

### D2. 对话链路的结构问题(r2 增补并发与双结算)

- **窗口无上限**:`SocraticDialogue._history` 全量拼 prompt(`soul/dialogue.py:215-223`),重启即丢(`dialogue.py:65,211`)。
- **后台学习无串行保证**:`respond` 的锁不覆盖 `asyncio.create_task(_background_learn())`(`dialogue.py:133`),相邻轮学习任务可并发 read/merge/write candidates 与画像;任务不在任何注册表,热重载后旧 engine 可能继续写共享 memory(r1 finding 1)。
- **双结算竞态**:probe scope 的 durable 回复由 `api/app.py:6491+` 成功侧效应结算 speculation;若同一回复再进 `learn_from_dialogue` 的 settles 通道,可被二次结算(r1 finding 2)。学习路径当前拿不到 scope/turn_id(`dialogue.py:125`)。
- **回灌等价性限制**:`chat_turns` 只有 popup durable 通道写入;CLI 对话不落表(`cli.py:748` 构造无 DB),OpenClaw 每请求新建 dialogue;probe 等 scope 轮次的 message 带前缀语境。因此回灌**只对 popup、scope='chat'** 成立(r1 finding 3)。
- **提取器看不见假设**:`DialogueInsightAnalyzer.extract` 输入只有 core memory + 当轮(`dialogue_insight_analyzer.py:61`);speculation 结算只在 probe scope。且该 builder 的 system prompt 未模块常量化、JSON 未 sort_keys(`llm/prompts.py:935` 附近,r1 finding 20)。
- **身份缺失**:`InsightHypothesis` 无 id(`soul/profile.py:72`),speculation 以 domain 为键(`speculator.py:75`)——settles 需要自然键模型(不变量 5)。

### D3. 系统缺少「看不懂」的表达形式

- awareness 产出观察(12h 认知循环,**cursor 增量读取** events,`soul/cognition_cycle.py`;r2 修正:非全量重读);`AwarenessNote` 无 id、无 source_event_ids(`soul/profile.py:61`)——证据链在觉察环节断裂,必须先补(r1 finding 7)。
- `awareness_analyzer` 解析器只返回 note 数组(`soul/awareness_analyzer.py:83`),且存在多处 list 语义调用方(如 `engine.py:1253`)——r3/R2-5:**保留 `analyze()` 签名与行为完全不变**,新增复合 API `analyze_with_confusions() -> (notes, confusion_candidates)`,仅 `cognition_cycle` 切换到新 API,其余调用方零改动(r1 finding 6)。
- speculation 的 CooldownEntry 只存 domain/时间(`speculator.py:158`),无正反信号历史——"正反混合"判据在不迁移约束下不可判定;僵局判据必须只用现存字段(r1 finding 5)。
- 「问用户」目前只服务 probe scope 的假设确认,不服务认知失调。

### D4. topic 域没有显式生命周期

- 兴趣权重由偏好分析浮动;speculation 转正受 max_primary_interests 约束(常量定义于 speculator 配置;r2 修正:`speculator.py:956` 为日志行,非上限实现点);dislike purge(`engine.py:887-898`);12h 整理压缩。无试用期/衰减/细分/归档语义,跃迁不留痕。序列化(`soul/profile.py:254` 附近)不保留新增状态字段——状态机若不接序列化,线上行为零变化(r1 finding 16),故本版含**最小消费例外**:archived 排除出 prompt 序列化(经 config 开关 + 回放对照,见 Phase 4)。

### D5. 画像变更不可回溯(r2 扩充写点清单)

画像层写入点枚举(台账挂钩清单,r1 finding 9):
1. `learn_from_dialogue` preference 覆写(`engine.py:855-885`)与 soul 重建(`engine.py:900-920`)
2. dislike purge(`engine.py:887-898`)
3. pipeline 各层 updater 持久化(`layer_updaters.py:184` 附近 flat preference persist 及各层 save)
4. feedback batch 学习写入
5. speculation promote/confirm/reject
6. 12h 画像整理(压缩/归档/**revert**)
7. init 全量建像(`engine.py:307` 附近 `preference_layer.data.clear()` 起的初始化写入)
8. cognition sync(awareness/insight 层持久化)

清单进 `docs/modules/soul.md`;实现时若发现清单外写点,补挂钩并更新清单(code review 义务)。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | 台账(枚举写点全挂钩)+ CLI 查询 + 觉察证据链前置(AwarenessNote id/source_event_ids) | **MUST** | 审计底座;觉察 id 是 Wave B 证据链的前置(r1 finding 7/17) |
| 1 | 对话线:学习串行队列、窗口+回灌(popup/chat 限定)、活跃清单注入+settles(自然键+所有权)、insight builder 合规化 | **MUST** | 含并发 bug 与 prompt 无限增长 bug |
| 2 | 疑惑对象(DB 级约束、两产生源、澄清三路、冻结+trial 补偿+held 存储) | **MUST** | 核心新增 |
| 3 | 态势门控(异步 shadow、三接入点全覆盖、enforce save-time 校验) | **MUST** | 深层一致性闸门 |
| 4 | topic 状态机(含 archived 序列化排除的最小消费,开关+对照) | RECOMMENDED | 可独立决策 |
| 5 | 觉察提炼节奏(事件量/强信号触发,状态原子化) | RECOMMENDED | 优化非纠错 |

**Wave A**:Phase 0+1。**Wave B**:Phase 2。**Wave C**:Phase 3。**Wave D**:Phase 4+5。每 Wave 完成其文档子集方可交付;Wave A 含 `docs/modules/cli.md`(ledger 命令,r1 finding 17)。

## Phase designs

### Phase 0 — 台账 + 觉察证据链前置

**`profile_update_ledger`**(schema 同 r1,增 `outcome TEXT(success|failed)`;r3/R2-6:**动作结束后一次 INSERT**——挂钩包裹写入动作,成功/失败后各记一行含 outcome,不做进入即写的 attempted 行,保持只追加单行语义)。挂钩 D5 清单全部 8 点;每点 try/except WARNING。
**CLI**:`openbiliclaw ledger [--line] [--days N]`(Wave A 内交付,`docs/modules/cli.md` 同步)。
**觉察证据链前置**:`AwarenessNote` 增加 `note_id`(生成式 uuid 短码)与 `source_event_ids`(本轮 cursor 消费的事件 id 子集,解析器按 note 归属拆分;拆分不可得时整批挂到各 note,标注 approximate);旧数据缺字段默认空,兼容读。此项从 r1 的 Wave D 提前(疑惑的 evidence_refs 依赖它)。
**验收门**:8 挂钩点用例 + CLI 输出用例 + AwarenessNote 兼容用例;`pytest tests/test_profile_ledger.py -q`。

### Phase 1 — 对话线(r2 大改:并发、所有权、身份、合规)

1. **学习串行队列(不变量 7)**:`learn_from_dialogue` 调用改投递 `DialogueLearnQueue`(单 worker asyncio 队列);worker 自持生命周期(不入 cancel_all 注册表),热重载 pause-drain、失败回滚 resume(详见不变量 7)。队列携带 `{message, reply, session, scope, turn_id}`——scope/turn_id 从 durable 路径与 `respond()` 透传(`dialogue.py:125` 签名扩展,默认 scope="chat")。
2. **窗口 + 回灌(范围限定,r1 finding 3)**:`DIALOGUE_WINDOW_TURNS = 20`(校准注释同 r1);回灌**仅 popup 会话、仅 `scope='chat'`、仅 completed** 的 chat_turns;CLI/OpenClaw 会话不回灌(文档注明:CLI 历史为进程内,现状不变)。≤20 轮 prompt 字节不变(基线先行)。
3. **活跃清单注入 + settles(自然键 + 所有权)**:注入清单项携带自然键(speculation: `domain`;insight: 内容 hash8,注入时生成;confusion: id)。输出 `settles: [{kind, ref, verdict, note}]`;**白名单 = 当轮注入清单**;scope != "chat" 的轮次跳过 settles(不变量 6);结算动作调既有函数(`user_confirm/reject_speculation`、`update_from_feedback`)并进台账,台账行含 turn_id(幂等观察键)。
4. **dialogue-insight builder 合规化(r1 finding 20)**:system 段模块常量化、JSON `sort_keys=True`、加入 invariance 清单——此为行为等价重构,回放对照覆盖。
**验收门**:队列串行(并发投递顺序断言)、drain、窗口/回灌/限定范围、settles 三类 + 未知键 + 非 chat scope 跳过、builder invariance;`pytest tests/test_dialogue_learn_queue.py tests/test_dialogue_context.py tests/test_soul_engine.py -q`。

### Phase 2 — 疑惑机制(r2:DB 约束、可判定僵局、held 存储、trial 补偿)

**`confusions` 表**:r1 schema + `held_updates TEXT`(JSON:冻结期被搁置的 topic 变更)+ partial unique index `WHERE status='clarifying'`(不变量 8)。
**产生源**:
- 认知失调(r4/R3-2 builder 分离):新增独立 builder `build_awareness_with_confusions_prompt`(静态 system,入 invariance 清单)与新 API `analyze_with_confusions() -> (notes, confusion_candidates)`;**既有 `analyze()` 与 `build_awareness_prompt` 一字不动**(字节不变门只保此路径)。`cognition_cycle` 切换到新 API 属**有意行为变更**——按质量铁律做新旧 awareness 输出 A/B 对照记录进 PR(无矛盾输入下 notes 语义等价抽查)。候选 ≤2/轮,白名单校验;`cognition_cycle` 落库。
- speculation 僵局(**可判定版**,r1 finding 5):speculation expire 时 `0 < confirmation_count < threshold`(部分确认未达标,现存字段可读)→ 生成疑惑;"正反混合"判据 v2。
**澄清三路**:ask(durable `scope="confusion"`,并发由 partial unique index 保证,冷却 72h 持久化于 `asked_at`;defer 复用探针忽略语义)/ probe(限可映射现有探针域)/ wait(TTL 14d)。
**resolved 三出口**:转正假设(insight hypothesis,初始置信=解读置信)/ 直接结算(候选送门控,门控未上线时直写+台账;相关觉察/事件盖折扣标记,复用 retraction metadata patch)/ dismissed。
**冻结反压(r2 语义精确化 + 补偿,r1 findings 12/13)**:
- 冻结集过滤实现在**preference 持久化 chokepoint**(`layer_updaters.py:184` 附近的 flat persist 与 `engine.py:855-885` 覆写共同经过的最窄处;实现时确认唯一收敛点,不可收敛则两处都挂,清单化)。
- 语义:**防后续强化**——冻结 topic 的新增/权重上调被搁置进 `held_updates`;已有权重与已入域事实不回滚(快线抢跑数小时可接受,Expected impact 措辞同步修正)。**例外补偿**:related topic 若处 trial 态(Wave D 后)→ 回滚至候选;Wave D 前记台账不回滚。
- 解冻(r3/R2-2/R2-3):
  - **按 resolution outcome 筛选**:resolved 且解读为「真实兴趣」型 → 重放;resolved 且解读为「代理行为/误读」型 → 丢弃(同 dismissed);dismissed/expired → 丢弃。全部进台账。
  - **重放 = rebase 提交**:held 项作为证据并入下次偏好分析输入,由 LLM 以当前画像为基重新评估(不直接写权重)。
  - **held 项状态机(r5/R4-1:回执与台账解耦,保守不双计)**:每项带稳定 held_id 与状态 `held → replaying → applied|applied_unverified|discarded`。重放时序:置 replaying 的**同时**在 confusions 行内持久记录 `replay_submitted_at + batch_id`(与状态同一 SQLite 事务,这是回执——**不依赖 best-effort 台账**)→ 提交偏好分析 → 成功后置 applied。崩溃恢复:12h 扫描发现 replaying 且**已有 replay_submitted 回执** → 置 `applied_unverified` + WARNING(**不重试提交**——可能已被吸收,宁漏勿双计,与防重复强化方向一致);replaying 且**无回执**(置状态与记回执之间理论上同事务不会分离,防御分支)→ 重试,`replay_attempts` 上限 2;重复 resolve 幂等。必测「记回执后、applied 前崩溃 → 恢复置 applied_unverified 且不重复提交」。台账行仍带 held_id(观察用途,非回执)。
**验收门**:唯一约束的跨连接竞争测试(两连接并发置 clarifying,恰一成功)、held 存储/重放/丢弃、僵局判据、复合返回兼容;`pytest tests/test_confusion_lifecycle.py -q`。

### Phase 3 — 态势门控(r2:异步 shadow、三接入点、enforce 校验)

**模式语义(不变量 3,r1 findings 14/15)**:
- `shadow`(默认):原写入**立即执行**(零延迟零阻塞);**在 commit boundary 捕获不可变快照**(before/after 摘要、source_refs、gate_id;r3/R2-4)——异步旁路任务(shadow 判定任务可进通用注册表,与对话学习队列 worker 的自持生命周期区分)只消费该快照,不回读活状态(后续写入不得污染判定语境,带断言测试);结果进台账(`shadow_*` verdict);LLM 异常记 `shadow_error` 行。
- `enforce`:写入前同步判定;异常/解析失败 → downgrade + WARNING(保守 fail-closed-to-hypothesis);save-time 校验(r4/R3-3 统一语义):保存 enforce 需**同时满足三条**——(a) 最早有效 shadow 判定行距今 ≥14 天(观察时长门);(b) 近 14 天有效判定(shadow_accept/downgrade/reject,不含 shadow_error)≥10 条(样本量门,校准:单用户日均 ~1-3 次 × 14 天下界);(c) 最近 7 天 ≥1 条(近期覆盖门)。任一不满足 blocking 拒绝(pitfall #7),`posture_gate_force_enforce=true` 逃生门(文档注明风险)。
- `off`:完全旁路,行为与现状逐字节一致(回放门)。
**三接入点(r1 finding 10/11)**:
1. dialogue candidates:按 kind 分流(interest/dislike → 现路径;goal/value/state → 门控;downgrade 置信 = candidate confidence × 0.6)。
2. pipeline VALUES/CORE 层 updater:层变更 diff 过门控(无 candidate confidence,downgrade 固定置信 0.5,校准注释);ROLE 层归表层不过门控(校准注释:ROLE 偏使用模式,错误代价低)。
3. soul 整份重建:rebuild diff 摘要过门控;downgrade → 放弃本次 rebuild、记台账(重建无假设可转,r1 finding 11)。
**验收门**:模式 × 接入点矩阵、异步 shadow 零延迟断言(写入不等判定)、save-time 拒绝、off 逐字节;`pytest tests/test_posture_gate.py -q`。

### Phase 4 — topic 状态机(r2:含最小消费)

同 r1(trial/active/decaying/archived/复燃/细分 shadow 提议、dislike 改归档+避雷),增:
- **最小消费(r1 finding 16)**:`build_profile_summary` 序列化排除 archived topic——经 config 开关 `topic_lifecycle_serialization=off|on`(默认 off)+ 回放对照(开关 off 时序列化字节不变;on 时 A/B 对照记录进 PR,质量铁律)。trial 小流量消费仍 out of scope(spec 与 plan 措辞一致化)。
- 序列化点:`soul/profile.py:254` 附近增加状态字段的持久化与兼容读(旧数据默认 active)。
**验收门**:状态机跃迁 + 兼容 + 开关两态序列化;`pytest tests/test_topic_lifecycle.py -q`。

### Phase 5 — 觉察提炼节奏(r2:状态原子化)

同 r1(≥30 事件 / 强信号插队,单飞),增(r1 finding 18):cognition state JSON 写入 tmp+rename 原子化;due-check + watermark 消费移入进程内单飞锁;取消/异常时 watermark 不前进(下轮重做);跨进程 lease 超范围(单 daemon,文档注明)。
**验收门**:并发触发恰一执行、异常不丢 watermark、原子写。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 0 | 枚举写点台账覆盖 0% → 100%(挂钩存在);觉察获得 id 与事件回溯 |
| Phase 1 | 对话学习从并发裸奔 → 串行队列;prompt 上界无限 → ~1.6k;popup 重启不失忆;普通聊天可结算假设且与 probe 路径无双结算 |
| Phase 2 | 「看不懂」获得表达;冻结防止疑惑期 topic **继续强化**(已入域部分不回滚,trial 例外);搁置变更可重放 |
| Phase 3 | 深层写入(对话/管线/重建三面)获得一致性判定;shadow 零侵入采数 ≥14 天再 enforce |
| Phase 4 | topic 显式状态机;archived 可(开关+对照)退出 prompt |
| Phase 5 | 觉察时延 12h → 自适应;状态写入原子化 |

## Documentation obligations

- `docs/modules/soul.md` — 三线架构、认知对象、门控、台账 + **写点清单**(新写点 review 义务)。
- `docs/modules/storage.md` — ledger、confusions 表与 DAO。
- `docs/modules/config.md` — posture_gate_mode / posture_gate_force_enforce / topic_lifecycle_serialization。
- `docs/modules/cli.md` — `openbiliclaw ledger`(**Wave A**)。
- `docs/architecture.md` + `docs/spec.md` §3 + README 双语架构图 — Soul 三线+门控节点,无条件。
- `docs/changelog.md` — 逐 Wave 条目。
