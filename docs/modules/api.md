# 后端 API

## 概述

`src/openbiliclaw/api/` 暴露本地 FastAPI 契约，并把 UI 请求编排到 durable storage、Soul、Dialogue 与 runtime。本文记录对话确认入口新增的公开端点；通用鉴权见 [api-auth.md](api-auth.md)，初始化端点见 [init.md](init.md)。

## 对话确认端点

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/chat/turns` | ✅ | 普通消息先返回 `pending` 并由 durable worker 生成回复。`scope="hypothesis"` 时服务端生成结构化卡片 payload（`type/kind/ref/title/evidence_refs/actions/state`），直接返回 `status="completed"`，不会调用 LLM worker。若双轨冷却允许，普通 durable 用户消息会先原子插入一条系统确认卡/问题，再写用户 turn；payload 的 `attached_to_turn_id` 负责重试与重启去重。 |
| `GET /api/chat/turns?session=<label>` | ✅ | `session` 只过滤当前 UI 可见 turn；不同 UI 仍共享一份认知 history。读取时会把 `applied=1` 的对象结算投影到所有 session 卡片，并校正超过 5 分钟且无活锚的遗留 `discussing` 卡片。 |
| `GET /api/chat/turns/{turn_id}` | ✅ | 返回单个 durable turn，并执行与列表端点相同的 applied-only 投影/遗留讨论校正。 |
| `GET /api/chat/pending-confirmations` | ✅ | 返回 `{"count":N,"items":[...]}`，只列未结算的高优先级对象且最多 3 条：未验证假设 `confidence>=0.60`、open 疑惑 `interpretation_confidence>=0.50`，按置信度降序。`?count_only=1` 只返回 `{"count":N}`，供 service worker badge 轻量轮询；`openbiliclaw questions` 读取完整响应且不复制筛选规则。用户主动列表不套用系统冷却。 |
| `POST /api/chat/pending-confirmations/{ref}/open` | ✅ | body 为 `{"session":"popup|webui|..."}`。用户主动打开零冷却；假设生成 completed card，疑惑生成 completed question 并 claim `clarifying`，两者都以 `pending_open` 建锚。相同 `(ref,session)` 原子复用，跨 session 各自产 turn。 |
| `POST /api/chat/cards/{turn_id}/action` | ✅ | body 为 `{"action":"confirm|reject|discuss|defer"}`。confirm/reject 走 ref 级原子仲裁与可接管三段 apply；discuss 先 CAS 写 `discussing_at+attempt_token` 再建锚，失败补偿回 pending；defer 只写 72h 对象冷却，不进入结算表。 |
| `POST /api/insights/feedback` | deprecated | 保留旧客户端响应结构，响应带 `Deprecation: true`，内部转发到同一结算路径，台账 `source="legacy_endpoint"`。 |

### 卡片 action 返回

- `outcome="applied"`：本请求完成存储事件、假设对象、rebuild marker 三段并发布 `applied=1`。
- `outcome="already_settled"`：已存在 `applied=1` 的对象结算；返回既有 verdict 并刷新本卡片投影。
- HTTP `202` + `outcome="processing"`：存在未完成且尚未超过 5 分钟的 claim；不会把 `applied=0` 伪装成终态。
- `outcome="discussing"` / `"deferred"`：分别表示活锚已建立 / 当前卡片已延期。

## 一致性边界

confirm/reject 的顺序固定为：`INSERT OR IGNORE` 仲裁 → claim token → event 原子占位+INSERT → 对象幂等段 → rebuild marker 幂等段+台账 → token-fenced `applied=1` → 跨 session 投影。只有 `applied=1` 可生成 confirmed/rejected 投影；旧执行者恢复后任何段写都会因 token 不匹配退出。

系统抛出的两个 gate 必须同时满足：距上次全局抛出至少 12 小时，且同 ref 的 `last_asked_at` / `deferred_until` 已超过 72 小时；两者持久化在 `memory/dialogue_confirmation_state.json`。用户主动 open 明确绕过这两个时间 gate，但疑惑仍受数据库 `clarifying <= 1` 约束。附着 turn 与用户 turn 同秒时，以 `(created_at,rowid)` 保证卡/问题在前；空消息校验与既有 `turn_id` 幂等检查均发生在附着前。

## 客户端入口约束（Wave D）

popup 与桌面 Web 只有 durable 对话中的假设卡片保留 confirm/reject/discuss/defer 主动动作；两端画像/认知更新区与移动 Web 的 active insights 均只读。`openbiliclaw questions` 也只发 GET 并展示列表。`POST /api/insights/feedback` 仍为旧客户端保留并转发共同结算路径，但新客户端不再调用它；因此“对话是唯一主动 UI 确认入口”与 legacy 兼容同时成立。
