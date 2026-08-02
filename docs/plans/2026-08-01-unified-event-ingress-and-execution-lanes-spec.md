# 统一事件入口与隔离执行泳道设计（2026-08-01）

## 1. 背景与结论

当前系统已经有运行时级的 LLM 总并发门、浏览器事件缓冲、durable chat turn、
dialogue settlement queue，以及本次修复新增的 durable feedback cursor；但“请求接收后由谁
消费”仍由各 API 自行决定：

- `POST /api/feedback` 已经只落 durable event，再由 feedback scheduler 消费；
- `POST /api/events` 对普通事件仍在请求内 backfill 并 `await pipeline.ingest_batch()`；
- `POST /api/recommendation-click` 落 event 后仍直接 `await pipeline.ingest()`；
- source `task-result`、account sync、CLI 与 OpenClaw 各自决定是否直接调用 pipeline；
- `POST /api/chat/turns` 有 durable row，却用裸 `create_task()` 启动回复；
- 图片 proxy miss 与后台 prefetch 各自直接出网，没有共享限流、优先级或 single-flight。

这会产生四类系统性风险：

1. 任一新事件入口都可能重新把 LLM 放回 HTTP 延迟路径；
2. 同一 event row 可能被“当前请求直喂 + 下次 backfill”重复转成随机 signal ID；
3. 裸内存任务在 shutdown / hot reload 后没有统一恢复与状态观测；
4. 图片慢请求、后台学习和聊天虽然都跑在 asyncio 上，但没有明确的泳道契约。

本规格选择：

> **统一 durable ingress，不统一业务命令；隔离执行泳道，共享受控资源门。**

所有行为事实先进入一个 `EventIngressService`，成功响应只依赖校验与 SQLite commit。
画像、反馈、补货等后续影响由 durable cursor consumer 异步处理。聊天回复、聊天结算、事件
消费和图片 I/O 使用不同的有界协程泳道；阻塞文件/SQLite 操作按需进入线程池，但不为每类
工作创建长期 OS 线程。

## 2. 目标与非目标

### 2.1 目标

- `/api/events`、`/api/feedback`、`/api/recommendation-click` 以及 source 派生的画像事件
  进入同一个持久化入口。
- 所有行为事件 HTTP 请求都不等待 `ProfileUpdatePipeline`、偏好 LLM、画像重建或补货。
- 普通事件像 feedback 一样拥有连续 durable cursor、稳定 signal ID，以及冷启动监听就绪后的
  scheduler-owned 后台恢复；hot reload 仍在 rebind 前同步恢复。
- 三个公开事件入口都要求 producer 提供稳定、严格字符串类型的 `event_id` / `request_id`；
  缺失、空白、非字符串或超过 400 字符的 ID 在任何持久化前返回 422。
- 插件重发同一事件时可以用 producer-owned idempotency key 返回同一 receipt，而不重复落行。
- durable chat turn 由独立回复 worker 领取；进程重启后主动恢复 pending turn。
- dialogue settlement 继续使用现有单 worker/anchor guard，不和回复生成或 event consumer 混队。
- 图片按“前台 miss 高优先、后台 prefetch 低优先”走同一个有界 fetch coordinator，
  同 URL 并发请求 single-flight。
- runtime status 暴露各泳道 depth / active / last error，便于真实运行验收。
- 除本规格明确收紧的请求 ID 契约外，保持已有客户端响应字段兼容；新增响应字段只做 additive
  change。

### 2.2 非目标

- 不引入 Kafka、Redis、Celery 或新的常驻外部服务；单用户本地部署用 SQLite 足够。
- 不把 `/chat`、探针、saved-sync、source task protocol 等业务命令伪装成通用 event POST。
- 不允许多个 worker 并发改同一份对话历史或同一层画像；吞吐不以破坏顺序为代价。
- 不在本次把所有 legacy `event_type` 一次性改成 namespaced 字符串；先统一入口和 owner，
  后续版本再迁移外部事件命名。
- 不让图片预取失败影响推荐、事件写入或聊天。
- 不在真实 E2E 中执行 like/favorite/follow/save/upvote 等会改变第三方账号状态的动作。

## 3. 目标架构

