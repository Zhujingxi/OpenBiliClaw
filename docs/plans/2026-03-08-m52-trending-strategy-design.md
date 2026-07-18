# 5.2 排行榜策略设计

## 背景

`5.1` 已经让 `SearchStrategy` 可以基于用户画像主动搜索内容，但发现引擎还缺一条“从公共热点池中为这个人挑出值得看的内容”的路径。`5.2 排行榜策略` 的目标不是简单拉榜单，而是把“榜单候选 + 用户画像评估”打通，让 TrendingStrategy 能直接产出可用候选内容。

当前代码现状：

- `BilibiliAPIClient.get_ranking(rid)` 已可用
- `TrendingStrategy` 仍是空壳
- `ContentDiscoveryEngine.evaluate_content()` 仍未实现

## 目标

完成一个可直接运行的 `TrendingStrategy`：

- 拉取全站榜 `rid=0`
- 额外选择 3 到 5 个与用户画像相关的分区榜
- 对每条候选内容做 LLM 相关性评估
- 只保留高于阈值的结果
- 能被 `ContentDiscoveryEngine.register_strategy()` 直接运行

## 非目标

本轮不做：

- 多策略并发执行
- 内容缓存写入 `content_cache`
- 相关推荐链
- 复杂的分区 taxonomy 系统

## 方案选择

### 方案 A：TrendingStrategy + 独立评估入口

把排行榜拉取和内容评估拆成两层：

- `TrendingStrategy` 负责候选内容获取与粗筛
- `ContentDiscoveryEngine.evaluate_content()` 负责统一相关性评估

优点：

- 后续 `5.3`、`5.5` 可直接复用同一套评估接口
- 避免把 LLM 评估逻辑写死在某一个策略里

缺点：

- 这轮要同时补 strategy 和 engine

### 方案 B：把评估逻辑直接写进 TrendingStrategy

优点：

- 实现更快

缺点：

- 后面其它策略一定复制同样逻辑

### 方案 C：只拉榜单 + 启发式过滤

优点：

- 改动最少

缺点：

- 不满足“与用户画像匹配”的核心要求

### 结论

采用方案 A。

## 架构设计

### TrendingStrategy

依赖注入：

- `bilibili_client`
- `llm_service`
- 可选 `score_threshold=0.65`
- 可选 `max_related_rids=4`

执行流程：

1. 选择分区 `rid`
2. 调 `get_ranking(rid)` 拉榜单
3. 映射为 `DiscoveredContent`
4. 通过评估入口获取 `score` / `reason`
5. 过滤掉低于阈值的内容
6. 去重后返回结果

### 分区选择

固定包含：

- `rid=0` 全站榜

其余分区走 LLM 结构化选择：

```json
{
  "rids": [36, 188, 119, 181]
}
```

如果 LLM 失败，回退到默认分区集合。

本轮不追求 taxonomy 完整，只要分区选择：

- 有用户画像驱动
- 有稳定 fallback

### 内容评估

评估入口放在 `ContentDiscoveryEngine.evaluate_content()`，输入：

- 用户 core memory
- Soul 画像摘要
- 内容信息：标题、UP 主、描述、时长、播放量

输出严格 JSON：

```json
{
  "score": 0.78,
  "reason": "这个视频的讲解深度和表达方式都更贴近你偏好的高信息密度内容。"
}
```

写回：

- `DiscoveredContent.relevance_score`
- `DiscoveredContent.relevance_reason`

## 错误处理

- 分区选择失败：回退到默认 `rid`
- 单个榜单请求失败：记录日志，继续其它榜单
- 单条内容评估失败：记 0 分并跳过
- LLM 返回坏 JSON：视为评估失败
- 全部榜单都失败：返回空列表

## 测试设计

### TrendingStrategy

- 会拉 `rid=0` 和额外相关分区
- 多榜单结果按 `bvid` 去重
- 只保留高于阈值的内容
- 某个榜单失败不影响整体

### 内容评估

- 评估成功时写入 `score/reason`
- 坏 JSON 时安全降级

### Engine

- 注册 `TrendingStrategy` 后可直接跑出结果

## 影响文件

- `src/openbiliclaw/discovery/strategies/strategies.py`
- `src/openbiliclaw/discovery/engine.py`
- `src/openbiliclaw/llm/prompts.py`
- `tests/test_trending_strategy.py`
- `tests/test_discovery_engine.py`
- `docs/v0.1-todolist.md`
- `docs/changelog.md`
