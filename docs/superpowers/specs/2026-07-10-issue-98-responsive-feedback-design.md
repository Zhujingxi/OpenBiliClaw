# Issue #98 即时反馈、可撤销提交与事件循环隔离设计

## 背景

GitHub issue #98 报告桌面 Web 在推荐反馈、猜测兴趣和猜测避雷等即时交互中出现数秒卡顿、按钮长期禁用、全量重绘导致卡片位置漂移，以及误点后无法撤销。

源码核对确认了三个独立问题：

1. 桌面推荐卡 `handleCardAction()` 在更新 UI 前等待 `POST /api/feedback`；兴趣/避雷两个展示面也在显示结果前等待 respond API。
2. 反馈后的 LLM 学习已经由 `FeedbackBatchScheduler` 延迟到后台，兴趣确认后的 `force_tick()` 也已经后台化；请求延迟并非来自这些 LLM 调用本身。事件循环仍可能被推荐 MMR 选择和 supergroup 语义聚类等同步纯 Python CPU 循环连续占用。
3. 当前工作区存在一版尚未完成的乐观反馈和 `asyncio.to_thread` 改动，但没有真正撤销语义，源码正则测试无法验证真实 DOM 行为，并且与其他未提交工作混在一起。本功能实施必须在独立 worktree 中从最新 `origin/main` 开始，不能覆盖或携带这些现有改动。

## 目标

- 推荐卡的喜欢、不感兴趣、忽略，以及兴趣/避雷探针的确认、拒绝、暂时忽略，在点击后的同一帧更新当前组件。
- 普通反馈不全量调用 `renderAll()` / `renderMessages()` / `renderProfileDetails()`，避免卡片位置因网络响应而漂移。
- 每个上述动作提供 10 秒真实撤销窗口；撤销后后端、事件历史和画像学习都不应留下该动作。
- 网络失败后恢复原状态，避免 UI 与后端事实分叉。
- 换一批不等待旧卡批量 dismiss，并且不会把当前可见卡再次端回。
- 推荐选择和语义聚类的同步 CPU 热路径不再连续独占 asyncio 事件循环；排序和聚类结果保持确定。
- 用行为级自动化测试和真实浏览器验证覆盖布局稳定、撤销、失败回滚、请求时序与事件循环响应性。

## 非目标

- 不引入新的消息队列、进程服务、运行时依赖或配置项。
- 不把纯 Python CPU 计算描述为真正并行；线程只用于让事件循环线程在 GIL 时间片边界重新获得调度。
- 不改变推荐排序规则、MMR 参数、平台/话题/风格上限或 amplification guard。
- 不给聊天消息提供撤销。聊天已经有独立的输入、thinking 和轮询结果状态，且会产生对话回复，不属于单击反馈。
- 不同时重写插件 side panel 和移动 Web。本 issue 的复现面是桌面 Web；共享后端行为保持向后兼容。

## 方案选择

采用“10 秒可撤销提交屏障”。点击先更新本地组件并登记 pending action，10 秒内撤销只取消本地待提交操作；到期后才向现有 API 提交。

未采用以下方案：

- **立即 POST，再调用反向撤销 API**：需要同时补偿 recommendations/content_cache、已追加的行为事件和可能已经启动的画像学习；在现有 append-only 事件模型中难以保证原子一致。
- **立即 POST，只恢复前端**：视觉上撤销但后端已经学习，属于错误语义。

提交屏障将 durable write 延迟 10 秒，但用户感知反馈仍是即时的。若页面在窗口内关闭，`pagehide` 会使用 `fetch(..., {keepalive: true})` 刷出未撤销操作；页面已经不可交互，此时撤销窗口自然结束。

## 前端架构

### Pending action 协调器

新增一个无 DOM 依赖的小型协调器，由桌面 `app.js` 使用。它负责：

- 以稳定目标键登记 pending action；推荐键为 `platform + (bvid || content_id)`，探针键为 `probe_type + normalized_domain`。
- 保存 `commit`、`rollback`、到期时间与计时器。
- 同一目标尚未到期时拒绝重复提交。
- `undo(key)` 在提交前取消计时器并调用 rollback。
- 到期时只调用一次 commit；commit 失败调用 rollback，并把错误交给 UI 提示。
- `flushAll()` 在 `pagehide` 中以 keepalive 请求提交尚未撤销的动作。
- 已进入 commit 的动作不能再显示可用的撤销入口。

