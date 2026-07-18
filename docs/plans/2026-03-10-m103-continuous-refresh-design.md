# 持续候选池刷新与插件通知设计

## 目标

让推荐系统从“手动补货”升级为“持续补货”：

- 用户不主动执行 `discover` 时，候选池也会持续更新
- 候选池不会长期停留在同一批内容
- 当系统捞到高置信度新推荐时，插件可以主动发出轻通知

## 当前问题

当前主线中：

- `init` 会顺手执行一次 `discover`
- `discover` 会把发现结果写入 `content_cache`
- `recommend` 会从 `content_cache` 里挑未推荐内容并生成表达
- 插件会采集事件、展示推荐、提交反馈

但还缺少“持续补货”：

- 没有后台刷新器
- 插件采集到的新行为不会自动触发新的 `discover`
- `content_cache` 缺少新鲜度和通知状态
- service worker 没有通知机制

## 设计原则

1. 事件驱动为主，定时保底为辅
2. 不把所有策略高频全量跑一遍
3. 通知只针对高置信度、未疲劳的内容
4. 尽量复用现有 `discover -> content_cache -> recommend` 主链
5. 第一版不引入独立 worker 或复杂任务队列

## 刷新机制

### 1. 行为触发小刷新

当自上次刷新后新增强信号事件达到阈值时，触发一次轻量刷新。

强信号事件：

- `view`
- `search`
- `favorite`
- `like`
- `coin`
- `comment`
- `feedback`

默认阈值：

- `>= 6` 条

执行策略：

- `SearchStrategy`
- `RelatedChainStrategy`

目标：

- 快速根据用户最近的显式兴趣变化补货

### 2. 定时保底刷新

即使没有达到强信号阈值，也要保底补货。

默认阈值：

- 距离上次 trending 刷新 `>= 3 小时`

执行策略：

- `TrendingStrategy`

目标：

- 确保榜单内容和新热视频进入候选池

### 3. 低频探索刷新

探索策略不应该高频触发。

默认阈值：

- 距离上次 explore 刷新 `>= 12 小时`

执行策略：

- `ExploreStrategy`

目标：

- 保持候选池的新鲜感与跨域惊喜，但不让探索内容挤爆主推荐池

## 候选池保鲜

在 `content_cache` 上补最小字段：

- `last_scored_at`
- `notification_sent`

已有字段：

- `discovered_at`

使用方式：

- 新补进的内容具有更高时效性
- 已通知过的内容不再重复发通知
- 后续读取候选时可以把过旧内容降权

第一版不做复杂衰减公式，只先支持：

- 新内容优先
- 已推荐过内容不再进“未推荐候选”
- 已通知过内容不再作为通知候选

## 运行状态

新增运行状态文件：

- `data/memory/discovery_runtime.json`

字段：

- `last_event_refresh_at`
- `last_trending_refresh_at`
- `last_explore_refresh_at`
- `last_processed_event_id`
- `last_notification_at`

作用：

- 控制刷新频率
- 控制事件游标
- 控制通知节流

## 后端常驻方式

在 `openbiliclaw start` 启动的 FastAPI 进程内挂一个后台 async loop。

行为：

- 每 60 秒检查一次是否满足刷新条件
- 只有达到阈值时才真正执行 discovery + recommendation
- 不额外引入 Celery、cron 或独立 worker

优点：

- 实现简单
- 与插件现有本地后端模型一致

## API 设计

### `GET /api/runtime-status`

返回：

- `initialized`
- `recommendation_count`
- `pending_signal_events`
- `last_refresh_at`
- `last_notification_at`
- `unread_count`

用途：

- popup 显示“还没初始化 / 正在观察 / 已有新推荐”

### `GET /api/notifications/pending`

返回至多一条当前最值得提醒的推荐：

- `recommendation_id`
- `bvid`
- `title`
- `reason`

选择规则：

- `confidence >= 0.82`
- 未通知过
- 未展示过
- 距离上次通知至少 2 小时

## 插件行为

### service worker

继续复用 `chrome.alarms`：

- flush 事件后检查一次 `notifications/pending`
- 若有待发通知，则调用 `chrome.notifications.create()`
- 点击通知后直接打开目标视频页

### popup

popup 不负责通知轮询，只负责展示运行状态：

- 未初始化：提示先跑 `init`
- 正在观察：显示“正在补货”
- 有新推荐：展示推荐列表并标明“新补进”

## 测试策略

### Python

- `runtime-status` 接口
- 自动刷新门控逻辑
- 定时 / 事件刷新分层逻辑
- 通知候选选择逻辑

### Extension

- service worker 通知轮询与通知创建
- popup runtime status 三态展示辅助

### 集成

- 模拟写入强信号事件后，后台刷新器生成新的推荐
- `notifications/pending` 只返回一条高置信未通知推荐

## 风险与边界

- 第一版后台刷新 loop 依赖 `start` 常驻；如果用户只跑 CLI 单命令，不会自动补货
- 第一版不做多通知队列，只返回 1 条待通知项
- 第一版不做复杂 topic 疲劳控制，只先用推荐历史和通知状态去重
- 第一版不把通知点击行为再次写回数据库，后续可补
