# 后端 API

## 概述

`src/openbiliclaw/api/` 暴露本地 FastAPI 契约，并把 UI 请求编排到 durable storage、Soul、Dialogue 与 runtime。本文记录对话确认入口新增的公开端点；通用鉴权见 [api-auth.md](api-auth.md)，初始化端点见 [init.md](init.md)。

## 对话确认端点

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/chat/turns` | ✅ | 普通消息先返回 `pending` 并由 durable worker 生成回复。`scope="hypothesis"` 时服务端生成结构化卡片 payload（`type/kind/ref/title/evidence_refs/actions/state`），直接返回 `status="completed"`，不会调用 LLM worker。 |
| `GET /api/chat/turns?session=<label>` | ✅ | `session` 只过滤当前 UI 可见 turn；不同 UI 仍共享一份认知 history。读取时会把 `applied=1` 的对象结算投影到所有 session 卡片，并校正超过 5 分钟且无活锚的遗留 `discussing` 卡片。 |
| `GET /api/chat/turns/{turn_id}` | ✅ | 返回单个 durable turn，并执行与列表端点相同的 applied-only 投影/遗留讨论校正。 |
| `POST /api/chat/cards/{turn_id}/action` | ✅ | body 为 `{"action":"confirm|reject|discuss|defer"}`。confirm/reject 走 ref 级原子仲裁与可接管三段 apply；discuss 先 CAS 写 `discussing_at+attempt_token` 再建锚，失败补偿回 pending；defer 只写 72h 对象冷却，不进入结算表。 |
| `POST /api/insights/feedback` | deprecated | 保留旧客户端响应结构，响应带 `Deprecation: true`，内部转发到同一结算路径，台账 `source="legacy_endpoint"`。 |

### 卡片 action 返回

- `outcome="applied"`：本请求完成存储事件、假设对象、rebuild marker 三段并发布 `applied=1`。
- `outcome="already_settled"`：已存在 `applied=1` 的对象结算；返回既有 verdict 并刷新本卡片投影。
- HTTP `202` + `outcome="processing"`：存在未完成且尚未超过 5 分钟的 claim；不会把 `applied=0` 伪装成终态。
- `outcome="discussing"` / `"deferred"`：分别表示活锚已建立 / 当前卡片已延期。

## 一致性边界

confirm/reject 的顺序固定为：`INSERT OR IGNORE` 仲裁 → claim token → event 原子占位+INSERT → 对象幂等段 → rebuild marker 幂等段+台账 → token-fenced `applied=1` → 跨 session 投影。只有 `applied=1` 可生成 confirmed/rejected 投影；旧执行者恢复后任何段写都会因 token 不匹配退出。
