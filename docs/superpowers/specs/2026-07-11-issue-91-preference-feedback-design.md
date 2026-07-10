# Issue #91 主动纠偏与反馈语义一致性设计

## 背景

GitHub Issue #91 报告了两类直接相关的问题：

1. 初始推荐不准时，用户不知道如何主动选择、修正偏好；连续点击“不喜欢”后，
   相似内容仍会出现，体验上像反馈没有生效。
2. 应用内通知中的兴趣、避雷操作只显示赞、减号、踩等图标，用户无法在点击前
   确认动作含义，尤其容易把“确认避雷”和“不是雷点”点反。

后续评论还报告了知乎任务失败和 LLM Token 消耗过快。只读调查确认这两项分别是
插件任务生命周期缺陷和独立的成本治理问题；它们不进入本设计：知乎另开专项修复，
Token 优化沿用 `perf/llm-token-diet` 工作流。

当前仓库已经具备两条主动纠偏能力：

- 桌面、移动、插件均可编辑画像，用户 override 会覆盖 AI 推断并进入后续推荐；
- 桌面、移动、插件均有自由文本聊天，用户可以直接说明喜欢、讨厌和原因。

因此本 Issue 不是重新发明画像编辑或聊天，而是修复反馈正确性，并把已有能力放到
用户遇到推荐错误的现场。

## 已确认根因

### 1. Topic 反馈只比较了一个不稳定轴

`Database.get_feedback_signals()` 只返回已反馈内容的 `topic_key`。Pool Curator 给候选
打分时却使用 `item.topic_group or item.topic_key`，即候选只要有 `topic_group`，就不再
比较 `topic_key`。

例如，用户不喜欢的内容记录为 `topic_key="动漫解说"`；新候选同时包含
`topic_key="动漫解说"` 和 `topic_group="动漫"`。当前实现会拿“动漫”去和只包含
“动漫解说”的反馈集合比较，最终 topic penalty 为零。

同步评分和带 embedding 的异步评分都有同一类单轴选择问题。

### 2. 跨平台反馈被标成 B 站事件

`POST /api/feedback` 已经能从 recommendation 记录中读取真实 `source_platform`，但
构造事件时仍固定使用 `SOURCE_BILIBILI`，context 也固定写成“在 B 站……”。知乎、
YouTube、小红书等反馈因此带着错误来源进入 memory 和后续画像学习。

### 3. 主动纠偏入口存在，但不在错误发生处

画像编辑藏在画像页，聊天藏在独立抽屉或最后一个 Tab。推荐区没有告诉用户：
卡片“不喜欢”是软信号；如果系统整体理解错了，应直接编辑画像或用自由文本纠正。

### 4. Probe 操作文案跨端漂移

后端对兴趣和避雷 probe 都支持 `confirm`、`reject`、`defer`、`chat`。当前各端表现：

- 桌面消息中心只显示图标，可见文字缺失；
- 移动消息层已有文字，但移动画像页仍只有 `✓/✗`；
- 插件消息中心和画像页有部分文字，但缺少 `defer`；
- 同一 action 在不同位置被写成“不是”“不喜欢”“暂时忽略”等不同语义。

## 目标

- 一次卡片“不喜欢”立即影响同 UP、同细粒度 topic、同粗粒度 topic 和同 franchise
  的后续候选软排序，不再因 `topic_key/topic_group` 轴错位而失效。
- 多平台反馈事件保留真实来源和自然语言 context。
- 用户在推荐错误的现场能发现“编辑画像”和“直接告诉阿B”两条确定性纠偏路径。
- 桌面、移动、插件中的兴趣/避雷 probe 使用同一套可见文字和 action 映射。
- `defer` 在三端消息中心和画像页均可用，且不被误写成 reject。
- 保持 Issue #98 已建立的乐观反馈、撤销和后台学习边界，不把 LLM 工作重新放回
  `/api/feedback` 请求路径。

## 非目标

- 不把单次卡片“不喜欢”直接写成永久 disliked topic；用户可能只是不喜欢该内容的
  质量、表达方式或作者，而非整个主题。
- 不改变 `feedback_batch_threshold`、LLM 画像重分析策略或候选池 admission policy。
- 不新增聊天 API、画像编辑 API、数据库表、配置字段、运行时依赖或消息队列。
- 不重构所有前端为共享组件，也不由后端下发 UI action schema。
- 不在本 worktree 修复知乎 dispatcher、任务 timeout/throttle 或 LLM Token 消耗。
- CLI 没有 probe 通知卡展示面；保留现有 `openbiliclaw feedback` 行为，并在文档中说明
  此次文案一致性只适用于桌面、移动和插件 UI。

## 方案选择

采用“后端反馈纠正 + 三端语义契约 + 就地纠偏入口”的聚焦式方案。

未采用以下方案：

- **只改桌面按钮文字**：能降低误点，却不能修复相似内容降权失效、跨平台来源错误和
  其他端的 `✓/✗` 漂移。
