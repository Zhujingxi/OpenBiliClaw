# 多层网状记忆系统设计

> OpenBiliClaw 的记忆系统是整个 Agent 的核心，决定了 Agent 对用户的理解深度。

## 设计理念

传统推荐系统用"标签"理解用户，而 OpenBiliClaw 用"记忆"理解用户。就像你了解一个好朋友，不是因为你给他贴了标签，而是因为你记得他说过什么、做过什么、在什么情况下开心或难过。

这套设计追求三个目标：

1. **分层稳定** — 底层信号可以很嘈杂，但越往上越稳定。一次偶然的点击不会让人格画像来回抖动。
2. **可解释** — 任何一条画像结论都能向下追溯到具体事件，而不是黑盒输出。
3. **双向关联** — 不是单向流水线。对用户的高层理解反过来指导如何解读新行为。

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        记忆系统 MemoryManager                     │
│                                                                 │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│   │ 灵魂层   │←→│ 洞察层   │←→│ 觉察层   │←→│ 偏好层   │←→│ 事件层   │
│   │ Soul     │  │ Insight  │  │Awareness │  │Preference│  │ Event    │
│   │ .json    │  │ .json    │  │ .json    │  │ .json    │  │ SQLite   │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
│        │             │             │             │             │
│   ┌────┴─────────────┴─────────────┴─────────────┴─────────────┴────┐
│   │                  核心记忆 Core Memory                            │
│   │  get_core_memory() → 裁剪摘要 → render_core_memory_prompt()     │
│   └────────────────────────────────┬────────────────────────────────┘
│                                    │                                │
│   ┌──────────────────────┐  ┌──────┴──────────────────────────────┐ │
│   │ 工作记忆 Working     │  │ 状态文件                             │ │
│   │ (仅当前会话，不落盘)  │  │ feedback_state / account_sync_state │ │
│   └──────────────────────┘  │ discovery_runtime / insight_candidates│ │
│                              │ cognition_updates                   │ │
│                              └────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
         │
         ↓ 核心记忆 prompt 注入
┌──────────────────────────────────────────────┐
│ LLMService  →  所有 LLM 调用自动携带用户上下文 │
└──────────────────────────────────────────────┘
```

## 五层记忆架构

### 事件层 (Event Layer)

**"他做了什么"** — 最底层的原始数据，只记事实，不做解释。

记录用户在 B 站的每一个行为：
- 点击、搜索、收藏、评论、点赞、投币
- 对应的 DOM 页面快照和浏览路径
- 时间戳和行为上下文
- Agent 推荐后的用户反应

**存储**：SQLite `events` 表（结构化事件日志，支持按类型/时间/关键词查询）

**支持的事件类型白名单**：
`view` `dialogue` `pause` `seek` `search` `favorite` `like` `coin` `comment` `click` `scroll` `hover` `snapshot` `feedback`

**数据示例**：

```json
{
  "id": 127,
  "event_type": "view",
  "url": "https://www.bilibili.com/video/BV1abc",
  "title": "20分钟讲透中东局势",
  "context": {},
  "metadata": {"bvid": "BV1abc", "duration": 1200},
  "created_at": "2026-03-28 14:30:00"
}
```

### 偏好层 (Preference Layer)

**"他喜欢什么/不喜欢什么"** — 从事件中提炼出的结构化偏好。

- 兴趣标签（带权重和时间衰减）
- 内容风格偏好（时长、节奏、制作质量）
- 情境模式（工作日/周末、碎片/沉浸）
- 探索开放度
- 常看UP主 / 不喜欢的主题

**存储**：`data/memory/preference.json`

**数据示例**：

```json
{
  "interests": [
    {
      "name": "国际时事",
      "category": "知识",
      "weight": 0.88,
      "source": "dialogue",
      "first_seen": "2026-03-15",
      "last_seen": "2026-03-28"
    },
    {
      "name": "游戏实况",
      "category": "娱乐",
      "weight": 0.45,
      "source": "behavior",
      "first_seen": "2026-03-01",
      "last_seen": "2026-03-20"
    }
  ],
  "style": {
    "depth_preference": 0.9,
    "preferred_duration": "medium_to_long"
  },
  "exploration_openness": 0.6,
  "disliked_topics": ["浅层热点复读", "标题党"],
  "favorite_up_users": ["何同学", "观视频工作室"]
}
```

### 觉察层 (Awareness Layer)

**"每天他在发生什么变化"** — Agent 每天的观察和思考，不是贴标签，是写"观察笔记"。

- 每日观察笔记（"今天他搜索了三次摄影相关内容"）
- 兴趣趋势追踪（"对游戏的兴趣在下降"）
- 情绪状态推测（"最近看放松类内容变多了，可能工作压力大"）
- 阶段性总结

**存储**：`data/memory/awareness.json`（+ 未来支持向量索引做语义检索）

**数据示例**：

```json
{
  "notes": [
    {
      "date": "2026-03-28",
      "observation": "连续三天搜索并观看国际局势深度解读类视频，且主动跳过了几条短平快时事评论。"
    },
    {
      "date": "2026-03-27",
      "observation": "今天晚间集中看了两小时纪录片，可能是周末放松模式。"
    }
  ]
}
```

### 洞察层 (Insight Layer)

**"为什么他会这样"** — 对用户行为的深层解读，保持假设语气，附带置信度。

- 动机分析（"他看探店视频不是为了美食，是享受发现的过程"）
- 心理需求推断（"刷游戏实况是为了获得掌控感和放松"）
- 潜在兴趣假设（"他可能会喜欢旅行vlog，因为也满足发现欲"）
- 行为模式归因

**存储**：`data/memory/insight.json`（可审查可修正）

**数据示例**：

```json
{
  "hypotheses": [
    {
      "hypothesis": "用户不是只想知道发生了什么，而是在用深度内容建立判断确定性。",
      "confidence": 0.84,
      "evidence": ["连续观看深度解读", "搜索'因果链'", "dislike 浅层复读"],
      "created_at": "2026-03-27"
    },
    {
      "hypothesis": "周末倾向于沉浸式长内容消费，工作日偏好碎片化浏览。",
      "confidence": 0.62,
      "evidence": ["周末纪录片观看时长明显增加"],
      "created_at": "2026-03-25"
    }
  ]
}
```

### 灵魂层 (Soul Layer)

**"他是一个什么样的人"** — 最高层的人格理解，从所有下层信号长期整合而来。

- 核心性格特质
- 价值观和审美倾向
- 当前生活阶段和状态
- 深层未被满足的需求

**存储**：`data/memory/soul.json`（核心记忆，始终在 Agent 上下文中）

**数据示例**：

```json
{
  "personality_portrait": "这是一个会主动追问复杂事件底层逻辑的人。他不满足于知道'发生了什么'，更想理解'为什么会这样'。偏好高信息密度、能把因果链讲透的内容，对表层热闹和情绪化表达缺乏耐心。周末会给自己安排沉浸式的深度内容时间。",
  "core_traits": ["追求深度理解", "独立思考", "耐心但挑剔"],
  "values": ["真实", "逻辑严谨", "信息密度"],
  "life_stage": "职场中期，工作压力较大，用深度内容充电",
  "deep_needs": ["建立对复杂世界的确定感", "在信息噪音中找到可靠判断锚点"]
}
```

### 一句话区分五层

| 层 | 回答的问题 | 稳定性 |
|----|-----------|--------|
| 事件层 | 发生了什么 | 最原始，每次行为都写入 |
| 偏好层 | 你喜欢什么 | 较稳定，累积多次行为才变 |
| 觉察层 | 你最近变成了什么样 | 每天更新，关注变化趋势 |
| 洞察层 | 你可能为什么会这样 | 需要多条证据才敢提假设 |
| 灵魂层 | 把前面这些长期整合后，你像什么样的人 | 最稳定，不轻易改写 |

## 四种记忆类型

| 类型 | 说明 | 组成 | 生命周期 |
|------|------|------|---------|
| 核心记忆 Core | 始终加载到 LLM 上下文 | Soul 层 + Preference 摘要 + 近期觉察 + 高置信洞察 | 每次 LLM 调用都注入 |
| 情景记忆 Episodic | 具体的交互片段 | Event 层 + Awareness 层 | 持久化到 SQLite/JSON，按需检索 |
| 语义记忆 Semantic | 用户相关的事实知识 | Preference 层 + Insight 层 | 持久化到 JSON，结构化查询 |
| 工作记忆 Working | 当前会话上下文 | `_working_memory` 字典 | 仅在进程内存中，进程退出即丢失 |

**类比**：

想象你和一个朋友相处了一年：

- **核心记忆** = 你随时记得的关于他的印象：他是个什么样的人，喜欢什么不喜欢什么
- **情景记忆** = 你能回忆起的具体片段：上次他看到那个纪录片时兴奋地聊了半小时
- **语义记忆** = 你知道的关于他的客观事实：他不喜欢标题党，他最近在研究国际局势
- **工作记忆** = 你们此刻正在聊的话题：他刚问你有没有好的中东局势分析视频

## 层间交互：双向网状关联

记忆层之间不是单向流水线，而是**网状双向关联**。这是区别于传统推荐系统的核心设计。

### 自底向上：事件驱动的理解构建

这是最常见的数据流向——新行为触发理解更新：

```
用户看了视频 → Event 层写入 view 事件
                 ↓
     SoulEngine 批量分析 → Preference 层更新兴趣权重
                              ↓
            SoulEngine 每日总结 → Awareness 层写入观察笔记
                                    ↓
              SoulEngine 推理 → Insight 层生成/更新假设
                                       ↓
                    变化足够显著 → Soul 层重建人格画像
