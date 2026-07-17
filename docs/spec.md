# OpenBiliClaw — vNext 后端规格与 v0.3 历史档案

> *你的跨平台 AI 内容朋友，比你更懂你想看什么* 🎯

> **Authority boundary:** §3.1 是当前权威 vNext 后端规格。§1–2 与 §3.2–6 是
> **Historical v0.3 archive**，只保留旧产品语义与删除追踪，不定义当前 API、CLI、
> 配置、安装或数据流。现有 Web/extension 已通过 generated client 消费该权威合同。

---

## Historical v0.3 archive — 1. 项目定位

OpenBiliClaw 是一个**本地优先、开源的跨平台个性化内容发现 AI Agent**。它像一个深度了解你的朋友或专属内容编辑——不仅知道你喜欢看什么，更理解你**为什么**喜欢，你**是一个什么样的人**，然后主动去 B 站、小红书、抖音、YouTube、X、知乎、Reddit 和通用 Web 等来源帮你发现那些你会喜欢但自己找不到的内容。

**核心理念**：
- 不是冷冰冰的推荐算法，而是一个**有温度的 AI 朋友**
- 不是被动过滤推荐流，而是**主动探索发现**
- 不是浅层兴趣匹配，而是**深层理解人格与需求**

### 与单平台官方推荐的区别

| 维度 | 单平台官方推荐 | OpenBiliClaw |
|------|------------|--------------|
| 推荐逻辑 | 协同过滤 + 热度，容易信息茧房 | LLM 深层理解 + 探索式发现 |
| 用户理解 | 隐式标签，用户不可见 | 深度人格画像，像朋友一样理解你 |
| 控制权 | 用户只能点"不感兴趣" | 对话式调整 + 主动"教"Agent |
| 发现方式 | 基于已有行为推荐相似内容 | 主动搜索、跨领域探索、挖掘潜在兴趣 |
| 推荐语气 | 算法式、无温度 | 朋友式、有人味、有洞察 |

---

## Historical v0.3 archive — 2. 核心功能模块

### 2.1 🧠 用户灵魂引擎 (User Soul Engine)

**目标**：从"他做了什么"到"他为什么这样"到"他是一个什么样的人"——建立有深度和温度的用户理解。

#### 2.1.1 行为数据采集

**浏览器插件（核心采集入口）**：
- 通过统一 `PlatformAdapter` 捕捉 B 站 / 小红书 / 抖音 / YouTube / X / 知乎的交互行为；Reddit 初始化 saved/upvoted/subscribed 信号复用插件登录态任务桥，日常 discovery 默认使用 rdt-cli 登录态命令后端，不可用时 fallback 到插件任务：点击、滚动、停留、评论、点赞、收藏、分享、关注、搜索，以及 B 站特有投币；click 在 capture 阶段记录，scroll 同时覆盖页面和内部 feed / modal 滚动容器
- 记录行为发生时的**完整上下文**：对应的 DOM 页面快照、当前浏览路径、时间戳、平台内容 ID
- 捕捉用户的**微行为**：鼠标悬停、视频进度条跳转、视频暂停 / 继续、页面导航等
- 记录用户的**主动反馈**：`dislike` 类动作统一规范成 `feedback` 事件，避免各平台负反馈语义分叉
- 插件 side panel 与桌面 / 移动 Web 使用同一 platform-neutral 保存契约：卡片先本地保存，保存页显式同步并轮询逐项任务；默认关闭自动同步，首次开启提示将修改对应平台账号；本地删除不删除平台记录
- 本机调试可通过 `/api/extension/e2e/run` 驱动已安装插件在抖音 / 小红书 / X 真实页面执行白名单 DOM 操作，再由后端校验 `/api/events` 是否自然入库；runner 会把复用 tab 归位到平台入口并在回传结果前 flush 捕捉 buffer，该链路不伪造行为事件，用于验证捕捉层本身。`/api/events` 在画像明确未初始化时会拒收普通行为事件，首轮画像信号只由点击「开始初始化」后的 guided init 来源任务拉取；初始化后 accepted 普通事件会先写入 memory，再进入 `ProfileUpdatePipeline`，随后通过 `request_replenishment(reason="event_ingest")` 排队补货需求。旧版本已经停在 discovery 水位后的普通行为事件由独立 `last_profile_pipeline_event_id` 补喂画像 pipeline，而 `pending_signal_events` 仍只是 search / related_chain refresh 的触发水位，不是画像待处理数。