```text
浏览器插件 / Web / CLI / OpenClaw / source task-result
                         │
                         ▼
              业务 API / compatibility adapter
                         │ canonical event(s)
                         ▼
                 EventIngressService
          normalize → validate → idempotency → commit
                         │
                  SQLite events/outbox
                         │ wake（仅提示，不承载事实）
                         ▼
               EventProcessingScheduler (1)
                 ├─ Generic profile cursor
                 ├─ Content-feedback cursor
                 ├─ retraction reconciliation
                 └─ lightweight replenishment request
                         │ durable enqueue
                         ▼
               ProfileUpdatePipeline + LLM gate

聊天请求 ──► DurableChatReplyScheduler (1) ──► interactive LLM slot
聊天学习/结算 ─► DialogueSettlementQueue (1) ─► maintenance LLM slot

GET image miss ─┐
                ├─► ImageFetchCoordinator (capacity 4, priority, single-flight)
cover prefetch ─┘
```

括号中的数字是首版固定 worker/capacity，而不是新增配置：

- event consumer：1，保证 cursor 与 pipeline 写入顺序；
- chat reply：1，保证共享认知对话历史有全序；
- dialogue settlement：沿用现有 1；
- image fetch：4，纯网络 I/O，前台优先。

LLM provider 的真实总并发仍由现有 `LLMConcurrencyGate` 唯一拥有。chat reply 使用
`interactive`，event/profile 与 settlement 使用 `maintenance`；现有“为 interactive 保留一个
总槽位”的规则继续生效。泳道不是第二套 LLM semaphore。

## 4. 泳道契约

| 泳道 | durable source of truth | 并发 | HTTP 是否等待 | shutdown / restart |
| --- | --- | ---: | --- | --- |
| Event ingress | SQLite `events` | DB 自身 | 只等 commit | commit 后必可恢复 |
| Event processing | event row + consumer cursor + pipeline buffer | 1 | 否 | stop 后从 cursor/buffer 续跑 |
| Chat reply | SQLite `chat_turns(status=pending)` | 1 | `/chat/turns` 否；legacy `/chat` 是兼容同步接口 | startup 主动领取 pending |
| Dialogue settlement | 现有 typed queue + durable settlement receipts | 1 | 依命令契约 | 沿用 pause/drain/permit handoff |
| Image fetch | disk cache；miss 本身可重取 | 4 | cache hit 立即；miss 等本次 fetch | prefetch 可丢，前台可重试 |

### 4.1 为什么聊天默认只有一个 reply worker

`SocraticDialogue` 有一份共享历史，连续两句话必须确定先后；增加两个真正并行的 reply worker
会让两个 prompt 都看见旧历史并交错追加。首版因此用一个 reply worker，并依靠 runtime-wide
LLM gate 让其它 interactive 请求仍可并行。以后若要扩展，只能按独立 conversation key 分片，
而不能简单把 worker 数改成 2。

### 4.2 为什么事件接收不能先排内存队列再返回

HTTP 的成功边界必须是 SQLite commit。`asyncio.Queue.put_nowait()` 或 `create_task()` 只可以
唤醒 consumer；进程在唤醒后立刻退出也不能丢事件。

### 4.3 为什么图片需要协程而不是专用长期线程

上游 fetch 使用 `httpx.AsyncClient`，属于异步网络 I/O。使用四个有界 coroutine slot 即可；
cache directory scan、较大的文件读写使用 `asyncio.to_thread()`，避免阻塞 event loop。没有必要
为每张图占一个 OS thread。

## 5. 统一 EventIngressService

新增独立模块（建议 `runtime/event_ingress.py`），API/source adapter 不再直接组合
`MemoryManager.propagate_event()` 与 `pipeline.ingest*()`。

### 5.1 输入与 receipt

服务只接收已经由 `sources.event_format.build_event()` 规范化的 canonical event：

```python
EventIngressService.accept_batch(
    events,
    producer="extension" | "feedback" | "recommendation" | "source:<slug>",
) -> EventIngressReceipt
```

receipt 至少包含：

- 每个输入 index 的 `event_id`；
- `event_type`；
- `inserted` / `duplicate`；
- 可独立拒绝的 validation error；
- 本批 `accepted`、`duplicates`、`rejected` 数。

`/api/events` 保留 ingress/business validation 的 partial-accept receipt。Pydantic 请求结构验证是
更早的边界：批内任一 `event_id` 缺失、空白、非 JSON string 或超过 400 字符时，整次请求返回
422，且事件表和 projection 都不得产生写入；通过结构验证后的行才进入一次事务和逐项 receipt。

