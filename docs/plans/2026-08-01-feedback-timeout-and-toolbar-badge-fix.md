# Feedback 超时与工具栏角标修复设计（2026-08-01）

## 问题与根因

### 1. 推荐反馈先超时、刷新后又成功

`POST /api/feedback` 已经写完 recommendation 反馈字段和 memory event 后，仍在响应内 `await ProfileUpdatePipeline.ingest()`。当 INTEREST buffer ready 时，`ingest()` 会进入真实偏好 LLM，生产耗时 30–76 秒；桌面客户端 30 秒先超时并回滚，后端随后成功，于是刷新后反馈又出现。连续点击还会让多个 ingest 同时 drain / 分析，并交错保存 `pipeline_state.json`。

单纯把 ingest 换成 `asyncio.create_task()` 仍不可靠：进程在任务把信号写入 pipeline state 前退出，只有内存 task registry，没有可重建的领取依据；shutdown cancel 会放大这个窗口。反过来，如果 HTTP 在 200 前等待 enqueue 的 `_ingest_lock`，正在运行的后台 tick / LLM 又会让第二、第三次点击继续等待几十秒。

### 2. 工具栏红色“1”与消息面板不一致

红色数字不是 popup `state.messages`，而是 service worker 每 30 秒请求 `/chat/pending-confirmations?count_only=1` 后写入 action badge。待聊确认是“对话”内部工作列表，不是全局未读消息；把它提升成工具栏数字会让用户在消息面板为空时长期看到“1”。

## 选择

### Durable feedback owner

统一兴趣线开启时，以 SQLite `events` 表作为 durable ingress queue。app-owned
`EventProcessingScheduler` 同时调度 generic 与 content-feedback owner；旧名
`FeedbackBatchScheduler` 只保留为兼容 alias，内容反馈语义仍由
`SoulEngine.process_feedback_batch_if_needed()` 独占：

```text
/api/feedback 或 /api/events(raw dislike)
  → events.feedback durable commit（request_id 幂等）
  → recommendation projection 独立 commit；失败由同 request_id 重试补齐
  → schedule debounce
  → HTTP 200（不获取 pipeline lock，不调用 LLM）

EventProcessingScheduler
  → query events.feedback where id > feedback cursor
  → explicit content reaction, non-import → FEEDBACK signal(id="feedback-event-{row_id}")
  → ProfileUpdatePipeline.checkpointed_enqueue_batch()
     （buffer + cursor 同一 pipeline_state.json 原子 snapshot）
  → ProfileUpdatePipeline.tick_if_buffered() → ready layer LLM
```

- `enqueue()` / `enqueue_batch()` 是公开的 buffer-only API，只做既有 retraction 预处理、append/evict、轻量 observe 与 `_save_state()`；`ingest_batch()` 复用同一个 locked helper，再消费 ready layer。
- stable event-derived signal ID 是 snapshot 重试的幂等键；buffer 中已有同 ID 时不追加。
- cursor 不再被一次性 migration marker 截断。marker 只保留升级 provenance；完成 owner-v2 cutover 后，所有后续 live rows 继续由同一个 cursor owner 领取。
- **v0.3.191 → owner v2 cutover fence**：旧版本在 marker 已存在后把 live feedback 同时写 event 与 direct pipeline，却不再推进 feedback cursor。若新版本直接从旧 cursor 连续扫，会把这些行再学一次。因此 consumer cursor、owner version、cutover time/event ID 全部写在权威 `pipeline_state.json` 中：检测到旧 marker 且尚未 cutover 时，在接受任何新 event-only 写入前，把 cursor fence 到当时最大的 feedback **row id**；不用 `created_at`，因为浏览器回填时间可乱序。`feedback_state.json` 只作为这次迁移的输入与兼容 provenance 镜像，不能用于 owner 验收。API startup 和两个反馈入口、CLI、OpenClaw 都先 prepare；fresh install 没有旧 marker 时只发布 owner v2、不抬 cursor，保留真正未领取的 durable 行。
- owner predicate 固定为 `event_type=feedback`、`metadata.feedback_type ∈ {like,dislike,comment,dismiss}` 且 `metadata.import_source` 为空。hypothesis feedback（无 `feedback_type`）与 Bangumi/import snapshot 已在各自对象结算或 guided init 中学习，只推进 feedback cursor，不进 FEEDBACK 强信号，也从 unified generic 增量路径排除；retraction 仍走 generic path，保留既有内存折价 / tombstone 语义。profile backfill 使用同一 namespace 过滤并仍推进自己的扫描水位。
- app startup 主动 schedule 一次统一线 owner，覆盖“event 已提交但 debounce 尚未运行就退出，之后用户也不再点击”的恢复场景。
- `unified_interest_line=false` 继续使用旧 feedback batch；不把 feedback enqueue 到 pipeline，保证 rollback 语义不变。

