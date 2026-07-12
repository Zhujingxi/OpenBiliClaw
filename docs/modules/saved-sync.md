# 原生保存同步

## 概述

`src/openbiliclaw/saved_sync/` 提供平台无关的收藏 / 稍后再看基础设施。它把本地保存和平台账号写入分成两个阶段：本地 membership 必须先提交成功，之后才允许创建原生同步任务。平台失败只更新逐项同步状态，不回滚本地保存。

当前模块已经实现 canonical identity / typed contracts、capability router、local-first sync service、SQLite DAO 边界、首个生产实现 `BilibiliNativeSaveAdapter`、平台中立 HTTP API / runtime 注册，以及插件 side panel、插件设置、桌面 Web、移动 Web 的保存与同步界面。旧端点仍只做本地 B 站兼容保存，不会因本次 wiring 自动修改平台账号。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| Canonical 保存身份 | ✅ | `SavedItemInput.item_key` 使用规范化的 `source_platform:content_id`；B 站 legacy storage key 兼容由 identity / storage 层处理。 |
| Capability router | ✅ | `NativeSaveRouter` 按 canonical 平台注册 adapter；`favorite` 只路由到 native favorite，`watch_later` 优先 native watch-later，不支持时仅在 favorite 可用时回退。adapter 的 `target_label()` 运行时返回必须是去空白后 1–256 字符且无控制字符的字符串，否则逐项安全失败且不会写 route / 调用平台。 |
| Local-first 保存 | ✅ | `SavedSyncService.save_local()` 先提交 membership；自动同步关闭时只落 `pending` native state，不调用 adapter。 |
| 持久化同步任务 | ✅ | 自动 / 手动触发统一经 `create_sync_task()` 生成一个非空 UUID，并在单个 `BEGIN IMMEDIATE` 事务中写入 `native_save_tasks` / `native_save_task_items` 快照，同时 claim eligible 项。缺失、已完成和已有 owner 的选择也会得到稳定的 terminal 快照；空的 all-eligible 请求仍持久化零项 task。执行前另以唯一 `task_runner_id` 原子领取 batch runner。 |
| 扩展原生保存 durable broker | ✅（基础设施） | `ExtensionNativeSaveBroker` 把六个非 B 站平台的 canonical item / route 裁成无凭证 job，写入独立 `extension_native_save_jobs` ledger；active job 按 platform + item + requested action 原子复用。未 claim 的 dispatch 超时持久化为 `extension_required/extension_unavailable`，已 claim 的 lease 超时固定为 `failed/extension_task_timeout` 且不自动重放。Task 1 仅提供 broker/ledger，尚未注册六平台 adapter、HTTP task multiplex 或扩展 executor。 |
| 批量逐项执行 | ✅ | `run_sync_task()` 只读取该 task ID 仍存在的 membership，按平台分组、平台内串行执行，并以 `execution_id` 原子 claim / 完成每一项；同一任务的并发 runner 不会重复调用 adapter，平台组之间仍可并行。执行中每 30 秒 owner-fenced heartbeat；240 秒是调用方响应 deadline，不假定能强制终止不遵守 cancellation 的底层 I/O。 |
| 可恢复任务查询 | ✅ | `get_sync_task()` 从独立 task/item ledger 重建结果，不依赖当前 membership 或可变的 `native_save_states.task_id`。删除本地 membership、service/API 重建后仍可按 UUID 查询同一批逐项快照；未知 UUID 在 HTTP 层返回 404。 |
| 安全失败归一化 | ✅ | 未注册 / 不支持的路由写为 `unsupported`；malformed target / result 写固定 `failed/invalid_adapter_result`；adapter 异常写 `failed/adapter_exception`。凡 adapter 因 item heartbeat 异常、响应 deadline 或调用方取消而进入 detached 状态，tracked watchdog 都会切换到 10ms–1s 有界退避的 owner-fenced heartbeat，直到真实终止后归一化 late terminal / malformed 结果，避免 detached 期间的后续心跳异常开放重叠重试。 |
| B 站原生 adapter | ✅ | favorite 精确复用或创建 `OpenBiliClaw` 收藏夹；watch-later 写 B 站稍后再看。任意 endpoint 的 `-101` → `login_required`；只有最终 favorite resource-deal POST 的 `11201` 会由 client 标记为 dedicated duplicate，且 adapter 仍要求 resolved action 为 favorite 才映射 `already_synced`；folder/resolver 的同码与非 favorite route 的该异常均为 `failed`；watch-later 的 `90003` 固定为 `failed/bilibili_video_unavailable`。 |
| 平台中立 HTTP API | ✅ | `/api/saved/{list_kind}` 提供 save/list/remove/status/sync，`/api/saved-sync/tasks/{task_id}` 返回 durable 逐项结果；`list_kind`、canonical key、选择和 UUID 均 fail closed。普通稳定键严格为 `<canonical-platform>:<nonblank-stable-id>`；知乎真实内容 ID 额外只放行 `zhihu:question/answer/article:<numeric-id>` 三种 typed identity，未知类型、空段和其它平台的额外冒号仍拒绝。URL fallback 严格为 `<platform>:url:<24位小写十六进制>`，URL 只接受无凭据、无空白/控制字符且 host/port 有效的 HTTP(S)。缺失 membership 只返回安全的 `failed/not_saved_locally`。 |
| 三个图形化保存界面 + CLI 配置可见 | ✅ | 插件 side panel、移动 Web、桌面 Web 的推荐卡只对本地保存做 optimistic update；状态与并发 fence 按 `list_kind:item_key` 隔离，迟到 status 水合不能覆盖新 mutation。插件 / 移动 saved 与 config 请求、桌面 saved 请求均有有界 timeout；插件的单个 deadline 覆盖初次设备会话交换、401 强制换票与受保护请求，认证 fetch 接收同一 AbortSignal。超时 mutation 释放 busy，poll 超时保留 recoverable task。保存页对 adapter 支持的状态提供不受自动开关影响的手动同步，显示真实 target、逐项状态、可恢复失败重试、批量确认和仅终态的分平台结果；`unsupported` 统一显示「仅本地保存 / 暂不支持平台同步」，不显示单项同步且不计入批量数量，避免对 Phase 1 尚无 adapter 的平台反复提交。列表成功加载后从持久化 `sync_task_id` 去重恢复任务，task→item ownership 把关联项显示为同步中并排除重复提交；页面重新可见时恢复查询，销毁时清理 tracker。刷新失败保留最后成功快照并显示重试。desktop 共享 normalizer 与后端一致把 `x` canonicalize 为 `twitter`；移动设置成功加载时 retry 控件保持隐藏。批量同步与重试加载先捕获列表级焦点并在重渲染后优先还原同一动作；卡片动作消失时再依次回退到相邻卡片、列表动作和页面标题。Task/result 文本先清洗再用 textContent/转义渲染；本地删除只调用 `/remove`。CLI 仅由 `config-show` 展示自动同步开关，不提供保存 / 同步动作。 |
| Runtime wiring | ✅ | `RuntimeContext` 把当前 `BilibiliAPIClient` 注册到 router/service；只有顶层 sync runner 交给 `BackgroundTaskRegistry`。service 自有的 heartbeat、adapter save 和 watchdog 会由 service 内部强引用到真实 I/O 结束，避免 registry 取消后失去 owner fencing。配置热重载先取消旧 registry task，并在所有新组件构造成功后原子替换 client 与 service。 |