### 5.2 producer-owned idempotency

`events` 增加 additive `ingest_key`（默认空）与 partial unique index：空 key 仅保留给历史数据和
明确的内部兼容调用；三个公开 HTTP 入口不得依赖这个存储层宽松性。

- `/api/events` 的每一项都必须携带 `event_id`；它必须是严格 JSON string，经 trim 后长度为
  1..400。插件在事件第一次进入持久化 buffer **之前**生成 UUID，网络重试、MV3 worker 恢复和
  parked-event 恢复都保留原值。
- `/api/feedback` 与 `/api/recommendation-click` 同样必须携带严格 JSON string 的 `request_id`，
  trim 后长度为 1..400。第一方客户端每次用户动作生成一次，并把 pending ID 持久化到该动作
  成功确认；网络重试必须复用，服务端不替缺失 ID 生成 fallback。
- 缺失、空串、纯空白、数字/布尔等非字符串或超过 400 字符的 ID 一律 422，并且在 DB/event/
  projection 写入前失败；合法首尾空白会被 trim 后作为幂等 key。
- source task-result 使用已有 task/item/branch stable identity 组合 key；同一结果重复回传不会重复
  生成画像 event。
- 服务端不做“标题相似”或时间窗口猜测去重；只有 producer 明确声明的 stable key 才去重。

重复提交返回原 `event_id` 并再次 wake consumer（wake 是幂等的），但不重复执行即时 cognition、
exploration buffer 等旁路副作用。

### 5.3 事务与返回边界

`Database` 增加 batch insert returning IDs / duplicate receipts；`MemoryManager` 提供返回 receipt
的持久化方法，旧 `propagate_event(s)` 兼容包装保留。

HTTP 成功前只允许：

1. schema/owner cutover 准备；
2. 必需的业务状态写入；
3. event/outbox commit；
4. O(1) scheduler wake 与轻量通知。

严禁：

- `pipeline.ingest*()` / `tick()` / `flush()`；
- preference/profile LLM；
- discover/eval/refill；
- 远程图片 fetch。

推荐反馈采用明确的 **event-first 两次 commit**，不宣称跨表原子：先按 `request_id` 提交 durable
event，再用独立事务写 recommendation projection。第二步失败时 HTTP 失败；相同 `request_id`
重试命中 duplicate event 后必须校验 durable payload，再补 projection。冲突 payload 返回 409，
不能覆盖首写或驱动投影。

## 6. Continuous generic profile owner

feedback owner 的 durable 模式扩展到普通画像事件，但两个 consumer 保留独立 cursor，因为
它们拥有不同语义和后处理。

### 6.1 状态

consumer 的**权威 cursor 必须与 pipeline buffer 位于同一个原子状态快照**，不能另放一个
`event_processing_state.json`。否则 `enqueue_batch()` 释放 pipeline lock 后，现有 periodic
`pipeline.tick()` 可以在 cursor 写入前抢锁消费 buffer；cursor 写失败或进程退出后，已经从
buffer 消失的 stable ID 仍会被重复学习。

`pipeline_state.json` additive 增加：

```json
{
  "consumer_cursors": {
    "profile_events": 123,
    "content_feedback": 456
  },
  "consumer_owner_versions": {
    "profile_events": 1,
    "content_feedback": 2
  },
  "consumer_cutover_at": {
    "profile_events": "...",
    "content_feedback": "..."
  },
  "consumer_cutover_event_ids": {
    "profile_events": 123,
    "content_feedback": 456
  }
}
```

`ProfileUpdatePipeline` 提供 checkpointed enqueue/advance API，在同一个 `_ingest_lock` 内同时
更新 buffer 与 cursor，再用 temp + `fsync` + `os.replace` 发布一个快照。旧
`feedback_state.json` 可保留 migration provenance 和兼容镜像，但 unified owner 读取的权威
offset 必须来自 pipeline state。不要复用 discovery
`last_processed_event_id`；它是补货触发水位，不是画像消费 offset。

### 6.2 升级 cutover

旧版本会把当前请求直接 ingest，且 `last_profile_pipeline_event_id` 只在下一次 backfill 时推进，
无法从旧随机 signal ID 判断某行是否已经学习。升级时采用与 feedback owner 相同的显式边界：

1. profile 已 ready 且 owner version 缺失时，在接受首条新 event-only 写入前读取当前最大 SQLite
   event row ID；
