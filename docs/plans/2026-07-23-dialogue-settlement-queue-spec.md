# 对话结算单队列 Spec

**Created:** 2026-07-23
**Status:** Proposed
**Baseline:** `feat/cognitive-profile-pipeline` @ `e16797ec`
**Plan:** [`2026-07-23-dialogue-settlement-queue-plan.md`](./2026-07-23-dialogue-settlement-queue-plan.md)
**Supersedes:** 已撤回的 `2026-07-23-settlement-serialization-{spec,plan}.md`

## 1. Goal

把**对话产生的 hypothesis、confusion 与 card 结算**收口到后端主进程内的一条
串行处理线：一个内存 `asyncio` 队列、一个消费者，按入队顺序 `await` 执行。

本方案只解决对话域内部的并发：

- 卡片四动作、锚处理、普通 chat settles、`scope=probe/confusion` 的 durable
  回复侧效应、legacy `/api/insights/feedback` 不再各自直接 mutation；
- 同一进程内不再有两个对话结算执行者，不再需要文件锁、5 分钟 lease、
  claim/fencing token 或三段 claim-CAS；
- 锚 generation 在**结算 job 受理/入队时**冻结，worker 只校验冻结值，
  绝不在执行时补抓当前锚；
- 保留 ref 级 SQLite winner 收据、卡片 payload 投影与可重试的副作用幂等，
  但收据也只由 worker 写，不增加 durable inbox、跨进程 owner 或启动恢复扫描。

### 1.1 量化完成门

| Gate | 必须达到 |
| --- | --- |
| 单消费者 | 100 个混合 job 并发提交，`max_active == 1`，严格按受理序执行 |
| 入口收口 | §2.2 的入口清单覆盖率 100%；runtime/API spy 中 worker 外业务 mutation 为 0 |
| ref 仲裁 | 同 ref 100 个相反 confirm/reject 请求只有 1 个 winner；event、对象更新、rebuild marker、投影各至多 1 次 |
| generation | “受理时 generation=0、执行前建立同 ref 新锚”及“旧代分析后换锚”各交错 100 次，旧请求业务副作用均为 0 |
| 事件循环安全 | worker 等待 LLM 时 20 Hz heartbeat 连续运行；不存在同步文件锁跨 `await`，不存在 worker 等待自己入队 Future |
| action 契约 | 空队列本地 action 仍返回 `200`；阻塞一个 LLM job 后 action 在 1.5 s 内返回既有 `202 processing`，释放后轮询得到终态 |
| crash/retry | event/object/derived/rebuild/projection/anchor 六个边界逐一故障注入；同请求重试后 mandatory effect 最终各 1 次 |
| 旧栈删除 | `claim_card_settlement`、`card_settlement_claim_guard` 的 runtime/test 命中均为 0；`apply_claim_*`/`seg_*` 只可存在于具名 legacy migration routine/fixture |
| 质量 | focused/full pytest 全绿，覆盖率 `>=70%`，Ruff、MyPy strict、extension test/typecheck/build 全绿 |

核心复现命令：

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_dialogue_settlement_guard.py \
  tests/test_dialogue_learn_queue.py \
  tests/test_dialogue_anchor.py \
  tests/test_confusion_lifecycle.py \
  tests/test_database.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py -q

test "$(rg -n \
  'claim_card_settlement|card_settlement_claim_guard' \
  src/openbiliclaw tests | wc -l | tr -d ' ')" = "0"
test "$(rg -n \
  'apply_claim_token|apply_claim_at|seg_event|seg_object|seg_marker' \
  src/openbiliclaw --glob '!storage/database.py' | wc -l | tr -d ' ')" = "0"
PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_database.py \
  -k 'legacy_card_settlement_columns_are_migration_only' -q
