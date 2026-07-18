# 6.3 推荐持久化设计

## 背景

当前 `6.1` 和 `6.2` 已经打通了推荐排序、朋友式推荐表达和 CLI 展示，但推荐记录仍停留在“已选中 / 已展示”的最小状态。`6.3` 的目标是把推荐记录补成一个可追踪、可反馈、可被后续记忆系统消费的闭环。

## 目标

- 为 `recommendations` 表补齐反馈结构：`feedback_type`、`feedback_note`、`feedback_at`
- 提供统一的推荐反馈更新接口
- 新增 `openbiliclaw feedback <id> <like|dislike>` 命令
- 每次反馈同时写一条 `event_type="feedback"` 事件，进入事件层

## 方案选择

### 方案 A：在 `recommendations` 表扩展反馈字段（采用）

优点：
- 最符合 v0.1 范围，改动小
- 查询推荐状态简单，后续统计和分析直接可用
- 不需要引入额外 feedback 表和 join 逻辑

缺点：
- 只保留“当前反馈状态”，不保留多次反馈历史

### 方案 B：沿用单个 `feedback` 文本字段

优点：
- 实现最快

缺点：
- 查询和统计都很脆
- 后续几乎一定返工

### 方案 C：单独建立 `recommendation_feedback` 表

优点：
- 数据模型最干净，支持多次反馈

缺点：
- 对 v0.1 偏重，不符合当前最小闭环目标

## 设计

### 数据库

- `recommendations` 表新增：
  - `feedback_type TEXT`
  - `feedback_note TEXT`
  - `feedback_at TIMESTAMP`
- `Database.initialize()` 需要做幂等迁移，兼容已有数据库
- 新增方法：
  - `get_recommendation_by_id(recommendation_id)`
  - `update_recommendation_feedback(recommendation_id, feedback_type, feedback_note="")`

### 推荐引擎

- `RecommendationEngine` 新增轻量入口：
  - `record_feedback(recommendation_id, feedback_type, note="")`
- 该入口只负责更新推荐记录，不承担事件写入和重分析逻辑

### CLI

- 新增命令：
  - `openbiliclaw feedback <id> <like|dislike>`
- 可选参数：
  - `--note "..."` 用于补备注
- 命令流程：
  1. 校验运行时配置
  2. 构建推荐引擎和 `MemoryManager`
  3. 查推荐记录是否存在
  4. 更新反馈字段
  5. 写入 `feedback` 事件
  6. 输出成功确认

### 事件层

- `feedback` 事件写入 `events` 表
- `metadata` 至少包含：
  - `recommendation_id`
  - `bvid`
  - `feedback_type`
  - `feedback_note`

## 错误处理

- 推荐 ID 不存在：CLI 明确报错并退出
- 反馈类型非法：由 Typer 参数或显式校验处理
- 数据库迁移重复执行：必须幂等，不影响已有数据

## 测试策略

- `Database`
  - 迁移后字段存在且可读写
  - `get_recommendation_by_id()` 返回正确记录
  - `update_recommendation_feedback()` 正确写入时间和字段
- `RecommendationEngine`
  - `record_feedback()` 正确调用数据库
- `CLI`
  - `feedback 7 like`
  - `feedback 7 dislike --note "太浅了"`
  - 不存在 ID 报错
- `MemoryManager`
  - 反馈命令会写入 `feedback` 事件