```

**关键**：`propagate_event()` 只负责写入事件层。上层的更新由 `SoulEngine` 在外部编排触发，不是写入事件后自动级联。这样做是为了控制更新频率和 LLM 调用成本。

### 自顶向下：理解指导解读

高层理解会反过来影响低层的解读方式：

```
Soul 层知道"这个人追求深度"
    ↓
解读同一个 view 事件时：
  - 如果他快速跳过了一条浅层视频 → 不是"不感兴趣"，是"嫌太浅"
  - 如果他反复暂停一条深度视频 → 不是"无聊"，是"在认真消化"
    ↓
Preference 层的权重调整会考虑人格背景
    ↓
Awareness 层的观察笔记会用人格语境来表述
```

> 注：当前 `top_down_reinterpret()` 方法尚未实现具体逻辑，这是计划中的功能。核心记忆已通过 prompt 注入间接实现了部分自顶向下的效果——LLM 在分析新事件时已经"知道"用户是什么样的人。

### 跨层直连：重大事件的快速通道

并非所有信号都要逐层传递。某些强信号可以跨层直达：

```
用户明确说："我最近完全不想看游戏了"
    ↓
这条 dialogue 事件 → 直接影响 Preference 层（删除/大幅降权"游戏"标签）
                   → 同时影响 Soul 层（可能触发生活阶段的判断修正）
                   → 跳过了 Awareness 和 Insight 的渐进式推理
```

## 画像更新机制：ProfileUpdatePipeline

画像更新的核心设计是**信号收拢 + 分层决策**：所有来源的信号统一进入一个入口（`ProfileUpdatePipeline.ingest()`），由 pipeline 决定信号影响哪一层、何时触发更新、如何更新。

### 为什么需要统一入口

用户的一个兴趣变化可能同时产生多种信号：连续看了 3 天国际新闻（行为事件）、在聊天里说了"我想了解国际局势"（对话洞察）、又给一条浅层新闻点了 dislike（推荐反馈）。这三个信号指向同一个方向，只有汇聚到一个地方才能综合判断。

### 架构

```
所有信号源
  ├── 浏览器插件行为事件 (view/search/click/scroll...)
  ├── 互动事件 (like/coin/favorite/comment)
  ├── 推荐反馈 (like/dislike)
  ├── 聊天洞察 (dialogue insight candidates)
  ├── 原始聊天轮次 (dialogue turn)
  └── 账户同步数据 (history/favorites/following)
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│              ProfileUpdatePipeline.ingest()               │
│                                                          │
│  1. 接收信号 → 分类：这个信号影响哪些洋葱层？              │
│  2. 投递到对应层的信号缓冲区                               │
│  3. 检查各层是否达到更新阈值（信号数 + 时间间隔）           │
│  4. 只更新达标的层，不动其他层                             │
│  5. 持久化缓冲区状态（防止进程重启丢信号）                  │
└──────────────────────────────────────────────────────────┘
        │
        ▼ 按层独立更新
┌────────┬──────────┬────────┬──────────┬────────┐
│Surface │ Interest │  Role  │  Values  │  Core  │
│ 纯计算  │ LLM+合并 │LLM+diff│LLM+delta │LLM+强保护│
│ 低门槛  │  中门槛   │ 中高门槛 │  高门槛   │ 极高门槛 │
└────────┴──────────┴────────┴──────────┴────────┘
        │
        ▼ Core/Values 变化时