- **重写反馈学习管线**：让单次反馈直接触发画像 LLM 会增加延迟、Token 成本和误判
  风险，也会破坏 Issue #98 的请求隔离。
- **后端下发 action schema**：长期能消除文案重复，但需要同时改 API models、缓存和
  三套前端解析；本 Issue 用明确契约与回归测试即可约束一致性。

## 后端设计

### 反馈 Topic 别名集合

`get_feedback_signals()` 同时返回 `topic_key` 和 `topic_group`。Pool Curator 构造
`FeedbackSignals` 时，把每条 like/dislike 的两个非空 topic 值都加入对应集合。

候选也同时暴露两个非空 topic 值：

```text
candidate_topics = {normalized(topic_key), normalized(topic_group)} - {""}
```

同步评分规则：

- `candidate_topics` 与 disliked 集合有任意交集，施加一次
  `_FEEDBACK_DISLIKE_TOPIC_PENALTY`；
- 与 liked 集合有任意交集，施加一次 `_FEEDBACK_LIKE_TOPIC_BONUS`；
- 即使 key 和 group 同时命中，也不叠加两次同类 topic penalty/bonus；
- UP 与 franchise 仍是独立轴，可以和 topic adjustment 叠加；
- 无关 key/group 的候选保持当前分数。

异步 embedding 评分规则：

- 同时为候选的 key/group 获取 embedding；
- 任一候选 topic 与任一 disliked topic 达到现有 similarity threshold 时，施加一次
  dislike topic penalty；
- liked topic 同理施加一次 bonus；
- embedding 不可用时回退到上述精确集合交集；
- 不改变 threshold、penalty 常量或 embedding provider。

`FeedbackSignals` 现有字段名保持不变，字段内容从“只含 topic_key”扩展为“包含
key/group 的标准化 topic 标签”，避免扩大公开接口改动。

### 跨平台反馈事件

`POST /api/feedback` 从 recommendation 记录读取 `source_platform`：

1. 非空值标准化后直接传给 `build_event()`；
2. 旧 recommendation 缺字段时兼容回退 `bilibili`；
3. 使用 `format_event_context()` 按真实平台生成基础 context；
4. feedback 类型继续保留 like/dislike/comment/dismiss 的明确动作词；
5. note 追加到 context，并继续写入 `feedback_note` metadata；
6. metadata 明确保留 recommendation id、内容 id 和 feedback type。

这条路径继续只做持久化、轻量 cognition 和后台 batch scheduling。它不等待画像 LLM。

## 前端语义契约

所有可操作 probe 使用以下可见文字；`aria-label`、`title` 和提交 action 必须一致：

| Probe | Action | 可见文字 | 后端值 |
| --- | --- | --- | --- |
| 兴趣 | confirm | 确认喜欢 | `confirm` |
| 兴趣 | defer | 暂时搁置 | `defer` |
| 兴趣 | reject | 确认不喜欢 | `reject` |
| 兴趣 | chat | 多聊聊 | `chat` |
| 避雷 | confirm | 确认避雷 | `confirm` |
| 避雷 | defer | 搁置避雷 | `defer` |
| 避雷 | reject | 不是雷点 | `reject` |
| 避雷 | chat | 多聊聊 | `chat` |

按钮必须显示文字，不用颜色、图标或 `title` 作为唯一语义。现有图标可以保留为装饰，
但文字始终可见。按钮使用真实 `<button type="button">`，支持键盘焦点；pending、成功、
失败状态继续通过现有 status/result 行和 `aria-live` 表达。

### 桌面 Web

- 消息中心把兴趣/避雷的赞、减号、踩图标组替换为上述文字按钮。
- 画像页的 probe 行同步使用契约文案，保留已有 pending action/撤销机制。
- 推荐区标题附近增加一条紧凑纠偏提示：
  “推荐不准？编辑画像，或直接告诉阿B。”
- “编辑画像”打开现有画像抽屉并进入编辑态；“直接告诉阿B”打开现有聊天抽屉并聚焦
  输入框，不新增 API。

### 移动 Web

- 消息层保留现有文本布局，但调整为契约中的精确文案。
- 画像页把 `✓/✗` 改为可见文字，并补齐 `defer`。
- 推荐 header 增加同一纠偏提示；两个入口切换到现有 profile/chat Tab，聊天入口聚焦
  现有输入框。

### 插件 Side Panel

- 消息中心和画像页统一契约文案，并补齐 `defer`。
- 推荐 header 增加同一纠偏提示；入口切换到现有画像/对话 Tab，画像入口直接进入编辑
  模式，对话入口聚焦 `chatInput`。
- 不新增浏览器系统通知；本 Issue 的“通知”仍指应用内消息中心。

## 数据流

### 卡片反馈

```text
用户点击喜欢/不喜欢
  -> 现有乐观 UI / 撤销屏障
  -> POST /api/feedback
  -> recommendation feedback 持久化
  -> 真实 source_platform 的 feedback event
  -> 轻量 cognition + 后台 FeedbackBatchScheduler
  -> 下一轮 Pool Curator 同时比较 topic_key/topic_group
  -> 相似候选立即获得软 penalty/bonus
```