2. 以该 ID 建立 fence 并发布 owner version；
3. fence 后的行全部归 continuous owner；
4. fresh/未初始化阶段不消费 guided-init 历史；profile ready 时先 fence 初始化已有行。

取舍是优先避免无界重复学习；旧版本“event commit 后、同步 ingest 前进程崩溃”的极小窗口可能
被 fence 越过，必须在 changelog 中如实记录。

### 6.3 消费顺序和幂等

每批按 SQLite `id ASC`：

1. 查询 `id > last_profile_event_id`；
2. 解码并过滤 owner namespace；
3. 转换为稳定 ID `event-row-{row_id}` 的 `ProfileSignal`；
4. 调用 pipeline checkpointed enqueue，在同一 `_ingest_lock` 和同一次原子 state replace 中
   持久化 buffer，并把 cursor 推进到本批最大扫描 row ID（包括合法但不属于该 consumer 的行）；
5. owner 在 HTTP 外调用 `pipeline.tick_if_buffered()` 消费 durable recovery work；空 buffer 直接返回，
   不运行 speculator/cognition 等周期维护。独立 periodic profile loop 才调用完整 `pipeline.tick()`。

外部 periodic tick 只能在这个原子 checkpoint 完成后取得 `_ingest_lock`。如果 state replace
失败，buffer 与 cursor 都保持旧快照并抛错，owner 不能继续 consume。冷启动只同步发布 owner
fence、完成本地 durable 准备并把 recovery 交给 scheduler-owned 后台 task；不得在 ASGI startup
中 await event scan、checkpoint、consume 或 provider/LLM。periodic loop 仍必须服从同一
`_ingest_lock`。hot reload 则在 `pause_and_drain` 后同步完成 owner recovery，再 rebind 新 runtime，
以守住代际边界。不得用短 timeout 或从外部取消 `_process_once()` 来伪造 readiness。

崩溃语义：

| 位置 | 恢复 |
| --- | --- |
| event commit 后、wake 前 | startup/periodic scan 领取 |
| checkpoint 前 | buffer/cursor 都未发布，重试 |
| 原子 snapshot replace 失败 | 内存 buffer/cursor 一起回滚，重试 |
| checkpoint 后、consume 前 | pipeline buffer+cursor 已共同持久化，后续 owner 恢复继续 |
| layer update 失败 | pipeline 恢复 signals，后续重试 |

### 6.4 owner 过滤

- generic owner 只消费 ingress 明确写入 `metadata.profile_update_owner="generic"` 的行；
  `/api/events` 普通行为、recommendation click 和 post-init source/account-sync 派生行为设置该值。
  不能因为某行的 legacy `event_type` 恰好在白名单里就猜它属于 generic owner。
- 显式内容 feedback 仍归 feedback cursor；generic owner 跳过。
- hypothesis/import feedback 归各自对象 owner；generic owner 跳过。
- retraction 继续走 generic owner，保留折价/tombstone。
- recommendation click 虽持久化为兼容 `event_type=click`，metadata 标记
  `event_namespace=recommendation` / `source=recommendation_click`，转换时生成
  `SignalType.RECOMMENDATION_CLICK`，不退化为弱普通 click。
- account/source import 是否进入增量画像由显式 metadata owner flag 决定，不从 route 名猜测。

这是必需边界：SQLite `events` 同时保存 engine 自己写入的 dialogue、hypothesis settlement、
guided-init snapshot 等事实，它们往往已经由原对象 owner 学习。如果 continuous owner 仅按
`event_type` 扫描，会把这些内部行重新送入画像。consumer 对所有扫描行都推进 cursor，但只对
明确 target 自己的行生成 signal。

## 7. API 与命令收敛边界

| 现有入口 | 对外形态 | 内部改造 |
| --- | --- | --- |
| `POST /api/events` | 保留 canonical batch API | normalize 后只调用 ingress；不再 direct ingest/backfill |
| `POST /api/feedback` | 保留兼容 command | recommendation 状态 + canonical content feedback；只 wake |
| `POST /api/recommendation-click` | 保留兼容 adapter，后续可 deprecated | 生成 canonical click，返回 additive `event_id/processing`；不再返回同步 layer 结果 |
| Delight like/dislike/dismiss | 保留 command | 状态改变成功后发 canonical reaction；chat 分支仍是 interactive command |
| probe/card action | 保留 command | 状态结算成功后发 namespaced audit fact；不塞进通用 behavior schema |
| source `task-result` | 保留采集协议 | 派生的 canonical behavior/profile event 统一 ingress |
| CLI/OpenClaw feedback | 保留 UX | 复用 ingress；显式命令可选择 await drain，但不是 HTTP 语义 |
| saved-sync / credential / init | 保留独立协议 | 不属于行为 event API |