┌──────────────────────┐
│ Portrait 综合叙事重生成 │
└──────────────────────┘
```

### 信号分类规则

每种信号天然有一个"影响深度"，决定它投递到哪些层的缓冲区：

| 信号类型 | 来源 | 目标层 |
|---------|------|--------|
| 行为事件 (view/search/click...) | 插件/账户同步 | Surface + Interest |
| 互动事件 (like/coin/favorite) | 插件/账户同步 | Interest + Surface |
| 推荐反馈 (like/dislike) | 插件/CLI | Interest + Surface |
| 对话洞察 kind=interest | 聊天学习 | Interest |
| 对话洞察 kind=dislike | 聊天学习 | Interest |
| 对话洞察 kind=goal | 聊天学习 | Role |
| 对话洞察 kind=value | 聊天学习 | Values |
| 对话洞察 kind=state | 聊天学习 | Core |
| 原始聊天轮次 | 聊天 | Surface |
| 账户同步快照 | 定时同步 | Interest + Surface + Role |

对话洞察会根据 `kind` 字段动态分类——聊天中说"我一直觉得真实比好看重要"会被 LLM 识别为 `kind=value`，从而路由到 Values 层。

### 各层更新阈值

不同深度的层有不同的更新门槛，越深越难触发：

| 层 | 最少信号数 | 最短间隔 | 缓冲上限 | 更新方式 | 说明 |
|----|----------|---------|---------|---------|------|
| **Surface** | 3 | 5 分钟 | 200 | 纯计算 | 快速响应。从事件统计时段分布、时长偏好、完播率 |
| **Interest** | 3 | 10 分钟 | 200 | LLM 提取 + 树状合并 | 委托 PreferenceAnalyzer 提取兴趣，按 domain→specifics 合并 |
| **Role** | 5 | 24 小时 | 50 | LLM 判断是否需要改 | 只问"生活阶段是否变了？"，返回 `no_change` 则不动 |
| **Values** | 5 | 24 小时 | 50 | LLM 返回增删 delta | 只允许"增加/删除哪些价值观？"，每次最多增删 1 项 |
| **Core** | 8 | 48 小时 | 30 | LLM + 强保护 | 核心特质极少变，MBTI 需 confidence>0.7 且 10+ 信号才更新 |
| **Portrait** | — | — | — | Core/Values 变化后触发 | 不独立缓冲，依附于深层变化 |

### 各层的更新方式

关键设计：**不再全量重建，而是各层独立更新**。

**Surface 层（纯计算，无 LLM）**
```
统计近期事件 → 计算时段分布、时长偏好、深度偏好
→ 直接写入 surface.cognitive_style / surface.style / surface.context
```

**Interest 层（LLM 提取 + 树状合并）**
```
事件 → PreferenceAnalyzer 提取兴趣标签
→ 按 category 分组为 domain→specifics 树
→ 与现有兴趣树合并（去重、取 max 权重、时间衰减）
→ 写入 interest.likes / interest.dislikes
```

**Role 层（LLM 判断，diff 保护）**
```
LLM 收到：当前 life_stage + current_phase + 近期证据
LLM 返回：{ "no_change": true } 或 { "life_stage": "新阶段", "current_phase": "新描述" }
→ 只有 LLM 明确提议变化时才写入，否则不动
```

**Values 层（LLM delta，限量保护）**
```
LLM 收到：当前 values + motivational_drivers + 近期证据
LLM 返回：{ "add_values": [...], "remove_values": [...], "no_change": bool }
→ 每次最多增删 1 项，拒绝大面积重写
```

**Core 层（LLM delta，最强保护）**
```
LLM 收到：当前 core_traits + deep_needs + 近期证据
LLM 返回：{ "add_traits": [...], "remove_traits": [...], "no_change": bool }
→ 每次最多增删 1 项
→ MBTI 只在初始推断或用户主动纠正时改
```

**Portrait 重生成（仅 Core/Values 变化后触发）**
```
Core 或 Values 层发生了实际变化
→ ProfileBuilder.build() 生成新 portrait
→ 只覆盖 personality_portrait 字段，不动各层结构化数据
```

### 防抖机制

在 pipeline 的分层更新之外，还有四道额外的防抖屏障：

**1. 兴趣时间衰减**

```
新权重 = 原权重 × 0.9 ^ (距上次活跃的周数)
```

两个月没被触发的兴趣，权重衰减到 `0.9^8 ≈ 0.43`。低于 `0.05` 自动移除。

**2. 聊天候选置信度门槛**

聊天提取的洞察先存入 `insight_candidates.json`，必须满足 `confidence ≥ 0.8` 且 `occurrences ≥ 2` 才会转化为正式的 `DIALOGUE_INSIGHT` 信号投递到 pipeline。

**3. Role/Values/Core 的 no_change 保护**

LLM 被明确要求：如果证据不足以改变当前理解，返回 `no_change: true`。代码层在收到 `no_change` 时直接跳过写入。

**4. 深层变更限量**

Values 和 Core 层每次更新最多增删 1 项。即使 LLM 建议大面积调整，代码层也只采纳第一项变更。

### pipeline 状态持久化

pipeline 的缓冲区状态持久化到 `data/memory/pipeline_state.json`，防止进程重启丢失未处理的信号：

```json
{
  "version": 1,
  "buffers": {
    "surface": {
      "signals": [...],
      "last_updated_at": "2026-03-29T10:00:00",
      "update_count": 12
    },
    "interest": { ... },
    "role": { ... },
    "values": { ... },
    "core": { ... }
  },
  "total_signals_ingested": 1234
}
```

每次 `ingest()` 调用后自动持久化。缓冲区超过上限时自动淘汰最旧的信号。

## 状态文件体系

除五层记忆数据外，MemoryManager 还管理一组运行状态文件，用于记录各子系统的处理进度：

```
data/memory/
├── event.json                  # 事件层
├── preference.json             # 偏好层
├── awareness.json              # 觉察层
├── insight.json                # 洞察层
├── soul.json                   # 灵魂层（OnionProfile v2 格式）
├── soul_profile.json           # 规范画像 JSON（双写镜像）
├── soul_profile.md             # 人类可读的画像 Markdown
├── soul_changelog.md           # 画像更新日志
├── pipeline_state.json         # 更新管线缓冲区状态
├── feedback_state.json         # 反馈处理游标
├── account_sync_state.json     # 账户同步游标
├── discovery_runtime.json      # 候选池刷新游标
├── insight_candidates.json     # 聊天候选洞察（中间态）
└── cognition_updates.json      # 认知变化记录（供插件通知）
```

| 文件 | 用途 | 主要消费者 |
|------|------|-----------|
| `pipeline_state.json` | 各层信号缓冲区、更新计数、上次更新时间 | ProfileUpdatePipeline |
| `soul_profile.json` + `.md` | 画像 JSON/Markdown 双写镜像 | Pipeline / 人类查阅 |
| `soul_changelog.md` | 每次画像更新的时间、层、变化、证据 | Pipeline / 人类查阅 |
| `feedback_state.json` | 记录"反馈处理到了哪一条"，避免重复分析 | SoulEngine |
| `account_sync_state.json` | 记录历史/收藏/关注的增量同步游标和签名 | AccountSyncService |
| `discovery_runtime.json` | 记录候选池刷新时间、通知游标、最近话题 | RefreshController |
| `insight_candidates.json` | 聊天中提取的候选洞察，等待置信度达标 | SoulEngine |
| `cognition_updates.json` | 系统最近形成的关键认知变化 | FastAPI → 浏览器插件通知 |

**设计原则**：每种状态独立文件，不和画像数据混存。这样 feedback 游标的写入不会锁住 preference 文件，各子系统可以独立推进。

## 核心记忆：LLM 如何"记得你是谁"

不是把所有原始 JSON 都喂给 LLM。`get_core_memory()` 做了裁剪，只提取稳定摘要：

```python
{
    "soul_summary": {
        "personality_portrait": "这是一个追求深度理解的人...",
        "core_traits": ["追求深度理解", "独立思考"],
        "values": ["真实", "逻辑严谨"],
        "life_stage": "职场中期",
        "deep_needs": ["建立对复杂世界的确定感"],
    },
    "preference_summary": {
        "top_interests": [前5个高权重兴趣],
        "style": {"depth_preference": 0.9},
        "exploration_openness": 0.6,
        "disliked_topics": ["浅层热点复读"],
        "favorite_up_users": ["何同学"],
    },
    "recent_awareness": [最近5条观察笔记],
    "active_insights": [置信度最高的5条假设],
}
```

`render_core_memory_prompt()` 将其格式化为中文 Markdown：

```markdown
## 用户画像
这是一个会主动追问复杂事件底层逻辑的人...