**B 站数据接口**：
- 通过 B 站 API 获取结构化数据（历史记录、收藏夹、关注列表等）
- 作为浏览器插件采集的补充和验证

#### 2.1.2 多层网状记忆架构 (Memory Architecture)

> 参考 MemGPT/Letta 的分层记忆设计和认知心理学模型，打造专为"理解一个人"设计的记忆系统。

**核心设计理念**：不是简单的数据存储，而是一个**活的、不断生长和自我修正的理解网络**。每一层之间有网状关联，上层理解会指导下层数据的解读，下层新数据会修正上层理解。

```
┌─────────────────────────────────────────────────────────────┐
│                   🌟 灵魂层 (Soul Layer)                     │
│  "他是一个什么样的人"                                         │
│  人格特质 · 核心价值观 · 深层需求 · 生活状态                     │
│  ↕ 双向修正                                                 │
├─────────────────────────────────────────────────────────────┤
│              💡 洞察层 (Insight Layer)                        │
│  "为什么他会这样"                                             │
│  动机分析 · 心理需求推断 · 潜在兴趣假设 · 行为模式归因            │
│  ↕ 双向修正                                                 │
├─────────────────────────────────────────────────────────────┤
│              📅 觉察层 (Awareness Layer)                      │
│  "每天他在发生什么变化"                                        │
│  每日观察笔记 · 兴趣趋势 · 情绪状态推测 · 阶段性总结             │
│  ↕ 双向修正                                                 │
├─────────────────────────────────────────────────────────────┤
│              📊 偏好层 (Preference Layer)                     │
│  "他喜欢什么/不喜欢什么"                                      │
│  兴趣标签(带权重+时间衰减) · 风格偏好 · 情境模式 · 探索倾向      │
│  ↕ 数据提取                                                 │
├─────────────────────────────────────────────────────────────┤
│              📝 事件层 (Event Layer)                          │
│  "他做了什么"                                                │
│  原始行为日志 · DOM快照 · 点击/搜索/收藏记录 · 反馈记录          │
└─────────────────────────────────────────────────────────────┘
```

**层间关系 — 网状而非单向**：

- **自底向上**：事件层的新数据不断注入偏好层，偏好层的变化推动觉察层更新观察笔记，觉察层的发现修正洞察层的推断，洞察层最终塑造灵魂层的人格理解
- **自顶向下**：灵魂层的人格理解指导洞察层如何解读新行为，洞察层告诉觉察层应该关注什么变化，偏好层根据上层理解来校准标签权重
- **跨层关联**：一个事件可能直接触发灵魂层的修正（重大行为变化），灵魂层可能直接影响事件层的采集策略（关注特定类型的行为）

**记忆类型**（参考 MemGPT/Letta 模式）：

| 记忆类型 | 作用 | 对应层 | 存储方式 |
|---------|------|--------|---------|
| **核心记忆** (Core Memory) | 始终在 Agent 上下文中的关键信息 | 灵魂层 + 偏好层摘要 | JSON 文件(可自编辑) |
| **情景记忆** (Episodic Memory) | 具体的交互片段和发现故事 | 事件层 + 觉察层 | SQLite + 向量索引 |
| **语义记忆** (Semantic Memory) | 用户相关的事实和知识 | 偏好层 + 洞察层 | 知识图谱/JSON（当前实现使用 JSON，未引入知识图谱） |
| **工作记忆** (Working Memory) | 当前会话的即时上下文 | 运行时 | 内存 |

**自我编辑能力**：Agent 可以自主决定什么信息该记住、什么该遗忘、什么该从事件层提升到灵魂层。

