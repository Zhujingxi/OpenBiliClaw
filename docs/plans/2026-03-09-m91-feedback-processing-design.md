# 9.1 反馈处理设计

## 背景

当前仓库已经具备部分反馈链路：

- CLI `openbiliclaw feedback <id> <like|dislike>` 已存在
- `recommendations` 表已经有 `feedback_type`、`feedback_note`、`feedback_at`
- 反馈会写入事件层，事件类型为 `feedback`

但从 `docs/v0.1-todolist.md` 的严格验收看，`9.1` 仍未完全收口，缺口主要有：

- `comment` 类型尚未接通
- 浏览器插件 popup 的“喜欢 / 不喜欢”按钮仍是占位态
- 后端 API 尚无统一 `POST /api/feedback` 入口

## 目标

将反馈处理补成统一闭环：

1. CLI 支持 `like / dislike / comment`
2. 后端 API 提供统一反馈入口
3. popup 可直接提交 `like / dislike / comment`
4. 所有入口最终都更新同一条推荐记录，并写入同一类 `feedback` 事件

## 设计原则

### 单一反馈写入路径

不在 CLI、API、popup 三处分别实现不同的业务逻辑。统一由 `RecommendationEngine.record_feedback()` + 事件层写入完成推荐反馈更新。

### v0.1 保持当前状态模型

本轮不新增独立反馈历史表。对同一条推荐，始终只保存“当前反馈状态”：

- `feedback_type`
- `feedback_note`
- `feedback_at`

若用户再次反馈，覆盖旧状态即可。

### popup 保持轻量交互

popup 不引入复杂模态框。`comment` 采用 inline 展开输入：

- 点击“写一句”
- 卡片内出现文本输入框和发送按钮
- 发送后收起并显示轻提示

## 接口设计

### CLI

命令扩展为：

```bash
openbiliclaw feedback <recommendation_id> <like|dislike|comment> [--note "..."]
```

规则：

- `like` / `dislike`：`--note` 可选
- `comment`：`--note` 必填

### API

新增：

`POST /api/feedback`

请求体：

```json
{
  "recommendation_id": 12,
  "feedback_type": "comment",
  "note": "这条方向不错，但讲得还是太浅。"
}
```

响应体：

```json
{
  "ok": true,
  "recommendation_id": 12,
  "feedback_type": "comment"
}
```

### popup

每张推荐卡片支持：

- `喜欢`
- `不喜欢`
- `写一句`

其中：

- `喜欢` -> `feedback_type = "like"`
- `不喜欢` -> `feedback_type = "dislike"`
- `写一句` -> `feedback_type = "comment"` 且附带 note

## 数据流

统一数据流如下：

1. 用户从 CLI 或 popup 触发反馈
2. 输入被标准化为 `(recommendation_id, feedback_type, note)`
3. 后端校验 recommendation 是否存在
4. 更新 `recommendations` 表的当前反馈状态
5. 追加一条 `event_type = "feedback"` 的事件
6. CLI 或 popup 显示反馈成功提示

事件 `metadata` 至少包含：

- `recommendation_id`
- `bvid`
- `feedback_type`
- `feedback_note`

## 错误处理

- recommendation 不存在：返回明确错误，不写事件
- `comment` 缺 note：返回校验错误
- popup 后端离线：显示“反馈失败，请确认本地后端已启动”
- 后端写入异常：返回失败，不伪造成功提示

## 测试策略

### Python

- API `/api/feedback`
  - `like` 成功
  - `comment` 缺 `note` 失败
  - recommendation 不存在失败
- CLI `feedback`
  - 支持 `comment --note`
- 推荐引擎 / 数据库
  - 推荐反馈字段写入正确
  - 反馈事件写入正确

### Extension

- popup helper
  - 构造反馈 payload
  - comment 缺 note 时拒绝提交
- popup runtime
  - `like` / `dislike` 触发正确请求
  - `comment` 发送成功后显示提示

## 文档更新

本轮完成后同步更新：

- `docs/v0.1-todolist.md`
- `docs/modules/recommendation.md`
- `docs/modules/cli.md`
- `docs/modules/extension.md`
- `docs/changelog.md`

`9.1` 中前三项应标记为完成。