协调器的时间源和 timer 函数可注入，以便不用真实等待 10 秒即可做确定性单元测试。

### 推荐卡反馈

点击喜欢、不感兴趣或忽略时：

1. 记录 `item.feedback_type` 原值和当前卡片状态。
2. 立即设置目标 feedback type，仅修改当前 card：按钮 active/disabled、pending 样式和 status-line 文案。
3. status-line 显示可聚焦的“撤销”按钮，并保留卡片在原网格位置；点击过程不调用 `renderAll()`。
4. 10 秒内撤销：取消 commit，恢复 item、按钮、样式和文案。
5. 到期：调用现有 `POST /api/feedback`。成功后移除撤销入口并显示已记录状态；为保持布局稳定，负向卡片不在响应回调里重排网格，而在下一次用户主动过滤、换批或后端 hydration 时按既有过滤规则消失。
6. 请求失败：恢复 item 和当前 card，重新启用动作并显示失败 toast。

评论反馈维持即时 composer 收起和 pending 状态，但不提供撤销；它包含用户文本，仍在后台提交并在失败时恢复可重试状态。

### 兴趣与避雷探针

消息抽屉和画像页共用同一 pending key，避免同一 domain 在两个展示面重复提交。

点击 confirm/reject/defer 时：

1. 立即把对应组件替换为结果文案和“撤销”按钮，不重建整个消息列表或画像详情。
2. 本地 `handledProbeKeys` 只在 pending 期间阻止旧 snapshot/runtime event 重水合该探针。
3. 10 秒内撤销会恢复原组件 HTML、事件绑定和 handled 状态。
4. 到期后调用现有 interest/avoidance respond API；成功后确认 handled 状态，并在后台刷新 profile。
5. 失败则恢复原组件和 handled 状态。

`chat` 响应继续走现有 inline composer、thinking bubble 和 chat-turn polling，不进入提交屏障。

### Toast 与可访问性

- 撤销入口优先放在当前组件的 status/result 行，避免多个快速动作竞争一个全局 toast。
- 全局 toast 只报告提交失败、页面级状态和换批结果。
- 撤销按钮使用真实 `<button type="button">`，可用键盘聚焦和 Enter/Space 激活。
- pending/committed 状态通过文本和 `aria-live` 表达，不只依赖颜色。
- 遵守已有 `prefers-reduced-motion`；本修复不新增强制位移动画。

## 换一批流程

现状会先等待所有当前卡片 dismiss，再发 reshuffle，造成可见阻塞。新流程为：

1. 始终收集当前可见卡的稳定内容 ID，作为 `excluded_bvids` 发送给 reshuffle API；该排除与“换一批前忽略当前推荐”开关相互独立。
2. 后端 reshuffle 请求体新增可选 `excluded_bvids`，缺省/空 body 保持旧客户端行为。
3. 推荐引擎在候选读取时为排除项补足取数窗口，并在平台 floor top-up 之后再次做最终排除，保证返回批次不含当前卡且在池足时仍能补满。
4. 收到新批次后立即替换列表并渲染。
5. 只有开关开启时，才在换批成功后 fire-and-forget 提交旧卡 dismiss；批量失败只提示，不把旧卡重新插入新批次。
6. 使用列表 generation token 保护所有延迟的列表写入，旧批次回调不能删除或恢复新批次同 key 卡片。

## 后端事件循环隔离

### 已经满足的请求路径

- `/api/feedback` 只落库、传播事件并调度 `FeedbackBatchScheduler`；昂贵的反馈学习不在响应内执行。
- interest confirm 的新探针 `force_tick()` 已经通过 background task 执行。
- avoidance confirm 的 dislike 画像写回已经通过 background task 执行。

这些事实通过回归测试固化，不重复引入新的线程层。

### 需要 offload 的 CPU 热路径