```

完整环境和验收命令见 companion plan §1、Wave 3。

## 2. 严格边界

### 2.1 队列拥有的对象

队列只拥有以下由对话入口触发的状态变化：

1. hypothesis 的 confirm/reject/support/contradict/revise 结算；
2. confusion 的澄清、answer/defer、锚 relation 与终态结算；
3. hypothesis card 的 `pending/discussing/deferred/confirmed/rejected` 状态及跨
   session 投影；
4. 普通 chat 的 `settles` 输出；其中 speculation 只有在它是本次对话分析结果时
   才随该 job 串行应用，不把 speculation 的全生命周期纳入本方案；
5. `scope=probe` durable 对话回复产生的 interest probe 结算侧效应，以及
   `scope=confusion` 回复产生的对话观察/锚结算侧效应。

“拥有”指业务 mutation 必须发生在 worker context；入口只可做参数校验、纯读和
构造不可变 payload。ref winner 收据也由 worker 按 queue sequence 创建；HTTP
handler 不预留 receipt，不在 worker 外形成第二个仲裁点。

创建一条初始 `payload.state=pending` 的 card/question turn 也属于 admission，
继续使用现有 SQLite `(ref, session)` 去重；guard 保护的是 admission 之后的
状态迁移、结算、投影与锚 mutation。chat reply/status 的 durable turn 写入同理不是
本 spec 要串行化的对话对象结算。

### 2.2 必须入队的入口

| 入口 | 当前位置 | 入队 action / 处理方式 |
| --- | --- | --- |
| 卡片 `confirm/reject` | `api/app.py:8117-8144` | `settle.hypothesis` |
| 卡片 `defer` | `api/app.py:7925-7959` | `card.defer` |
| 卡片 `discuss` | `api/app.py:7961-8013` | `card.discuss`，worker 内建锚 |
| pending open / confusion question 建锚 | `api/app.py:2743-2906` | `anchor.establish`；turn 创建仍使用既有 SQLite 去重 |
| durable confusion turn 建锚 | `api/app.py:7641-7674` | `anchor.establish`，先于该 turn 的 learn job |
| 锚 relation/解除/归属结算 | `soul/engine.py:2872-3037` | 已处于 learn job 的 worker context，调用内部 apply，不二次入队 |
| 普通 chat `settles` | `soul/engine.py:1811-1820,3239-3310` | 已处于 learn job 的 worker context，调用内部 apply |
| durable `scope=probe` 回复侧效应 | `api/app.py:7730-7794` | `probe.reply.apply` |
| durable `scope=confusion` 回复侧效应 | `api/app.py:7869-7882` | `confusion.reply.apply` |
| confusion attribution 补放 | `soul/engine.py:2669-2803` | 12h hook 只 submit；分析/应用仍在同一 worker |
| legacy insight feedback | `api/app.py:9038-9092` | façade 提交 `settle.hypothesis`，保留 deprecated headers |
| card projection / orphan discussion repair | `api/app.py:2622-2683` | GET 不直接写；需要时投递 `card.reconcile` |

`SocraticDialogue` 不再允许 `learn_queue is None` 时
`asyncio.create_task(learn_from_dialogue)` 的旁路。runtime 未安装队列时，学习/结算
必须显式失败或降级为“只回复、不 mutation”，不得静默创建第二 writer。

`probe.reply.apply` 只收口 probe 对 hypothesis/speculation、feedback history、
visible cognition/event 的结算侧效应。现有 weak-positive 分支中的 exploration
buffer record/promotion 仍属于 §2.3，必须从 handler 边界上明确拆开，不能借
`probe.reply.apply` 把 exploration 写入偷带进队列。

### 2.3 Out of scope

以下路径**不进入本队列，也不为本方案重构**：

- `force_tick` 与 avoidance 调度；
- exploration buffer 的记录、promotion 与相关画像写回；
- cognitive/profile pipeline 深层写入及其通用 writer 收口；
- OpenClaw bootstrap/operations；
- 直接 interest/avoidance probe button API 与 avoidance dialogue
  (`scope=avoidance_probe`)；
- CLI 的非对话对象操作。CLI 若以后要修改 hypothesis/confusion/card，只能调用
  本地 API；否则保持现状；
- 多进程/多 daemon/多 Uvicorn worker 的 owner 选举；
- durable inbox、job 表、5 分钟接管、跨进程 lease/fencing；
- 把整个 SoulEngine、所有 SQLite 写或所有 LLM 调用变成全局 actor。

这些是其他任务的 writer。正常产品路径假定它们与本 spec 的对话对象不争用；
刻意制造的跨路径极端并发只作为 §10 已知限制记录，不借此扩张本方案。

## 3. 现状诊断与必须删除的锁栈

行号以 baseline 为准；实现时以符号名为准。

| 位置 | 现状问题 | 目标 |
| --- | --- | --- |
| `src/openbiliclaw/soul/dialogue_learn_queue.py:35-202` | 只有 learn payload；无 typed action、completion Future 或 worker guard | 泛化为统一队列骨架 |
| `src/openbiliclaw/soul/dialogue.py:194-226` | 有队列时 submit；无队列时 detached direct learn | 删除 direct fallback |
| `src/openbiliclaw/api/runtime_context.py:137-150,1287-1306,1345-1347` | queue handler 只绑定 `learn_from_dialogue` | 绑定统一 dispatcher |
| `src/openbiliclaw/storage/database.py:44,90-102` | settlement 专属进程内 `RLock` + 文件锁依赖 | 删除 |
| `src/openbiliclaw/storage/database.py:1325-1327` | 每数据库构造 `.card-settlement.lock` | 删除 |
| `src/openbiliclaw/storage/database.py:2526-2567` | 5 分钟 lease、claim/takeover token | 删除 |
| `src/openbiliclaw/storage/database.py:2569-2610` | 文件锁 fence 与 claim guard | 删除 |
| `src/openbiliclaw/storage/database.py:2612-2782` | token 条件下 event/object/marker 三段 CAS + complete CAS | 替换为单 worker 的 receipt/effect 幂等 helper |
| `src/openbiliclaw/storage/database.py:1004-1018,9184-9222` | schema/migration 保留 claim 与三段字段 | table rebuild 到简化 schema；保留 winner 数据 |
| `src/openbiliclaw/soul/engine.py:1089-1097` | `anchor_generation <= 0` 时执行期补抓 current anchor，会捕获“未来锚” | 无条件删除；`0` 永不升级 |
| `src/openbiliclaw/soul/engine.py:1184-1460` | request coroutine 自己跑 claim + 三段 executor | 仅 worker 可调用的简化 apply |
| `src/openbiliclaw/soul/engine.py:1373-1401` | 持 `card_settlement_claim_guard` 时 await object apply | 删除锁与 guard |
| `src/openbiliclaw/soul/engine.py:1406-1439` | 持同一 guard 时 await rebuild marker | 删除锁与 guard |
| `src/openbiliclaw/api/app.py:7901-8013,8117-8144,9038-9092` | action/legacy 直接 await engine 或直接改 card/anchor | 只 submit/有界等待 |

旧 `card.discuss` 的 `attempt_token + 5min stale repair` 也不再承担并发 fencing。
单 worker 下改为：worker CAS `pending→discussing`，建锚失败立即补偿回
`pending`；进程崩溃留下的 “discussing 但无匹配锚” 在下一次
`card.reconcile`/action 中立即、幂等地回到 `pending`，不等待 lease。

## 4. 前三轮验收真问题如何落位

| 真问题 | 单队列后的处理 |
| --- | --- |
| 两个请求重复执行同一对象副作用 | 同进程只剩一个 consumer；ref winner 收据仍决定相反 verdict 的唯一赢家 |
| claim 前后 TOCTOU、旧 executor 接管后恢复 | 没有第二 executor、takeover 或 lease，整类交错消失 |
| OS 文件锁跨 `await` 导致事件循环自锁 | 删除 settlement 文件锁、guard 和 `asyncio.to_thread(claim)`，整类死锁消失 |
| 卡片、legacy、普通 chat、锚、probe reply 在对话域内旁路 | §2.2 全部 submit；worker 内嵌套 settle 走内部 apply，不递归排队 |
| LLM 后 generation 校验与对象提交间仍有窗口 | 同一 worker 内校验 frozen generation 后连续 apply；本队列内无第二锚 writer 插入 |
| `engine.py:1089` 捕获未来锚 | 删除执行期推断；受理为 0，执行永远为 0 |
| effect 后、旧 segment flag 前崩溃可能重复 ledger/derived | effect 使用稳定 key/幂等 set-upsert；不再依靠三段 fencing CAS 获得正确性 |
| force_tick/pipeline/OpenClaw 等跨任务同时写 | 不自动消失；只在 §10 已知限制中承认，不扩为跨进程协调器 |

## 5. 目标结构

```text
card action / pending-open / legacy / durable reply / Socratic learn
                              |
                   validate + freeze generation
                              |
                              v
             one in-memory asyncio.Queue[DialogueJob]
                              |
                    one async worker task
                              |
          ContextVar worker permit + typed dispatcher
          /          |           |          \
       learn      settle       anchor       card/probe effect
    (LLM+apply)  (internal)   (internal)       (internal)
          \          |           |          /
               idempotent local effects
                              |
                 receipt applied + projection