## 偏好摘要
兴趣标签: 国际时事, 纪录片, 历史
不喜欢: 浅层热点复读
常看UP主: 何同学, 观视频工作室

## 近期观察
- [2026-03-28] 连续三天搜索国际局势深度解读类视频

## 当前洞察
- 用户在用深度内容建立判断确定性 (置信度: 84%)
```

这段文本由 `LLMService` 自动注入到**所有** LLM 调用的 system prompt 中，包括偏好分析、觉察生成、洞察推理、聊天学习、内容发现评分等场景。这意味着系统在"记得你是谁"的前提下继续理解新信号。

## 系统集成：谁在读写记忆

```
                    ┌──────────────┐
                    │  浏览器插件   │
                    │  (Extension) │
                    └──────┬───────┘
                           │ HTTP POST /api/events / feedback / chat
                           ↓
┌───────────┐       ┌──────────────┐     propagate_event()
│  CLI      │──────→│  FastAPI API │ ──────────────────────→ MemoryManager
│ init/chat │       │  (app.py)    │ ←── load_cognition_updates()
│ /feedback │       └──────┬───────┘
└───────────┘              │
                           ↓
              ┌─────────────────────────┐
              │       SoulEngine        │
              │                         │
              │  ┌───────────────────┐  │
              │  │ProfileUpdatePipeline │  ← 唯一更新入口
              │  │                   │  │
              │  │ ingest() → 分类   │  │
              │  │ → 层缓冲区        │  │
              │  │ → 达标时分层更新   │  │
              │  │ → changelog       │  │
              │  └─────────┬─────────┘  │
              │            │            │
              │  ┌─────────┴─────────┐  │
              │  │   Layer Updaters  │  │
              │  │ Surface: 纯计算    │  │
              │  │ Interest: LLM+合并 │  │
              │  │ Role: LLM+diff    │  │
              │  │ Values: LLM+delta │  │
              │  │ Core: LLM+强保护   │  │
              │  └───────────────────┘  │
              └─────────────────────────┘
                           │
         ┌─────────────────┼──────────────────────┐
         ↓                 ↓                      ↓
  ┌──────────────┐  ┌──────────────┐      ┌───────────────┐
  │ LLMService   │  │MemoryManager │      │AccountSyncSvc │
  │              │  │              │      │               │
  │render_core_  │  │五层 JSON     │      │ pipeline      │
  │memory_prompt │  │pipeline_state│      │ .ingest()     │
  │get_core_     │  │soul_profile  │      │               │
  │memory        │  │changelog     │      │               │
  └──────────────┘  └──────────────┘      └───────────────┘
```

各组件的职责边界：

- **ProfileUpdatePipeline** — 唯一的画像更新决策点。接收所有信号，分类缓冲，按层独立更新
- **SoulEngine** — 持有 pipeline，提供 `build_initial_profile()` 和 `get_profile()` 等高层 API
- **Layer Updaters** — 每层独立的更新执行器（Surface 纯计算，Interest/Role/Values/Core 各有 LLM 策略）
- **LLMService** — 只读核心记忆，注入 prompt
- **MemoryManager** — 持久化所有层数据 + pipeline 状态 + 双写文件
- **FastAPI / CLI** — 将用户行为转为 `ProfileSignal` 投递到 `pipeline.ingest()`
- **AccountSyncService** — 将同步数据转为 `ProfileSignal` 投递到 `pipeline.ingest()`
- **RefreshController** — 定时调用 `pipeline.tick()` 检查缓冲区是否达标

## 完整示例：一个用户行为如何穿透五层

下面用一个具体场景，完整演示一个行为信号从产生到改变推荐的全过程。

### 背景

用户"小明"是一个科技爱好者，系统已经建立了初始画像。最近他开始对国际时事产生兴趣。

### 第 1 天：行为发生 → Surface + Interest 缓冲区收到信号

小明在 B 站上做了这些事：

1. 搜索了"中东局势 深度分析"
2. 看了一条 20 分钟的深度解读视频（看完了 95%）
3. 给这条视频点了赞和投币
4. 又看了一条相关推荐（看了 60%）

浏览器插件捕获到这些行为，通过 `POST /api/events` 发送给后端。每条事件经过两个路径：

1. `propagate_event()` → SQLite 事件层（原始存储）
2. `pipeline.ingest()` → 信号分类 → 投递到目标层缓冲区

```
信号                    分类结果           目标缓冲区
search "中东局势"       BEHAVIOR_EVENT  → [Surface, Interest]
view   "20分钟讲透..."  BEHAVIOR_EVENT  → [Surface, Interest]
like   "20分钟讲透..."  ENGAGEMENT_EVENT → [Interest, Surface]
coin   "20分钟讲透..."  ENGAGEMENT_EVENT → [Interest, Surface]
view   "中东各方势力..."  BEHAVIOR_EVENT  → [Surface, Interest]
```

**5 条信号同时进入 Surface 和 Interest 缓冲区**。

### 第 1 天（几分钟后）：Surface 层立即更新

Surface 缓冲区已有 5 条信号（≥ 阈值 3），且距上次更新超过 5 分钟 → **触发 Surface 更新**。

```
update_surface() 纯计算：
  - 5 条事件中 search 占比高 → depth_preference: 0.70 → 0.73
  - 结果写入 surface.style.depth_preference
```

**Surface 层变化**：深度偏好微调。其他层（Role, Values, Core）不受影响。

### 第 1 天（10 分钟后）：Interest 层更新

Interest 缓冲区已有 5 条信号（≥ 阈值 3），且距上次更新超过 10 分钟 → **触发 Interest 更新**。

```
update_interest() 委托 PreferenceAnalyzer：
  - LLM 提取兴趣 → "国际时事" (category=知识, weight=0.65)
  - 按 category 分组为树：知识 → [国际时事]
  - 写入 interest.likes 和 preference.json
```

**Interest 层变化**：新增宽域"知识"下的窄域"国际时事"。

`soul_changelog.md` 记录：
```
### 2026-03-28T14:40:00
- [interest] 新增兴趣: 国际时事 (0.65)
  - 触发: 偏好分析
  - 证据: 分析了 5 条事件
