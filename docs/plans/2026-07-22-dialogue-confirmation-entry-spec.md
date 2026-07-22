# 对话确认入口 Spec — 假设/疑惑在对话中确认:卡片、待聊列表、话题锚与归属判断

**Created:** 2026-07-22(与用户五轮讨论定稿)
**Scope:** `soul/dialogue.py`、`soul/dialogue_insight_analyzer.py`、`soul/engine.py`、`soul/confusion.py`、`llm/prompts.py`、`api/app.py`(durable chat 扩展 + 待聊列表端点)、`storage/database.py`(锚状态持久化)、`cli.py`(questions 命令)、前端 `extension/popup/` 与 `src/openbiliclaw/web/`(卡片渲染/待聊入口/角标)。基于分支 `feat/cognitive-profile-pipeline`(认知底座:疑惑/假设/门控/台账均已实现)。
**Out of scope:** 探针(保留在推荐流,不迁入对话);系统级推送通知(角标即上限);移动 Web 卡片(跟进版,本版显式排除并在 PR 声明);对话原文跨会话语义检索(v2);多锚并行(同时最多 1 个锚)。

## Goal

认知底座落地后,假设/疑惑的用户确认面散落三处(苏格拉底提问、推荐流探针、认知更新区洞察卡片),且均为单轮单发,无多轮澄清能力。用户定稿的产品方向:**对话是唯一的主动确认入口**——

1. **假设**:对话流内嵌卡片,「是 / 不是 / 聊聊这个 / 以后再说」;点「聊聊」或直接回复进入**多轮**讨论,支持修正式结算。
2. **疑惑**:苏格拉底口吻的提问气泡,**只聊不选**(开放澄清,不暴露候选解读列表)。
3. **提醒**:插件角标(只计高优先级待确认项)+ 对话顶部「阿b 有 N 件事想聊」入口;**用户主动点开不受冷却,系统主动抛出受严格节流**。
4. **多轮机制 = 同一条对话流 + 话题锚**:不建新通道不开线程;锚是会话级状态,归属判断合并进现有学习提取调用(零新增 LLM 调用)。
5. **无 session 模式**:永续对话 + 逐轮相对时间戳(事实而非模式,无"重逢"判定)+ 朴素 TTL。

验证:`pytest tests/test_dialogue_anchor.py tests/test_api_app.py tests/test_soul_engine.py -q`;popup/桌面 Web 卡片交互 Playwright 用例;真实端到端(本机对话结算一条假设、澄清一条疑惑)记录在 PR。

## Design invariants (MUST hold)

1. **对话为唯一主动确认入口**:洞察确认卡片迁入对话流(认知更新区改只读);疑惑 ask 与假设讨论都发生在**现有唯一对话流**中——不新建通道、不建线程。探针保留在推荐流(隐形验证,非"确认入口")。
2. **锚的生命周期(确定性,无模式)**:会话级 `anchor {kind: hypothesis|confusion, ref, established_at}`,同时最多 1 个。建立:系统抛出疑惑提问 / 用户点卡片「聊聊这个」/ 用户从待聊列表点开。解除(仅三条):①结算发生;②连续 2 轮 `relation=unrelated`;③绝对 TTL 2 小时(校准:覆盖一次完整讨论,首轮重校)。**锚失效不丢功能**——普通轮的检索式 settles 是保底路径(锚只是显式快路径)。锚状态持久化(重启随回灌恢复),所有建立/解除进台账。
3. **归属判断合并进现有提取调用(零新增 LLM 调用)**:学习提取的输入增加「当前锚」段,输出增加 `anchor {relation: support|contradict|revise|answer|unrelated, interpretation?, derived[]}`。**防双计三道防线**:(a) prompt 指令:归锚内容禁止出现在 candidates;(b) 代码防御:candidates 与锚主题做规范化关键词重叠比对,重叠即丢 + WARNING;(c) 结算所有权:锚定轮不走检索式 settles,结算只经锚处理器(复用既有单一所有权规则)。`relation=revise` → 原假设 reject + 按 `derived` 派生修正假设(走既有 confirmed→门控通道)。解析失败/越界值:不结算、锚保持、candidates 丢弃 + WARNING。
4. **打扰预算(双轨)**:系统主动抛出(卡片/疑惑提问)——一次抛出后**全局冷却 ≥12h**(校准),同对象 72h 冷却(既有),疑惑 clarifying 并发 ≤1(既有 DB 约束);**用户主动**(点角标/待聊列表/卡片按钮)不受任何冷却。角标只计高优先级项(置信度进入待确认区间的假设 + open 疑惑中优先级高者,预期 0-3)。
5. **时间即事实**:对话窗口渲染每轮附相对时间标注(「3 天前」「刚刚」,由 `chat_turns.created_at`/内存轮时间戳确定性生成);**无任何间隔阈值判定、无重逢模式**。此为有意 prompt 变更:回放基线一次性更新并单独提交,时间标注函数确定性(注入固定 now 可测,禁止在渲染路径调用不可注入的当前时间)。
6. **结算复用不重造**:卡片「是/不是」→ 既有 `update_from_feedback` confirm/reject(自然键定位);「以后再说」→ defer(复用探针忽略/冷却语义);疑惑结算 → 既有 resolve 三出口;全部进台账带 turn_id,幂等(重复点按状态不劣化)。
7. **卡片即结构化 chat turn**:假设卡片 = durable chat turn 携带结构化 payload(`card: {kind, ref, title, evidence_refs, actions[]}`),前端按 payload 渲染;无卡片渲染能力的端(CLI)降级为文字 + 序号回复。scope 扩展 `"hypothesis"`(结算所有权归 durable 侧效应,与 confusion 同款)。
8. **prompt-cache / 回放 / 台账 / 阈值出处 / LLM 输出防御**:沿用全部既有不变量;被触碰 builder 保持静态 system + invariance 清单;新常量(锚 TTL 2h、全局抛出冷却 12h、角标上限)带校准注释。
9. **四端契约**:popup 侧边栏 + 桌面 Web 本版交付(卡片/待聊/角标);CLI `openbiliclaw questions` 只读列表;移动 Web 显式排除至跟进版(PR 声明)。

