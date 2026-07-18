# Delight Frontpage Design

## 背景

当前插件里的“惊喜推荐”还是一条临时消息：

- `delight.candidate` 到来时只进消息收件箱和系统通知
- `recommend` 页首屏没有任何稳定承接位
- popup 重新打开后不会主动从 `/api/delight/pending` 恢复
- 通知点击只能打开 `recommend` tab，不能把人带到具体惊喜内容

这会导致惊喜推荐明明是高价值内容，却被做成了一个很容易错过的边角提醒。

## 目标

1. 把惊喜推荐升格为 `recommend` tab 的首屏模块，而不是藏在消息中心
2. 让 popup 重开后仍能恢复当前待处理惊喜推荐
3. 保留现有消息/通知链路，但把它们降级为提醒和兜底入口
4. 不改后端 delight 协议，只消费现有 `/api/delight/pending` 与 runtime stream

## 方案

采用“首屏惊喜位 + 消息中心兜底”的轻量落地方案。

### 1. 信息架构

- 在 `recommend` 视图头部和推荐列表之间增加一个 `delight-slot`
- 有待处理惊喜推荐时展示，无内容时完全隐藏
- 惊喜卡成为主入口；消息中心只保留提醒/稍后处理入口，不再承担首要发现职责

### 2. 数据流

- popup 初始化、runtime 重连、`init_completed` 后主动调用 `/api/delight/pending`
- runtime stream 收到新的 `delight.candidate` 时，立即刷新首屏惊喜卡
- `pending` 与 runtime 事件按 `bvid` 去重，避免重复插入

### 3. 交互

- 首屏卡展示封面、hook、标题、理由和状态文案
- 保留 `看看 / 不感兴趣 / 聊一聊`
- 新增 `稍后看`
- `看看` 打开视频，同时把卡片切到稳定的“已查看”态
- `不感兴趣` 和 `聊一聊` 不再秒退，而是短暂保留处理结果
- `稍后看` 只隐藏首屏展示，不上传负反馈

### 4. 通知落点

- 系统通知点击时打开 `popup/popup.html?tab=recommend&delight=<bvid>`
- popup 启动后读取 `delight` 参数，对应惊喜卡进入高亮态
- 如果 `pending` 里存在该 `bvid`，直接首屏定位；不存在则只保留普通推荐页落点

## 实现边界

本次允许调整：

- `extension/popup/popup.html`
- `extension/popup/popup.js`
- `extension/popup/popup-api.js`
- `extension/popup/popup-helpers.js`
- `extension/src/background/notifications.ts`
- `extension/src/background/service-worker.ts`
- 对应前端测试、扩展文档与 changelog

本次不做：

- 多条惊喜推荐历史收件箱
- 惊喜推荐本地长期持久化
- 惊喜推荐排序/轮播策略
- 后端 delight 协议扩展

## 验证口径

自动化需要覆盖：

1. `/api/delight/pending` 的 popup API 调用
2. delight helper 对 pending/runtime 数据、展示状态和深链参数的处理
3. 通知 URL 包含 delight 深链参数
4. popup HTML 确实存在首屏 delight slot，且保留四个动作入口

手动验收期望：

1. popup 打开时若后端已有 pending delight，会在 `recommend` 首屏看到
2. 收到新的 `delight.candidate` 时，首屏卡会立刻更新
3. 点击系统通知会落到对应惊喜推荐
4. 处理后状态稳定可见，不会立即蒸发
