# 候选池即时出片设计

## 目标
把推荐系统从“刷新时现找现做”改成“后台持续补 discovery pool，前台即时从池子里换一批”，让 popup 点击后能立刻看到新内容。

## 问题
当前 `立即刷新` 走的是完整 `discover + recommend`，即使已经异步化，仍然要等待 LLM 生成朋友式文案或 provider fallback。用户真正想要的是“马上有新内容看”，不是“马上完成一整轮后端流水线”。

## 核心设计

### 1. content_cache 升级为真正的 discovery pool
现有 `content_cache` 保留候选内容、`relevance_score`、`relevance_reason`，并补充池子状态字段：
- `pool_status`: `fresh | shown | feedbacked | stale`
- `recommended_at`
- `feedback_type`
- `feedback_at`

入池时就写好基础可解释理由 `relevance_reason`，使内容天然具备“可展示性”。

### 2. refresh 和 reshuffle 职责分离
- `POST /api/recommendations/refresh`
  - 继续表示“后台补货”
  - 只负责往池子里补内容
- `POST /api/recommendations/reshuffle`
  - 从 discovery pool 即时挑一批新的候选
  - 立刻写入 `recommendations`
  - 返回前端可用结果

### 3. 推荐理由分层
- 基础层：`relevance_reason`
  - discover 入池时生成
  - popup 可以直接展示
- 增强层：`expression/topic_label`
  - recommendation 阶段补充
  - 有则覆盖基础理由
  - 没有也不阻塞展示

### 4. popup 交互
- 将按钮文案从“立即刷新推荐”调整为“换一批”
- 点击后调用 `reshuffle`
- 成功后立刻重拉 `/api/recommendations`
- 继续保留后台持续补货逻辑，但不再把它当作“马上给用户新内容”的路径

## 验收标准
- popup 点“换一批”后，能在秒级看到新推荐列表
- 新推荐可以直接回退展示 `relevance_reason`
- `refresh` 仍然可后台补货，不影响 `reshuffle`
- 已展示/已反馈内容不会立刻重复出现在下一批里
