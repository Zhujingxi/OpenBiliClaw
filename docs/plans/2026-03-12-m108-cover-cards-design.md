# M108 推荐卡片封面展示设计

## 目标
在插件 popup 的推荐卡片中展示视频封面，让用户在不打开视频前就能快速判断内容风格和质量。

## 现状
- 后端推荐链路和 discovery pool 已经持有 `cover_url`
- popup 当前只展示标题、UP 主、推荐理由和操作按钮
- `/api/recommendations` 与 `/api/recommendations/reshuffle` 的响应模型尚未暴露 `cover_url`

## 方案
采用最小改动方案：
- API 响应新增 `cover_url`
- popup 推荐卡片改成“封面 + 文本信息 + 操作区”结构
- 封面区域与内容区共享打开视频行为
- 若 `cover_url` 为空，则回退到简洁占位块，不阻塞卡片渲染

## 交互
- 点击封面或标题区都可打开视频
- `多来点 / 少来点 / 说说原因 / 发出去` 仍只走反馈逻辑，不触发跳转
- 保持现有亮色视觉语言，不新增复杂动效

## 测试
- Python：API 测试覆盖推荐接口返回 `cover_url`
- Extension：popup helper / DOM 断言覆盖封面渲染与占位回退
- 回归：现有推荐刷新、反馈、聊天 tab 行为不退化