```

### 第 5 天：小明在聊天中提到兴趣

小明和 Agent 聊天时说："我最近确实想多了解下国际形势。"

对话被 `DialogueInsightAnalyzer` 提取为 insight candidate：

```json
{
  "kind": "goal",
  "content": "想更系统地理解国际局势",
  "confidence": 0.85,
  "occurrences": 1
}
```

只出现了 1 次，**还不够 ≥ 2 的门槛**。候选保留在 `insight_candidates.json`，不投递到 pipeline。

### 第 8 天：候选达标 → Interest 层再次更新

小明在另一次聊天中又提到国际局势。同一个 candidate 的 occurrences 变为 2，且 confidence ≥ 0.8 → **达标！**

候选转为 `DIALOGUE_INSIGHT` 信号（kind=interest），投递到 Interest 缓冲区：

```
pipeline.ingest(signal_type=DIALOGUE_INSIGHT, kind="interest")
  → Interest 缓冲区收到信号
  → 加上这几天积累的其他行为信号，总数 ≥ 3
  → 触发 Interest 更新
  → "国际时事" 权重从 0.65 提升到 0.82
```

同时，因为 kind=goal 的候选也达标了，另一条信号投递到 Role 缓冲区。但 Role 层要求 5 条信号且间隔 24 小时——**暂不触发，继续积累**。

### 第 15 天：Values 层收到足够信号

过去两周，小明在多次对话中表达了类似的深层动机。`DIALOGUE_INSIGHT` 中 kind=value 的信号在 Values 缓冲区积累到了 5 条，距上次更新也超过了 24 小时。

```
update_values() LLM delta：
  LLM 收到：当前 values=["真实", "逻辑严谨"] + 近期证据
  LLM 返回：{ "add_values": ["信息密度"], "remove_values": [], "no_change": false }
  → values 列表新增 "信息密度"，其他不动
```

**Values 层变化**：新增一个价值观。

因为 Values 层发生了实际变化 → **触发 Portrait 重生成**：

```
regenerate_portrait()：
  ProfileBuilder.build() → 生成新的 personality_portrait
  → 只覆盖 portrait 文本，不动各层结构化数据
```

**之前的画像**：
> 这是一个对科技和前沿技术有热情的人，喜欢深度内容...

**重建后的画像**：
> 这是一个会主动追问复杂事件底层逻辑的人。他不满足于知道"发生了什么"，更想理解"为什么会这样"。偏好高信息密度、能把因果链讲透的内容。

注意：`core_traits` 和 `deep_needs` **没有被重写**——因为 Core 层没有达到更新阈值（需要 8 条信号 + 48 小时间隔）。只有 Portrait 文本刷新了。

### 第 16 天：推荐变化

Discovery Engine 使用更新后的核心记忆生成搜索关键词，SearchStrategy 产出了新的候选内容：

```
"国际关系底层逻辑" → 找到 3 条深度解读视频
"地缘政治 纪录片"  → 找到 2 条长纪录片
```

Recommendation Engine 排序后推荐给小明，并附带解释：

> 你最近一直在找能把国际局势讲透的内容，这条视频正好从历史源头梳理了中东问题的因果链。

小明点击观看 → 新的 view 事件写入 → 新信号进入 pipeline → 循环继续。

### 时间线总结

```
Day 1   ██░░░░░░░░  Surface: depth_preference 微调 (纯计算, 5分钟内)
Day 1   ███░░░░░░░  Interest: 新增"国际时事"(0.65) (LLM, 10分钟内)
Day 5   ███░░░░░░░  聊天候选: 出现 1 次，暂不投递 pipeline
Day 8   █████░░░░░  Interest: 权重提升到 0.82 (候选达标 → 信号投递)
Day 8   █████░░░░░  Role 缓冲区: 收到信号，但 ≤5 条，继续积累
Day 15  ████████░░  Values: 新增"信息密度" (LLM delta, 24h 间隔达标)
Day 15  ████████░░  Portrait: 综合叙事重生成 (Values 变化触发)
Day 16  █████████░  推荐内容随之变化
Day ??  ██████████  Core 层: 需要 48h + 8 信号, 短期内不会触发
```

**和旧架构的关键区别**：

- Surface 层在**第 1 天就更新了**（以前要等全量重建才会改 style）
- Interest 层 **10 分钟内响应**（以前要等反馈批量积累）
- Core 层**没有被动摇**（以前全量重建可能连 core_traits 都改了）
- Values 层经过**两周积累**才谨慎新增一项（以前要么不动，要么全部重写）
- Portrait 只在**深层真正变化时才重生成**，不是每次偏好微调都重建

## 自我编辑

Agent 可以自主：
- 决定什么信息提升到更高层（通过置信度和出现次数阈值）
- 合并重复或过时的记忆（兴趣按 `name+category` 去重）
- 标记需要验证的假设（洞察层的 confidence 字段）
- 在反馈后调整各层内容（feedback 事件触发偏好重分析）
- 自然遗忘不再活跃的兴趣（时间衰减 `0.9^weeks`）

## 自迭代优化系统

画像模块的 prompt、阈值、权重公式等参数不靠人工反复调试，而是通过两种自迭代循环持续优化。整套系统借鉴了**随机梯度下降（SGD）**和**强化学习（RL）**的核心思想。

### 两个任务 × 两种模式

画像系统有两个核心任务，每个任务都支持两种优化模式：

| | 任务 1: Init（从零生成） | 任务 2: Update（增量更新） |
|---|---|---|
| **输入** | 浏览历史 + 收藏 + 关注 | 行为事件流（view/like/feedback...） |
| **流程** | `build_initial_profile()` | `pipeline.ingest()` → 分层更新 |
| **输出** | 完整五层画像 | 特定层的增量变化 |
| **评估重点** | 画像整体是否准确 | 变化是否反映了事件的真实信号 |

```
        ┌──────────────────┬──────────────────────┐
        │  模式 1 人工评测   │  模式 2 全自动优化     │
┌───────┼──────────────────┼──────────────────────┤
│ Init  │ run_init_eval.py │ run_auto_optimize.py │
│从零生成│ 真实数据→画像→人评 │ 模拟persona→init→评估│
├───────┼──────────────────┼──────────────────────┤
│Update │run_update_eval.py│run_update_auto_      │
│增量更新│ 真实事件→变化→人评 │optimize.py           │
│       │                  │ 模拟演变→pipeline→评估│
└───────┴──────────────────┴──────────────────────┘
```

#### Init 任务的两种模式

```
模式 1 (人工): 真实 B站 数据 → build_initial_profile → 用户逐层评测
模式 2 (自动): 模拟 persona → 模拟事件 → init pipeline → Eval Agent 评分 → Optimizer
```

#### Update 任务的两种模式

```
模式 1 (人工): 已有画像 + 最近事件 → pipeline 增量更新 → 用户评测每层变化
模式 2 (自动):
  ① 生成"初始画像"和"演变后画像"两个版本
     （例如：用户从"只看科技"演变为"开始关注国际时事"）
  ② 生成两组事件：init_events(初始行为) + update_events(体现变化的行为)
  ③ init_events → build_initial_profile → 基线画像
  ④ update_events → pipeline.ingest + flush → 最终画像
  ⑤ Eval Agent 对比"最终画像"vs"演变后 ground truth"
  ⑥ Optimizer 针对 pipeline 的分层更新逻辑提出修改
