# OpenBiliClaw — 项目规格说明书 (SPEC) v0.3

> *你的跨平台 AI 内容朋友，比你更懂你想看什么* 🎯

---

## 1. 项目定位

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

## 2. 核心功能模块

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

### 3.1 vNext 领域与持久化基础（尚非运行时权威）

v0.4.0 先冻结 feature-oriented 领域契约和确定性策略，再在其外侧加入隔离的 SQLAlchemy/Alembic persistence、类型化系统设置和凭据加密。连线标出已实现的依赖方向与后续 use case 的预期关系，不表示生产请求已切换；v0.3 API、runtime、legacy storage 和四端客户端仍是当前实际路径。

```text
Future source adapters
        │
        ▼
SourceManifest + SourceConnector
        ├────────► ActivityEvent ──► ProfileSignal ──► ProfileSnapshot / ProfileDelta
        └────────► ContentItem ──► CandidateAssessment ──► FeedEntry ──► Interaction
                         ├────────► CollectionItem
                         └────────► ChatTurn
        │
        │ typed repository contracts
        ▼
SQLAlchemy repositories + UnitOfWork
        ├─ settings + DatabaseSettings / UserSettings / SettingsService
        ├─ source_accounts ── Fernet ciphertext ◄── OPENBILICLAW_SECRET_KEY
        ├─ activity/profile/content/feed/collection/chat tables
        └─ source_tasks/job_runs/ai_runs foundations
        │
        ▼
Alembic 0001 ──► isolated data/vnext/openbiliclaw.db

Implemented now: domain contracts/policies, schema/migration, repositories/UoW,
                 typed settings, encrypted-at-rest source credential adapter
Deferred: production composition, installer key lifecycle, legacy data migration,
          AI/source/job services, use cases, /api/v1, frontend cutover
Authoritative now: v0.3 legacy storage/runtime shown in section 3.2
```

### 3.2 当前 v0.3 生产架构

```text
interactive ─────────────────────────────────────────┐
                                                    ├─ runtime total gate (default 4) ─ global Chat route
background ─ background admission (default 3) ──────┘
             ├─ refill: expression > evaluation > supply
             │  └─ while queued: guarantee 2, may borrow all 3
             │     expression owner: 8 immediate / 3s fixed tail / 60 drain / 30×2 provider
             └─ maintenance: at most 1 while refill waits;
                parked when canonical available = 0
```

下图的对话/反馈入口共享同一失败原子链路：`Web / CLI / OpenClaw → SocraticDialogue`。成功才写入 user+agent 历史并后台学习；失败/超时回滚临时用户历史，再由边界返回安全错因或持久化 `failed / reply=""`。桌面 Web 的推荐、runtime 与次级 hydration 是独立分支。

模型配置只有一条生产数据流：`connection-type descriptor registry → ModelConfigService → 原生 ordered Chat/Embedding factories → RuntimeModelBundle → Soul / Dialogue / Discovery / Recommendation / CLI / OpenClaw`。桌面、移动、插件与 `/setup/` 消费同一脱敏 snapshot 和 descriptor；CLI 直接调用同一 service。旧 `[llm]` 只在加载时生成明确待确认的迁移候选，legacy `/api/config` 只提供无凭据只读投影，二者都不参与生产 route 构造或权威写入。

```text
descriptors ─┬─ Desktop / Mobile / Extension / Setup ─ model API ─┐
             └─ CLI models ────────────────────────────────────────┤
                                                                  ▼
                                                         ModelConfigService
                                                                  │ native [models]
                                                                  ▼
                                              ordered Chat / Embedding factories
                                                                  │
                                                                  ▼
                                                        RuntimeModelBundle
                                                                  │
                                                                  └─ all model callers
```