### Pipeline concurrency and commit boundary

`enqueue`、`ingest_batch`、`tick_if_buffered`、`tick`、`flush` 的 buffer mutation / layer drain 共享 `_ingest_lock`。ready layer 的 LLM update 不重叠，pipeline state snapshot 不交错。event owner 用 `checkpointed_enqueue_batch()` 在同一锁内发布 buffer+cursor，replace 失败时两者一起回滚；随后 `tick_if_buffered()` 只在存在 durable signals 时 drain，空恢复 pass 不跑 speculator/cognition。独立周期维护才调用完整 `tick()`。两种 tick 都在 layer drain 成功后立即持久化，再释放 lock 执行必要的非 buffer maintenance，后续失败不会让重启重放已经应用的 signal。

### Toolbar badge contract

`computeActionBadge(reachable, uninitialized)` 恢复为两信号决策表：

| 后端状态 | badge |
|---|---|
| 未探测 | 空 |
| 不可达 | 浅灰 `!`，提示启动后端 |
| 可达、未初始化 | 橙色 `!`，提示完成初始化 |
| 可达、已初始化 | 空 |

service worker 删除 pending count 状态、count-only 请求、runtime-stream debounce 与 30 秒 alarm 附带刷新。popup / desktop 的「对话 → 待聊确认」计数、列表和 open API 不变。

## 非目标

- 不删除后端 `GET /api/chat/pending-confirmations` 或 `count_only` 兼容参数；其它客户端仍可使用。
- 不移除 popup / desktop 内部待聊计数，不把待聊条目迁到消息面板。
- 不改变 `unified_interest_line=false` 的旧反馈学习阈值与分析方式。
- 不缩短真实 LLM provider timeout；本修复把它移出交互请求，并增加移动端 30 秒网络上限。
- 不改变 recommendation feedback、exploration buffer、即时 cognition 的业务语义。

## 故障与重启语义

| 退出 / 失败位置 | 恢复语义 |
|---|---|
| event commit 失败 | 请求失败；recommendation projection 尚未写 |
| event 已写、recommendation projection 失败 | 请求失败；同 `request_id` 重试校验首写 payload 后补 projection |
| event 已写、scheduler 尚未领取 | startup schedule 从 feedback cursor 领取 |
| checkpoint 原子 replace 失败 | buffer/cursor 同时回滚，owner 重试且不消费 |
| checkpoint 已写、consume 未开始 / 被取消 | 重建 pipeline 读回持久 buffer；startup owner 消费 |
| layer update 失败 | `_update_layer` 恢复 signals 并保存，后续 tick 可重试 |
| layer update 成功、maintenance 失败 | drain 已先保存，重启不重复应用 |
| scheduler 异常发生在 checkpoint 前 | buffer/cursor 不发布；startup / 下一次 schedule 重试 |
| v0.3.191 旧 marker + cursor 后仍有 direct-owned 行 | 首次 v2 写入前按最大 feedback row id fence 并与 owner-version 同次原子 replace；历史不重学，fence 后新行连续领取 |

## 验收清单

- [x] 阻塞 scheduler tick / LLM 时，连续两个 `POST /api/feedback` 快速返回，两个 event 均已落库。
- [x] 单次 POST 只唤醒 scheduler 一次，HTTP 不调用 pipeline enqueue / ingest。
- [x] scheduler 将每个 event row 转成一个稳定 ID 的 FEEDBACK signal；live row 在 migration marker 后仍持续领取。
- [x] checkpoint replace 失败时 buffer/cursor 同时回滚；checkpoint→consume 崩溃后重建能读回 buffer。
- [x] app startup 能在没有新点击时唤醒遗留 feedback。
- [x] 升级态 old marker + cursor=2 + rows 3..5 不 enqueue；即使 row 5 的 `created_at` 早于 row 4 仍 fence 到 id 5，随后 row 6 正常稳定 enqueue。
- [x] `/api/events` 显式内容反馈只走 cursor owner；hypothesis/import feedback 跳过但推进 cursor，retraction 仍走 generic 折价路径。
- [x] 并发 ingest / tick 的 layer LLM 最大 in-flight 为 1；maintenance 失败不导致重启重放。
- [x] 移动端 feedback 有 30 秒 timeout；三端 Delight 失败保卡并显示可重试状态。
- [x] 健康且已初始化的 toolbar badge 恒空；离线 / 未初始化 `!` 保留。
- [x] service worker 不再请求 pending count；popup / desktop 待聊入口与内部计数保留。
- [ ] 真机桌面连续反馈、移动端断网重试、Chrome MV3 worker 重启手工 E2E。