#### 2.1.3 画像输出

**自然语言人格描述**（像一个了解你好多年的老朋友）：

> *"小白骨子里是一个追求'深度理解世界运作方式'的人——无论是 AI 原理、历史脉络还是一道菜的风味逻辑，他都想搞明白'为什么'。他表面上是个技术宅，但我观察到他对摄影的兴趣在增长，我怀疑这是他在寻找一种技术之外的创造性表达。工作压力大的时候他会刷 RPG 游戏实况来获得掌控感，这时候千万别推他技术视频。他最近在看探店视频——不是因为想学做菜，而是享受'发现隐藏的好东西'那种感觉。这个特质很有意思，也许可以给他推荐一些小众但高质量的 UP 主。"*

**结构化数据**：JSON 格式的多层记忆数据，供系统内部使用。

#### 2.1.4 用户自述通道 — 苏格拉底式深度对话

不是简单地"记录用户说了什么"，而是**主动追问、假设、确认、调整**：

```
用户：我最近对美食不太感兴趣了
Agent：了解。不过我很好奇——你之前看探店视频是因为喜欢美食本身，
      还是享受"发现隐藏好东西"的过程？如果是后者，也许我可以
      帮你在其他领域找到类似的发现感？
用户：嗯你说得对，我确实更喜欢发现的过程
Agent：那我理解了。这是一个很有意思的特质——你可能也会喜欢"小众
      宝藏UP主挖掘"或者"冷门但高质量的纪录片"这种内容。
      我先假设你对这类内容有兴趣，推荐一些试试？如果不对我再调整。
```

核心策略：
- **追问 Why** — 不止记录偏好，挖掘背后动机
- **提出假设** — 基于理解主动猜测，而不是等用户说
- **确认验证** — 带着假设去推荐，看结果来验证
- **动态调整** — 根据验证结果修正理解模型

---

### 2.2 🔍 内容发现引擎 (Content Discovery Engine)

**目标**：像一个熟悉多个内容社区的专业编辑一样，通过多种方式主动发现好内容。

#### 发现策略