```

Update 任务的自动优化比 Init 更复杂，因为它测试的是 **pipeline 能否正确识别"哪一层应该变"**——比如用户开始关注国际新闻，Interest 层应该变但 Core 层不应该变。

### 共享评估框架：ProfileEvaluator

两种模式共用同一套评估核心，使用 **Claude Agent SDK** 做语义级评分（不是字符串匹配）：

```python
@dataclass
class FieldScore:
    layer: str        # "core" / "interest" / "values" / ...
    field: str        # "core_traits" / "mbti.type" / "likes" / ...
    score: float      # 0.0-1.0
    deviation: str    # "预测的'底层逻辑导向'与期望的'分析型思维'语义重叠度 0.65"
    severity: str     # "correct" / "partial" / "wrong" / "missing"
```

各字段类型的评分方式：

| 字段类型 | 评分方式 | 举例 |
|---------|---------|------|
| core_traits (列表) | Claude 语义相似度 | "底层逻辑导向" vs "分析型思维" → 0.65 (部分重叠) |
| values (列表) | Claude 语义相似度 | "真实性" vs "追求真相" → 0.85 (高度重叠) |
| portrait (文本) | Claude 语义覆盖率 | 核心洞察是否一致 → 0.7 |
| mbti.type (字符串) | 字母匹配率 | "INTJ" vs "INTP" → 0.75 (3/4 匹配) |
| mbti.dimensions (数值) | 1 - MAE | I:0.85 vs I:0.80 → 0.95 |
| likes (兴趣树) | domain 覆盖率 + specifics 覆盖率 | 结构化匹配 |
| depth_preference (数值) | 1 - abs(diff) | 0.8 vs 0.7 → 0.9 |

### 模式 1 详解：人工评测循环

#### Init 任务的人工评测

```bash
python scripts/run_init_eval.py
```

1. **数据拉取**：从 B 站 API 获取真实浏览历史(500条) + 收藏夹 + 关注列表
2. **画像生成**：走完整 init 流程（analyze_events → build_initial_profile）
3. **逐层展示**：以洋葱模型五层结构展示画像
4. **用户评测**：用户对每层给出 ✅准确 / ⚠️部分准确 / ❌不准确
5. **反馈修正**：不准确的字段直接修正，记录到 `soul_changelog.md`
6. **循环迭代**：修正后重新生成，再次评测

**实际运行示例：**

```
━━━ 兴趣层 Interest ━━━
  讨厌:
    ✗ 二次元手游宣传曲/EP    ← 用户反馈：❌ 这个不对
    ✗ 低质标题党              ← ✅
    ✗ 无脑黑内容              ← ✅

用户反馈: "二次元手游宣传曲/EP 这个 dislike 不太对"

系统操作:
  1. 从 interest.dislikes 中移除该项
  2. 同步更新 preference.json
  3. 写入 soul_changelog.md:
     ### 2026-03-30
     - [Interest] 移除不准确的 dislike: 二次元手游宣传曲/EP
       - 触发: 人工评测反馈
       - 证据: 用户确认该 dislike 不准确
```

#### Update 任务的人工评测

```bash
python scripts/run_update_eval.py --events 50
```

1. **加载现有画像**：记录每一层的当前值（快照 before）
2. **获取最近事件**：从数据库取最近 N 条真实行为事件
3. **Pipeline 更新**：`pipeline.ingest_batch() + flush()` → 分层更新
4. **逐层 diff 展示**：对比 before vs after，高亮每一层的变化
5. **用户评测**：用户判断变化是否合理

**实际运行示例：**

```
━━━ 表层 Surface ━━━  ⚡ 有变化
  depth_preference: 0.90 → 0.93       ← 最近看了更多深度内容

━━━ 兴趣层 Interest ━━━  ⚡ 有变化
  likes: 新增 domain "国际时事" (0.65)
  likes: "科技" 权重 0.95 → 0.99       ← AI 相关视频增多

━━━ 核心层 Core ━━━  (无变化)           ← pipeline 正确保护了核心层
━━━ 角色层 Role ━━━  (无变化)
━━━ 价值层 Values ━━━  (无变化)

用户评测:
  Surface 变化 ✅ 合理 — 确实最近看深度内容更多了
  Interest 变化 ⚠️ 部分合理 — "国际时事"对，但权重应该更高
  Core 不变 ✅ 正确 — 核心特质不应该因为几天的事件就改
```

**和 Init 评测的区别**：Init 评测的是"画像整体准不准"，Update 评测的是"变化合不合理"——特别是 pipeline 是否正确判断了"哪一层应该变、哪一层不应该变"。

#### 人工评测的价值

人工评测能发现系统自己无法发现的两类问题：

1. **误判**："二次元手游宣传曲"被错误地标记为 dislike——系统无法区分"不喜欢"和"没看到"
2. **不该变的变了**：只看了两天国际新闻，Core 层的 `core_traits` 就被改了——这应该是 Interest 层的事

### 模式 2 详解：全自动 SGD/RL 优化循环

模式 2 对 Init 和 Update 两个任务使用不同的数据生成和评估策略，但共享同一套 SGD/RL 循环框架。

#### 核心思想

| 概念 | SGD/RL 原型 | 画像优化映射 |
|------|-----------|-------------|
| 参数 θ | 神经网络权重 | prompt 模板、阈值、衰减系数 |
| 损失函数 L | MSE / CrossEntropy | 逐层逐字段偏差的加权总分 |
| 梯度 ∂L/∂θ | 反向传播 | EvalReport 归因："Interest recall 低 → prompt 没要求提取窄域" |
| 学习率 α | 步长 | 每轮最多改 2 处 prompt（小步更新） |
| Mini-batch | 随机采样训练数据 | 每轮 K 个多样化模拟用户（覆盖不同 MBTI/兴趣/深度） |
| ε-greedy | 探索 vs 利用 | 80% 修复 worst field（利用），20% 随机扰动（探索） |
| Early stopping | 验证集不提升则停 | 连续 3 轮分数不提升则停止 |
| Validation | 训练集/验证集分离 | batch 末尾留 2 个 persona 做验证，决定 commit/rollback |

#### 流程

```
python scripts/run_auto_optimize.py --rounds 5 --batch 3
```

每个 Epoch 的详细步骤：

```
Step 1: Sample mini-batch
  从 6 种 MBTI × 2 种兴趣广度 × 3 种深度 的组合中随机采样 K 个
  例如: [{mbti: ISTP, depth: moderate, breadth: specialist},
         {mbti: ENFP, depth: casual, breadth: generalist}]

Step 2: Forward pass (对每个 persona)
  2a. Persona Agent (Claude) 生成 ground truth 画像
      → 完整的 OnionProfile，包含所有五层数据
  2b. Event Agent (Claude) 生成 50-100 条模拟行为事件
      → view/search/like/favorite，标题和 UP 主符合 persona 特征
  2c. Init Pipeline 处理模拟事件
      → analyze_events() + build_initial_profile() → 预测画像
  2d. Eval Agent (Claude) 逐层逐字段语义评分
      → EvalReport (overall_score + per-field scores)