第一阶段继续存 legacy `event_type`，但第一方写入补 `metadata.event_namespace`：

- `content`：浏览、互动、推荐反馈；
- `recommendation`：推荐卡点击/反馈；
- `hypothesis`：假设结算；
- `probe`：兴趣/避雷确认；
- `import`：guided init/account snapshot；
- `retraction`：撤销动作。

旧行缺字段时由既有 predicate 兼容推断。后续 namespaced v2 event type 另开规格，避免本次破坏
分析 prompt、数据库查询和插件协议。

## 8. Durable chat reply lane

新增 `DurableChatReplyScheduler`（建议位于 `runtime/dialogue_reply_scheduler.py`）：

- 内存 `asyncio.Queue[str]` 只存 `turn_id`，事实仍在 `chat_turns`；
- `pending` / `in_flight` set 防止 GET polling 重复排队；
- 一个 worker 调用现有 `_complete_durable_chat_turn(turn_id)`；
- `POST /api/chat/turns` commit pending row 后 `schedule(turn_id)`，立即返回 pending；
- `GET /api/chat/turns/{id}` 看到 pending 只补 wake，不创建裸 task；
- app startup 查询所有可恢复的 pending chat turn，按 rowid 顺序入队；
- shutdown 先停止接受，再给 active reply 有界 drain；未完成 row 保持 pending，重启续跑；
- hot reload 时 processor 每次解析当前 `ctx.dialogue`，不得持有已淘汰 runtime 的引用；
- status 暴露 depth/active/last_error/processed。

现有 `DialogueSettlementQueue` 保持独立：reply 完成后产生的 learn/settlement job 仍进入它。这样
“用户回复生成”和“画像学习/anchor mutation”不会互相堵住同一 FIFO。

legacy `POST /api/chat` 因响应体就是 reply，仍然必须等待模型；它通过同一个 interactive LLM gate，
但不宣称 durable async。桌面、移动和插件继续以 `/api/chat/turns` 为主路径。

## 9. Image fetch lane

新增稳定的 `ImageFetchCoordinator`，由 API route 与 RefreshRuntime prefetch 共用：

- capacity 4；
- prefetch 另受 capacity 3 的 background gate 约束，始终为前台 miss 保留至少一个总槽位；
- priority 0：用户正在请求的 `/api/image-proxy` miss；
- priority 10：后台 prefetch；
- key 为规范化 URL/cache key；相同 key 同时 miss 时只启动一个 fetch task，其他调用
  `await shield(shared_future)`；某个客户端取消不能取消共享 fetch；
- slot 覆盖出网 + bounded read；保存用原子 temp/replace，并把较大磁盘 I/O 放到 thread；
- cache hit 不占 slot；
- prefetch 失败只记现有 rate-limited warning；
- foreground 保留现有 whitelist、redirect revalidation、10MB ceiling、direct/proxy routing；
- coordinator shutdown 取消未开始的 prefetch，对 active foreground 给短 grace；前台失败仍可重试。

该改动解决性能隔离和同 URL stampede，不改变浏览器缓存头和图片安全边界。

## 10. 生命周期与观测

app startup 顺序：

1. image cache cleanup；
2. guided-init / confusion recovery；
3. 同步建立 profile/feedback owner cutover，并只做本地 durable 准备；
4. 启动 event scheduler，把 owner recovery 纳入 app-owned 后台 task 并 wake 一次，但不 await
   event scan/checkpoint/consume/provider；即使 provider 401、永久不返回或 pipeline 已有 pending
   buffer，监听与 `/api/health` 也必须及时就绪；
5. 启动 chat reply scheduler，领取 pending turns；
6. 启动既有 background runtime。

hot reload 使用不同边界：先 pause/drain 旧 event lane，再同步 recover owner，最后 rebind 新 runtime。
这个同步代际切换不能因冷启动异步化而退化。

app shutdown 顺序：