### 主动纠偏

```text
推荐区纠偏提示
  -> 编辑画像 -> 现有 /api/profile/edit -> user override -> 确定性影响推荐
  -> 告诉阿B -> 现有 chat API -> dialogue learning -> 后台更新画像
```

probe 的 confirm/reject/defer/chat 继续使用现有 endpoint；本设计只修展示契约和缺失入口。

## 并发与错误处理

- 不改变 Issue #98 pending action coordinator 的一次性提交、撤销和失败回滚语义。
- `defer` 请求失败时恢复原按钮状态，不把 probe 从本地列表永久移除。
- profile/chat 跳转只操作本地导航；目标元素不存在时保留当前页面，不发送写请求，并显示
  现有 toast/status 错误。
- recommendation 缺 `source_platform` 时回退 B 站，保证旧数据库向后兼容；未知非空 slug
  交给 `format_event_context()` 按 slug 展示，不伪装成 B 站。
- topic key/group 为空时不产生 topic adjustment；UP/franchise adjustment 仍正常执行。

## 测试设计

### 后端

- `tests/test_storage.py`：feedback signal 同时返回 `topic_key` 和 `topic_group`。
- `tests/test_pool_curator.py`：
  - 候选 group 非空时，相同 key 仍触发一次 penalty；
  - key 不同但 group 相同仍触发一次 penalty；
  - key/group 同时命中不重复 penalty；
  - liked topic 同样覆盖 key/group 且只加一次 bonus；
  - 异步 embedding 路径对 key/group 取最大匹配并只调整一次；
  - 无关 topic 保持中性。
- `tests/test_api_app.py`：知乎/YouTube 推荐反馈生成真实 source/context，旧记录缺来源时回退
  B 站，note 和 metadata 保持完整。

### 桌面 Web

- 更新 `tests/test_desktop_web_probe_defer.py`，禁止 icon-only probe action，锁定八条文案与
  action payload。
- 扩展 `tests/test_desktop_web_issue_98_e2e.py`，验证可见文字、defer payload、失败回滚和
  pending/撤销语义未回归。
- 新增或扩展纠偏入口测试，验证画像入口进入编辑态、聊天入口打开并聚焦输入框。

### 移动 Web

- 扩展 `tests/test_mobile_web_view_models.py` 和 profile view 测试，验证消息层与画像页使用
  同一文案，`defer` 调用正确 endpoint。
- 验证推荐 header 的画像/聊天入口切换到正确 Tab。

### 插件

- 扩展 `extension/tests/popup-message-actions.test.ts`，覆盖兴趣/避雷四动作和精确文案。
- 扩展 profile probe 测试，证明 `defer` 可提交且失败会恢复按钮。
- 增加推荐纠偏入口测试，验证 Tab 切换、进入画像编辑和聚焦聊天输入框。

### 完整验证

```bash
PYTHONPATH=src .venv/bin/pytest -q --tb=short
.venv/bin/ruff check src tests
.venv/bin/mypy src
cd extension && npm test && npm run typecheck && npm run build
```

真实 UI 验证覆盖 375px、768px、1024px、1440px：按钮文字不截断、无水平滚动、键盘
焦点可见、撤销/失败状态不改变 action 语义。

## 文档范围

按仓库强制规则同步：

- `docs/modules/recommendation.md`：feedback topic 双轴软调整和跨平台来源；
- `docs/modules/soul.md`：卡片软反馈与画像编辑/对话确定性纠偏边界；
- `docs/modules/runtime.md`：三端纠偏入口及后台学习边界；
- `docs/changelog.md`：Issue #91 用户可感知修复。

本设计不新增模块、依赖、配置、CLI 命令或顶层数据流节点，因此不修改架构图、
`docs/modules/config.md`、`docs/modules/cli.md` 和安装器文档。

## 验收标准

- 对 `topic_key="动漫解说", topic_group="动漫"` 的内容点不喜欢后，下一候选只要 key
  或 group 任一匹配，就获得一次 topic penalty；两者同时匹配也只罚一次。
- 喜欢反馈的 key/group bonus 遵循同一规则。
- 知乎推荐反馈事件的 `source_platform` 为 `zhihu`，context 明确写“在知乎”；其他平台
  不再被伪装成 B 站。
- 桌面、移动、插件的兴趣/避雷按钮在点击前即可读懂，八条文案与后端 action 完全一致。
- 三端画像页和消息中心都能搁置 probe；搁置不会被记录成 reject。
- 三端推荐区都能就地进入画像编辑或自由文本聊天。
- 卡片反馈仍保持乐观 UI、10 秒撤销和后台学习，不新增请求内 LLM 延迟。
- 定向测试、Ruff、MyPy、完整 Python 测试、插件测试/typecheck/build 全部通过。
