# Discovery Concurrency Optimization Design

**Date:** 2026-03-15

## Goal

在不明显降低推荐质量、不过度放大 B 站或 LLM 限流风险的前提下，缩短 `openbiliclaw init` 和 `openbiliclaw discover` 的首轮耗时。

## Current Bottlenecks

- discovery engine 只在“策略之间”并发，策略内部仍有大量串行调用
- `SearchStrategy` 对 query/page 逐个发起搜索请求
- `TrendingStrategy`、`RelatedChainStrategy`、`ExploreStrategy` 会对候选逐条串行打分
- `init` 首轮会同步执行完整 discover，因此所有串行链路直接影响首次可用时间

真实运行中已经观察到：

- 交互式 `init` 能完成配置和认证写入
- 画像阶段可正常完成
- discover 阶段会持续发起大量成功的 B 站和 DeepSeek 请求，但整体耗时偏长

## Non-Goals

- 不改默认 provider 选择逻辑
- 不把 `init` 拆成“快速模式”和“后台补全模式”
- 不新增面向用户的并发配置项
- 不追求极限吞吐

## Approaches Considered

### 1. Conservative Bounded Concurrency

在 discovery 内部引入共享 semaphore，对 B 站请求和 LLM 评分分别限流，默认值保守。

优点：

- 对现有行为侵入最小
- 风险可控
- 能直接消除最明显的串行瓶颈

缺点：

- 首轮时间会改善，但不会压到极限

### 2. Split First-Run Discovery

`init` 只跑小批量首轮候选，把 `trending/explore` 留给后续 refresh。

优点：

- 首轮体感最好

缺点：

- 是行为改动，不只是性能优化
- 会改变当前“初始化即完整 discover 一次”的语义

### 3. Aggressive Parallelism

大幅提高 query、评分、related chain 的并发度。

优点：

- 理论上最快

缺点：

- 最容易触发 B 站或 LLM 429
- 代理和网络抖动时不稳定

## Recommended Design

采用方案 1：保守受控并发。

### Concurrency Model

新增共享的 discovery 并发控制器：

- `bilibili_request_concurrency = 2`
- `llm_evaluation_concurrency = 2`

该控制器由 `_build_discovery_engine()` 创建一次，并传给：

- `ContentDiscoveryEngine`
- `SearchStrategy`
- `TrendingStrategy`
- `RelatedChainStrategy`
- `ExploreStrategy`

这样即使 engine 继续并发运行多个 strategy，所有策略内部对 B 站和 LLM 的访问仍共享同一组 semaphore，不会把外部请求量放大到不可控。

### Strategy Changes

#### SearchStrategy

- 预先生成 `(query, page)` 请求计划
- 使用受控并发并发执行搜索请求
- 按原本的 `query_index/page/item_index` 顺序回放结果，保持去重和截断逻辑稳定

#### TrendingStrategy

- 并发拉取多个 rid 的榜单
- 候选评分改成受控并发
- 保持原有阈值过滤和结果截断

#### RelatedChainStrategy

- 先保持 BFS 结构不变
- 对每个 related batch 内的候选评分改成受控并发
- 暂不激进并发整个 frontier，避免一次放大太多 related 请求

#### ExploreStrategy

- 并发执行 domain/query 搜索
- 候选评分改成受控并发
- 保持 novelty bonus 和排序逻辑不变

### Engine Changes

`ContentDiscoveryEngine.evaluate_content()` 不直接裸调 LLM，而是通过共享控制器进入 LLM semaphore。这样所有策略里通过 evaluator 打分的调用都会统一受控。

## Error Handling

- 单个 B 站请求失败：保持现有“记录日志并继续”
- 单个 LLM 评分失败：保持现有“score=0 / 跳过”
- 并发任务内部异常仍在 strategy 级别收口，不让一次失败拖垮整个 discover

## Testing Strategy

### Unit Tests

- 验证 runtime 控制器会限制 B 站请求最大并发数
- 验证 `SearchStrategy` 在并发搜索后仍保持去重和 limit 行为
- 验证 `evaluate_content()` 在并发评分时不会超过配置上限
- 验证 `TrendingStrategy` / `ExploreStrategy` 的评分路径仍能返回正确结果

### Regression Checks

- 现有 discovery、strategy、CLI 测试全部保持通过
- `init` 与 `discover` 的用户输出不变

## Docs Impact

需要更新：

- `README.md`
- `docs/modules/cli.md`
- `docs/modules/config.md`
- `docs/changelog.md`

文档只说明“首轮 discover 已做保守并发优化，但仍可能持续几分钟”，不暴露新的用户配置面。