1. 停止接受新的后台 wake；
2. close event scheduler，取消并 gather 它拥有的 recovery task（不要求等 LLM 完整跑完，durable
   state 可恢复，且不得遗留 app-owned task）；
3. drain/cancel chat reply scheduler，pending row 留存；
4. drain dialogue settlement queue；
5. close image coordinator 与其它 clients。

`GET /api/runtime-status` additive 字段：

```text
event_lane_depth / event_lane_active / event_lane_last_error
chat_reply_depth / chat_reply_active / chat_reply_last_error
image_fetch_active / image_fetch_waiting / image_fetch_inflight_keys
```

`event_lane_depth` 只是 dirty wake 的 `0/1`，不是 SQLite event backlog；真实 backlog 由 events 表和
两个 pipeline checkpoint 决定。不得在状态或日志中输出用户消息正文、Cookie、token 或完整含
签名图片 URL。

## 11. 实施计划

### Phase A：基础设施与兼容契约

1. 新增 event ingress receipt、DB `ingest_key`/returning IDs、MemoryManager 兼容包装。
2. 在 `pipeline_state.json` 增加 consumer cursor/owner/cutover 的原子读写，不新增第二份权威状态。
3. 新增 generic profile owner + stable event-derived signals + cutover。
4. 把现有 feedback scheduler 扩展/替换为统一 event processing scheduler；保留旧类名 alias，
   避免测试和第三方注入立即断裂。

### Phase B：入口迁移

5. `/api/events` 移除 request-inline generic pipeline/backfill。
6. `/api/feedback` 通过 ingress receipt 驱动旁路副作用幂等。
7. `/api/recommendation-click` 改为 canonical durable event，保留强信号转换。
8. 迁移 XHS/DY/YT/Zhihu task-result 的 canonical final 投影、account sync、CLI 与 OpenClaw 的本轮
   事件写入；Reddit task-result 不在本次两阶段完成改造中。任何 API/source adapter 中剩余的
   direct `pipeline.ingest*` 必须逐项解释或移除。
9. 更新插件 buffer 与第一方 feedback/click payload 的 stable request/event ID；三个公开入口把
   缺失、空白、非字符串和超长 ID 作为 422 且零写入处理。

### Phase C：聊天与图片泳道

10. 新增 durable chat reply scheduler，替换 chat turn 的裸 `create_task()`，补 startup recovery。
11. 新增 image fetch coordinator，API miss 与 RefreshRuntime prefetch 共用。
12. 接入 lifecycle、hot reload 和 runtime status。

### Phase D：文档与验证

13. 更新 `docs/modules/api.md`、`runtime.md`、`memory.md`、`soul.md`、
    `soul-pipeline-architecture.md`、`extension.md`。
14. 同步 `docs/architecture.md`、`docs/spec.md`、README CN/EN 架构图和 changelog 当前版本块。
15. 新增真实运行 verifier 和可复现命令；临时 root/log/screenshot 不提交。

## 12. 自动化验收

### 12.1 Event ingress / processing

- 慢/阻塞 pipeline 与 LLM 时，`/api/events`、`/feedback`、`/recommendation-click` 都在 durable
  commit 后返回，handler 不调用 pipeline。
- 三个公开入口对缺失、空串、纯空白、数字/布尔和超过 400 字符的 ID 返回 422，且事件表和
  recommendation projection 都是零写入；首尾空白会被 trim，合法边界值正常接受。
- `/api/events` 通过请求结构验证后的业务拒绝仍保持 partial acceptance，并返回正确 receipt；
  schema/ID 失败则整次请求在 route 前 422，不能混称 partial success。
- 相同 `ingest_key` 重放两次：事件表只一行，receipt 第二次为 duplicate，同 event ID。
- `/api/feedback` 与 `/api/recommendation-click` 的顺序重放和 concurrent replay 复用同一
  `request_id`；语义冲突继续返回 409，不以新 ID 逃避冲突检测。
- generic consumer 将每个 row 转成稳定 `event-row-{id}`，checkpoint retry 不重复。
- 故意让 periodic `tick()` 与 checkpoint 竞争，证明它只能在 buffer+cursor 原子快照发布前或发布后
  取得锁；快照写失败时不得消费。
