# 5.1 搜索策略设计

## 背景

当前 `discovery/` 层只有 `SearchStrategy`、`TrendingStrategy` 等空壳，`ContentDiscoveryEngine` 虽然可以注册策略，但没有任何真正可运行的发现能力。`5.1 搜索策略` 是内容发现主链路的第一个 P0 能力，需要先把“画像 -> 搜索词 -> B 站搜索结果 -> `DiscoveredContent`”打通。

本轮目标不是一次做完整内容发现，而是让搜索策略可以被直接注册、直接运行，并为后续 `5.5 内容评估`、`5.6 发现引擎编排` 保留稳定接口。

## 目标

完成一个最小但可直接运行的 `SearchStrategy`：

- 基于用户画像和核心记忆生成 5 到 10 个搜索关键词组合
- 对每个关键词调用 `BilibiliAPIClient.search()`
- 跨关键词结果按 `bvid` 去重
- 将搜索结果映射为 `DiscoveredContent`
- 能被 `ContentDiscoveryEngine.register_strategy()` 注册并成功执行

## 非目标

本轮不做以下内容：

- 不做 LLM relevance 打分
- 不把内容写入 `content_cache`
- 不实现 `TrendingStrategy` / `RelatedChainStrategy`
- 不新增 CLI `discover` 端到端命令

## 方案选择

### 方案 A：可运行的 SearchStrategy + 轻量 Engine 集成

让 `SearchStrategy` 成为一个带依赖注入的真正策略：

- 注入 `LLMService`
- 注入 `BilibiliAPIClient`
- `discover()` 内部完成 query 生成、搜索、去重、映射

`ContentDiscoveryEngine` 只保持当前编排职责，不负责建依赖和做复杂排序。

优点：

- 范围清晰，直接满足 `5.1`
- 后续 `5.5`、`5.6` 不会返工接口
- 测试容易做 mock

缺点：

- 本轮 `relevance_score` 还不会很聪明

### 方案 B：把内容评估一起做

在搜索后立即用 LLM 对每个候选做相关性评分。

优点：

- 结果质量更高

缺点：

- 范围膨胀到 `5.5`
- 测试和运行成本明显上升

### 方案 C：只做 query 生成器

只落地关键词生成，不真正接 B 站搜索。

优点：

- 改动少

缺点：

- 不能“直接跑起来”

### 结论

采用方案 A。

## 架构设计

### SearchStrategy

`SearchStrategy` 新增构造依赖：

- `llm_service: LLMService`
- `bilibili_client: BilibiliAPIClient`
- 可选配置：`queries_per_run`、`page_size`

`discover(profile, limit)` 流程：

1. 基于 `profile` 生成搜索词
2. 遍历搜索词调用 `bilibili_client.search()`
3. 映射结果为 `DiscoveredContent`
4. 全局按 `bvid` 去重
5. 截断到 `limit`

### 关键词生成

关键词生成统一走 `LLMService.complete_structured_task()`，自动注入 core memory。

Prompt 输入：

- Soul 画像摘要
- 偏好层摘要
- 最近觉察/洞察（如果存在）

Prompt 输出：

```json
{
  "queries": ["纪录片 原理", "影像表达 分析", "历史 长视频 深度"]
}
```

本地约束：

- 去空字符串
- 去重
- 截断到 10 条

### Fallback

如果 LLM 返回空、坏 JSON 或无有效 `queries`：

- 回退到本地 query 生成
- 优先从 `profile.preferences.interests` 取前 3 到 5 个兴趣标签
- 必要时再拼接 `core_traits` 中可搜索短词

目标是即使 LLM 不稳定，搜索策略仍可运行。

### DiscoveredContent 映射

从 B 站搜索结果中提取：

- `bvid`
- `title`
- `up_name`
- `up_mid`
- `cover_url`
- `duration`
- `view_count`
- `description`
- `source_strategy="search"`

`relevance_score` 本轮先用简单启发式：

- 默认 0
- 如果来自更靠前 query 或更靠前搜索位次，可给很小的排序增益

但不引入真正的用户匹配评分。

## 错误处理

- query 生成失败：记录日志，回退到本地 query
- 单个 query 搜索失败：记录日志，继续其他 query
- 全部 query 都失败：返回空列表，不抛出致命异常
- 缺失 `bvid` 的搜索结果：直接跳过

这样 `ContentDiscoveryEngine` 可以安全地继续运行其它策略。

## 测试设计

### SearchStrategy

- LLM 成功返回 query 列表时，会对每个 query 调用 `search()`
- 多 query 结果按 `bvid` 去重
- LLM 返回坏 JSON 时会回退到本地 query
- 单个 query 失败不影响其它 query
- 返回值是完整的 `DiscoveredContent`

### ContentDiscoveryEngine

- 注册 `SearchStrategy` 后能直接跑出结果
- strategy 返回空列表时不会崩溃

## 影响文件

- `src/openbiliclaw/discovery/strategies/strategies.py`
- `src/openbiliclaw/discovery/engine.py`
- `src/openbiliclaw/llm/prompts.py`
- `tests/test_search_strategy.py`
- `tests/test_discovery_engine.py`
- `docs/v0.1-todolist.md`
- `docs/changelog.md`
