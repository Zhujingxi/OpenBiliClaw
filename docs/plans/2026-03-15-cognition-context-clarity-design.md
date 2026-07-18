# 画像认知卡片上下文与展开状态澄清设计

## 背景

当前 popup「我的画像」里的“阿B 最近新记住了什么”已经支持：

- 结构化认知卡片
- 默认收起、展开看影响/原因/依据
- 历史分页加载

但现在默认态仍然有两个明显问题：

1. 只有一句结论，没有说明这是对哪条内容、哪几条内容，或哪轮聊天形成的判断
2. 用户需要自己猜“这张卡片能不能点开”，因为可展开和不可展开的视觉差异不够明确

这会直接影响这块信息的可信度和可读性。像“方向对，但我想看更深一点”“像‘讲透城市与建筑’这种内容你大概率会划走”这类结论，如果不告诉用户它对应哪条内容或哪组内容，就会显得像悬空判断。

## 目标

- 让每张认知卡片默认态就能回答“这是对什么形成的判断”
- 明确区分“可展开”和“仅结论”两种卡片，不让用户靠猜
- 保持现有分页、展开详情和后端认知链的主体结构不变
- 继续优先使用后端生成的可信上下文，不让前端脑补

## 非目标

- 不改动认知卡片的分页协议
- 不改动展开后的三段详情结构
- 不新增完整行为时间线或内容 diff
- 不在本次引入新的 LLM 推理链

## 方案概述

### 1. 默认态从“结论 + 泛来源”升级成“结论 + 上下文 + 状态”

每张认知卡片默认态统一展示三层：

- `结论`
- `上下文`
- `状态提示`

示例：

- `阿B 刚记下了：方向对，但我想看更深一点。`
  `来自：《某条内容》`
  `展开`

- `阿B 记住了：像“讲透城市与建筑”这种内容你大概率会划走。`
  `基于最近内容：《A》 / 《B》`
  `展开`

- `阿B 觉得你最近更在意内容有没有把事情讲透。`
  `基于最近几条相关内容`
  `仅结论`

### 2. 用独立上下文字段替代“画像观察”兜底

现有 `source` 只能说明来源大类，例如 `feedback / chat / profile_refresh`，不足以承担“这是对哪个内容说的”这个问题。

因此认知卡片新增：

- `context_line`: 默认态直接展示的上下文短句
- `source_label`: 来源标签的人类可读版本，例如 `推荐反馈 / 聊天 / 聚合观察`
- `expand_hint`: `expandable` 或 `summary_only`

这样前端不再拿 `source || "画像观察"` 充当上下文，而是明确分开：

- `source_label` 说明判断类型
- `context_line` 说明判断对象

### 3. 上下文生成规则按信号类型区分

#### 单条反馈

- 有标题时：`来自：《标题》`
- 没标题时：`来自：这次推荐反馈`

#### 单条聊天信号

- 能从 evidence 或 content 提取对象时：`来自最近这轮聊天：<对象>`
- 提取不到对象时：`来自最近这轮聊天`

#### 聚合判断

- 优先列出 1-3 个代表性内容标题或主题
- 实在拿不到可信对象时，回退为：`基于最近几条相关内容`

这个回退是刻意保守的，承认“它是聚合判断，但当前没有足够精确的对象名”。

### 4. 展开能力由后端显式声明，前端只负责视觉区分

前端不再只根据 `impact / reasoning / evidence` 是否为空来推断视觉提示，而是以后端给出的 `expand_hint` 为准：

- `expandable`: 渲染为按钮，右侧显示 `展开 / 收起`
- `summary_only`: 渲染为静态卡片，右侧显示 `仅结论`

兼容旧数据时，前端仍可沿用老逻辑兜底：

- 有详情字段则按 `expandable`
- 没详情字段则按 `summary_only`

## 数据契约

目标结构示意：

```json
{
  "summary": "阿B 刚记下了：方向对，但我想看更深一点。",
  "context_line": "来自：《中东局势拆解》",
  "source_label": "推荐反馈",
  "expand_hint": "expandable",
  "impact": "画像里对这类方向的偏好会更明确，后面会更容易继续往深一点补。",
  "reasoning": "这属于单条明确反馈，先记作方向修正，不直接重写整张画像。",
  "evidence": "你评论《中东局势拆解》时说：方向对，但我想看更深一点。",
  "created_at": "2026-03-15T12:00:00"
}
```

聚合判断示意：

```json
{
  "summary": "阿B 记住了：像“讲透城市与建筑”这种内容你大概率会划走。",
  "context_line": "基于最近内容：《城市更新争议》 / 《现代建筑史拆解》",
  "source_label": "聚合观察",
  "expand_hint": "expandable"
}
```

保守回退示意：

```json
{
  "summary": "阿B 觉得你最近更在意内容有没有把事情讲透。",
  "context_line": "基于最近几条相关内容",
  "source_label": "聚合观察",
  "expand_hint": "summary_only"
}
```

## 前端展示规则

- 默认态顺序固定为：`summary` → `context_line` → `source_label + expand_hint`
- `expandable` 卡片保留按钮语义和展开箭头
- `summary_only` 卡片不渲染为按钮，避免假点击感
- `source_label` 不再单独占整行抢主信息，而作为 meta 的一部分出现

## 风险与取舍

### 风险 1：上下文写得太假或太像前端拼出来的

处理：

- `context_line` 由后端生成
- 拿不到可信对象时宁可写“基于最近几条相关内容”，也不伪造内容标题

### 风险 2：默认态信息变重

处理：

- 上下文始终限制为一行短句
- 来源和展开状态合并为紧凑 meta，不新增大块文案

### 风险 3：旧数据没有 context/expand hint

处理：

- 前端 helper 对缺失字段做回退
- 老卡片仍可显示，但会优先标为 `仅结论`

## 测试策略

- Soul: 验证单条反馈、聊天、聚合判断都会生成合适的 `context_line`
- API: 验证 `/api/profile-summary` 返回 `context_line / source_label / expand_hint`
- Popup helper: 验证新字段规范化和旧数据回退
- Popup UI: 验证默认态出现上下文行，且 `展开 / 收起 / 仅结论` 状态正确

## 影响模块

- `src/openbiliclaw/soul/engine.py`
- `src/openbiliclaw/api/models.py`
- `src/openbiliclaw/api/app.py`
- `extension/popup/popup-helpers.js`
- `extension/popup/popup.js`
- `extension/popup/popup.html`
- `tests/test_soul_engine.py`
- `tests/test_api_app.py`
- `extension/tests/popup-helpers.test.ts`
- `extension/tests/popup-copy.test.ts`
- `docs/modules/soul.md`
- `docs/modules/extension.md`
- `docs/changelog.md`
