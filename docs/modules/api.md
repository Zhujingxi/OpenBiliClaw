# 后端 API

## 概述

`src/openbiliclaw/api/` 暴露本地 FastAPI 契约，并把 UI 请求编排到 durable storage、Soul、Dialogue 与 runtime。本文记录对话确认入口新增的公开端点；通用鉴权见 [api-auth.md](api-auth.md)，初始化端点见 [init.md](init.md)。

## 初始化期间的配置探测

`POST /api/config/probe-service` 只在内存副本上应用设置页草稿并真实探测 LLM、默认链、embedding 或网络策略，不写 `config.toml`、不热重载 runtime。它因此不受 guided init 的 HTTP 写端 409 门控；初始化运行时仍可测试，LLM 请求继续经过进程级稳定 total gate。`PUT /api/config` 仍在初始化期间返回 `409 init_running`，避免替换本轮任务正在使用的组件。

## 公开项目统计

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/project-stats` | ✅ | 桌面 Web 与扩展读取 GitHub Star 数量的公开同源端点。后端通过海外网络策略请求 GitHub，持久化 12 小时缓存并使用 ETag 条件请求；遇到 403 / 429 时遵循 `Retry-After` / `X-RateLimit-Reset` 有界退避。GitHub 失败不会透传为 HTTP 错误：有缓存返回 `source="cache", stale=true`，无缓存返回 `source="unavailable", stale=true` 且省略 `github_stars`，两者均为 200。该端点不包含用户数据，在密码门禁和降级模式下保持公开。 |

## 惊喜推荐消费契约

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/delight/respond` | ✅ | `response="dismiss"` 是三端“× / 看过了，不再推荐”的永久消费动作：服务端按 `bvid` 解析 `content_cache` 中的 canonical `source_platform/content_id`，先写 `seen_items`，再置 `delight_notified=1`；后续普通推荐与惊喜推荐均硬排除。`view` 只置惊喜已读，`dislike` 另记录负偏好，`like/chat` 继续保留当前候选。 |
| `POST /api/delight/sent` | ✅ | 仅确认主动通知已送达并维护推送冷却，不代表用户已看，不写 `seen_items`；UI 叉号不得把它作为消费路径。 |

## 降级配置恢复

`PUT /api/config` 在 `llm_registry_unavailable` 降级态下不再只写盘并要求重启。服务端会复用当前进程已经初始化的数据库、MemoryManager、事件总线、任务注册表和 LLM total gate，通过正常热重载路径原子构造完整的 LLM Registry、Soul、Discovery、Recommendation、来源客户端与 runtime controller。构造全部成功后才同步解除业务 API 的 503 guard，并返回 `reloaded=true`、`restart_required=false`；`/setup/` 和插件设置页可以在同一进程里立即继续。

如果核心运行时构造失败，已有 `config.toml` 会从事务备份恢复，响应为 HTTP 503、`ok=false`、`rollback_applied=true`，降级 guard 保持不变。若核心已经成功发布、只是附属后台循环重启失败，则保留已生效的新配置与健康运行时，返回 `ok=true`、`reloaded=true` 并携带 warning，避免把磁盘配置回滚成与内存运行时不一致的旧版本。只有没有可回滚旧文件且进程内激活失败的异常 bootstrap 路径，才保留 `restart_required=true` 兼容兜底。

## 小红书任务安全边界

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `GET /api/sources/xhs/next-task` | ✅ | native-save job 仍是用户显式动作；自动 discovery 的 search / creator / bootstrap 则在每次 claim 前动态检查 `sources.xiaohongshu.enabled` 与 `scheduler.enabled`。任一关闭时返回 bodyless 204，既有任务保持 pending，不会再驱动扩展打开页面。search / creator 的 `task_interval_seconds` 由后端持久化执行；处于节流或平台冷却时返回 204，存在明确等待时间时附 `Retry-After`。 |
| `POST /api/sources/xhs/task-result` | ✅ | 除 `ok / partial / empty / error` 外接受 `status="rate_limited"`。legacy task 命中后终结该任务、持久化 1 小时平台冷却，并将关联 `source_keyword_id` 从 executing 无损退回 pending；native-save 结果命中同样打开平台级冷却。debug 只接受扩展给出的结构化风险原因，不要求或存储验证页全文。 |
| `GET /api/sources/status` | ✅ | 来源仍开启且冷却生效时，将小红书 legacy 状态投影为 `state="rate_limited"`、`feed_paused=true` 并显示剩余分钟；来源已关闭时不让冷却覆盖 `enabled=false` 的正交配置事实。该端点只读本地状态，不访问小红书。 |

## 对话确认端点