- checkpoint→consume crash 后重建 pipeline 可继续；空 owner recovery 不运行周期 maintenance。
- 冷启动用一个永不返回（以及一个 401）的 fake consumer/provider 证明：recovery 已开始但
  `TestClient`/监听和 `/api/health` 及时就绪；shutdown 后 recovery task 已完成取消/gather、无泄漏。
- hot reload 继续证明 pause/drain → synchronous recover → rebind 的顺序，不复用冷启动的异步
  shortcut。
- 普通 event upgrade fence 不重学旧 direct-owned rows，fence 后新行连续领取。
- content feedback 不被 generic owner 消费；retraction 只走 generic；hypothesis/import 被各自 owner 处理。
- recommendation click 经异步路径仍是 `RECOMMENDATION_CLICK` 强信号。
- XHS/DY/YT/Zhihu task-result 不再 request-inline await profile LLM；Reddit 未宣称迁移。

### 12.2 跨泳道并发

用 controllable blockers 同时启动：一个 60 秒 fake maintenance LLM、两个 chat turn、六个 image
miss 和两批 events，断言：

- event HTTP commit 不受 maintenance blocker 影响；
- chat 使用 interactive slot，不被 background maintenance 排队饿死；
- chat reply 最大 active=1 且按 durable row 顺序；
- image active 最大 4、prefetch active 最大 3；同 URL 只 fetch 一次；foreground 排在尚未开始的
  prefetch 前，且 prefetch 不能占满全部总槽位；
- 任一 image fetch 阻塞不增加 event/chat lane depth；
- status/event reader 保持 active SQLite cursor 时，对话结算 writer 必须使用另一条 thread-affine
  connection；不得复现 `sqlite3.OperationalError: another row available`，也不得用 process-wide mutex
  阻塞 event loop；
- TestClient/request thread 上的 token backfill 即使两个 UPDATE 都命中零行，也必须结束隐式事务；
  请求返回后主线程 facade write 不等待 `database is locked`；
- shutdown/restart 后 event 与 pending chat 都可恢复。

