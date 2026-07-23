# 后端 API

## 概述

`src/openbiliclaw/api/` 暴露本地 FastAPI 契约，并把 UI 请求编排到 durable storage、Soul、Dialogue 与 runtime。本文记录对话确认入口新增的公开端点；通用鉴权见 [api-auth.md](api-auth.md)，初始化端点见 [init.md](init.md)。

## 对话确认端点

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/chat/turns` | ✅ | 普通消息先返回 `pending` 并由 durable worker 生成回复。`scope="hypothesis"` 时服务端生成结构化卡片 payload（`type/kind/ref/title/evidence_refs/actions/state`），直接返回 `status="completed"`，不会调用 LLM worker。若双轨冷却允许，普通 durable 用户消息会先原子插入一条系统确认卡/问题，再写用户 turn；payload 的 `attached_to_turn_id` 负责重试与重启去重。 |
| `GET /api/chat/turns?session=<label>` | ✅ | `session` 只过滤当前 UI 可见 turn；不同 UI 仍共享一份认知 history。列表中的每个非终态卡片只 submit `card.reconcile` 到唯一结算队列并返回本次 durable 快照；request task 不直接写 card/object/anchor。 |
| `GET /api/chat/turns/{turn_id}` | ✅ | 返回单个 durable turn。若读到非终态卡片，只同步 admission `card.reconcile` 并立即返回快照。worker 会为 `applied=1` receipt 补 stable audit、跨 session projection 与 exact-generation 解锚，也会把没有对应 active anchor 的 orphan `discussing` 校正回 `pending`；因此 publication gap 的第一次 GET 可仍见旧态，queue 完成后的下一次 GET 见权威状态。 |
| `GET /api/chat/pending-confirmations` | ✅ | 读取前先经唯一结算 worker 扫描并释放 `clarifying` 但其 `ask_turn_id` 没有对应 durable turn 的 crash-gap 孤儿，再返回 `{"count":N,"items":[...]}`；只列未结算的高优先级对象且最多 3 条：未验证假设 `confidence>=0.60`、open 疑惑 `interpretation_confidence>=0.50`，按置信度降序。`?count_only=1` 只返回 `{"count":N}`，供 service worker badge 轻量轮询；`openbiliclaw questions` 读取完整响应且不复制筛选规则。用户主动列表不套用系统冷却。 |
| `POST /api/chat/pending-confirmations/{ref}/open` | ✅ | body 为 `{"session":"popup|webui|..."}`。打开前同样恢复无 turn 的 orphan clarifying claim；用户主动打开零冷却；假设生成 completed card，疑惑通过 `confusion.open.sync` 进入 `clarifying`，并由 `anchor.establish` 以 `pending_open` 建锚。相同 `(ref,session)` 原子复用，跨 session 各自产 turn；API 只 submit/await required local job，不在 request task 执行 protected mutation。 |
| `POST /api/chat/cards/{turn_id}/action` | ✅ | body 为 `{"action":"confirm|reject|discuss|defer"}`。四动作分别 submit `settle.hypothesis`、`card.discuss`、`card.defer` 到唯一队列；confirm/reject 与锚定 `support/contradict/revise/answer`、普通 chat settles、legacy endpoint 共用 immutable ref winner。discuss 在 worker 内 `pending→discussing→建锚`，建锚失败立即补偿回 pending；defer 只对 pending/discussing 卡在 worker 内更新卡片/冷却，若卡已 confirmed/rejected 则返回权威终态的 `already_settled` 且不写 cooldown。HTTP 最多等本地 job 1 秒，完成保持同步 `200`，队头阻塞返回 `202 processing` 且不会取消已入队 job。 |
| `POST /api/insights/feedback` | deprecated | 保留旧客户端响应结构和 `Deprecation: true`，内部通过共同 façade submit 同一队列，台账 `source="legacy_endpoint"`；1 秒内未完成时同样返回 HTTP `202`，不新增 legacy 专用 executor。 |

### 卡片 action 返回

- `outcome="applied"`：本次已由 worker 完成 event/object/derived/rebuild marker 并发布 `applied=1`。
- `outcome="already_settled"`：已存在 `applied=1` 的对象结算；返回既有 verdict 并刷新本卡片投影。
- HTTP `202` + `outcome="processing"`：本地 1 秒等待预算耗尽；入队 completion 被 shield、继续在唯一 worker 执行，不会把 `applied=0` 伪装成终态。
- `outcome="discussing"` / `"deferred"`：分别表示活锚已建立 / 当前卡片已延期。

## 一致性边界

所有声明的对话结算入口只进入一个 `DialogueSettlementQueue`、一个 actual worker。confirm/reject 的顺序固定为：`INSERT OR IGNORE` 固化 immutable winner → event identity 与 event 同事务 → object → derived → rebuild marker + stable audit → `applied=1` → 跨 session projection → exact-generation 解锚。卡片 action、legacy endpoint、锚关系与无锚 chat 的 speculation/insight/confusion settles 共用这条 ref 路径；只有 `applied=1` 可生成终态投影。protected façade 校验 actual worker Task + lifecycle nonce，API request 或继承 ContextVar 的 child task 均不能写。

`card_settlements` 不再保存 claim/lease/token/`seg_*`，也没有文件锁、takeover 或恢复 scanner。rebuild marker 仍使用同目录临时文件 `flush+fsync` 后原子替换；写盘失败会使 job 失败且 receipt 保持 `applied=0`，不会提前投影卡片。后续同 ref 显式重试采用原 winner，幂等 effect 补齐缺口。

队列本身是进程内、非 durable 的：若进程在 `202` 后重启，尚未执行的 job 可以丢失，但 durable card/receipt 不会伪终态。popup 与桌面 Web 对 `202 processing` 按 `1s/2s/5s`（之后保持 5s）读取 `GET /api/chat/turns/{turn_id}`，总截止 30 秒；终态立即停止，超时、读取持续失败或页面 abort 显示本地 `retryable_error`，允许刷新或重试 action。移动 Web 的 active insights/对话确认面保持只读，没有卡片 action 入口；CLI/OpenClaw 也不消费该 HTTP action 契约。

系统抛出的两个 gate 必须同时满足：距上次全局抛出至少 12 小时，且同 ref 的 `last_asked_at` / `deferred_until` 已超过 72 小时；两者持久化在 `memory/dialogue_confirmation_state.json`。用户主动 open 明确绕过这两个时间 gate，但疑惑仍受数据库 `clarifying <= 1` 约束。附着 turn 与用户 turn 同秒时，以 `(created_at,rowid)` 保证卡/问题在前；空消息校验与既有 `turn_id` 幂等检查均发生在附着前。

## 客户端入口约束（Wave D）

popup 与桌面 Web 只有 durable 对话中的假设卡片保留 confirm/reject/discuss/defer 主动动作，并共享上述按需轮询 helper；同步 `200` 不启动额外轮询。两端画像/认知更新区与移动 Web 的 active insights 均只读。`openbiliclaw questions` 也只发 GET 并展示列表。`POST /api/insights/feedback` 仍为旧客户端保留并转发共同队列，但新客户端不再调用它；因此“对话是唯一主动 UI 确认入口”与 legacy 兼容同时成立。