| 方法与路径 | 状态 | 契约 |
|---|---|---|
| `POST /api/chat/turns` | ✅ | 普通消息先返回 `pending` 并由 durable worker 生成回复。`scope="hypothesis"` 时服务端生成结构化卡片 payload（`type/kind/ref/title/evidence_refs/actions/state`），直接返回 `status="completed"`，不会调用 LLM worker。若双轨冷却允许，普通 durable 用户消息会先原子插入一条系统确认卡/问题，再写用户 turn；payload 的 `attached_to_turn_id` 负责重试与重启去重。 |
| `GET /api/chat/turns?session=<label>` | ✅ | `session` 只过滤当前 UI 可见 turn；插件、移动 Web、桌面 Web 的主聊天统一使用 `session=popup` 并读取完整 `chat/hypothesis/confusion` 可见历史，因此三端共享普通消息、确认卡和澄清问题；其它 session 仍可用于隔离集成。不同 UI 仍共享一份认知 history。列表中的每个非终态卡片只 submit `card.reconcile` 到唯一结算队列并返回本次 durable 快照；request task 不直接写 card/object/anchor。 |
| `GET /api/chat/turns/{turn_id}` | ✅ | 返回单个 durable turn。若读到非终态卡片，只同步 admission `card.reconcile` 并立即返回快照。worker 会为 `applied=1` receipt 补 stable audit、跨 session projection 与 exact-generation 解锚，也会把没有对应 active anchor 的 orphan `discussing` 校正回 `pending`；因此 publication gap 的第一次 GET 可仍见旧态，queue 完成后的下一次 GET 见权威状态。 |
| `GET /api/chat/pending-confirmations` | ✅ | 读取前在 settlement worker 空闲时扫描 orphan claim：只有 `clarifying` claim 已超过 30 秒创建安全窗、ask-turn identity 未变化、且 durable turn 仍不存在时才释放；worker 正忙时跳过该次修复并直接返回 durable 快照，避免只读 UI 被长 LLM job 卡住，下一次空闲读取/open 会继续修复。随后返回 `{"count":N,"items":[...]}`；只列未结算的高优先级对象且最多 3 条：未验证假设 `confidence>=0.60`、active 疑惑 `interpretation_confidence>=0.50`。无活跃澄清时疑惑固定预留 1 席；已有全局 `clarifying` 时只保留该持有者，隐藏必然无法 claim 的其它 open 疑惑。UI 传 `?session=popup|webui` 后，若该持有者已在本 session 有 turn，则不重复显示；其它 session 仍可打开同一 ref 并获得本地 turn。主聊天三端使用 `popup`，`webui` 保留为兼容的独立 session。`?count_only=1` 只返回 `{"count":N}`，供 service worker badge 轻量轮询；`openbiliclaw questions` 读取完整响应且不复制筛选规则。用户主动列表不套用系统冷却。 |
| `POST /api/chat/pending-confirmations/{ref}/open` | ✅ | body 为 `{"session":"popup|webui|..."}`。若唯一 settlement worker 正在处理长 LLM job 或处于原子交接，端点在任何 claim/turn 写入前返回 `503 detail.code="dialogue_busy"` 与 `Retry-After: 2`；popup、移动 Web 与桌面共享 helper 最长按安全热重载窗口自动重试并显示等待态。空闲后，假设生成 completed card；疑惑通过 required `confusion.open.sync` 进入 `clarifying`，再由 required `anchor.establish` 以 `pending_open` 建锚，不使用会超时后继续执行的 1 秒 fast path，因此不会留下“claim 已完成、turn 未创建”的半截状态。相同 `(ref,session)` 原子复用，跨 session 各自产 turn；API 不在 request task 执行 protected mutation。 |
| `POST /api/chat/cards/{turn_id}/action` | ✅ | body 为 `{"action":"confirm|reject|discuss|defer"}`。四动作分别 submit `settle.hypothesis`、`card.discuss`、`card.defer` 到唯一队列；confirm/reject 与锚定 `support/contradict/revise/answer`、普通 chat settles、legacy endpoint 共用 immutable ref winner。discuss 在 worker 内 `pending→discussing→建锚`，建锚失败立即补偿回 pending；defer 只对 pending/discussing 卡在 worker 内更新卡片/冷却，若卡由 pending-open 建锚但仍保持 `pending`，会按 origin turn 精确释放同代锚，若卡已 confirmed/rejected 则返回权威终态的 `already_settled` 且不写 cooldown。HTTP 最多等本地 job 1 秒，完成保持同步 `200`，队头阻塞返回 `202 processing` 且不会取消已入队 job。 |
| `POST /api/insights/feedback` | deprecated | 保留旧客户端响应结构和 `Deprecation: true`，内部通过共同 façade submit 同一队列，台账 `source="legacy_endpoint"`；1 秒内未完成时同样返回 HTTP `202`，不新增 legacy 专用 executor。**锚冲突返回 `409`**：当另一张卡片持有对话锚时结算会被拒绝（`outcome=stale_anchor` / `anchor_dependency_failed`），此时 `card_settlements` 与台账都没有写入，端点返回 `409` 并在 detail 里说明原因，`Deprecation` / `Link` 头仍然保留。旧行为把这种拒绝包装成 `200 {"ok":true,"matched":false}`，老客户端会误以为确认成功。 |

### 卡片 action 返回

