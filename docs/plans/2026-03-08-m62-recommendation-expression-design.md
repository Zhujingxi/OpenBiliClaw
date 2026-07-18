# 6.2 朋友式推荐表达设计

## 背景

`6.1` 已经完成推荐排序和最小推荐历史闭环，但推荐结果仍然缺少“为什么是你会喜欢这条内容”的自然语言表达，CLI `recommend` 也还只是 stub。`6.2` 的目标是把推荐链条推进到“能真实展示给用户”的程度。

## 目标

- 实现 `RecommendationEngine.generate_expression()`
- 让 `generate_recommendations()` 为每条推荐补 `expression` 和 `topic_label`
- 将文案回写到 `recommendations` 表
- 将 CLI `recommend` 从 stub 升级为真实展示，并在展示后标记 `presented`

## 范围

### 包含

- 结构化推荐表达 prompt
- 推荐表达和个性化 topic label 生成
- 推荐记录的表达更新与展示状态更新
- CLI `recommend` Rich 输出

### 不包含

- 用户反馈写回
- 推荐主题分组展示
- API / 插件消费推荐结果

## 设计

### 1. 输出结构

`generate_expression()` 让 LLM 输出严格 JSON：

```json
{
  "expression": "我觉得你会喜欢这个，它讲问题的方式很对你最近那种想把事情想透的状态。",
  "topic_label": "你最近那种想把问题想透的状态"
}
```

约束：

- `expression`：50~150 字
- 中文、口语化、有温度
- 必须解释“为什么是这个人会喜欢这个内容”
- 避免“根据你的兴趣”“你可能会喜欢”这类算法腔套话

### 2. 数据流

`generate_recommendations()`：

1. 先得到排序后的 `Recommendation`
2. 对每条调用 `generate_expression()`
3. 把 `expression/topic_label` 回写到 `recommendations` 表对应记录
4. 返回补完后的推荐列表

### 3. CLI 展示

`openbiliclaw recommend`：

- 若无未推荐内容：提示先执行 `discover`
- 若有推荐：
  - 显示标题、UP 主、推荐理由、BV 号/链接
  - 展示结束后将对应 recommendation 标记为 `presented=1`
  - 同时写 `presented_at`

## 数据库接口

`Database` 增加：

- `update_recommendation_content(id, expression, topic)`
- `mark_recommendations_presented(ids)`

## 测试策略

- 验证 `generate_expression()` 能解析结构化 LLM 响应
- 验证 `generate_recommendations()` 会把表达回写到历史记录
- 验证 CLI `recommend` 在有推荐/无推荐时的输出
- 验证 CLI 展示后会把推荐记录标记为已展示

## 文档更新

- `docs/v0.1-todolist.md`
- `docs/modules/recommendation.md`
- `docs/modules/cli.md`
- `docs/changelog.md`
