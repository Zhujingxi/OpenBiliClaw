# 画像页认知变化展开卡片设计

## 背景

当前 popup「我的画像」里的“阿B 最近新记住了什么”最初只展示 `recent_cognition_updates: list[str]`。在结构化卡片方案落地后，用户已经能看到一小批可展开认知卡片，但仍然只能看到很少几条最近更新，缺少继续往下翻的历史上下文。

因此这个区块现在同时存在两类问题：

- 到底发生了什么
- 这对当前画像产生了什么影响
- 系统为什么会这么判断
- 这类变化最近到底是一次偶发，还是已经持续出现了一段时间

这让认知变化提示有“看见结果、看不见推理”的问题，可信度和可解释性都不够。

## 目标

- 将“阿B 最近新记住了什么”从短字符串列表升级成可展开卡片
- 默认只展示一句总结，保持画像页首屏紧凑
- 支持继续向下查看更多认知变化历史，而不是只停留在最近几条
- 展开后展示：
  - 这次发生了什么
  - 对画像的影响
  - 为什么这么判断
  - 支撑这次判断的具体证据
- 优先保证解释可信，不允许前端自行脑补推理

## 非目标

- 不做完整画像 diff 时间线
- 不做长篇报告式解释
- 不在本次引入新的 LLM 调用链路

## 方案概述

### 1. 数据从字符串升级为结构化认知卡片

现有 `cognition update` 继续保留 `summary`，并增加：

- `impact`: 这条更新会如何改变用户画像
- `reasoning`: 系统对这次变化的判断和思考
- `evidence`: 触发这次变化的明确证据
- `source`: 来源，如 `chat` / `feedback` / `refresh`
- `created_at`: 生成时间

`/api/profile-summary` 不再只返回字符串数组，而是返回结构化卡片分页结果。首屏默认返回 3 条，并携带继续拉取历史所需的分页信息。

### 2. 推理由后端生成，前端只做展示

前端不拆文案、不拼推理。后端在生成 `cognition update` 时同时写好：

- `summary`
- `impact`
- `reasoning`
- `evidence`

这样“结论 / 影响 / 原因 / 证据”来自同一次判断，口径一致，避免 UI 层猜测。

### 3. 展示采用可展开卡片，并支持继续查看历史

在 popup 画像页里：

- 默认展示卡片摘要 `summary`
- 展示时间/来源轻标签
- 点击卡片后展开详情：
- `这对画像的影响`
- `为什么这么判断`
- `这次依据`
- 默认全部收起
- 同一时刻只展开一张
- 首屏先展示 3 条
- 滚动到列表底部时自动拉取下一页
- 列表底部保留“加载更多”按钮作为兜底
- 新页追加后保留当前已展开卡片状态

## 数据契约

目标 API 结构示意：

```json
{
  "initialized": true,
  "personality_portrait": "...",
  "core_traits": ["理性", "好奇"],
  "deep_needs": ["理解世界"],
  "top_interests": ["地缘政治", "AI"],
  "recent_cognition_updates": [
    {
      "summary": "阿B 现在更确定，你最近会主动吃“地缘政治拆解”这一口。",
      "impact": "画像里“理性分析、世界运行机制”这条偏好会更靠前。",
      "reasoning": "这更像是稳定兴趣强化，不只是一次随手点开。",
      "evidence": "因为你最近连续点开了相关内容，还在聊天里主动提到这类主题。",
      "source": "chat",
      "created_at": "2026-03-14T22:30:00"
    }
  ],
  "has_more_cognition_updates": true,
  "next_cognition_cursor": "3"
}
```

分页采用简单 cursor 语义：

- `limit`：请求条数，默认 3
- `cursor`：下一页起始偏移，字符串形式返回和传递
- 认知更新按“未通知优先 + 时间倒序”稳定排序
- `has_more_cognition_updates` / `next_cognition_cursor` 用于前端续页

## 生成规则

### 即时聊天 / 反馈

针对单条强信号：

- `summary`：描述新记住的结论
- `impact`：说明这会让哪条偏好更明确、更靠前，或让某条避雷方向更明确
- `reasoning`：强调这是“早期确认”还是“方向修正”
- `evidence`：直接引用这次聊天/评论/踩雷内容

### 批量画像更新

针对 refresh/rebuild 这类聚合更新：

- `summary`：描述画像变化结果
- `impact`：写清画像哪一块发生了转向或加强
- `reasoning`：说明这不是单次波动，而是重复出现后的稳定判断
- `evidence`：给出最近聚合到的行为或主题模式

## 前端兼容策略

- 旧数据如果只有 `summary`，前端仍然展示为一张简化卡片
- 缺少 `impact / reasoning / evidence` 时，不渲染对应详情块
- 这样历史 `cognition update` 不需要一次性迁移也不会炸
- 旧前端如果忽略分页字段，仍然至少能看到首屏 3 条，不会因为多字段而报错

## 风险与取舍

### 风险 1：解释写得像“编的”

处理：

- 强制后端生成结构化字段
- `reasoning` 口径保持“当前推断”，避免过度确定

### 风险 2：画像页信息量过大

处理：

- 默认只显示摘要
- 首屏只显示 3 条，更多历史按需加载
- 一次只展开一张

### 风险 3：即时更新和批量更新口径不一致

处理：

- 统一认知卡片字段
- 即时链路和批量链路都走同一套结构化输出语义

## 测试策略

- Memory/Soul：验证结构化 cognition update 生成
- API：验证 `/api/profile-summary` 返回新结构
- Popup helper：验证结构化数据规范化和旧数据回退
- API：验证分页字段、下一页 cursor 和超界 cursor 回退
- Popup UI：验证可展开卡片默认收起、展开显示详情、同一时刻只展开一张
- Popup UI：验证滚动续页、按钮兜底、加载中/失败/无更多状态

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
- `docs/modules/soul.md`
- `docs/modules/extension.md`
- `docs/changelog.md`