- `outcome="applied"`：本次已由 worker 完成 event/object/derived/rebuild marker 并发布 `applied=1`。
- `outcome="already_settled"`：已存在 `applied=1` 的对象结算；返回既有 verdict 并刷新本卡片投影。
- HTTP `202` + `outcome="processing"`：本地 1 秒等待预算耗尽；入队 completion 被 shield、继续在唯一 worker 执行，不会把 `applied=0` 伪装成终态。
- `outcome="discussing"` / `"deferred"`：分别表示活锚已建立 / 当前卡片已延期。
- `state="revised"`（终态，文案「已按你的修正记下」）：修正式结算——原假设被替换、派生假设已写入。它**不是** `rejected`；把 revise 投影成否定会让刚说完「我认可修正版」的用户看到「已标记不准」。
- `outcome="stale_anchor"` / `"anchor_dependency_failed"`（`state="stale"`）：对话锚被另一张卡片占用，本次结算被拒绝，`card_settlements` 与台账均无写入。前端共享 helper 把这两个 outcome 归入 `retryable_error`：乐观态回滚到操作前的真实状态，提示用户先结束当前正在聊的那条再重试——**不得**回落到乐观终态，否则卡片会显示「已确认」而后端什么都没记。

## 一致性边界

所有声明的对话结算入口只进入一个 `DialogueSettlementQueue`、一个 actual worker。confirm/reject 的顺序固定为：`INSERT OR IGNORE` 固化 immutable winner → event identity 与 event 同事务 → object → derived → rebuild marker + stable audit → `applied=1` → 跨 session projection → exact-generation 解锚。卡片 action、legacy endpoint、锚关系与无锚 chat 的 speculation/insight/confusion settles 共用这条 ref 路径；只有 `applied=1` 可生成终态投影。protected façade 校验 actual worker Task + lifecycle nonce；worker 内嵌套 settle 由该 task 直接 `_apply_*`，不会 submit/inline dispatcher。API request、active child 与跨 job detached child 均不能写或冒充队外 producer。

`card_settlements` 不再保存 claim/lease/token/`seg_*`，也没有文件锁、takeover 或恢复 scanner。rebuild marker 仍使用同目录临时文件 `flush+fsync` 后原子替换；写盘失败会使 job 失败且 receipt 保持 `applied=0`，不会提前投影卡片。后续同 ref 显式重试采用原 winner，幂等 effect 补齐缺口。

对话内结算（锚归属 `support/contradict/revise/answer`）落库在**回复完成之后**——worker 还要跑归属判断和队列 job。因此桌面 Web 在回复完成后继续按 1/2/5/5/5/5/5 秒重读对话，直到卡片进入终态或用完 ~30 秒预算（与卡片 action 的 `CARD_ACTION_POLL_DEADLINE_MS` 同量级）；只在屏幕上确有未结算卡片时才轮询。少了这步，用户说完「我认可修正版」后卡片会一直停在「正在聊这条」直到手动刷新——真机浏览器 E2E 实测 8 秒预算会漏掉。

队列本身是进程内、非 durable 的：若进程在 `202` 后重启，尚未执行的 job 可以丢失，但 durable card/receipt 不会伪终态。popup、移动 Web 与桌面 Web 对 `202 processing` 按 `1s/2s/5s`（之后保持 5s）读取 `GET /api/chat/turns/{turn_id}`，总截止 30 秒；终态立即停止，超时、读取持续失败或页面 abort 显示本地 `retryable_error`，允许刷新或重试 action。CLI/OpenClaw 不消费该 HTTP action 契约。

系统抛出的两个 gate 必须同时满足：距上次全局抛出至少 12 小时，且同 ref 的 `last_asked_at` / `deferred_until` 已超过 72 小时；两者持久化在 `memory/dialogue_confirmation_state.json`。用户主动 open 明确绕过这两个时间 gate，但疑惑仍受数据库 `clarifying <= 1` 约束。附着 turn 与用户 turn 同秒时，以 `(created_at,rowid)` 保证卡/问题在前；空消息校验与既有 `turn_id` 幂等检查均发生在附着前。

## 客户端入口约束（Wave D）

popup、移动 Web 与桌面 Web 只有 durable 对话中的假设卡片保留 confirm/reject/discuss/defer 主动动作，并共享上述按需轮询 helper；同步 `200` 不启动额外轮询。三端画像/认知更新区均只读。`openbiliclaw questions` 也只发 GET 并展示列表。`POST /api/insights/feedback` 仍为旧客户端保留并转发共同队列，但新客户端不再调用它；因此“对话是唯一主动 UI 确认入口”与 legacy 兼容同时成立。

## Runtime stream 保活与重连

`GET ws://.../api/runtime-stream` 在 20 秒没有业务事件时发送 `{"type":"runtime.heartbeat","sent_at":"..."}`。心跳与普通事件共用唯一 writer，避免并发 `send_json`；鉴权撤销仍在每次发送前和 15 秒 watchdog 中 fail closed。桌面 Web 收到心跳即确认“实时连接正常”，异常 close 则显示“实时流重连中”、记录 close code/reason，并按 3 秒节奏重连；页面进入后台时仍按 visibility 生命周期主动关闭，不把该主动关闭显示成后端离线。