| 策略 | 说明 |
|------|------|
| **兴趣关键词搜索** | 根据用户画像生成关键词组合搜索 |
| **搜索灵感脑暴** | 可选地从 like 二级兴趣抽样；`OnionProfile.interest.likes` 会优先展开 specifics，一级 domain 只在缺少 specifics 时兜底，并按 parent 计数降权防止小窗口被同一领域占满；结合 recent interest selection count、关键词覆盖频次、raw candidate 数量 / 占比 / dominant content type 和最终候选池占比降权高频兴趣，coverage join 统一走 `_normalize_match_text()` 折叠大小写 / 空白漂移，画像整理会同步迁移 keyword 与 selection ledger 标签，完整 coverage 只在本地控制环使用，LLM payload 只携带 must-cover + 少量 cooldown 摘要；随后由 `discovery.keyword_brainstorm` 脑暴带 `kind_fit=regular|explore|both` 的搜索 probe branch，每兴趣最多 2 条，regular + explore 同轮触发时共用一次 brainstorm 和一次 grounding stage；按 `[discovery].inspiration_search_backends` 通过 search provider 链（默认已启用平台源 → Exa → You.com free MCP）grounding 具体实体 / 社区词 / 讨论点，stage 级搜索预算由 `inspiration_max_probe_searches_per_stage` 控制，平台源扇出由 `inspiration_platforms_per_probe` 控制，每 probe 翻页 / 扩量由 `inspiration_search_pages_per_probe` 控制，B 站 / 抖音 / X 等 risk-controlled 来源受 `inspiration_riskcontrolled_probe_budget` 与 cooldown / 限流约束；`platform_sources` 只把 B站 / YouTube / X / Reddit、抖音 direct client，以及小红书 / 知乎 bridge 可用时的搜索标题 / URL / 摘要作为灵感 evidence，不入候选池；泛词不是硬错误，会交给 curator 结合画像、平台 guide 和覆盖约束判断；再经 `discovery.keyword_inspiration` 做 Profile Curator / Detail Expander，优先生成按平台 keyed 的 `platform_keywords`；`platform_guides.query_style` 明确 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit 的平台检索语法；写库前由系统侧执行 must-cover 排序、每平台二级兴趣 / lens family 上限、原样证据标题 / URL / 过长 query / 平台语言不匹配 / 平台检索语法不匹配过滤、grounding hint `source_interest` 校正、explore 横向 lens 校验，缺失 must-cover 兴趣时用 `discovery.keyword_inspiration.repair` 做一次 bounded repair，repair 仍缺词时用 deterministic platform-native backfill 补齐；默认关闭，开启后 admission yield 会回填 inspiration / expansion 反馈计数；实验开关可让 due 平台完全跳过旧 merged keyword planner，只用新流程产词，并在 B 站 explore 到期时写入 `keyword_kind="explore"` 的探索词池；`keyword-inspiration-dry-run` 可真实预览中间链路但不写关键词池，且使用独立 preview selection scope，`keyword-inspiration-report` 对比 inspiration / merged cohort、输出 production / preview 抽中分布并给出 replace 门禁 |
| **相关推荐链探索** | 从已知好内容出发，沿相关推荐不断深入 |
| **分区热门/排行榜** | 固定全站榜，并按本地洗牌轮转覆盖非 0 分区榜，结合用户画像筛选 |
| **UP 主追踪** | 追踪关注的和发现的优质 UP 主的新动态 |
| **评论区挖掘** | 从评论区发现用户推荐的其他内容/UP 主 |
| **跨领域探索** | 刻意推荐用户从未接触过但心理画像暗示可能喜欢的领域；当统一 `KeywordPlanner` 已有 merged keyword 调用、`explore_refresh_hours` 到期或即将到期且 B 站仍有补货空间时，默认会把 `explore_domains` 合并进同一次关键词生成，把探索 query 写入 B 站 `keyword_kind="explore"` query cache。开启 inspiration-only 替换模式后，这部分也改由 search-backed inspiration flow 生成 `query_kind="explore"` 的 B 站探索词。`ExploreStrategy` 后续从该 explore 候选池 claim query 搜索；池为空时不再单独打一次 explore 计划 LLM |
| **热点关联** | 追踪热点话题，判断是否与用户深层兴趣相关 |

#### 内容评估

> 评估的核心依据是**用户的 Soul（灵魂画像）和深层兴趣**，而非通用指标。

- **核心评估**：这个内容是否匹配这个用户的深层兴趣和当前状态？
- **可选辅助指标**：播放量/点赞/弹幕质量等——由用户画像决定是否参考（有些用户在意质量指标，有些人不在意）
- **统一待评估池与准入**：API daemon 的不同来源 raw candidates 进入 `discovery_candidates` 后，由唯一 `CandidateEvalCoordinator` tokenized claim；默认 3 个 30 条 LLM worker 并行，任一完成即补位，SQLite 完成提交与 admission 串行。pipeline 单次 enqueue callback 立即唤醒这个 owner，refresh / managed producer 不再同步 drain。串行 lane 先持久化全部 token-owned 评分，再按 `target - available - admitted_pending_copy` admission；超过 headroom 的达标结果保留为 `evaluated`。OpenClaw direct one-shot 不启动 daemon owner，`recommend(refresh_if_needed=True)` 的首轮 source supply / inline claim 固定 ≤4（fetch oversample=1、min eval batch=4、inline evaluator=1），随后请求再补下一批，并在每次 durable admission 后同步 drain ≤4 条 expression copy、`max_extra_requests=0`；首 batch 的有效 subset 立即可 serve，未完成行保持 durable pending 由下一请求续补，既不遗留 notify-only coordinator，也不遗留 provider copy task。调度 projected 固定为 `available + admitted_pending_copy + evaluated_pending_admission`，普通 `pending_eval/evaluating` 不计入；60 秒只作 API coordinator 的安全 backstop。来源只影响取数方式、配额和 prompt 上下文；平台节流、raw ceiling 与准入阈值不变。