```

### 5.1 Queue envelope

`DialogueJob` 是进程内 typed envelope，至少包含：

- `job_id`：仅用于日志/测试关联，不持久化；
- `kind`：固定白名单；
- `payload`：提交时复制，worker 不读取调用方后续可变对象；
- `anchor_target_ref` / `anchor_ref` / `anchor_generation`：提交时冻结。active
  anchor 与 payload 的 target kind/ref 匹配时保存其 generation；target 已知但当时
  无匹配锚时保存 target ref + generation `0` 作为 tombstone；完全不涉及锚的 job
  才保存空 ref + `0`；
- `accepted_at` / 单调 `sequence`；
- 可选 `completion: asyncio.Future[DialogueJobResult]`：
  action/legacy 使用；fire-and-forget learn 不使用。

队列本身不写 `settlement_jobs` 表，不扫描 SQLite，不做 owner lock。
`shutdown`/热重载沿用当前 self-owned worker 的 pause/drain 语义；进程被强杀时未执行
job 可丢。

### 5.2 单 worker 与禁止递归入队

worker 在 dispatch 前设置私有 `ContextVar` permit，`finally` 中复位。所有受保护
mutator 必须验证 permit。

如果 worker 内的 learn/anchor handler 再调用公开 `submit_and_wait()`，队列立即抛
`DialogueSettlementReentryError`；不得把子 job 放到自己身后再等待。普通 chat
settles 与锚 relation 必须调用 `_apply_*` 内部函数，共享当前 worker context。

异常只令当前 job 失败并完成其 Future；worker 记录结构化日志后继续消费下一项。
禁止持同步文件锁跨 `await`，禁止 worker 内 detached `create_task` mutation。

## 6. Generation 冻结

1. producer 在 `queue.submit()` 接受 payload 的同一同步片段读取一次 anchor snapshot；
2. snapshot 复制进 envelope；target 已知但无匹配锚时保存 generation `0`
   tombstone，不能丢掉 target ref；
3. worker 对锚相关 job 在第一个业务 effect 前调用
   `validate_snapshot(target_ref, anchor_ref, generation)`；
4. generation `>0` 时要求 ref/generation 仍精确相等；generation `==0` 时要求
   target ref 仍没有 active anchor；
5. 不匹配时返回 `stale_anchor`，不写对象、candidate、replay、ledger、profile、
   projection，也不解除当前锚；因此 admission 为 0、执行前出现同 ref 新锚的旧
   job 被整体丢弃，而不是按新锚或“无锚”继续；
6. 删除 `SoulEngine.settle_hypothesis()` 中所有 current-anchor 推断。

`anchor.establish` 自身是 worker job，不受“未来锚”推断。若后续 learn/settle 必须
归属这个新锚，producer 必须先 await 该本地 job 的 completion，并用返回的
`established_generation` 提交后续 job；只保证两个 job 的 FIFO 顺序、却在第二个
job 执行时读取锚，是禁止的。durable confusion turn 因而先完成
`anchor.establish`，再生成/提交会触发学习的回复；若前序长 LLM 占用 worker，
durable turn 保持 pending，而不是伪造 generation。

对 `learn` job，“受理”定义为 Socratic reply 完成后向统一队列提交学习/结算分析的
时刻；对 HTTP action/legacy/open，则定义为 handler 完成校验并调用 queue 的时刻。

## 7. Ref 收据与副作用幂等

### 7.1 简化后的 durable 部分

`card_settlements` 继续保存 winner：

- `ref` 主键；
- `verdict`、`turn_id`、冻结后的 deterministic `payload`；
- `applied`、可选稳定 `result`；
- event 是否已原子记录所需的最小字段；
- `created_at/updated_at`。

worker 先校验 envelope 的 frozen anchor，再创建新 receipt；`stale_anchor` job 不占
ref winner。若 receipt 已存在，retry 必须采用并重新校验 stored winner payload，
不能用 contender snapshot 覆盖。

删除 `apply_claim_at`、`apply_claim_token`、`seg_event`、`seg_object`、
`seg_marker` 及其 runtime 语义。event 恢复只保留一个非 claim/CAS 的稳定
`event_id`（或等价字段）。旧表通过 table rebuild 迁移：

- `applied=1` 原样成为 terminal business receipt，不重放对象语义；显式
  retry/reconcile 仍可幂等补跑 projection/anchor publication；
- `applied=0` 保留 winner payload，等待同 ref 请求重试；
- 旧 `seg_event=1` 映射为“event 已记录”，避免升级后重复 event；
- 旧 object/marker 状态不作为接管权；重试依赖下述幂等函数安全重做。

### 7.2 固定 effect 规则

| Effect | 幂等规则 |
| --- | --- |
| event | event insert 与 receipt 的 event 标记同一 SQLite transaction；稳定 key=`dialogue:{ref}:event` |
| hypothesis/confusion 对象 | deterministic set/update；已终态返回当前结果，不追加第二次语义 |
| derived hypothesis | 按规范化 content hash upsert；对应 ledger 使用稳定 effect key |
| rebuild marker | trigger ref 做 set-union；同 ref 重放不刷新 debounce clock、不清 retry |
| card projection | `UPDATE ... WHERE state IN (...)`，重做得到相同终态；跨 session 全投影 |
| anchor | 必须匹配 frozen generation；release/note 已终态时为 no-op |
| audit ledger | stable effect key + `INSERT OR IGNORE`；仍是 observer，写失败不得回滚已完成业务对象 |

固定顺序为：event → object/derived → rebuild marker → `applied=1` →
projection → anchor publication。event、object/derived 与 rebuild marker 是置
applied 前的 mandatory business effect；projection/anchor 只允许读取 applied
receipt 后执行，并在每次 receipt retry/reconcile 时幂等重放。因此进程可能在
`applied=1` 后、投影前退出，但同 ref
重试会补齐投影/锚且不会重做业务语义。这里不靠 durable scanner 自动恢复。

## 8. LLM 与队头阻塞决策

### 8.1 现状依据

`SoulEngine.learn_from_dialogue()` 当前在 `engine.py:1703-2016` 内交错：

1. dialogue insight extract；
2. anchor relation apply / ordinary settles；
3. posture gate（可能逐 candidate 调 LLM）；
4. preference analysis；
5. rebuild gate；
6. 必要时 profile build；
7. 多处本地持久化。

provider timeout 默认是 `config.py:127,349-354` 的 300 秒。把 analyze 搬到 worker
外并非一个轻量切点：必须为 preference、candidate、anchor、profile 各自再引入
snapshot digest、失配重算和 apply CAS，正是本次要避免的复杂度。

### 8.2 本版结论

- **不拆 analyze/apply**；LLM-bearing learn job 在专属 worker 内顺序 await。
- “对话相关 LLM”在本 spec 中指 reply 之后的 settlement extract、probe
  classification、gate 与必要 rebuild/profile build；生成用户可见 reply 的
  interactive LLM 保持现有请求线，不塞进 settlement queue。
- 沿用 provider 的有限 timeout，不再套一个可能在多段 mutation 中途取消的
  whole-job timeout；队列记录 `depth`、`oldest_age`、job duration 与 timeout/error。
- 对话队列不承载 force_tick/exploration/pipeline/OpenClaw 的 LLM 工作；等待远程
  LLM 时只让出 event loop，不持同步锁，因此不堵其他任务的执行线。
- 对话 settlement 的 admission/顺序不与其他任务共享；当前全局 provider
  concurrency gate 仍只是所有调用的资源上限，本方案不另造 provider pool。
- 若上线后连续 7 天 action `202` 比例 `>1%` 或 action 入队到 applied 的 p95
  `>5s`，再单独设计 read-only analyze + frozen snapshot apply；不在本版预埋。

这接受“一个正在运行的 learn job 会延后其后的 action”，但 HTTP/UI 不必同步等完整
LLM 链，见 §9。

## 9. HTTP 契约：保留 200，按需使用既有 202

不把所有 action 一刀切成异步协议。

1. action/legacy submit 后，用 `asyncio.shield(completion)` 有界等待 **1.0 秒**；
2. 空队列下本地 settlement 通常在窗口内完成，继续返回现有 `200` 与终态 body；
3. 若 worker 正处理长 LLM，card action 返回现有语义的 `202` processing body；
   不新增 job id/status schema，HTTP timeout 不取消已入队 job；
4. card 客户端用既有 `GET /api/chat/turns/{turn_id}` 轮询 payload.state，采用
   1/2/5 秒退避；远端仍为 `pending` 时保留本地 processing，不倒退 UI，
   `confirmed/rejected/deferred/discussing` 或页面关闭时停止；无需新增 job
   status 表/端点；
5. deprecated legacy 保留现有 `InsightFeedbackResponse` body、deprecation headers
   与 `202/ok=false` 表达；调用方可按同 hypothesis 重试，ref winner 收据确保
   读取/恢复同一 verdict，不为它增加 outcome/job 字段；
6. worker 失败时 card 保持原 durable state，刷新后允许用户重试，不伪造终态。

1.0 秒是本地 SQLite/JSON action 的首轮预算，不是 LLM 预算；实现须带校准注释并
记录 queue wait。若隔离测试 100 次 action 任一次超过 1.0 秒，应先修本地慢点，
不能靠扩大同步窗口掩盖。

## 10. Design invariants（可证伪）

| ID | MUST | 失败判据 |
| --- | --- | --- |
| Q1 | §2.2 的对话结算全部经同一 queue | 任一入口 spy 到 worker 外 protected mutation |
| Q2 | 主进程内恰一个 active consumer | 100-job 测试出现 `max_active > 1` 或热重载出现新旧 worker 重叠 |
| Q3 | generation 在 admission 冻结 | payload 后改、worker 补抓 current、0 升级为未来代次任一发生 |
| Q4 | worker 不等待自己 | worker 内 public submit 未立即报 reentry，或测试超过 100 ms 未结束 |
| Q5 | 不持同步 settlement lock 跨 await | 旧锁符号仍存在，或 heartbeat 在 LLM await 期间停止 |
| Q6 | ref receipt 仅由 worker 创建，且 winner payload 不变 | handler 预留 receipt、同 ref 两 verdict 都写对象，或 retry 使用 contender payload |
| Q7 | mandatory effect 可安全重做 | 六个 crash gap 任一最终计数不是 1 |
| Q8 | 只有 applied receipt 可投影终态 | `applied=0` 时任一卡片变 confirmed/rejected |
| Q9 | card 跨 session 投影一致 | 同 ref 两 session 终态不同 |
| Q10 | queue 明确非 durable | 新增 job/inbox scanner、owner lock 或宣称重启自动恢复 |
| Q11 | action 常态 200、拥塞才 202 | 空队列总是 202，或拥塞请求同步挂到 LLM 完成 |
| Q12 | 对话域旁路矛盾只可在已知限制场景复现 | 单 backend、仅 §2.2 入口仍能产生相反对象终态 |

## 11. Wave

| Wave | 内容 | 交付门 |
| --- | --- | --- |
| Wave 0 | 冻结入口/受保护 mutator 清单；把 deadlock、future anchor、旁路与重试语义写成 RED tests | 清单覆盖 100%，失败原因确定 |
| Wave 1 | 泛化 `DialogueLearnQueue` 为 typed 统一队列；Future、frozen snapshot、worker permit、reentry/lifecycle | 100 mixed jobs 单消费者；无 detached fallback |
| Wave 2 | 简化 SQLite receipt/effect 幂等；删除锁/lease/token/三段 executor；删除执行期 generation 推断 | 旧符号 0；六 crash gap 可重试 |
| Wave 3 | cards/anchor/chat/probe/confusion/legacy cutover；按需 202+轮询；护栏、旧测试改写、文档与全量门 | 所有量化门全绿 |

Waves 0–1 可独立提交。Wave 2 的 schema/executor 删除与 Wave 3 的入口 cutover
属于同一个不可拆分的 runtime 迁移窗口：可按 Task 顺序开发，但不得发布只含
Wave 2 的中间状态；任何入口也不得同时 direct mutation 又 submit。

## 12. 已知限制

1. **不支持跨进程串行。** 两个 backend/多 Uvicorn worker 指向同一 data dir 时，
   各有自己的内存队列；SQLite winner 收据能限制 confirm/reject winner，但不能让
   JSON/anchor/profile 的全部副作用全局串行。支持部署仍是单 backend writer 进程。
2. **内存 job 可丢。** SIGKILL、机器掉电或强制 reload 可丢尚未执行/正在执行的
   queue item。已有 ref receipt 的 action 可由用户重试恢复；没有请求重试就不会
   自动完成。
3. **有界队头延迟仍存在。** 一个正在运行的 dialogue learn LLM job 不被抢占；
   后续 action 可能先收到 202，最终完成时间受 provider timeout 影响。
4. **跨任务极端并发不保证线性一致。** force_tick、avoidance、exploration、
   pipeline、OpenClaw、非对话 CLI writer 若被刻意安排在同一对象/文件的极端交错，
   本队列不提供全局 serializability；这不是重引入 owner/lease 的理由。
5. **legacy 只有幂等重试，没有 durable job polling。** 新 card UI 轮询 turn；
   deprecated insight endpoint 保持兼容 façade，不为它新增 job API。
6. **audit observer 可缺失。** audit ledger 写失败不回滚已完成业务对象；稳定
   effect key 只保证重试不重复，不把审计日志升级成强事务依赖。

## 13. 文档义务

本次 planning commit 只新增本 spec 与 companion plan，不改变 runtime/API，因此
不改模块文档、changelog 或架构图。

实现 PR 必须按 `CLAUDE.md#documentation-requirements` 同步：

- `docs/modules/soul.md`：统一 queue、worker-only mutation、generation 冻结；
- `docs/modules/api.md`：action 的 200 fast path / 202 processing + turn polling；
- `docs/modules/storage.md`：简化 receipt schema、旧 lease 表迁移、effect 幂等；
- `docs/modules/runtime.md`：self-owned queue 的 start/pause/drain/shutdown；
- `docs/modules/llm.md`：对话 LLM 串行、provider timeout 与 HOL 指标；
- `docs/modules/extension.md`：仅在 processing 轮询行为有客户端改动时更新；
- `docs/changelog.md`：当前版本块增加一条；
- 数据流改变，必须同步 `docs/architecture.md`、`docs/spec.md` §3、
  `README.md` 与 `README_EN.md` 顶部架构图；
- CLI/config 未改变，不更新对应文档；若实现超出本 spec，必须先修订 spec。

这不是 release，不新增 README 版本 highlights。