## 公开 API

### Adapter protocol 与路由

```python
from openbiliclaw.saved_sync.router import NativeSaveAdapter, NativeSaveRouter

router = NativeSaveRouter([adapter])
adapter, route = router.route("reddit", "watch_later")
```

`NativeSaveAdapter` 暴露：

- `capability: NativeSaveCapability`
- `target_label(action) -> str`
- `async save(item, route) -> NativeSaveResult`

Router 不读取配置或存储。未注册平台、缺失 favorite 能力，或既无 watch-later 也无 favorite fallback 时抛出带 `unsupported` 的 `ValueError`；service 会把它转换为逐项 `unsupported` 结果。

### Extension native-save broker

```python
from openbiliclaw.saved_sync.extension_broker import (
    ExtensionNativeSaveBroker,
    ExtensionNativeSaveJob,
    ExtensionNativeSaveResultIn,
)

broker = ExtensionNativeSaveBroker(database, wake_platform=wake_platform)
job_id = broker.enqueue(item, route)
job = broker.claim_next("reddit")
if job is not None:
    broker.submit_result(
        ExtensionNativeSaveResultIn(job.job_id, job.item_key, "synced")
    )
```

- `ExtensionNativeSaveJob` 只包含 UUID、canonical platform/slug/item identity、清洗后的 allow-listed HTTPS URL、content type、requested/resolved action 与 target label；不包含标题、Cookie、token、HTML 或响应正文。
- `ExtensionNativeSaveResultIn` 只接受计划明确的 status/code 组合。扩展传入的 message 永不原样持久化；SQLite 只写后端自有固定文案，并拒绝 Unicode category-C 字符。
- `save()` 的一个 dispatch deadline 同时覆盖 best-effort wake 与 pending poll；wake 挂起或抛错不会越过 deadline。claim 后改用 execution lease，超时结果不会重新进入 pending。
- 该 API 当前是后续 adapter/API/extension wiring 的稳定基础；Task 1 不改变现有 runtime 注册，生产真实账号写入仍只有 Bilibili adapter。