---

### 2.3 📬 推荐与呈现 (Recommendation & Delivery)

**目标**：像一个真正了解你的朋友，在合适的时候以真诚的方式推荐内容。

#### 推荐类型

- **即时推荐**：发现特别匹配的内容时即时推送
- **每日精选**：定时推荐列表
- **个人专题**：深度个性化的主题推荐——完全基于对这个人的理解，不是通用分类

> 专题示例（不是"周末放松包"这种通用的，而是只属于这个人的）：
> - *"你最近在探索摄影——这几个视频从你习惯的'搞明白原理'的角度讲构图和光影，我觉得很对你的胃口"*
> - *"最近工作是不是有点累？这两个 RPG 实况节奏特别好，适合你晚上用来切换状态"*
> - *"我发现一个 UP 主讲历史的方式跟你喜欢的那种'深层逻辑分析'风格很像，但他讲的是经济史，你说不定会打开一个新世界"*

#### 推荐表达（有温度、有洞察的朋友式推荐）

不是：*"因为你观看了相关视频，推荐以下内容"* ❌

而是：*"我觉得你会喜欢这个——这个 UP 主讲 AI 的角度很独特，有点像你喜欢的那种'把复杂的事情讲透'的风格，但他会加入很多生活化的类比。我理解你最近对 AI 的关注不仅是工作需要，更多是一种对未来的好奇，这个视频正好聊到了你可能感兴趣的方向。"* ✅

核心要素：
- **"我觉得"** — 有主观判断，像朋友一样
- **"我理解你"** — 展示对用户的深层理解
- **关联洞察** — 不只是"你看过类似的"，而是"我理解你为什么喜欢"
- **个性化** — 每一条推荐都只属于这个用户

---

### 2.4 🔄 反馈学习系统 (Feedback Loop)

- **隐式反馈**（浏览器插件自动采集）：是否点击、观看时长、是否收藏分享
- **显式反馈**：在插件中点赞/踩、对话式反馈
- **桌面端提交屏障**：普通推荐与正向/避雷探针的非聊天动作先即时更新 UI，10 秒内可真实撤销且不写后端；超时或页面离开才提交，失败恢复原状态。评论/聊天因依赖文本语义与服务端回复保持直接提交
- **记忆迭代**：反馈触发多层记忆网络更新——事件层记录事实，偏好层调整权重，觉察层写观察笔记，洞察层修正假设，灵魂层在必要时更新人格理解
- **策略自省**：Agent 自我评估推荐命中率，反思发现策略和理解模型的有效性

---

### 2.5 🔧 Skill 系统 (Extensible Skills)

**目标**：支持自定义扩展能力，让用户和社区可以为 Agent 增加新技能。

- **Skill 定义**：每个 Skill 是一个独立模块，包含说明文档 + 执行逻辑
- **内置 Skill**：B 站 / 知乎等来源搜索、内容浏览、评论区分析、作者追踪等
- **自定义 Skill**：用户可以创建新 Skill 扩展 Agent 的能力
  - 例如：新平台接入、特定领域的内容评估策略、新的推荐呈现方式
- **Skill 注册**：Agent 自动发现可用 Skill，根据任务需要选择调用

---

## 3. 系统架构

### 3.1 vNext 领域、薄 `/api/v1`、独立 worker 与 generated clients

