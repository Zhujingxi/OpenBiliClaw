# 原生保存同步

## 概述

`src/openbiliclaw/saved_sync/` 提供平台无关的收藏 / 稍后再看基础设施。它把本地保存和平台账号写入分成两个阶段：本地 membership 必须先提交成功，之后才允许创建原生同步任务。平台失败只更新逐项同步状态，不回滚本地保存。

当前模块已经实现 canonical identity / typed contracts、capability router、local-first sync service 与 SQLite DAO 边界。生产平台 adapter、HTTP API 和四端 UI 属于后续任务，当前提交不会对任何真实平台账号执行写操作。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| Canonical 保存身份 | ✅ | `SavedItemInput.item_key` 使用规范化的 `source_platform:content_id`；B 站 legacy storage key 兼容由 identity / storage 层处理。 |
| Capability router | ✅ | `NativeSaveRouter` 按 canonical 平台注册 adapter；`favorite` 只路由到 native favorite，`watch_later` 优先 native watch-later，不支持时仅在 favorite 可用时回退。adapter 的 `target_label()` 运行时返回必须是去空白后 1–256 字符且无控制字符的字符串，否则逐项安全失败且不会写 route / 调用平台。 |
| Local-first 保存 | ✅ | `SavedSyncService.save_local()` 先提交 membership；自动同步关闭时只落 `pending` native state，不调用 adapter。 |
| 持久化同步任务 | ✅ | 自动 / 手动触发统一经 `create_sync_task()` 生成一个 UUID，并在单个 `BEGIN IMMEDIATE` 事务中 claim 所有 eligible 项；执行前另以唯一 `task_runner_id` 原子领取 batch runner。第二个 service 不能执行 item、刷新 heartbeat 或释放 live runner 的 pending。runner 正常 / 取消退出立即释放余项，崩溃后超过 5 分钟由轮询或下一次创建回收。 |
| 批量逐项执行 | ✅ | `run_sync_task()` 只读取该 task ID 仍存在的 membership，按平台分组、平台内串行执行，并以 `execution_id` 原子 claim / 完成每一项；同一任务的并发 runner 不会重复调用 adapter，平台组之间仍可并行。执行中每 30 秒 owner-fenced heartbeat；240 秒是调用方响应 deadline，不假定能强制终止不遵守 cancellation 的底层 I/O。 |
| 可恢复任务查询 | ✅ | `get_sync_task()` 只从 `native_save_states` 与 membership/item join 重建结果，进程内不保存易丢失的任务结果。 |
| 安全失败归一化 | ✅ | 未注册 / 不支持的路由写为 `unsupported`；malformed target / result 写固定 `failed/invalid_adapter_result`；adapter 异常写 `failed/adapter_exception`。凡 adapter 因 item heartbeat 异常、响应 deadline 或调用方取消而进入 detached 状态，tracked watchdog 都会切换到 10ms–1s 有界退避的 owner-fenced heartbeat，直到真实终止后归一化 late terminal / malformed 结果，避免 detached 期间的后续心跳异常开放重叠重试。 |

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

### Local-first service

```python
service = SavedSyncService(database, router, task_starter=starter)
local = service.save_local("watch_later", item, auto_sync=False)
created = service.create_sync_task("watch_later", [item.item_key], "manual_single")
result = await service.run_sync_task(created.task_id)
persisted = service.get_sync_task(created.task_id)
```

- `save_local(list_kind, item, note="", auto_sync=False)`：先写本地；首次关闭自动同步时返回 `pending` 且 `sync_task_id=""`。重复保存只更新 membership / 内容快照，不会把既有 terminal 或 active native state 降级或改写 owner。
- `create_sync_task(list_kind, item_keys, trigger)`：真正的空 `item_keys` 表示该列表全部 eligible 项；非空但全部为空白的选择会 fail closed。terminal success 和已有 task owner 的 active 项不会重新入队。若注入的 `task_starter(name, coro)` 登记失败，刚 claim 的 pending owner 会先原子释放再重新抛错；旧的未启动 owner 在 5 分钟保护窗后由后续创建回收。
- `run_sync_task(task_id)`：先原子领取唯一 runner token；领取失败只返回 durable snapshot。task heartbeat 与 work 使用 `FIRST_COMPLETED` 监控，task heartbeat 失败立即取消 work并 owner-fenced 释放余项。adapter 在 item heartbeat 异常、响应 deadline 或调用方取消后仍存活时，独立 watchdog 统一持续重试 execution heartbeat，并在 late 结果落库后自清理。进程崩溃才由 poll / 下一次创建按 5 分钟 lease 回收。
- `get_sync_task(task_id)`：非空未知 task 返回同一 ID 和空 items；已有 task 返回持久化逐项结果。空白 `task_id` 在 service 与 DAO 两层都 fail closed，既不会聚合未领取 pending 行，也不会触发 adapter。

## 数据流与边界

```text
SavedItemInput
  -> Database.upsert_saved_membership()  # 本地事务先提交
  -> Database.claim_native_sync_task(pending, task_id)  # atomic batch ownership
  -> injected task_starter
  -> SavedSyncService.run_sync_task()
  -> Database.claim_native_save_item(execution_id)      # atomic item ownership
  -> NativeSaveRouter.route()
  -> Database.update_native_save_claim_route()           # owner fence
  -> NativeSaveAdapter.save() + owner heartbeat/deadline
  -> Database.complete_native_save_claim(item result)   # owner-checked terminal write
  -> SavedSyncService.get_sync_task()
```

删除本地 membership 仍只由 storage/API 层负责，不会反向删除平台账号内容。当前 `saved_sync/adapters/` 只导出 protocol；第一个生产 adapter 将在后续 Bilibili 任务中接入。