- `_select_diversified_batch()` / `_select_with_mmr()` 保持同步、纯函数式和确定性，新增 async wrapper 通过 `asyncio.to_thread()` 调用。
- supergroup canonical map 的两两相似度与 union-find 构建保持同步 helper，新增 async wrapper 通过 `asyncio.to_thread()` 调用。
- `serve()` 与 prewarm 调用 async wrapper。
- 记录总墙钟耗时；超过既定慢阈值时写 warning，便于后续判断是否需要 numpy/进程池优化。

`asyncio.to_thread()` 对这些纯 Python 循环不会带来多核并行，但会让 event-loop 线程在 GIL 时间片边界继续处理 `/api/health`、反馈和 WebSocket。独立进程池会引入候选/embedding 大对象序列化、生命周期和打包复杂度，本切片不采用。

## 并发与错误处理

- pending action 的 key 使用稳定内容/领域身份，不使用 `Date.now()` 合成的 recommendation id 做去重。
- commit、timeout 和 pagehide flush 共享一次性状态转换，最多发出一个请求。
- 在列表被 reshuffle/hydrate 后，旧 generation 的 DOM/list rollback 变为 no-op。
- 请求 4xx/5xx/timeout 均视为失败；保留后端 detail 优先的现有错误文案解析。
- pagehide keepalive 请求不尝试更新已卸载页面；正常页面中的失败才回滚 DOM。
- reshuffle 返回空批次时保留当前列表，不提交批量 dismiss。

## 测试设计

### JavaScript 行为测试

对 pending action 协调器使用可注入 fake timer，覆盖：

- 点击后立即进入 pending，10 秒前不调用 commit。
- undo 调用 rollback 且永不 commit。
- 到期只 commit 一次。
- 重复同 key 动作不重复提交。
- commit failure 调用 rollback。
- flush 与 timeout 竞态仍只提交一次。

对桌面 app 的行为契约覆盖：

- 推荐反馈分支不在网络回调中调用 `renderAll()`。
- 两个探针展示面都使用协调器和同一稳定 key。
- pagehide 注册 flush。
- reshuffle 始终发送 visible exclusions，dismiss 不阻塞换批。

### Python/API 测试

- reshuffle 可选 body 向后兼容。
- `excluded_bvids` 在大于旧候选窗口和平台 floor top-up 两种情况下都不会回流，池足时仍返回 limit。
- `/api/feedback` 在 LLM batch 尚未完成时立即返回，scheduler 后台执行。
- MMR/supergroup async wrapper 确实通过线程 offload，并与 sync helper 输出完全一致。
- 在可控 CPU 选择运行期间，event loop heartbeat/health coroutine 能持续推进。

### 真实浏览器验证

使用 Playwright 拦截 API 并人为延迟响应：

- 连续点击相邻卡片时，第二次点击命中的卡片 identity 不改变。
- 点击反馈后的首帧出现状态与撤销入口，网格卡片 bounding box 不移动。
- 10 秒内撤销不产生反馈请求。
- 到期后恰好产生一个请求；500 响应恢复卡片。
- 兴趣/避雷消息和画像行同样即时、可撤销且不全量重绘。

## 文档与发布

按仓库强制规则同步：

- `docs/modules/runtime.md`：桌面即时交互、提交屏障和 CPU offload。
- `docs/modules/recommendation.md`：推荐反馈、reshuffle exclusions 与确定性 offload。
- `docs/modules/soul.md`：兴趣/避雷探针的可撤销提交与后台 LLM 边界。
- `docs/changelog.md`：issue #98 用户可感知修复。

本改动不新增配置字段、CLI 命令或跨模块架构节点，因此不修改 config/CLI 参考，也不需要重画顶层架构图。

## 验收标准

- 延迟 30 秒的反馈 API 不会让其他卡片按钮或探针按钮失去即时响应。
- 普通反馈点击与响应完成都不会触发全网格/全列表重建。
- 10 秒内撤销后，网络、数据库事件与画像均无该操作；到期或 pagehide 后至多提交一次。
- API 失败时组件恢复到点击前状态。
- 换一批不等待 dismiss，且新旧批次无内容 ID 交集。
- 相同输入下 offload 前后推荐顺序与 supergroup map 完全相同。
- 定向测试、ruff、mypy、完整 pytest 通过；真实浏览器场景通过。
