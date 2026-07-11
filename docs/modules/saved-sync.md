# 原生保存同步

## 概述

`src/openbiliclaw/saved_sync/` 提供平台无关的收藏 / 稍后再看基础设施。它把本地保存和平台账号写入分成两个阶段：本地 membership 必须先提交成功，之后才允许创建原生同步任务。平台失败只更新逐项同步状态，不回滚本地保存。

当前模块已经实现 canonical identity / typed contracts、capability router、local-first sync service 与 SQLite DAO 边界。生产平台 adapter、HTTP API 和四端 UI 属于后续任务，当前提交不会对任何真实平台账号执行写操作。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| Canonical 保存身份 | ✅ | `SavedItemInput.item_key` 使用规范化的 `source_platform:content_id`；B 站 legacy storage key 兼容由 identity / storage 层处理。 |
| Capability router | ✅ | `NativeSaveRouter` 按 canonical 平台注册 adapter；`favorite` 只路由到 native favorite，`watch_later` 优先 native watch-later，不支持时仅在 favorite 可用时回退。 |
| Local-first 保存 | ✅ | `SavedSyncService.save_local()` 先提交 membership；自动同步关闭时只落 `pending` native state，不调用 adapter。 |
| 持久化同步任务 | ✅ | 自动 / 手动触发统一经 `create_sync_task()` 生成一个 UUID，并在单个 `BEGIN IMMEDIATE` 事务中 claim 所有 eligible 项；已有 owner 的 `pending/syncing` 行不会被重复任务窃取，注入 `task_starter` 后异步执行。 |
| 批量逐项执行 | ✅ | `run_sync_task()` 只读取该 task ID 仍存在的 membership，按平台分组、平台内串行执行，并以 `execution_id` 原子 claim / 完成每一项；同一任务的并发 runner 不会重复调用 adapter，平台组之间仍可并行。 |
| 可恢复任务查询 | ✅ | `get_sync_task()` 只从 `native_save_states` 与 membership/item join 重建结果，进程内不保存易丢失的任务结果。 |
| 安全失败归一化 | ✅ | 未注册 / 不支持的路由写为 `unsupported`；adapter 调用或 target resolution 异常写为 `failed/adapter_exception`；取消与超时 lease 写为可手动重试的 `failed/interrupted`；adapter 返回 `pending/syncing` 会被拒绝为 `failed/invalid_adapter_result`。所有路径都不落异常正文，也不安排无限重试。 |

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
- `create_sync_task(list_kind, item_keys, trigger)`：空 `item_keys` 表示该列表全部 eligible 项；terminal success 和已有 task owner 的 active 项不会重新入队。若构造 service 时注入 `task_starter(name, coro)`，该方法会登记后台执行。
- `run_sync_task(task_id)`：仅原子 claim 持久化的 `pending` 行；`synced` / `already_synced` 等已完成状态不会被同一 task 重跑。进程内同 task runner 单飞，跨 service / 进程由逐项 `execution_id` claim 防重；超过 5 分钟的遗留 `syncing` lease 会先落为 `failed/interrupted`，由用户手动重试。
- `get_sync_task(task_id)`：未知 task 返回同一 ID 和空 items；已有 task 返回持久化逐项结果。

## 数据流与边界

```text
SavedItemInput
  -> Database.upsert_saved_membership()  # 本地事务先提交
  -> Database.claim_native_sync_task(pending, task_id)  # atomic batch ownership
  -> injected task_starter
  -> SavedSyncService.run_sync_task()
  -> Database.claim_native_save_item(execution_id)      # atomic item ownership
  -> NativeSaveRouter.route()
  -> NativeSaveAdapter.save()
  -> Database.complete_native_save_claim(item result)   # owner-checked terminal write
  -> SavedSyncService.get_sync_task()
```

删除本地 membership 仍只由 storage/API 层负责，不会反向删除平台账号内容。当前 `saved_sync/adapters/` 只导出 protocol；第一个生产 adapter 将在后续 Bilibili 任务中接入。