### 12.3 静态与回归

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/pytest -q --tb=short
cd extension && npm test && npm run typecheck && npm run build
```

大改动最终跑全量 pytest 与 extension test；如遇 main 同样失败的基线，必须用 main 精确复现并
单独报告，不能把范围测试通过冒充全量绿灯。

## 13. 真实端到端验收

复用仓库已有 `scripts/verify_unified_line_live.py` 验证真实 content-feedback owner 权威；其 owner
版本、cursor 与 cutover fence 只读取 `pipeline_state.json`，`feedback_state.json` 仅作为跳过的
provenance 观察项。聊天、generic event、图片 lane 与重启窗口由同一个临时隔离 uvicorn runbook
逐项记录，不新增未交付的 verifier 文件。可以复制当前配置和必要画像/DB 到临时目录以使用用户
真实 LLM provider，但不得输出或提交 secret；结束后删除临时目录。

### 13.1 真实 HTTP + SQLite

1. 向真实 `/api/events` POST 一批带稳定唯一 `event_id` 的 safe synthetic view/search/click；记录
   HTTP latency、receipt IDs；
2. 重放同一批，断言 duplicate receipt 与 SQLite 行数不变；
3. 轮询 event cursor、pipeline state/ledger，证明 HTTP 返回后才异步消费；
4. 杀掉后端于 event commit 后、consumer 前，重启并证明监听先就绪、后台 recovery 随后领取事件；
5. 真实 `/api/recommendation-click` 携带稳定 `request_id` 并返回 durable receipt；随后 ledger 出现
   强 click signal。若 provider
   限流导致下游 ledger 在窗口内未完成，必须把 durable commit/recovery 与 external block 分开报告。

### 13.2 真实 LLM chat

1. 使用临时 root 中配置的真实 provider/model，POST `/api/chat/turns`；
2. 断言创建请求快速返回 `pending`；
3. 同时 POST event，并证明 event commit 不等待 chat；
4. 轮询 turn 到 `completed`，记录 provider/model（不记录 key）与耗时；
5. 后端在 pending 状态重启一次，证明 chat reply scheduler 的 startup recovery。

若真实 provider 不可用，只能报告 `blocked: provider unavailable`，不得换 mock/Ollama 冒充。

### 13.3 真实图片上游

1. 从隔离 DB 的真实推荐行选一个 whitelist CDN cover；没有时使用仓库既有公开 fixture URL；
2. 并发请求同一 `/api/image-proxy` URL，断言一个 upstream fetch、其余共享结果；
3. 再请求一次得到 `X-Image-Cache: hit`；
4. 并发请求多个真实 cover，断言 image lane cap 与 event/chat 无互相阻塞；
5. 不把签名 URL 完整写入交付日志。

### 13.4 已安装插件安全 E2E（环境可用时）

- 通过现有 trusted-local `/api/extension/e2e/run` 使用已安装 OpenBiliClaw 插件；
- 只跑 `snapshot` / `scroll` / 普通 `click` 等安全动作；
- 证明真实页面 capture → extension durable buffer（首次持久化前分配并保留 `event_id`）→
  `/api/events` → canonical SQLite row →
  async consumer；
- 不跑 like/favorite/follow/save/upvote，不读取或输出 Cookie 值。

如果没有连接中的真实插件，明确记为环境未满足，不能用临时 CDP 浏览器替代并宣称插件 E2E。

本次隔离实测覆盖 durable feedback/event 幂等、冷启动纳入后台 task 的 generic checkpoint recovery、
hot reload 同步 recovery、真实 provider chat completion 与 pending restart recovery、
recommendation-click stable strong-signal buffer，以及真实
CDN singleflight/cache。content-feedback 的 durable cursor/buffer 已发布，但克隆环境随后进入 provider
rate-limit cooldown，所以下游 feedback ledger 在窗口内未完成，按 external blocked 单列；已安装插件
未连接该隔离后端，安全 snapshot 返回 runtime unavailable，未用临时 CDP 冒充通过。

同一隔离运行还暴露过一次生产并发失败：chat reply 已 completed，但 dialogue settlement sequence=1
在 event/status worker 活跃时由共享连接抛出 `another row available`。修复改为 Database facade
thread-affine connections，并在 lock retry 前 rollback；自动化回归已覆盖 active reader + settlement writer
与跨线程 legacy foreign-key 语义。永久 request-thread connection 随后又暴露 XHS token backfill 的
零行 UPDATE 事务泄漏：旧逻辑按正 `rowcount` 才 commit，现已改为每个成功 direct DML 都 commit、
异常 rollback，并用 request 返回后的跨线程写入回归锁定。修复后的真实结算与 API heartbeat 重跑
结果须在最终验收记录中单列，不能用上述单测代替。

最近一次 Python 全量（补 strict-string 参数用例前）为 `6663 passed, 47 skipped, 1 failed`；唯一失败
是 Phase 7 测试 fixture 未补新必填 `event_id`，并已按生产契约修复 fixture、单测回归通过。该小修后
尚未重跑耗时全量，不能宣称全量绿灯。受影响验收为：strict ID/trim `18 passed`、Phase 7 单例
`1 passed`、P0 lifecycle 组合 `6 passed`；`tests/test_api_app.py` 在最后新增 401 单例前全文件
`545 passed`，401 单例随后独立通过。extension `1226/1226` tests、typecheck、build 全通过；Ruff
lint、format check、MyPy 228 个源文件与 `git diff --check` 全通过。

## 14. 完成定义

- [x] 本次范围内的生产 HTTP/source 事件入口统一经过 `EventIngressService`；guided init 等显式 owner
      例外不冒充迁移完成。
- [x] 行为 HTTP handler 都不等待 pipeline/LLM。
- [x] `/api/events`、`/api/feedback`、`/api/recommendation-click` 要求严格字符串 stable ID；非法 ID
      422 且零写入，第一方重试复用同一 ID。
- [x] generic + feedback consumer 都有 durable cursor、stable IDs；冷启动 recovery 由 app-owned
      后台 task 承担，hot reload 保留同步 recovery。
- [x] chat turn 不再使用无主裸 task，pending 可主动恢复。
- [x] image proxy/prefetch 共用有界、优先、single-flight coordinator。
- [x] runtime status 能观察三条新泳道，且 `event_lane_depth` 明确为 dirty wake 0/1。
- [x] scope tests、最近一次全量结果和 extension build 已执行并如实报告；fixture 小修后的全量未
      重跑，不以受影响单测通过冒充全量绿灯。
- [x] 真实 HTTP、真实 LLM、真实图片请求已执行；真实插件 E2E 按环境可用性单列；provider 限流
      不得把 durable evidence 冒充 downstream ledger 通过。
- [x] 强制模块文档、架构图、README CN/EN 与 changelog 已同步。