v0.4.0 的权威运行面由 feature-oriented 领域契约、七平台 connector、generic source task、SQLAlchemy persistence、nested typed settings、来源凭据加密、cookie/CSRF + bearer access、只经 LiteLLM 的 typed AI、activity/profile/feed/library/chat application service、薄 `/api/v1`、四任务 Huey worker 和 OpenAPI-generated Web/extension clients 组成。worker 固定注册七个平台，不扫描动态插件；direct/CLI client 与凭据解密均延迟到首次真实调用，默认全部来源 disabled，因此 composition 不触发 live call。DB→Huey 先提交 pending row，再 immediate enqueue，最后写 dispatch marker；queue failure 可按 undispatched row reconcile，worker startup 则重发全部 pending row，覆盖 dequeue 后、应用 claim 前崩溃，重复消息由原子 claim 消解。Huey result 只属于 transport，业务状态、幂等、运行中取消、单调 progress 与恢复以 `job_runs` 为唯一权威。每个 handler 的条件 running guard 与其 feature writes 共用一个 UoW；cancellation 及其它 running state transition 也以条件 UPDATE 开始而不先读，两个 SQLite writer 按写序和有限 busy timeout 原子排序 cancellation 与 activity、profile+ledger、feed graph、cleanup effect，等待耗尽显式失败。profile 另使用独立 consumed-evidence ledger 与 expected base revision；explicit edit 把 override evidence 与一个新 revision 原子提交。Feed 在 batch 前排除 durable 历史并执行任一 topic 饱和即拒绝的 hard cap。Web 使用 cookie + CSRF，扩展使用 finite bearer；SSE 经 authenticated fetch stream，浏览器任务只经 generic claim/complete dispatcher。

```text
HTTP / CLI / logged-in browser transports
        │ raw rows contained by source package
        ▼
7 explicit built-in SourceManifest + SourceConnector adapters
        ├────────► ActivityEvent ──► ProfileSignal ──► ProfileSnapshot / ProfileDelta / ProfileEdit
        └────────► ContentItem ──► CandidateAssessment ──► FeedEntry ──► Interaction
                         ├────────► LibraryItem (CollectionItem + ContentItem)
                         └────────► ChatTurn / public ChatHistoryTurn
        └────────► SourceTaskService ─ deadline + lease/cancel/abandon ─► source_tasks
        │
        │ typed repository contracts
        ▼
SQLAlchemy repositories + UnitOfWork
        ├─ nested settings + DatabaseSettings / UserSettings / SettingsService
        ├─ auth_state.session_epoch (non-secret session revocation)
        ├─ source_accounts ── Fernet ciphertext ◄── OPENBILICLAW_SECRET_KEY
        ├─ activity/profile + consumed-evidence/content/feed/collection/chat tables
        └─ source_tasks/job_runs/ai_runs foundations
        │
        ▼
installer / Compose one-shot migrate ─► Alembic 0001 + 0002 ─► isolated data/vnext/openbiliclaw.db
API + worker startup ─► read-only schema-head gate

Application services + worker handlers
        ├─ typed TaskSpec/PydanticAI Agent ─► TaskRunner ─► ai_runs metadata only
        │                                        │ SDK retry=0
        │                                        ▼
        │           obc-interactive / obc-analysis ─► LiteLLM ─► providers
        ├─ EmbeddingService ─ model=obc-embedding ────┘
        └─ AIHealthService ─ alias-only redacted status + explicit safe public Admin URL
                                             LiteLLM ─► dedicated PostgreSQL

Huey (separate huey.db) ─► source_sync / profile_projection / feed_replenishment / cleanup
                              └─► JobService ─► all-pending recovery/claim/cancel/txn guard

Implemented now: domain contracts/policies, seven source manifests/connectors/settings,
                 generic lease-safe source tasks and HTTP claim/complete, schema/migration,
                 repositories/UoW, typed settings, encrypted source credentials and installer
                 lifecycle, six typed AI tasks, application services, seven-source composition,
                 four durable jobs, embedding/health clients, offline eval datasets,
                 thin /api/v1 routers, SSE chat/progress, browser/extension auth, operational CLI,
                 deterministic OpenAPI + unified error envelope, LiteLLM/Huey Compose
Implemented: existing Web/extension generated-client and generic dispatcher wiring
Deferred: final unreachable legacy deletion (Task 23); legacy data is archived, not migrated
Authoritative now: /api/v1, vNext application database, API/worker, source-task routes,
                   TaskRunner chat, job_runs product state, and operational CLI
```

