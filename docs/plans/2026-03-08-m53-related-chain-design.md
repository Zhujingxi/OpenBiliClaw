# 5.3 相关推荐链策略设计

## 背景

`RelatedChainStrategy` 目前仍是占位实现，但 `BilibiliAPIClient.get_related_videos()`、`ContentDiscoveryEngine.evaluate_content()`、事件层查询能力都已经可用。`5.3` 的核心不是再造一套评估体系，而是确定“从哪些种子视频出发、沿相关推荐链扩展到什么程度、如何控制请求量并保持结果质量”。

## 目标

- 让 `RelatedChainStrategy` 在不依赖手工预热的情况下单独可运行
- 优先从近期真实行为中挑出高价值种子视频
- 沿 B 站相关推荐链发现至少 10 条新内容
- 统一复用 `ContentDiscoveryEngine.evaluate_content()` 做相关性评分

## 范围

### 包含

- 从事件层中提取 `view` / `favorite` / `like` 等视频种子
- 在种子不足时，从偏好层线索和已有策略结果中补种子
- 调用 `get_related_videos()` 扩展有限深度的相关推荐链
- 对候选内容打分、过滤、去重并返回 `DiscoveredContent`

### 不包含

- 图搜索或无限递归扩展
- 把发现结果写入 `content_cache`
- 并行执行多个 discovery 策略
- 评论区、UP 主追踪或跨领域探索

## 设计

### 1. 种子选择

种子来源按优先级依次为：

1. **事件层**：从最近事件中挑带 `bvid` 的 `view` / `favorite` / `like` 项
2. **偏好补种子**：若事件种子不足，用高权重兴趣标签或常看 UP 主做小范围搜索，补 1~2 个种子
3. **策略兜底**：若仍不足，再调用 `SearchStrategy` 或 `TrendingStrategy` 获取前几个高分内容作为种子

种子总数默认限制为 5 个，避免请求量过快膨胀。

### 2. 相关推荐链扩展

- 每个种子调用一次 `get_related_videos(bvid)`
- 默认只扩展 1 层，先把链路做成“有限边界的推荐链”
- 每个种子最多保留前 8 个相关推荐候选
- 排除原始种子本身，并对全局候选按 `bvid` 去重

### 3. 评分与过滤

- 所有候选统一走 `ContentDiscoveryEngine.evaluate_content()`
- `RelatedChainStrategy` 可附加轻量链路先验：
  - 越靠前的种子，初始优先级越高
  - 更浅的深度有微弱加成
- 最终仍以 `evaluate_content()` 的 `relevance_score` 为主
- 默认阈值先用 `0.65`

### 4. 失败与降级

- 某个种子的 `get_related_videos()` 失败，只记录日志并继续
- 某个候选评分失败，按 0 分处理
- 所有种子都失败时返回空列表
- 偏好补种子或策略兜底失败，不影响已有事件种子路径

## 测试策略

- 验证种子优先来自事件层
- 验证种子不足时会走偏好补种子和策略兜底
- 验证相关推荐候选会排除种子自身并按 `bvid` 去重
- 验证只保留高于阈值的结果
- 验证单个种子请求失败不会中断整体发现流程

## 文档更新

- `docs/v0.1-todolist.md`
- `docs/modules/discovery.md`
- `docs/changelog.md`
