# Candidate Supply Upgrade 设计

## 背景
当前发现链路已经具备 `search`、`trending`、`related_chain`、`explore` 四种策略，也有运行时自动刷新机制，但还不能稳定保证“用户需要内容时一定有足够候选”。

现状问题主要有四个：
- `ContentDiscoveryEngine.discover()` 只返回当前找到的结果，不会在不足目标数量时主动补货
- `search` 策略召回高，但只查有限 query 和第一页结果
- `trending` / `related_chain` / `explore` 使用固定阈值过滤，在窄兴趣用户上容易掉量
- `content_cache` 没有持久化 `relevance_score` / `relevance_reason`，导致从缓存补候选时无法保持统一排序口径

## 目标
- 让 discovery 阶段在正常情况下尽量稳定产出至少 12 条候选
- 最终 recommendation 阶段仍只生成 10 条推荐
- 保持“主候选优先，兜底候选次之”，不要为了凑数破坏整体质量
- 统一 freshly discovered 和 cache backfill 的排序依据

## 设计原则
- **平衡供给与质量**：优先保持当前质量标准，只在不足时逐层放宽
- **显式分层**：区分 `primary` 和 `backfill` 候选，避免后续调试和展示时混淆
- **缓存可复用**：缓存内容必须保留相关性分数，才能成为可靠的补货来源
- **最小入侵**：优先在 discovery / recommendation / storage 三层收口，不重写 runtime 触发逻辑

## 方案
### 1. 持久化候选质量信号
扩展 `content_cache`：
- 新增 `relevance_score REAL DEFAULT 0.0`
- 新增 `relevance_reason TEXT DEFAULT ""`
- 新增 `candidate_tier TEXT DEFAULT "primary"`

写缓存时同步落这些字段；从缓存回读时恢复为 `DiscoveredContent` 对象。

这样 recommendation 阶段即使不是直接消费本轮 discovery 结果，也能按真实相关性排序，而不是退化成只看 `view_count`。

### 2. Discovery 两阶段产出
`ContentDiscoveryEngine.discover()` 改为：

第一阶段：
- 跑当前选中的策略
- 去重、按质量排序
- 若候选数 `>= target_primary_count`，直接结束

第二阶段补货：
- 若不足，执行 `backfill_discover(...)`
- 补货来源分三层：
  1. 扩搜索召回：更多 query、更深页数
  2. 放宽高精度策略阈值：`trending` / `related_chain` / `explore`
  3. 从 `content_cache` 读取未推荐且高分的历史候选

最终：
- 合并后再次去重
- 保留 `candidate_tier`
- 返回按统一口径排序后的结果

### 3. 策略级补货能力
不推翻现有 `DiscoveryStrategy` 抽象，而是给支持补货的策略增加“扩量模式”参数。

具体建议：
- `SearchStrategy.discover(..., limit, expanded=False)`
  - 正常模式：当前 query 数 + 当前页数
  - 扩量模式：更多 query、更多页
- `TrendingStrategy` / `RelatedChainStrategy` / `ExploreStrategy`
  - 正常模式：维持现有 `score_threshold`
  - 扩量模式：使用较低但仍受控的阈值，比如 `0.58`

如果不想改公共接口，也可以在 engine 内新建“补货版实例”来跑第二轮。

### 4. Recommendation 统一排序
`RecommendationEngine.generate_recommendations()` 的输入分两种：
- 直接消费本轮 discovery 返回值
- 从缓存回读未推荐内容

两种都统一按以下口径排序：
- `candidate_tier`：`primary` 优先于 `backfill`
- `relevance_score`：高分优先
- `discovered_at` / `last_scored_at`：较新优先
- `view_count`：作为次级质量信号

这样可以避免“缓存回读时低相关高播放内容反而压过真正高相关候选”。

## 运行时行为
`ContinuousRefreshController` 不需要重写触发逻辑，仍沿用：
- 行为事件累计达到阈值时触发 `search + related_chain`
- `trending` / `explore` 按时间窗口刷新

变化只在 discovery 内部：
- 不再把“这轮只找到 3 条”直接视为最终结果
- controller 拿到的是“已完成补货后的候选集合”

## 目标数建议
- discovery 主候选目标：`12`
- discovery 补货上限：`18`
- recommendation 最终输出：`10`

原因：
- 12 条足够给后续推荐留出排序空间
- 18 条能避免为了补货过度放宽
- 10 条仍符合当前 popup / API 输出预期

## 边界与取舍
- 不做无限翻页补货，避免调用成本失控
- 不允许把已推荐内容再次回流为未推荐候选
- cache backfill 必须受 freshness 约束，避免不断复推旧内容
- 这轮不引入额外复杂的 bandit / online learning 排序器

## 测试策略
需要覆盖：
- 主候选足够时，不触发补货
- 主候选不足时，触发扩搜索
- 扩搜索后仍不足时，触发阈值放宽
- 仍不足时，从缓存补齐
- 缓存回读排序按 `relevance_score` 而不是仅按 `view_count`
- `candidate_tier` 正确落库和恢复
- 已推荐内容不会因补货逻辑重新进入 recommendation

## 涉及文件
- `src/openbiliclaw/discovery/engine.py`
- `src/openbiliclaw/discovery/strategies/strategies.py`
- `src/openbiliclaw/recommendation/engine.py`
- `src/openbiliclaw/storage/database.py`
- `tests/test_discovery_engine.py`
- `tests/test_recommendation_engine.py`
- `tests/test_storage.py`
- `tests/test_refresh_runtime.py`
- `docs/modules/discovery.md`
- `docs/modules/recommendation.md`
- `docs/modules/config.md`（如果把目标数做成可配置）
- `docs/changelog.md`
