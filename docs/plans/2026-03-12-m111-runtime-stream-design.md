# 运行时实时状态流设计

## 目标
让 popup 在打开期间实时看到后端当前在做什么，例如“正在分析新行为”“正在补搜索候选”“刚补进 6 条新的”，而不是只靠静态数字和手动刷新。

## 问题
当前 popup 只通过 REST 读取 `runtime-status`，所以池子状态虽然有数量，但缺少“过程感”。用户看见的常常只是：
- 当前池子里还有 X 条可换
- 刚补进 0 条新的
- 最近在补：还在继续摸你的口味

这不能表达后端当前是否正在补池子、补到哪个阶段，也不够像一个“活的”助手。

## 核心设计

### 1. 新增统一 runtime event hub
在后端新增一个很薄的事件广播层：
- 维护当前连接的 websocket clients
- 暴露 `publish(event)` 用于广播运行时状态事件

刷新器、手动补货、即时换一批都往这个 hub 发事件，API 只负责把这些事件推给 popup。

### 2. 新增 WebSocket：`/api/runtime-stream`
popup 打开后自动建立一条 websocket 连接，接收统一格式的运行时事件。

事件结构统一为：
- `type`
- `phase`
- `strategy`
- `message`
- `pool_available_count`
- `last_replenished_count`
- `recent_pool_topics`

例如：
- `refresh.started`
- `refresh.strategy`
- `refresh.pool_updated`
- `recommendation.reshuffled`
- `refresh.failed`

### 3. popup 把 ws 作为增强层
popup 保留现有 REST 初始化流程不变：
- 首次打开时继续拉 `runtime-status`
- 没有 ws 也能工作

ws 连上后，再用事件流实时更新：
- 底部提示横条
- 池子状态摘要

这样即使 websocket 中断，推荐、画像、聊天等核心功能也不会失效。

### 4. 刷新阶段文案
第一版把运行时状态收敛成几类更像“阿B 正在忙什么”的文案：
- `runtime.idle`：阿B 这会儿先盯着你的新动作
- `runtime.observing`：又记下了 1 个新信号，先继续看看
- `refresh.started`：开始给你补候选了
- `refresh.strategy/search`：先从你刚刚的口味里搜一轮
- `refresh.strategy/related_chain`：再顺着你最近点开的内容往外捞
- `refresh.strategy/trending`：顺手看看站内热榜里有没有你会吃的
- `refresh.strategy/explore`：再给你探一点你可能会意外喜欢的
- `refresh.pool_updated`：刚补进 6 条新的
- `recommendation.reshuffled`：这批先给你换好了
- `refresh.failed`：这次补货卡了一下，稍后再试

## 验收标准
- popup 打开后会自动连接 `/api/runtime-stream`
- 后端刷新器在关键阶段会推送实时事件
- popup 底部提示条和池子摘要能随事件实时变化
- websocket 断开时，popup 仍能继续依赖 REST 回退正常工作