三个模型别名必须精确为 `obc-interactive`、`obc-analysis`、`obc-embedding`。应用层不得选择 provider deployment；provider routing/fallback、网络重试、限流和缓存由 LiteLLM 独占，`TaskRunner` 只允许 task 声明的 semantic output retry，并把 BYPASS 映射为 proxy 的 `cache.no-cache`。六个内置 task 覆盖 profile、keyword、单候选、batch candidate、chat 与 recommendation；candidate assessment row ID 由 application 层生成。AI run schema/API 只接收 metadata/usage/error class，不存在输入或输出 payload 字段。浏览器 Admin 导航只来自显式、credential-free `OPENBILICLAW_LITELLM_ADMIN_URL`，不从 internal proxy base/key 推导。Docker Compose 要求本地生成的 LiteLLM master key 与 PostgreSQL password，源码/预构建路径挂同一 policy；唯一 `migrate` 服务成功后才允许 API/worker 启动，两个 runtime 只读检查 schema head；本阶段没有 live provider/Compose E2E。`/health?model` 可能调用 provider，仅用于显式诊断，并区分 degraded、transport、auth、missing alias、server 与 provider unhealthy。

FastAPI 只公开 `/api/v1/auth|system|settings|onboarding|sources|source-tasks|events|profile|feed|interactions|library|chat|jobs`。Router 只调用注入的 application service；chat send 与 progress 走 SSE，chat history 是 bounded JSON page，扩展来源工作走 generic typed claim/complete。vNext auth 只从 runtime environment 读取，不 fallback legacy config；Web 使用 same-origin password→HttpOnly cookie 并对 unsafe request 强制 `Origin + X-OBC-Auth`。Extension origin 即使来自 loopback 也不能使用 `trust_loopback`/CORS bypass，只能 device-key exchange finite bearer。Login/exchange 使用分离、per-peer、有界且可过期的 failure limiter。`auth_state` epoch 统一撤销 session，不撤销 installer bearer；startup password state 对 fresh absent/first enable/unchanged 保持幂等，rotation、removal 的 `disabled` sentinel 与 re-enable 都和 epoch bump 原子提交。Source manifest 暴露 safe settings/credential/per-operation schemas；API 只在 schema-head gate 成功后构造 settings-backed registry。五个平台没有 per-source field，Douyin `mode` 与 Reddit `backend` 各自有明确 transport consumer；其余 enabled/weights/schedule/feed policy 由 global `UserSettings` 管理。per-source settings 经 GET/PUT 写入现有 `settings` table 并在 registry rebuild 时应用，disconnect 幂等删除 encrypted material；library read 返回 joined renderable content。Explicit profile revision 的 timestamp 严格晚于上一 revision。Worker 在 composition/recovery/consumer 前安装/reuse owned console/file sinks 并应用 persisted network/logging settings；退出或失败时只清理本次创建的 sinks，保留 host root policy，并精确恢复 proxy、package logger 与四个 CA environment variables。包括 Starlette 404/405 在内的所有 JSON error 使用同一个 `{error:{code,message}}` runtime/OpenAPI envelope。CLI 只保留 `serve`、`worker`、`doctor`、`eval` 与 `db migrate/backup`。现有静态 Web/扩展已通过 generated clients 接线，旧运行树留待最终删除。

### 3.2 Historical v0.3 archive — 已停止作为入口的实现

> 本节仅作为只读历史索引，不再是当前规格，也不定义任何可实现合同。v0.3 的详细运行图、接口和认证流程保留在 Git 历史中。

v0.3 曾由浏览器专用实时通道、平台登录态回传、原生账号保存、应用内 provider 编辑器和自定义模型路由共同驱动。vNext 已分别以 authenticated SSE、generic source-task claim/complete、本地 collections、LiteLLM aliases/Admin，以及 header-only 的 finite extension bearer 取代这些路径。任何仍需追溯的 v0.3 行为都应查询 Git 历史，而不是从本文件恢复为当前实现。

## Historical v0.3 archive — 4. 技术选型

