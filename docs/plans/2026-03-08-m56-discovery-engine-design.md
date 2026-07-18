# 5.6 发现引擎编排设计

## 背景

`SearchStrategy`、`TrendingStrategy`、`RelatedChainStrategy`、`ExploreStrategy` 都已经具备独立运行能力，`evaluate_content()` 也已落地，但 `ContentDiscoveryEngine.discover()` 仍然是串行执行、内存内去重排序、没有缓存写入。`5.6` 的目标是把这些单点能力收敛成一个真实可复用的“发现闭环”。

## 目标

- 让 `ContentDiscoveryEngine.discover()` 并发运行多个策略
- 在引擎层统一去重、排序、裁剪
- 将最终发现结果写入 SQLite `content_cache`
- 为 `6.1 推荐排序` 提供稳定输入

## 范围

### 包含

- `asyncio.gather()` 并发执行策略
- 单策略失败不中断全局结果
- 按 `bvid` 去重并保留高分版本
- 将最终结果写入 `content_cache`
- 提供读取缓存的方法，方便测试和后续推荐模块使用

### 不包含

- 内容缓存失效策略
- 多来源 `source_strategy` 列表合并
- 分页加载缓存
- 推荐历史过滤

## 设计

### 1. 引擎编排

`ContentDiscoveryEngine.discover()` 调整为：

1. 选出 active strategies
2. 通过 `asyncio.gather(return_exceptions=True)` 并发执行
3. 合并所有 `DiscoveredContent`
4. 按 `bvid` 去重，若重复则保留更高分版本
5. 按 `relevance_score` 降序排序
6. 裁剪到 `limit`
7. 将最终结果写入 `content_cache`

### 2. 错误处理

- 单个策略抛异常：记日志并继续
- 缓存写入失败：只记录错误，不让 `discover()` 整体失败
- 空结果：直接返回空列表，不写缓存

### 3. 缓存接口

`Database` 增加：

- `get_cached_content(limit=100)`：读取缓存内容，供测试和 `6.1` 使用

写缓存时使用已有 `cache_content()`，至少写入：

- `bvid`
- `title`
- `up_name`
- `up_mid`
- `duration`
- `description`
- `cover_url`
- `view_count`
- `like_count`
- `source`

### 4. 合并规则

- 重复 `bvid` 的内容，保留 `relevance_score` 更高的对象
- `source_strategy` 跟随保留版本
- 不额外引入来源列表字段，避免过早复杂化

## 测试策略

- 验证 `discover()` 会并发执行多个策略
- 验证单个策略失败不会影响其它策略
- 验证重复 `bvid` 时保留更高分结果
- 验证最终结果排序正确
- 验证缓存写入成功，并能通过 `get_cached_content()` 读回

## 文档更新

- `docs/v0.1-todolist.md`
- `docs/modules/discovery.md`
- `docs/changelog.md`