## 现状要点(实现基准,分支代码)

- 对话:单例永续流,窗口 20 轮 + popup/chat/completed 回灌;durable chat scope ∈ {chat, delight, probe, avoidance_probe, confusion};scope≠chat 跳过 settles,归 durable 侧效应结算。
- 疑惑 ask 已有 `scope="confusion"` 单轮问答 + 情感分类结算;本 spec 将其升级为锚定多轮(单轮分类器保留为锚处理器的输入信号之一)。
- 洞察确认:`update_from_feedback`(confirm≥0.75/reject≤0.35)由认知更新区 UI 驱动——迁移为对话卡片驱动,函数不变。
- 学习提取:`DialogueInsightAnalyzer.extract` 已注入活跃清单、输出 candidates+settles;本 spec 扩展锚段与 anchor 输出。
- 台账/疑惑表/门控/rebuild_pending 均已就位,本 spec 只消费不新建认知机制。

## 交互设计定稿(供实现对照)

- **假设卡片**:标题「阿b 的猜测」+ 假设内容 + 可展开依据(evidence_refs → 台账链摘要)+ 四按钮。已结算态原地替换(「✓ 已确认,阿b 记下了」)。
- **疑惑提问**:普通聊天气泡 + 轻标识(小图标),无按钮无选项;回答含糊可追问一句,两次不理会转静默(defer 计数既有)。
- **待聊列表**:对话顶部入口「阿b 有 N 件事想和你聊聊 ›」,展开为卡片列表;点某条 → 该对象立即以卡片/提问形式进入对话流并建锚。
- **角标**:插件图标 badge = 高优先级待确认数;桌面 Web 在对话入口同位显示。
- **多轮示例契约**(验收对照):「聊聊→反驳→修正→确认」四轮完成修正式结算;中途插入无关问题(锚保持 1 轮)后回归话题可续;连续 2 轮无关静默解锚。

## Phase / Wave

| Wave | 内容 | Tier |
| --- | --- | --- |
| A | 时间戳渲染(基线更新)+ 锚状态与生命周期 + 归属判断扩展(后端核心) | MUST |
| B | 假设卡片 turn(scope="hypothesis" + payload + 结算端点)+ 疑惑锚定多轮升级 + 待聊列表/角标 API + 抛出冷却 | MUST |
| C | 前端:popup + 桌面 Web(卡片渲染/待聊入口/角标/已结算态)+ 洞察卡片迁移(旧区只读) | MUST |
| D | CLI `questions` + 文档 + 真实端到端 | MUST(收尾) |

每 Wave 完成其文档子集方可交付(soul.md 对话确认入口小节、api 文档、extension.md、cli.md、changelog;架构图:对话线增"确认入口"注记)。

## Expected impact

| Lever | Effect |
| --- | --- |
| 确认入口收敛 | 三处散落 → 对话唯一入口;洞察卡片迁移完成 |
| 多轮澄清 | 疑惑/假设从单轮单发 → 锚定多轮,支持修正式结算 |
| 提醒 | 从"等你撞见" → 角标 + 待聊列表(主动权在用户,系统抛出受 12h 全局冷却) |
| 时间感 | 逐轮时间戳,无模式无阈值 |

## Documentation obligations

`docs/modules/soul.md`(确认入口 + 锚)、`docs/modules/extension.md`(角标/卡片)、`docs/modules/cli.md`(questions)、API 文档相应端点、`docs/architecture.md`/`docs/spec.md` 对话线注记(README 图若不含对话粒度则声明不触发)、`docs/changelog.md`。隐私:无新采集面,不触发。