### B 站 adapter

```python
from openbiliclaw.bilibili import BilibiliAPIClient
from openbiliclaw.saved_sync.adapters import BilibiliNativeSaveAdapter

client = BilibiliAPIClient(cookie="SESSDATA=...; bili_jct=...")
adapter = BilibiliNativeSaveAdapter(client)
```

- `favorite` 的真实目标是 `B站 OpenBiliClaw 收藏夹`，按 exact title 复用后调用 B 站收藏 resource endpoint。
- `watch_later` 的真实目标是 `B站稍后再看`，不经过 favorite fallback。
- `SESSDATA` 与 `bili_jct` 缺任一项都会在任何视频 lookup / POST 前返回 `login_required`；Cookie、CSRF、服务端 message/body 不会进入 `NativeSaveResult`。
- BV → aid 使用 application-code-aware GET，aid 必须是非 bool 的正整数才允许写 POST。GET/POST 共用脱敏 transport mapping；HTTP 412/429 分别保留为安全数值 code `-412/-429`（保留异常 cause）并归一化为 `rate_limited`。
- 同一个 client 实例内、同一 title 的收藏夹 ensure 由实例内 async lock 串行；锁内重新查询 exact title，因此仅保证该实例内的竞争调用创建一次。不同 client（即使代表同一账号）、不同进程或不同 event loop 之间不协调。
- `RuntimeContext` 已把本 adapter 绑定到当前 B 站 client。只有开启默认关闭的自动同步，或显式调用手动 `/sync`，才会执行账号写入；旧 `/api/watch-later`、`/api/favorites` POST 固定 `auto_sync=False`。

### 平台中立 HTTP API

| 方法 | 路径 | 语义 |
|------|------|------|
| `POST` | `/api/saved/{favorite|watch_later}` | 严格 canonical identity 本地 upsert；按运行时 `saved_sync.auto_sync_enabled` 决定是否只创建后台任务，响应不等待平台 I/O。 |
| `GET` | `/api/saved/{favorite|watch_later}` | 分页返回 metadata snapshot、membership 和最新 native state。 |
| `POST` | `/api/saved/{favorite|watch_later}/remove` | 用 exact `item_key` 只删本地 membership，不反向取消平台保存。 |
| `GET` | `/api/saved/{favorite|watch_later}/status?item_key=...` | 查询单项本地 / 同步状态。 |
| `POST` | `/api/saved/{favorite|watch_later}/sync` | 手动同步；`item_keys=[]` 表示全部 eligible，且始终无视自动同步开关。 |
| `GET` | `/api/saved-sync/tasks/{uuid}` | 轮询持久化逐项状态；已存在的零项 task 返回 200，未知 UUID 返回 404；`login_required` / `rate_limited` / `failed` 不包装成泛化成功。 |

所有 `/api/*` 路径继续受现有 API auth middleware 保护。所有 adapter-controlled `resolved_target/error_code/error_message` 在进入 task poll、单项 status、通用列表和 legacy 列表/state 响应前，都会先移除全部 Unicode category-C 字符，再按字段上限截断。旧 `/api/watch-later` 与 `/api/favorites` 保留 B 站 `bvid` 契约，state/list 响应增量返回 identity、sync 与脱敏后的逐项结果字段；POST 通过 service 本地保存但永不自动同步。

### Local-first service

```python
service = SavedSyncService(database, router, task_starter=starter)
local = service.save_local("watch_later", item, auto_sync=False)
created = service.create_sync_task("watch_later", [item.item_key], "manual_single")
result = await service.run_sync_task(created.task_id)
persisted = service.get_sync_task(created.task_id)
```