```
┌──────────────────────────────────────────────────────────────┐
│                  用户交互层 (浏览器插件)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐    │
│  │ 统一行为采集   │  │ 推荐展示 UI   │  │ 对话/反馈/探针   │    │
│  │ Adapter: B/XHS│  │ (LUI 界面)   │  │ (durable turn) │    │
│  │ +DY/YT/X/ZH   │  │ +真实可换数   │  │                │    │
│  │ +停留满意度   │  │ +文字卡渲染   │  │                │    │
│  └──────────────┘  └──────────────┘  └─────────────────┘    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ bili/xhs/dy/yt/zhihu/reddit 任务调度 + 源开关/比例配置（后台 tab / 初始化导入 / 配比建议）│ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ B 站 / 抖音 / X Cookie 同步（runtime-stream 请求 + 扩展回传）│   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 扩展捕捉 E2E：run -> runtime-stream -> 入口归位 -> DOM 操作 -> /api/events │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 普通 /api/events：accepted -> memory -> ProfileUpdatePipeline -> request_replenishment │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ delight / interest.probe / avoidance.probe 主动推送（含probe_mode）│ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 后台 LLM 请求暂停配置（设置页调度区 + presence gate）          │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 开机自启动开关：/api/autostart-status + apply（本机可写）     │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 配置离线缓存 + 降级模式修复 UI（保存后提示重启）              │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 手机版二维码：桌面/插件 -> /api/qr-info(lan_ip) -> /m        │   │
│  │ 跳过 /api/health readiness / embedding probe                 │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 推荐/消息封面：UI -> /api/image-proxy -> 白名单 CDN -> UI    │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 海外网络：config/UI -> direct|system|custom -> LLM/YT/updater │   │
│  │ 国内客户端保持独立直连，不消费海外路由策略                    │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ API Auth Gateway（可选）：/api/* 密码门禁中间件             │   │
│  │   本机/扩展免登录 · LAN/远程需密码 · auth_epoch 撤销         │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 推荐点击：content_id/url/source_platform -> source-aware click signal │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 推荐/探针反馈：即时 UI -> 10s 可撤销提交 -> API；推荐再经 5s 合并学习 │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ runtime status：available/raw/pending 库存 -> 插件/移动/桌面 │   │
│  │ 补池：available-by-source deficit + raw-material headroom     │   │
│  │ 推荐消费池后：refresh.pool_updated 快照 -> 三端库存提示收敛   │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 画像编辑：编辑面板 -> /api/profile/edit -> 覆盖层（插件/移动/桌面三端） │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 引导初始化：画像信号来源选择 + 前置清单 -> /api/init + 进度流（B 站可取消；Reddit 可独立初始化）│ │
│  └──────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│                      Agent 核心层                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           Agent Orchestrator (自研)                   │   │
│  │   (任务调度 / 策略决策 / 多步推理 / 自省 / Skill 调度)    │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────┐      │
│  │ User Soul    │ │ Content      │ │ Recommendation │      │
│  │ Engine       │ │ Discovery    │ │ Engine         │      │
│  │ (词表画像+探针)│ │ (发现+待评估池)│ │ (排序+表达)     │      │
│  └──────────────┘ └──────────────┘ └────────────────┘      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │     PoolCurator + 双轴 fatigue + per-group 窗口 + 新兴趣放大保护 │ │
│  │     request_replenishment + 定时/手动补货 + B/XHS/DY/YT/X/Zhihu/Reddit=5/1/1/1/1/1/1 │ │
│  │ API CandidateEvalCoordinator: durable projected -> 3×30 workers -> serial headroom admit │ │
│  │ OpenClaw refresh: first source/eval <=4 -> copy <=4/no split retry -> canonical subset; both hosts recover first │ │
│  │     内容元数据：时长/互动/发布时间 -> candidates -> content_cache -> API -> 四端 │ │
│  │     Query inspiration cache: search preview -> inspiration/expansion -> keyword provenance │ │
│  │     InspirationKeywordPipeline: axis library learning loop (yield backfill/lifecycle) + breadth config │ │
│  │     LLM gate: scheduler + extension presence          │   │
│  │     Soul taxonomy: CATEGORY_VOCAB + category migration + homonym-aware consolidation │ │
│  │     Autostart: user login item + Ollama preflight/self-heal + Ollama.app runtime 校验 │ │
│  │     Bili DOM fallback + XHS/Douyin/YouTube/X/Zhihu/Reddit producers: 按平台缺口独立补池 │ │
│  │     Hot reload one-shots: interest/avoidance force_tick │   │
│  │     Probe arbiter: interest / avoidance 每轮最多推送一条   │   │
│  │     Interest probes: near 5 + challenge 3 独立 active 额度 │   │
│  │     Probe memory: domain / axis / distance + exploration buffer │ │
│  │     AccountSync: B 站账号增量 -> Memory/Soul bootstrap     │   │
│  │     Guided init: selected profile-signal sources + LLM/embedding live probe -> run_guided_init + InitCoordinator │ │
│  │     Pool readiness: servable/raw/pending 统一库存口径       │   │
│  │     Atomic maintenance: canonical protected -> topic/source/raw -> invariant/rollback │ │
│  │     Source bootstrap seen-key guard -> Memory/Profile      │   │
│  │     Profile overrides overlay: 用户编辑 -> profile_overrides.json │ │
│  │       -> get_profile()/sync_profile_files 读时叠加（抗画像重建）│ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ /api/saved/* -> membership 先提交 -> native_save_tasks/items 快照 -> router │
│  │ -> BilibiliNativeSaveAdapter（收藏夹/稍后再看）-> durable task-item poll │
│  │ 六平台 adapter -> ExtensionNativeSaveBroker -> extension_native_save_jobs -> native_save multiplex │
│  │ extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension                │
│  │ exact OpenBiliClaw / YouTube Watch Later targets -> authenticated safe task-result                 │
│  │ trusted-local extension E2E exact auth -> single saved sync item -> six-field safe callback        │
│  │ -> /api/sources/{xhs,dy,yt,x,zhihu,reddit}；unsupported_adapter_missing 可重试 │
│  │ -> 插件/桌面/移动 saved UI；CLI config-show（自动同步默认关闭）    │
│  │ NATIVE_SAVE_EXECUTE/RESULT：tab-launch mutex（XHS exact manual 可越过）+ per-task deadline + bounded replay │
│  │ shared MV3 recovery barrier 在领取任务前清理全部 runner-owned orphan tabs       │
│  │ final/source URL 与 tab/task/item 严格关联；Reddit/X/YT/XHS/DY/Zhihu 6/6 已接 │
│  │ （fixture 全覆盖；2026-07-14 六平台 favorite + watch-later/fallback 真实终态均成功）│
│  │ Zhihu typed ID -> exact identity control/dialog -> OpenBiliClaw checked proof │
│  │ YT favorite 精确 OpenBiliClaw；重复 exact 行优先 checked/稳定复用；Watch Later 只认 WL │
│  │ unsupported_content_type 保持 local-only                         │
│  │ UI: pending + 空 task_id 可手动同步；非空 task_id / syncing 禁重复 │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Skill System (可扩展技能)                 │   │
│  │  [搜索] [浏览] [评论分析] [UP主追踪] [自定义...]         │   │
│  └──────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│           多源适配层 (SourceAdapter Protocol, v0.3.0+)         │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────┐    │
│  │ B 站 Adapter  │  │ Bili/小红书/抖音/YouTube/知乎/Reddit任务桥│ │ Web Adapter │  │
│  │ (WBI API+DOM兜底)│ │ (扩展代理 + DOM-first)│  │ (Playwright │    │
│  │              │  │ + profile/search/feed/yt/zhihu)│ │ + LLM 抽取)│    │
│  └──────────────┘  └──────────────────┘  └─────────────┘    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ sources.platforms：七平台 alias / strategy / URL host      │ │
│  │                  → 统一 pool accounting / viewed identity │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ DouyinDiscoveryService: 首页 DOM 触发 search / 热点 seed-related / feed │ │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ YoutubeDiscoveryProducer: 后端直连 yt_search/trending/channel │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ XAdapter + XDiscoveryProducer: 服务端 cookie 重放(twitter-cli) │ │
│  │   search / feed(For-You) / creator(账号订阅) + 源健康状态机   │   │
│  │   行为采集: 扩展 MAIN-world GraphQL tap + generic collector   │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ZhihuDiscoveryProducer: 插件登录态 search/hot/feed/creator/related -> pending eval │ │
│  │   fetch-zhihu 只做 smoke；guided init 勾选知乎才进首版画像       │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ RedditDiscoveryProducer: rdt-cli 默认 + 插件 fallback search/hot/subreddit/related -> pending eval │ │
│  │   Reddit bootstrap_events: saved/upvoted/subscribed -> 首版画像信号 │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Cookie/登录态、runtime-stream presence、任务持久化/claim、seen-key 去重 │ │
│  └──────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│ 模型配置 API + 事务型有序 Chat/Embedding route + 全配置入口（阶段 9–14）│
│ Desktop：ordered list + inspector；Extension/Mobile：sequential list→detail │
│ CLI：models list/add/edit/remove/move/probe → ModelConfigService │
│ setup/bootstrap/install/Docker/package → native [models] writer │
│ Chat/Embedding/Runtime tabs；descriptor fields；Embedding 共享设置 │
│ GET/PUT model-config → strict secret-safe schema ────────┐    │
│ descriptors + exact probe → safe probe/circuit summary ─┤    │
│ legacy /api/config → read-only projection/write guard ──┤    │
│ native [models] → strict parser/revision/safe endpoint ──┤    │
│ legacy [llm] → effective base+local inspection/map ──────┤    │
│                 → secret-safe report → closed resolution │    │
│                 → authoritative final validation ────────┤    │
│                                                         └→ ModelConfigService path lock │
│ redacted snapshot/revision → credential action → local fence │
│ build complete RuntimeContext candidate → canonical writer   │
│ init guard + immediate reread → rebase / authority conflict  │
│ lifecycle-locked settled runtime/task snapshot before replace │
│ legacy backup → temp/fsync/replace → app lifecycle activation │
│ publish graph → serialized drain/restart → clear degraded → one final-slot event │
│ failure/cancel → shielded restore reacquires lifecycle ownership │
│ Chat records → connection_factory → ID adapter → OrderedLLMRoute │
│                                   ├→ total deadline + safe attempts │
│                                   └→ revision-aware CircuitTable   │
│ Embedding providers → shared settings adapter → OrderedEmbeddingRoute │
│                                   ├→ finite/dimension validation + circuit │
│                                   └→ fixed PNG exact probe + shared cache namespace │
│ probe: gate/init → path-lock init/revision/credential capture → network unlocked → revision recheck │
│ RuntimeModelBundle → 全 consumer 原子发布并激活对应后台任务        │
│ 普通保存保留 raw；四端共用原生 schema；CLI 显式 DTO→domain、revision rebase │
├──────────────────────────────────────────────────────────────┤
│         LLM 适配层 + Embedding 服务（双层缓存）                 │
│  ┌──────────────────────────┐  ┌────────────────────────┐   │
│  │ OpenAI / Claude / Gemini │  │ EmbeddingService       │   │
│  │ DeepSeek / Ollama /      │  │ L1 内存 + L2 SQLite    │   │
│  │ OpenRouter + Codex OAuth │  │ 共享 namespace + 安全降级 │   │
│  └──────────────────────────┘  └────────────────────────┘   │
│  Desktop bundle: official Ollama.app runtime (ollama + runner dylibs/assets) │
│  LLMService normal/structured/multimodal/tools → one global route │
│  caller only controls admission/usage; llm_usage records connection identity │
│  caller tags → concurrency + usage only; no module model selection │
│  response → provider/model + connection ID/type/preset/position    │
│  discovery evaluator: text + metrics + optional compressed cover image input │
│  OpenAI auth_mode: api_key / experimental Codex CLI OAuth      │
│  结构化 JSON helper: wrapper / fenced / JSONL / schema echo / MiMo 容错 │
├──────────────────────────────────────────────────────────────┤
│                    多层网状记忆存储                             │
│  ┌───────────┐ ┌─────────────┐ ┌────────────┐ ┌─────────┐  │
│  │ 核心记忆    │ │ 情景记忆     │ │ 语义记忆    │ │ 工作记忆 │  │
│  │ (JSON)     │ │ (SQLite +   │ │ (知识图谱/  │ │ (内存)  │  │
│  │ Soul+偏好   │ │  向量索引)   │ │  JSON)     │ │         │  │
│  └───────────┘ └─────────────┘ └────────────┘ └─────────┘  │
│  SQLite: events(inferred_satisfaction) / discovery_candidates     │
│          discovery_keywords(+cohort gate) / discovery_inspiration_*│
│          content_cache(item_key) / recommendations(item_key) / chat_turns / avoidance_state │
│          saved_items/memberships/native_save_states + durable task ledger │
└──────────────────────────────────────────────────────────────┘
```

远程浏览器扩展认证独立于平台登录态：管理员通过 CLI 生成设备密钥，后端只保存摘要；扩展向 `/api/auth/extension-token` 换取短会话。普通 HTTP 使用 Bearer Header，WebSocket 与图片代理只携带短会话 query。该能力默认关闭，撤销设备密钥会使全部现有会话立即失效。

---

## 4. 技术选型

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

## 5. 版本规划

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

## 6. 设计原则

1. **灵魂优于标签** — 理解一个人，而不是给他贴标签
2. **有温度的表达** — Agent 的每一次输出都像朋友在说话
3. **主动追问和假设** — 不等用户说，主动猜测并验证
4. **用户掌控权** — 用户可以查看、修正、引导 Agent 的理解
5. **隐私本地化** — 所有数据和计算在本地
6. **开放可扩展** — 通用开源设计 + Skill 系统

---

*文档版本: v0.3 | 日期: 2026-06-25 | 状态: 持续更新*
