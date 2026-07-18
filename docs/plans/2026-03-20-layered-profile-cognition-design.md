# Layered Profile Cognition Design

## Problem

当前画像里的「这会儿的你」经常更像把高权重兴趣和作品名串成一段顺滑文案，而不是对用户形成多层次认知。用户能看到的结果是：

- `personality_portrait` 里堆了很多题材、UP 主和作品例子
- `top_interests` 又把这些兴趣再展示一次
- 最终看起来像“偏好标签润色稿”，不像“我为什么会被这些内容吸引”的总结

## Goal

把画像生成从“兴趣堆砌”收口成三层非临床认知结构：

1. **认知风格**：这个人怎么处理信息、怎么形成判断
2. **内在驱动力**：他在内容里长期在找什么
3. **当前阶段**：最近更像在经历什么状态变化

兴趣 topic 继续保留，但降级为证据层，不再主导画像正文。

## Guardrails

- 只借用**非临床**心理学框架做 prompt 约束，不输出理论术语，不做人格测评或病理化判断
- 深层需求继续使用保守需求层表达，优先贴近 `自主性 / 胜任感 / 连接感 / 秩序感 / 审美沉浸`
- 不引入 MBTI、九型、病理标签，也不把任何理论名直接暴露给用户
- 兴趣例子最多少量出现，只能作为证据，不能占据主体

## Recommended Approach

### A. 扩充 SoulProfile 的结构化认知层

在现有 `personality_portrait / core_traits / deep_needs` 基础上，新增：

- `cognitive_style: list[str]`
- `motivational_drivers: list[str]`
- `current_phase: str`

这样可以让画像既有一段自然语言总结，也有可展示、可约束的中层结构。

### B. 重写画像生成 prompt

Prompt 需要明确要求：

- 先判断“这个人怎么理解世界”，再判断“他在内容里长期在找什么”，最后判断“最近处于什么阶段”
- `personality_portrait` 主要写认知方式、驱动力和阶段变化
- 兴趣、作品名、UP 主最多只举 1 到 2 个例子
- 禁止把多个 topic 平铺成一长段“反差感”叙事

同时把 prompt 里的 schema 升级到包含上述三个新字段。

### C. 画像生成输入不再只靠 history + preference

当前 `ProfileBuilder` 只接收 `history_summary + preference_summary`，这会天然偏向 topic noun。

改为同时接收：

- `history_summary`
- `preference_summary`
- `recent_awareness`
- `active_insights`

其中 `recent_awareness` 和 `active_insights` 负责把“最近在变什么”“为什么会这样”喂给画像层，减少 topic list 主导。

### D. popup 同步展示新的认知层

如果只改后端 schema，不改 popup，用户仍然只会看到：

- 一段画像 prose
- `core_traits`
- `deep_needs`
- `top_interests`

所以前端同步新增两组轻量信息：

- `你怎么处理信息` -> `cognitive_style`
- `这阵子更像在经历什么` -> `current_phase`

`motivational_drivers` 可合并进“你更在意什么”一起展示，或者单独展示为“你在内容里长期在找什么”。本次推荐单独展示，避免与 `deep_needs` 混淆。

## Alternatives Considered

### Option 1: 只改 portrait prompt，不改 schema

优点：

- 改动最小

缺点：

- 没有结构化约束，模型很容易回退到 topic 堆砌
- popup 仍然只能显示旧分组

### Option 2: 只在 popup 侧重排现有字段

优点：

- 前端可见变化快

缺点：

- 后端画像本身仍然是旧质量
- 只是换个摆法，不是认知升级

### Option 3: 新增中层字段 + prompt 重构 + popup 接入

优点：

- 能同时约束生成质量和最终呈现层次
- 结构上更接近“理解这个人”而不是“记住他看了什么”

缺点：

- 需要同步更新后端 schema、API、popup 和测试

**Recommendation:** 采用 Option 3。

## Testing Strategy

- `tests/test_profile_builder.py`
  - 锁定新 schema 字段解析
  - 锁定 prompt 中对“兴趣只能作证据、不要堆砌 topic”的约束
  - 锁定 awareness / insights 被注入
- `tests/test_soul_engine.py`
  - 锁定初始画像和重建画像会携带新的结构化认知字段
- `tests/test_api_app.py`
  - 锁定 `/api/profile-summary` 返回新增字段
- `extension/tests/popup-helpers.test.ts`
  - 锁定 profile summary 归一化逻辑
- popup 手动验证
  - 画像 tab 里不再只有一段 prose + 兴趣 chips，而是能直接看到“怎么处理信息 / 这阵子更像什么状态”

