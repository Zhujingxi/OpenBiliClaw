# M84 Popup Tabs 设计

## 背景

当前 popup 只提供推荐列表与反馈按钮，存在两个明显问题：

1. 用户无法在插件内直接看到“阿花如何理解我”，只能回到 CLI 使用 `profile` / `chat`
2. 推荐卡片整卡可点击，导致 `喜欢 / 不喜欢 / 写一句 / 发送` 等交互存在误跳转到视频页的 bug

本次目标是在不拆分独立页面的前提下，将 popup 升级为同一窗口内的三 tab 视图，并修复推荐卡片交互边界。

## 目标

- popup 顶部支持 `推荐` / `我的画像` / `和阿花聊聊` 三个 tab
- 推荐 tab 保留当前推荐卡片与反馈能力，但修复误跳转 bug
- 新增画像 tab，直接展示后端生成的用户画像摘要
- 新增聊天 tab，通过后端调用现有 `SocraticDialogue` 获得回复

## 方案

### 1. Popup 结构

- 保持单个 `popup.html`
- 顶部新增 tab bar
- 三个视图都在同一文档中切换显示，不跳转新页面
- `popup.js` 负责：
  - 初始化 tab 状态
  - 请求后端接口
  - 调用各视图 renderer
- `popup-helpers.js` 继续保留纯函数
- 新增 `popup-api.js` 统一封装 popup 对后端的 fetch

### 2. 推荐 tab

- 卡片拆成：
  - 内容区：可打开视频
  - 操作区：仅按钮交互，不触发跳转
- `打开视频` 仍显式保留
- `喜欢 / 不喜欢 / 写一句 / 发送` 全部只触发反馈请求
- 修复策略不是继续堆 `stopPropagation()`，而是取消整卡点击，改成明确的可点击内容区

### 3. 画像 tab

- 新增 `GET /api/profile-summary`
- 返回字段：
  - `initialized`
  - `personality_portrait`
  - `core_traits`
  - `deep_needs`
  - `top_interests`
- popup 只展示适合阅读的摘要，不直接暴露整份 `soul.json`

### 4. 聊天 tab

- 新增 `POST /api/chat`
- 请求：
  - `{ "message": "..." }`
- 返回：
  - `{ "reply": "..." }`
- 后端直接复用 `SocraticDialogue.respond()`
- popup 内只保留本次打开期间的临时消息列表，不做本地持久化

## 测试

- API：
  - `/api/profile-summary`
  - `/api/chat`
- popup：
  - tab 状态切换
  - 推荐按钮不再误跳转
  - 画像状态切换
  - 聊天成功与失败提示

## 文档更新

- `docs/modules/extension.md`
- `docs/changelog.md`
- `docs/v0.1-todolist.md`
- 必要时 `docs/index.md`