- `save_local(list_kind, item, note="", auto_sync=False)`：先写本地；首次关闭自动同步时返回 `pending` 且 `sync_task_id=""`。重复保存只更新 membership / 内容快照，不会把既有 terminal 或 active native state 降级或改写 owner；若原自动同步仍有 active owner，新 save 响应使用新 no-op task 的完整 `failed/sync_already_in_progress` 快照，不与旧 membership 的 route/error 字段拼接。
- `create_sync_task(list_kind, item_keys, trigger)`：真正的空 `item_keys` 表示该列表全部 eligible 项；非空但全部为空白的选择会 fail closed。每次调用都先持久化 task row 和不可变的 item 集合：显式缺失项写 `failed/not_saved_locally`，terminal 项复制现状，已有 live owner 写 `failed/sync_already_in_progress`，只有新 claim 项进入 `pending` 并启动 runner。若 `task_starter(name, coro)` 登记失败，刚 claim 的 pending owner 与 task ledger 会先回滚清理再重新抛错。
- `run_sync_task(task_id)`：先原子领取唯一 runner token；领取失败只返回 durable snapshot。task heartbeat 与 work 使用 `FIRST_COMPLETED` 监控，task heartbeat 失败立即取消 work并 owner-fenced 释放余项。adapter 在 item heartbeat 异常、响应 deadline 或调用方取消后仍存活时，独立 watchdog 统一持续重试 execution heartbeat，并在 late 结果落库后自清理。进程崩溃才由 poll / 下一次创建按 5 分钟 lease 回收。
- `get_sync_task(task_id)`：已有 task 从 `native_save_task_items` 返回持久化逐项结果；service 用 `has_sync_task()` 区分未知 task 与合法零项 task，HTTP 对前者返回 404。空白 `task_id` 在 service 与 DAO 两层都 fail closed，既不会聚合未领取 pending 行，也不会触发 adapter。

### Task ledger 保留策略

当前版本把已经返回给调用方的 `native_save_tasks` / `native_save_task_items` 保留到该 SQLite 数据库被用户删除或重建为止，**没有 TTL、容量上限或自动 pruning**；唯一立即删除的是 task starter 注册失败、从未返回给调用方的任务。这样可保证现阶段 durable polling 不会因后台清理变成 404，但长期运行的账本会持续增长。有界保留窗口、容量阈值与 active/recent task 保护规则尚未在计划中定义，因此作为后续存储治理项延期，不在本任务中发明破坏性过期策略。

## 数据流与边界

```text
SavedItemInput
  -> POST /api/saved/{list_kind}  # strict identity; local response first
  -> Database.upsert_saved_membership()  # 本地事务先提交
  -> Database.create_native_sync_task_snapshot() # task/items + live claims, one transaction
  -> injected task_starter                    # top-level runner only
  -> SavedSyncService.run_sync_task()
  -> Database.claim_native_save_item(execution_id)      # atomic item ownership
  -> NativeSaveRouter.route()
  -> Database.update_native_save_claim_route()           # owner fence
  -> BilibiliNativeSaveAdapter.save()                     # first production adapter
  -> BilibiliAPIClient authenticated POST + owner heartbeat/deadline
  -> Database.complete_native_save_claim(item result)   # state + task snapshot, one transaction
  -> SavedSyncService.get_sync_task()
  -> GET /api/saved-sync/tasks/{task_id} # truthful per-item polling
  -> UI list reload recovers sync_task_id # deduped tracker + item ownership
```

删除本地 membership 仍只由 storage/API 层负责，不会反向删除平台账号内容。B 站 adapter 不读取配置、HTTP request 或全局 runtime，也不自行重试；平台中立 service 继续拥有任务、route 与持久化状态，API route 只在保存请求当下读取当前热重载配置。

验证边界同样分层：自动化和默认 smoke 只使用 mock adapter 或本地 membership，不发送任何
Bilibili favorite / watch-later 请求。真实 `favorite` 与 `watch_later` 都会改变账号状态，必须
为具体 BV ID 取得用户当次授权或使用指定测试账号后才能运行；验证记录只保留 task ID、
状态、计数和脱敏错误码。YouTube、小红书、抖音、X、知乎与 Reddit 的账号写入 adapter
仍是后续独立计划，Phase 1 不能宣称这些平台已经原生同步。

2026-07-13 已在当次明确授权下完成 B 站真实账号授权 E2E：手动收藏、手动稍后再看和
开启配置后的自动收藏均返回 `synced`；删除三条本地 membership 后，两条平台收藏与一条
平台稍后再看仍可读，自动同步配置也恢复为测试前的关闭值。该结果只证明 Bilibili Phase 1
adapter 的授权链路，不扩大其它平台边界。