Step 3: Compute loss
  train_mean = average(all reports' overall_score)

Step 4: Backward pass (归因 + 决策)
  if random() < 0.2:
    EXPLORE: 随机扰动一个参数
  else:
    EXPLOIT: 收集 batch 中所有 worst fields
             → 归因到具体 prompt / 阈值
             → Optimizer Agent 读代码 + 提出 diff 级修改

Step 5: Validation
  用修改后的 prompt 重跑 validation personas
  val_score > best_score → COMMIT（保留修改）
  val_score <= best_score → ROLLBACK（恢复原文件）

Step 6: Early stopping
  连续 3 轮 ROLLBACK → 停止
  val_score >= 0.85 → 达标停止
```

#### 实际运行示例

```
═══ 训练报告 ═══

Epoch   Train   Changed                                  Action
  1     0.270   prompt 增加"证据不足时留空"规则            EXPLOIT ✓
  2     0.255   prompt 增加 MBTI dimensions 必填声明       EXPLOIT ✗ (rollback)

Best val score: 0.270 at epoch 1
```

Epoch 1 的详细过程：

```
[1.1] Persona: ISTP specialist moderate
  GT traits:  ["务实主义者", "沉默型观察者", "动手能力强"]
  预测 traits: ["底层逻辑偏执狂", "极客实用主义", "系统化思维"]
  语义评分: 0.30 (核心概念有重叠但措辞差异大)

[1.2] Persona: ENFP casual generalist
  GT traits:  ["好奇心爆棚", "社交型弹幕选手", "三分钟热度"]
  预测 traits: ["浪漫理性主义", "结构化好奇心", "高门槛审美"]
  语义评分: 0.07 (几乎完全不匹配——ENFP 被错误预测为 INTJ 风格)

Train mean: 0.270

Optimizer 归因:
  worst: values.values (0.00) — GT 为空但预测凭空生成了内容
  worst: core.mbti.dimensions (0.00) — 缺少维度数据
  → 根因: prompt rules 没有"证据不足时留空"的规则
  → 修改: rules 第 4 条增加"如果证据不足，该字段返回空数组或空字符串"

Validation: 因为是首轮，直接 ACCEPT (0.270 > 0.0)
```

Epoch 2 的 ROLLBACK 示例：

```
[2.1] Persona: ENTP specialist moderate
  Score: 0.249

[2.2] Persona: ISTP specialist moderate
  Score: 0.260

Train mean: 0.255 <= best 0.270 → ROLLBACK
  → 恢复 prompt 文件到 Epoch 1 的版本
  → patience = 1/3
```

#### Update 任务的自动优化

```bash
python scripts/run_update_auto_optimize.py --rounds 3 --batch 2
```

Update 的自动优化比 Init 更复杂——它要测试 pipeline 的**分层更新能力**，而不只是一次性生成质量。

**关键区别：生成"演变型" ground truth**

Init 只需要一个 persona。Update 需要**两个版本**：初始画像 + 演变后画像，以及对应的两组事件。

```
Persona Agent 生成:
  initial_persona:  ISTP, 专注游戏和科技
  evolved_persona:  ISTP, 游戏和科技 + 新增国际时事兴趣
  change_summary:  "用户开始关注国际新闻，Interest 层新增宽域"

Event Agent 生成:
  init_events (30条):   view "杀戮尖塔攻略", search "AI模型对比"...
  update_events (20条): view "中东局势深度解读", search "地缘政治"...
```

**评估重点不同：** Init 评的是"最终画像整体准不准"，Update 评的是"演变后的画像是否正确反映了变化"：

```
期望:
  Interest: 应该新增"国际时事"宽域 ✓
  Core:     不应该变（只是兴趣扩展，不是人格改变）✓
  Role:     不应该变 ✓
  Surface:  depth_preference 可能微调 ✓

典型失败模式（Update 特有）:
  - Interest 没变: pipeline 没检测到新兴趣信号 → 评分低
  - Core 也变了:   pipeline 误判为核心层变化 → 评分低
  - 变化幅度过大:  20 条事件就把权重从 0 拉到 0.9 → 不合理
```

**四种兴趣变化模式的覆盖**：

mini-batch 中的 persona 覆盖四种典型兴趣演变：

| 变化类型 | 说明 | 评估重点 |
|---------|------|---------|
| `new_interest` | 用户开始关注全新领域 | Interest 层正确新增宽域 |
| `deepen` | 用户对已有兴趣大幅加深 | 窄域增加，权重提升 |
| `abandon` | 用户放弃之前很喜欢的领域 | 权重下降，而非直接删除 |
| `life_change` | 用户生活阶段发生变化 | Role 层更新，Core 层基本不变 |

#### Optimizer Agent 的工作方式

Optimizer 是整个系统中最强大的 Agent——它使用 Claude Agent SDK，拥有 **Read/Grep/Glob** 工具权限，可以自主阅读项目代码：

```
Optimizer 收到:
  评估报告: core.core_traits score=0.07, deviation="ENFP 被错误识别为 INTJ 风格"
  归因: core.core_traits → soul_profile_prompt

Optimizer 操作:
  1. Read src/openbiliclaw/llm/prompts.py  ← 读取当前 prompt
  2. 分析 build_soul_profile_prompt() 的 rules 和 output_schema
  3. 定位问题: rules 中没有"根据 MBTI 调整输出风格"的指引
  4. 生成 diff:
     old_text: "core_traits 控制在 3 到 6 条"
     new_text: "core_traits 控制在 3 到 6 条，描述风格应匹配推断出的 MBTI 类型"
     reason: "当前 prompt 无论什么 MBTI 都输出 INTJ 风格的特质描述"
```

#### 参数空间

优化循环可以调整两类参数：

**连续参数（数值扰动）：**
- `interest_decay_factor`: 0.9 ± 0.03（兴趣时间衰减系数）
- `candidate_confidence_threshold`: 0.8 ± 0.05（聊天候选置信度门槛）
- `candidate_occurrence_threshold`: 2 ± 1（聊天候选出现次数门槛）

**离散参数（LLM 改写）：**
- `build_preference_analysis_prompt()` 中的 rules 和 output_schema
- `build_soul_profile_prompt()` 中的 rules、output_schema 和 mbti_rules
- `build_awareness_prompt()` 和 `build_insight_prompt()` 中的规则

### 两种模式的适用场景

| | 模式 1（人工评测） | 模式 2（全自动） |
|---|---|---|
| **适合** | 验证真实画像质量、发现系统盲区 | 批量优化 prompt、调参、回归测试 |
| **优点** | 能发现数据层面无法区分的误判 | 无需真实用户数据，可大量重复 |
| **局限** | 需要人参与，不可规模化 | 模拟数据可能不完全代表真实场景 |
| **频率** | 每次 prompt 大改后跑一轮 | 日常持续运行 |
| **成本** | 人的时间 | LLM API 调用费用 |

### 两个任务 × 两种模式如何协同

```
                  Init 任务                        Update 任务
             ┌─────────────────────┐         ┌──────────────────────┐
  模式 2     │ 自动跑 10 轮          │         │ 自动跑 10 轮           │
  (自动)     │ → 发现 Core 层分数低   │         │ → 发现 Interest 更新    │
             │ → prompt 缺少留空规则  │         │   检测不到新兴趣信号    │
             │ → 自动修改 prompt     │         │ → 调整 pipeline 阈值   │
             └─────────┬───────────┘         └──────────┬───────────┘
                       │                                │
                       ▼                                ▼
  模式 1     ┌─────────────────────┐         ┌──────────────────────┐
  (人工)     │ 用户评测真实画像      │         │ 用户评测增量变化       │
             │ → "手游宣传曲"误标    │         │ → "只看了2天国际新闻   │
             │   为 dislike          │         │   Core 层就被改了"     │
             │ → 模式 2 无法发现     │         │ → pipeline 保护逻辑    │
             │ → 人工修正           │         │   需要加强             │
             └─────────────────────┘         └──────────────────────┘

协同逻辑:
  1. 模式 2 先跑: 批量发现系统性问题（prompt 缺陷、阈值偏差）
  2. 模式 1 再跑: 用真实数据验证，发现模拟数据覆盖不到的边界情况
  3. 人工反馈反哺模式 2: 发现的边界情况变成新的 persona 约束条件
```

| 问题类型 | Init 发现 | Update 发现 |
|---------|----------|------------|
| prompt 缺少必要规则 | ✅ 模式 2 | ✅ 模式 2 |
| 阈值设置不合理 | — | ✅ 模式 2 |
| 数据误判（假阴/假阳） | ✅ 模式 1 | ✅ 模式 1 |
| 分层保护失效 | — | ✅ 模式 1 + 2 |
| 边界用户类型覆盖不足 | ✅ 模式 1 反馈 → 模式 2 | ✅ 模式 1 反馈 → 模式 2 |

### 评估日志与运行追溯

每次评估运行会在 `data/eval/runs/` 下生成一个完整的日志目录，包含所有原始输入、LLM prompt、LLM 返回、中间结果和最终评分。目的是让每一步都可追溯、可复现。

#### Init 人工评测日志

```
data/eval/runs/init_human_20260330T120000/
├── 01_input/
│   ├── history.json              # 500 条原始浏览历史（B站 API 返回）
│   ├── favorites.json            # 收藏夹每个文件夹的内容
│   └── following.json            # 关注列表（250人）
├── 02_preference/
│   ├── prompt.txt                # 偏好分析 LLM prompt (system + user 完整文本)
│   ├── events_input.json         # 输入事件采样（前 50 条）
│   └── result.json               # PreferenceAnalyzer 的输出结果
├── 03_profile/
│   ├── prompt.txt                # 画像生成 LLM prompt (含 output_schema 和 rules)
│   └── result.json               # 生成的完整 OnionProfile
├── 04_display/
│   └── output.txt                # 展示给用户的逐层文本
└── summary.json                  # 运行元数据: 数据源数量、画像摘要、时间戳
```

**用途**：当画像不准时，打开 `02_preference/prompt.txt` 看 LLM 收到了什么 prompt，再看 `result.json` 看 LLM 返回了什么——精确定位是 prompt 问题还是数据问题。

#### Init 自动优化日志

```
data/eval/runs/auto_init_20260330T180000/
├── epoch_001/
│   ├── persona_01/
│   │   ├── ground_truth.json     # Persona Agent 生成的 ground truth OnionProfile
│   │   ├── persona_prompt.txt    # Persona Agent 的原始 LLM 返回
│   │   ├── simulated_events.json # Event Agent 生成的 50 条模拟事件
│   │   ├── predicted.json        # Pipeline 生成的预测画像
│   │   └── eval_report.json      # Eval Agent 的逐层逐字段评分
│   ├── persona_02/
│   │   └── ...                   # 同上结构
│   └── optimizer/
│       ├── eval_input.json       # 给 Optimizer Agent 的归因报告（worst fields）
│       └── optimizer_output.json # Optimizer 建议的 prompt 修改 diff
├── epoch_002/
│   └── ...
└── summary.json                  # best_score、epochs_run、完成时间
```

**用途**：当 Epoch 1 的 score 是 0.27 但 Epoch 2 回滚了，打开 `epoch_001/optimizer/optimizer_output.json` 看 Optimizer 具体建议了什么修改，再对比 `epoch_002/` 看为什么分数反而降了。

#### Update 人工评测日志

```
data/eval/runs/update_human_20260330T150000/
├── 01_before/
│   └── profile_before.json       # 更新前的画像快照（完整 OnionProfile）
├── 02_events/
│   └── recent_events.json        # 输入的最近 N 条真实事件
├── 03_after/
│   ├── profile_after.json        # 更新后的画像
│   └── pipeline_result.json      # Pipeline 执行详情: 信号数、缓冲层、各层是否更新
└── summary.json                  # events_count、any_change、layers_updated
```

**用途**：看 `pipeline_result.json` 确认 pipeline 的分层决策是否正确——Surface 变了 0.03 是因为什么信号？Interest 新增了一个 domain 但 Core 没变，这个判断对不对？

#### Update 自动优化日志

```
data/eval/runs/auto_update_20260330T190000/
├── epoch_001/
│   ├── persona_01/
│   │   ├── initial_gt.json       # 初始 ground truth（演变前）
│   │   ├── evolved_gt.json       # 演变后 ground truth
│   │   ├── change_summary.txt    # "用户开始关注国际时事"
│   │   ├── init_events.json      # 30 条初始行为事件
│   │   ├── update_events.json    # 20 条增量行为事件（体现变化）
│   │   ├── predicted.json        # Pipeline 生成的最终画像
│   │   └── eval_report.json      # 预测 vs 演变后 GT 的评分
│   └── optimizer/
│       ├── eval_input.json
│       └── optimizer_output.json
└── summary.json
```

**用途**：查看 `persona_01/change_summary.txt` 是"用户放弃了游戏兴趣"，但 `eval_report.json` 显示 Interest 层评分低——打开 `update_events.json` 看模拟的"放弃"事件是否合理，再看 `predicted.json` 中游戏权重是否真的下降了。

#### 汇总报告

除了 runs 目录，`data/eval/reports/` 存放汇总级别的报告：

```
data/eval/reports/
├── real_eval_20260330T025322.json       # 真实数据评估的汇总报告
├── auto_optimize_20260330T182440.json   # Init 自动优化的训练报告
└── auto_update_optimize_*.json          # Update 自动优化的训练报告
```

## 为什么不用传统的推荐系统架构

| 传统推荐系统 | OpenBiliClaw 记忆系统 |
|-------------|---------------------|
| 协同过滤：和你相似的人喜欢什么 | 深度理解：你为什么喜欢，你是什么样的人 |
| 标签向量：用户 = 一组兴趣权重 | 分层画像：从行为事实到人格理解 |
| 实时更新：每次点击立刻影响推荐 | 渐进更新：多重防抖，画像保持稳定 |
| 解释性弱："因为你看过类似视频" | 可解释："因为你一直在找能把因果链讲透的内容" |
| 系统视角：优化点击率 | 朋友视角：理解你、帮你发现好东西 |
