# 6.1 推荐排序设计

## 背景

`content_cache` 已经能稳定写入 discovery 结果，`RecommendationEngine` 仍然是空壳。`6.1` 的关键不是生成文案，而是先把“从缓存中挑出最值得推、并且没推过”的最小闭环立起来。只要这条链路成立，后面的 `6.2` 表达生成和 `6.3` 推荐持久化都可以在此基础上扩展。

## 目标

- 从 `content_cache` 中选出 Top-N 未推荐过的内容
- 按 `relevance_score` 为主进行排序
- 将本轮选中的结果写入 `recommendations` 表，形成最小历史闭环
- 为后续 CLI `recommend` 和推荐表达生成提供稳定输入

## 范围

### 包含

- 查询未进入 `recommendations` 表的候选内容
- 构造最小 `Recommendation` 结果
- 写入推荐历史，避免下次重复选中
- 补最小模块文档 `docs/modules/recommendation.md`

### 不包含

- 朋友式推荐文案生成
- `topic_label` 个性化主题分组
- `presented_at` 真正展示时机的更新
- 用户反馈回写

## 设计

### 1. 推荐来源

优先级：

1. 如果显式传入 `discovered` 列表，则从该列表排序
2. 如果未传 `discovered`，则从 `content_cache` 读取未推荐内容

这样兼容两种调用方式：

- discovery 刚完成后立即推荐
- CLI 或后续定时任务直接从缓存拉取推荐

### 2. 排序规则

- 主排序键：`relevance_score` 降序
- 次排序键：`view_count` 降序
- 若仍相同，按 `bvid` 稳定排序

推荐对象先只填：

- `content`
- `confidence` = `content.relevance_score`
- `presented` = `False`

### 3. 推荐历史

`generate_recommendations()` 产出推荐时立即写入 `recommendations` 表：

- `bvid`
- `confidence`
- `expression=""`
- `topic=""`
- `presented=0`

语义：

- “已被系统选中，但未确认展示给用户”

这样可以避免下一批重复选择相同内容，同时不把“选中”和“展示”混淆。

### 4. 数据库接口

`Database` 增加：

- `get_unrecommended_content(limit=100)`
- `insert_recommendation(...)`
- `get_recommendations(limit=100)`

## 测试策略

- 验证只返回未推荐内容
- 验证排序按 `relevance_score` 优先
- 验证生成推荐后会写入历史
- 验证第二次调用不会重复选中已记录内容

## 文档更新

- `docs/v0.1-todolist.md`
- `docs/changelog.md`
- `docs/modules/recommendation.md`