| 模块 | 技术方案 | 说明 |
|------|---------|------|
| 编程语言 | **Python** (后端) + **TypeScript** (插件) | 后端 AI 生态 + 前端插件 |
| LLM 接入 | **多模型**：OpenAI / Claude / DeepSeek / 本地模型等 | 全部支持，优先效果 |
| B 站交互 | **API 优先** (bilibili-api-python)（实际实现使用自研 `BilibiliAPIClient`，不依赖此库）+ **agent-browser** (浏览器操作) | API 快速高效，agent-browser 补充复杂交互 |
| 浏览器操作 | **[agent-browser](https://github.com/vercel-labs/agent-browser)** | Vercel 的 AI Agent 专用浏览器 CLI |
| 浏览器插件 | **Chrome Extension** (Manifest V3) | 行为采集 + 交互 UI + LUI |
| Agent 框架 | **自研轻量框架**，按需扩展 | 灵活可控，支持 Skill 系统 |
| 记忆存储 | **SQLite** + **向量索引** + **JSON** | 分层存储，匹配不同记忆类型需求 |
| 任务调度 | **asyncio runtime loops** + `[scheduler]` 配置 | 按前端可换候选缺口、raw-material headroom、行为阈值和策略间隔执行内容发现；pending raw 评估有独立 loop；不依赖 cron |
| 运行模式 | **本地运行** | 用户自己的电脑上执行 |

---

## Historical v0.3 archive — 5. 版本规划

### v0.1 — MVP：最小推荐闭环

> **核心目标**：证明"深度理解用户 → 主动发现内容 → 有温度地推荐"是可行的。

- [ ] 项目骨架搭建（Python 后端 + Chrome 插件 + 配置管理）
- [ ] B 站 API 接入 + agent-browser 集成
- [ ] 浏览器插件 MVP：基础行为采集（点击/浏览/搜索 + 页面快照）
- [ ] 多层记忆架构基础版（事件层 + 偏好层 + 灵魂层）
- [ ] 基础 Soul Engine：从行为数据中构建初步人格理解
- [ ] 基础内容搜索与推荐
- [ ] 插件内 UI：查看推荐、提供反馈、基础对话
- [ ] 多 LLM 支持框架

### v0.2 — 更深层的理解

- [ ] 完整行为采集（微行为、DOM 上下文、浏览路径）
- [ ] 完整五层记忆架构 + 网状关联 + 自我编辑能力
- [ ] 苏格拉底式深度对话（追问/假设/确认/调整）
- [ ] 多策略内容发现（相关推荐链、排行榜、评论区挖掘）
- [ ] "发现惊喜"模式：跨领域探索
- [ ] Skill 系统 v1：内置 Skill + 自定义 Skill 支持
- [ ] 推荐质量自省和策略迭代

### v0.3 — 更好的体验

- [ ] 插件 UI 升级：丰富的 LUI 交互体验
- [ ] 情境感知推荐（时间/情绪/场景自适应）
- [ ] 定时自动发现和推送
- [ ] 记忆可视化（查看 Agent 对你的理解）
- [ ] UP 主追踪和新视频提醒

### v1.0 — 成熟的开源工具

> 注：项目已确定为严格单用户设计，不再计划多用户支持。

- [ ] 多用户支持 + 配置系统
- [ ] 完善的安装和使用文档
- [ ] 插件商店发布
- [ ] 社区 Skill 市场
- [x] 跨平台内容发现（已落地 B 站 / 小红书 / 抖音 / YouTube / X / 知乎 / Reddit / 通用 Web，后续继续扩展更多 adapter）

---

## Historical v0.3 archive — 6. 设计原则

1. **灵魂优于标签** — 理解一个人，而不是给他贴标签
2. **有温度的表达** — Agent 的每一次输出都像朋友在说话
3. **主动追问和假设** — 不等用户说，主动猜测并验证
4. **用户掌控权** — 用户可以查看、修正、引导 Agent 的理解
5. **隐私本地化** — 所有数据和计算在本地
6. **开放可扩展** — 通用开源设计 + Skill 系统

---

*当前规格: vNext §3.1 | Historical v0.3 archive snapshot: 2026-06-25*
