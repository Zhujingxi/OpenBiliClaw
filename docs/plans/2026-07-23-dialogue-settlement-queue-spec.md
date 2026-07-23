# 对话结算单队列 Spec

**Created:** 2026-07-23
**Status:** Proposed — revised after adversarial review round 1
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
  绝不在执行时补抓当前锚；队列级 admission registry 同时看见尚未执行的
  `anchor.establish` 预约，不能只读当前持久化锚；**[F2]**
- 保留 ref 级 SQLite winner 收据、卡片 payload 投影与可重试的副作用幂等，
  但收据也只由 worker 写，不增加 durable inbox、跨进程 owner 或启动恢复扫描。

这里的“唯一 writer”只约束 §2.1–2.2 的 API/runtime 对话对象。CLI 与 OpenClaw
继续使用显式命名的既有 direct-learning 兼容模式，不接入本队列、不降级成
“只回复不学习”；这是边界保持，不是新增入口。**[F5]**

### 1.1 量化完成门

| Gate | 必须达到 |
| --- | --- |
| 单消费者 | 100 个混合 job 并发提交，`max_active == 1`，严格按受理序执行 |
| 入口收口 | §2.2 的入口清单覆盖率 100%；runtime/API spy 中 worker 外业务 mutation 为 0；pending-open 的 `schedule_ask`、retarget、rollback 与原始 `Database.update_confusion` sink 也计入 **[F3]** |
| ref 仲裁 | 同 ref 100 个相反 confirm/reject 请求只有 1 个 winner；event、对象更新、rebuild marker、投影各至多 1 次 |
| generation/admission | “无锚 job 先受理、后建锚”与“`anchor.establish` 已入队未执行、后续 settle 再入队”各交错 100 次；后者绑定 admission 预约、前者保留 no-anchor tombstone，均不在执行期重读/升级 **[F2]** |
| 事件循环安全 | worker 等待 LLM 时 20 Hz heartbeat 连续运行；不存在同步文件锁跨 `await`，不存在 worker 等待自己入队 Future |
| worker 身份 | worker 直接调用 protected mutator 成功；worker 内 `create_task()` 的 child 即使继承 context 也必须失败，证明 permit 绑定实际 worker Task **[F4]** |
| replay kind | 12h attribution 只提交 `confusion.attribution.replay`；dispatcher 有独立分支，同 replay identity 重放 10 次只分析/应用一次 **[F1]** |
| action 契约 | 空队列本地 action 仍返回 `200`；阻塞一个 LLM job 后 action 在 1.5 s 内返回既有 `202 processing`；有限 poll deadline 后进入可重试状态，模拟重启丢 job 后可再次提交 **[F6]** |
| crash/retry | event/object/derived/rebuild/`after_applied_before_projection`/projection/anchor 七个边界逐一故障注入；applied gap 可由 GET reconcile 补投影且不重做对象语义 **[F7]** |
| probe handoff | probe classification 在 worker 内恰 1 次；weak-positive exploration intent 经 completion result 交给队外写，exploration mutation 时无 worker permit **[F8]** |
| 兼容模式 | CLI/OpenClaw 无 queue 对话仍各触发既有 direct learn 1 次、queue submit 0 次；API runtime 无 queue 不允许静默 direct learn **[F5]** |
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
  tests/test_cognition_cycle.py \
  tests/test_database.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py \
  tests/test_cli.py \
  tests/test_openclaw_adapter.py -q

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
| pending open / confusion question claim、retarget、rollback | `api/app.py:2743-2906`，尤其 `2762-2782,2879-2891` | `confusion.open.sync` 的同一 handler 处理 `schedule/retarget/rollback`；成功后 `anchor.establish`；turn 创建仍使用既有 SQLite 去重 **[F3]** |
| durable confusion turn 建锚 | `api/app.py:7641-7674` | `anchor.establish`，先于该 turn 的 learn job |
| 锚 relation/解除/归属结算 | `soul/engine.py:2872-3037` | 已处于 learn job 的 worker context，调用内部 apply，不二次入队 |
| 普通 chat `settles` | `soul/engine.py:1811-1820,3239-3310` | 已处于 learn job 的 worker context，调用内部 apply |
| durable `scope=probe` 回复侧效应 | `api/app.py:7730-7794` | `probe.reply.apply`；worker 返回 typed classification/exploration intent，exploration 写由原调用 task 在队外消费 **[F8]** |
| durable `scope=confusion` 回复侧效应 | `api/app.py:7869-7882` | `confusion.reply.apply` |
| confusion attribution 补放 | `soul/engine.py:2669-2803` | 12h hook 只提交独立 `confusion.attribution.replay`；分析/应用在其专属 dispatcher handler，不借 `learn`/`confusion.reply.apply` **[F1]** |
| legacy insight feedback | `api/app.py:9038-9092` | façade 提交 `settle.hypothesis`，保留 deprecated headers |
| card projection / orphan discussion repair | `api/app.py:2622-2683` | GET 不直接写；需要时投递 `card.reconcile` |

pending-open 的 endpoint 只能创建/读取初始 turn、构造 command 并等待本地
completion。`ConfusionManager.schedule_ask`、用于 ask turn retarget/rollback 的
`Database.update_confusion`，以及 anchor mutation 都只能由上述 worker handler
调用；turn 创建失败或 SQLite 去重返回另一 canonical turn 时，也必须再次提交同一
`confusion.open.sync` handler 的 `rollback/retarget` operation，不能在 exception
分支恢复 direct sink。AST inventory 与 runtime spy 对这些原始 sink 记录
`asyncio.current_task()`，§2.2 流程中 worker 外调用必须为 0。**[F3]**

API/runtime 的 `SocraticDialogue` 不再允许 `learn_queue is None` 时
`asyncio.create_task(learn_from_dialogue)` 的隐式旁路：正常 API runtime 缺 queue
必须显式失败；无学习 test double 才可显式选“只回复”。但
`cli.py:971` 与 `integrations/openclaw/operations.py:300-306` 必须显式选择命名的
`legacy_direct`（名称可等价）兼容模式，保持当前回复后 direct learning，不能因
全局删除 fallback 静默只回复。这两个 callsite 不提交本队列、也不安装 worker
guard。**[F5]**

`probe.reply.apply` 只收口 probe 对 hypothesis/speculation、feedback history、
visible cognition/event 的结算侧效应。现有 weak-positive 分支中的 exploration
buffer record/promotion 仍属于 §2.3，必须从 handler 边界上明确拆开，不能借
`probe.reply.apply` 把 exploration 写入偷带进队列。worker 只在 completion result
返回 immutable exploration intent；提交它的原 task 在 await completion 后、无
worker permit 时调用既有 exploration helper，且不得再次分类。**[F8]**

### 2.3 Out of scope

以下路径**不进入本队列，也不为本方案重构**：

- `force_tick` 与 avoidance 调度；
- exploration buffer 的记录、promotion 与相关画像写回；
- cognitive/profile pipeline 深层写入及其通用 writer 收口；
- OpenClaw bootstrap/operations；唯一允许的改动是给现有构造点显式标注
  `legacy_direct` 兼容模式并补回归测试，不把它接进 queue/guard；**[F5]**
- 直接 interest/avoidance probe button API 与 avoidance dialogue
  (`scope=avoidance_probe`)；
- CLI 的非对话对象操作。`openbiliclaw chat` 的现有 direct-learning 只做同样的
  显式兼容标注与测试，行为不变；CLI 若以后要修改 hypothesis/confusion/card，
  只能调用本地 API；否则保持现状；**[F5]**
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
| `src/openbiliclaw/soul/dialogue.py:194-226` | 有队列时 submit；无队列时隐式 detached direct learn，API 与 CLI/OpenClaw 无法区分 | API/runtime 删除隐式 fallback；CLI/OpenClaw 仅在显式 `legacy_direct` 模式保留既有学习 **[F5]** |
| `src/openbiliclaw/api/runtime_context.py:137-150,1287-1306,1345-1347` | queue handler 只绑定 `learn_from_dialogue` | 绑定统一 dispatcher |
| `src/openbiliclaw/api/app.py:2762-2782,2879-2891` | pending-open 的 schedule、retarget、create-failure rollback 直接写 confusion | 同一 `confusion.open.sync` worker handler，endpoint 原始 sink 调用为 0 **[F3]** |
| `src/openbiliclaw/soul/engine.py:2669-2803` | 12h replay 是一个直接执行分析/settlement 的宽函数，没有 typed command identity | 独立 `confusion.attribution.replay` kind、handler、dispatcher 分支和 replay receipt 检查 **[F1]** |
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
| `src/openbiliclaw/cli.py:971`、`src/openbiliclaw/integrations/openclaw/operations.py:300-306` | 构造无 queue 的 `SocraticDialogue`，依赖现有 direct learn | 显式 pin 为 out-of-scope `legacy_direct`；不接 queue、不改学习语义 **[F5]** |

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
| `engine.py:1089` 捕获未来锚 | 删除执行期推断；真实无锚冻结 absent tombstone，已排队 establish 冻结 reservation，worker 均只用 admission snapshot **[F2]** |
| effect 后、旧 segment flag 前崩溃可能重复 ledger/derived | effect 使用稳定 key/幂等 set-upsert；不再依靠三段 fencing CAS 获得正确性 |
| force_tick/pipeline/OpenClaw 等跨任务同时写 | 不自动消失；只在 §10 已知限制中承认，不扩为跨进程协调器 |

### 4.1 对抗 review round 1 追踪

| Finding | 本 spec 的封口 | Implementation plan |
| --- | --- | --- |
| F1 | 独立 replay kind/handler/result identity（§2.2、§5.3） | Task 1.1、3.2 |
| F2 BLOCKER | queue-global reservation + no-anchor tombstone（§6） | Task 0.1、1.1、2.2 |
| F3 | pending-open 三类 mutation 归同一 handler（§2.2、§5.3） | Task 0.2、3.1 |
| F4 | permit 以实际 worker Task 身份为必要条件（§5.2） | Task 0.2、1.1 |
| F5 | API 删除隐式 fallback；两处边界外 caller 显式保学习（§2.2–2.3） | Task 1.2 |
| F6 | 30 秒 poll deadline + restart/lost-job resubmit（§9） | Task 3.3 |
| F7 | `after_applied_before_projection` + GET publication-only reconcile（§7.2） | Task 2.3 |
| F8 | typed completion/result 把 exploration intent 交到队外（§8.3） | Task 3.2 |
| F9 | 三个点名旧测试与全部旧机制测试逐项改写 | Task 2.1、3.4 的具名映射表 |

## 5. 目标结构

```text
card action / pending-open / legacy / durable reply / Socratic learn
                              |
         queue-global admission snapshot / anchor reservation [F2]
                              |
                              v
             one in-memory asyncio.Queue[DialogueJob]
                              |
                    one async worker task
                              |
       actual-worker-Task-bound permit + typed dispatcher [F4]
          /          |           |          \
       learn      settle       anchor       card/probe effect
    (LLM+apply)  (internal)   (internal)       (internal)
          \          |           |          /
               idempotent local effects
                              |
                 receipt applied + projection
                              |
          typed completion result ──> exploration writer outside permit [F8]
```

### 5.1 Queue envelope

`DialogueJob` 是进程内 typed envelope，至少包含：

- `job_id`：仅用于日志/测试关联，不持久化；
- `kind`：固定白名单；`confusion.attribution.replay` 与
  `confusion.open.sync` 是独立 kind，不能 alias 到 `learn`、
  `confusion.reply.apply` 或 `anchor.establish`；**[F1][F3]**
- `payload`：提交时复制，worker 不读取调用方后续可变对象；
- `anchor_snapshot: AnchorAdmissionSnapshot`：提交时从队列级 registry 冻结，
  是 `persisted(kind, ref, generation)`、`reserved(kind, ref, reservation_id)`、
  `absent(target_kind, target_ref, tombstone_epoch)` 或 `not_applicable` 之一；不能用
  `("", 0)` 同时表示“没有锚”与“不涉锚”；**[F2]**
- `accepted_at` / 单调 `sequence`；
- 可选 `completion: asyncio.Future[DialogueJobResult]`：
  action/legacy 使用；`probe.reply.apply` 还通过 result 返回 worker 已计算的
  classification 与可选 exploration intent，队外不得再分类。**[F8]**

队列本身不写 `settlement_jobs` 表，不扫描 SQLite，不做 owner lock。
`shutdown`/热重载沿用当前 self-owned worker 的 pause/drain 语义；进程被强杀时未执行
job 可丢。

### 5.2 单 worker、实际 Task permit 与禁止递归入队

queue/guard 在 worker task 启动时登记**实际 `asyncio.Task` 对象**与本次 lifecycle
nonce，dispatch 前可用私有 `ContextVar` 携带 nonce，但
`require_dialogue_settlement_worker()` 必须同时验证
`asyncio.current_task() is registered_worker_task`。ContextVar 传播本身永远不能
授权 mutation；worker handler 内 `asyncio.create_task()` 出来的 child 即使继承
nonce，也必须抛 `DialogueSettlementMutationOutsideWorker`。worker 退出/热替换时
在 `finally` 注销 task + nonce，旧 task 不得复用许可。**[F4]**

如果 worker 内的 learn/anchor handler 再调用公开 `submit_and_wait()`，队列立即抛
`DialogueSettlementReentryError`；不得把子 job 放到自己身后再等待。普通 chat
settles 与锚 relation 必须调用 `_apply_*` 内部函数，共享当前 worker context。

异常只令当前 job 失败并完成其 Future；worker 记录结构化日志后继续消费下一项。
禁止持同步文件锁跨 `await`，禁止 worker 内 detached `create_task` mutation。

### 5.3 Typed dispatcher 与结果边界

- `confusion.attribution.replay` 有具名 handler；12h hook 只做 read-only candidate
  enumeration + submit。handler 先查现有 turn/replay/settlement receipt：已有终态
  返回同一 result；classification gap 才分析一次，然后以稳定
  `(confusion_id, turn_id, replay_id)` 应用。**[F1]**
- `confusion.open.sync` 一个 handler 接受白名单 operation
  `schedule|retarget|rollback`；三者都在 worker task 内调用 confusion façade/raw
  DAO，禁止 endpoint exception branch 直接恢复。**[F3]**
- `probe.reply.apply` 返回 `ProbeReplyApplyResult(classification, classifier,
  resulting_action, exploration_intent?)`。in-scope hypothesis/speculation、
  feedback history、visible cognition/event 在 worker 内完成；exploration intent
  只是数据，不在 dispatcher 内执行。**[F8]**

## 6. Admission anchor timeline 与 Generation 冻结 **[F2]**

只读持久化 anchor 不能定义 admission：`anchor.establish` 可能已经排在队列里却尚未
写文件。为此统一队列必须拥有一个**进程内、队列全局、单调推进**的
`AnchorAdmissionRegistry`。它不是第二个队列、不是 durable owner，也不跨进程。

1. queue 启动时从持久化 anchor 初始化 logical state。每次 submit 在同一个不含
   `await` 的 admission critical section 内完成：分配 sequence → 更新/读取 logical
   state → 深拷贝 snapshot → `put_nowait`；并发 producer 不能在 snapshot 与入队
   之间穿插。
2. `anchor.establish` admission 必须**先**创建 opaque `reservation_id`，把 logical
   state 设为 `reserved(kind, ref, reservation_id)`，再入队。后续 learn/settle 即使
   此时持久化层仍无锚，也冻结该 reservation，而不是错误冻结空 generation。
3. establish handler 成功后把 reservation 原地 resolve 为实际
   `(kind, ref, generation)`；失败/补偿则标为 failed。后续持 reservation 的 job
   只能使用 resolved generation；reservation failed 时返回
   `anchor_dependency_failed`，绝不能退回 current/no-anchor 推断。
4. logical state 真正无锚时创建
   `absent(target_kind, target_ref, tombstone_epoch)`。tombstone 是“本请求受理时无
   锚”的全局 admission 事实，不是空字符串。worker 只验证该 logical snapshot；
   若执行前出现一个不属于该 snapshot 的锚，则返回 `stale_anchor`，不把 `0`
   升级成未来 generation。
5. 对 `persisted`/已 resolve `reserved` snapshot，worker 在第一个业务 effect 前
   要求 active anchor 的 kind/ref/generation 精确相等；不匹配时不写 object、
   candidate、replay、ledger、profile、projection，也不 release 当前锚。
6. 删除 `SoulEngine.settle_hypothesis()` 及其他 handler 的所有 current-anchor
   推断。worker 可以 resolve envelope 已携带的 reservation，不能向 anchor manager
   询问“现在该归谁”后改写 snapshot。
7. 每个 worker-side anchor mutation 完成后，都把实际 `persisted/absent/failed`
   outcome 回报 registry。回报只 resolve 对应 sequence/reservation；若已有更晚的
   admission transition（例如 B 的 establish reservation），旧 sequence 的完成
   不得覆盖 logical head。这样 release 完成后的新 submit 能看到 absent，而较早
   establish 的完成也不能抹掉已登记的较晚预约。
8. reservation/tombstone 只保留到没有 queued/running envelope 引用；queue
   shutdown 后丢弃，重启从持久化 anchor 重建。它不恢复丢失 job，符合 §12。

必须有两个无 sleep 的确定性交错：

- `anchor.establish(A)` submit 后用 barrier 阻止 worker → `settle(A)` submit →
  放行 worker；settle envelope 在 admission 已引用 A 的 reservation，执行时使用
  establish 返回的 generation，不能冻结空锚、不能二次读取 current；
- `settle(A)` 先在真实无锚状态 submit → 后续 `anchor.establish(A)` submit；前者
  保持自己的 no-anchor tombstone，并按 FIFO 在新锚前执行，不能被后一次 admission
  反向升级。

每个交错循环 100 次并断言 snapshot kind/reservation/tombstone、对象 effect 与
anchor release 都符合受理时 timeline。单测还要覆盖 establish 失败：依赖 job
得到 `anchor_dependency_failed` 且业务 effect=0；以及 “A 完成后、B reservation
已登记” 时 A 的 completion 不覆盖 B logical head。

显式 await `anchor.establish` completion 仍可用于 durable confusion UI 的产品
时序（拿到 generation 后才生成 reply），但 correctness 不能依赖每个 producer 都
记得 await；admission registry 必须兜住“establish 已入队、后续 job 随即入队”的
合法顺序。

对 `learn` job，“受理”定义为 Socratic reply 完成后向统一队列提交学习/结算分析的
时刻；对 HTTP action/legacy/open，则定义为 handler 完成校验并进入上述 admission
critical section 的时刻。

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
`after_applied_before_projection` 故障点 → projection → anchor publication。
event、object/derived 与 rebuild marker 是置 applied 前的 mandatory business
effect；projection/anchor 只允许读取 applied receipt 后执行，并在每次 receipt
retry/reconcile 时幂等重放。**[F7]**

`applied=1` 是对象语义的 terminal boundary：此后任意 retry 或
`card.reconcile` 必须直接进入 publication-only 分支，不得再次调用对象/derived/
rebuild mutator。GET 仍然只读并 submit `card.reconcile`；测试在
`after_applied_before_projection` 注入退出后调用一次 GET，断言该 request direct
write=0；等待 queue drain 后第二次 GET 应看到所有 session 卡片终态，同时对象语义
调用计数保持 1。这里不靠 durable scanner 自动恢复。
**[F7]**

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
  classification、`confusion.attribution.replay` gap analysis、gate 与必要
  rebuild/profile build；生成用户可见 reply 的 interactive LLM 保持现有请求线，
  不塞进 settlement queue。**[F1][F8]**
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

### 8.3 Probe classification → exploration completion handoff **[F8]**

durable `scope=probe` 的 producer 只提交一次 `probe.reply.apply` 并 await shielded
completion。worker 内：

1. 以稳定 `turn_id` 查已有 feedback/result；没有才调用 classifier；
2. 完成队列拥有的 speculation/hypothesis、feedback history、visible cognition/event；
3. weak-positive 时只构造
   `ExplorationIntent(domain, source_event, specifics, evidence_id)`；
4. 返回包含 classification/classifier/resulting_action/intent 的 immutable result。

classification result 使用现有 durable chat-turn payload/effect receipt 以 `turn_id`
保存（不加新表）；duplicate `probe.reply.apply` 从该 result 重建 completion，
classifier 不再调用。`ExplorationIntent.evidence_id=turn_id`，使既有队外 buffer
helper 对重复 handoff 仍按原有 evidence 语义去重。

completion 恢复的是提交 job 的原 task，不是 worker 或 worker child。该 task 先断言
自己没有 worker permit，再把 intent 交给现有
`_record_exploration_buffer_event`/promotion 路径。队外函数只消费 result，不能再次
调用 `_classify_probe_sentiment`。若没有 intent，则 exploration 调用数为 0。

测试用 classifier spy + exploration spy 证明：一个 weak-positive turn 的 classifier
调用数=1，exploration mutation=1，mutation 时
`asyncio.current_task() is not worker_task` 且 guard 未授权；同一 completion result
重读不重复 classification。进程在 handoff 前退出仍受 §12“内存 job 可丢”限制，
本方案不为 exploration 新增 inbox/receipt。

## 9. HTTP 契约：保留 200，按需使用既有 202

不把所有 action 一刀切成异步协议。

1. action/legacy submit 后，用 `asyncio.shield(completion)` 有界等待 **1.0 秒**；
2. 空队列下本地 settlement 通常在窗口内完成，继续返回现有 `200` 与终态 body；
3. 若 worker 正处理长 LLM，card action 返回现有语义的 `202` processing body；
   不新增 job id/status schema，HTTP timeout 不取消已入队 job；
4. card 客户端用既有 `GET /api/chat/turns/{turn_id}` 轮询 payload.state，采用
   1/2/5 秒退避，但单次 action 的 processing poll 有 **30 秒 deadline**。deadline
   内远端仍为 `pending` 时保留本地 processing；到期则停止 timer，把本地 overlay
   转成 `retryable_error`（显示“处理未完成，可重试”）并重新启用原 action。durable
   turn 的 `payload.state` 仍是 `pending`，不得伪造失败/终态；**[F6]**
5. deprecated legacy 保留现有 `InsightFeedbackResponse` body、deprecation headers
   与 `202/ok=false` 表达；调用方可按同 hypothesis 重试，ref winner 收据确保
   读取/恢复同一 verdict，不为它增加 outcome/job 字段；
6. worker 失败、页面刷新或 backend 重启导致内存 job 丢失时，card 保持原 durable
   state；用户再次提交同一 action 会创建新 queue job：无 receipt 时正常受理，
   `applied=0` 时继续原 winner，`applied=1` 时只 reconcile publication。迟到的旧
   job 若其实仍存活，也只能命中同一 ref winner，不能产生第二对象语义；**[F6]**
7. `confirmed/rejected/deferred/discussing`、页面关闭或 abort 立即停止轮询。无需
   job status 表/端点，也不以延长 deadline 掩盖 HOL。

1.0 秒是本地 SQLite/JSON action 的首轮预算，不是 LLM 预算；实现须带校准注释并
记录 queue wait。若隔离测试 100 次 action 任一次超过 1.0 秒，应先修本地慢点，
不能靠扩大同步窗口掩盖。

30 秒 poll deadline 的首轮校准依据是：覆盖 1/2/5 秒退避的多个本地 publication
周期，同时避免 non-durable job 在 backend 重启后形成永久 spinner；它不是 provider
300 秒 timeout 的替代。上线后按 §8.2 的 202/p95 门复核，调整只能改客户端等待
体验，不能增加 durable job 范围。**[F6]**

## 10. Design invariants（可证伪）

| ID | MUST | 失败判据 |
| --- | --- | --- |
| Q1 | §2.2 的对话结算全部经同一 queue | 任一入口 spy 到 worker 外 protected mutation，包括 pending-open 原始 confusion sink **[F3]** |
| Q2 | 主进程内恰一个 active consumer | 100-job 测试出现 `max_active > 1` 或热重载出现新旧 worker 重叠 |
| Q3 | queue-global admission timeline 冻结 persisted/reserved/absent 状态 | 已入队 establish 对后续 job 不可见、worker 补抓 current、absent 升级为未来代次任一发生 **[F2]** |
| Q4 | worker 不等待自己 | worker 内 public submit 未立即报 reentry，或测试超过 100 ms 未结束 |
| Q5 | 不持同步 settlement lock 跨 await | 旧锁符号仍存在，或 heartbeat 在 LLM await 期间停止 |
| Q6 | ref receipt 仅由 worker 创建，且 winner payload 不变 | handler 预留 receipt、同 ref 两 verdict 都写对象，或 retry 使用 contender payload |
| Q7 | mandatory effect 可安全重做 | 七个 crash gap 任一最终计数不是 1；applied gap reconcile 重做对象语义 **[F7]** |
| Q8 | 只有 applied receipt 可投影终态 | `applied=0` 时任一卡片变 confirmed/rejected |
| Q9 | card 跨 session 投影一致 | 同 ref 两 session 终态不同 |
| Q10 | queue 明确非 durable | 新增 job/inbox scanner、owner lock 或宣称重启自动恢复 |
| Q11 | action 常态 200、拥塞才 202 | 空队列总是 202，或拥塞请求同步挂到 LLM 完成 |
| Q12 | 对话域旁路矛盾只可在已知限制场景复现 | 单 backend、仅 §2.2 入口仍能产生相反对象终态 |
| Q13 | 12h attribution 使用独立 typed kind | hook 直接分析/应用，dispatcher 无 `confusion.attribution.replay` 分支，或借用 learn/reply kind **[F1]** |
| Q14 | mutation permit 绑定实际 worker Task | worker child task 继承 context 后可调用 protected mutator **[F4]** |
| Q15 | 202 一定有有限页内退路 | 30 秒后仍永久 processing，或重启丢 job 后同 action 不能再提交 **[F6]** |
| Q16 | probe classification 与 exploration 写跨边界交接 | classifier 调用 >1、exploration 在 worker/child task 内 mutation，或 worker 直接 promotion **[F8]** |
| Q17 | CLI/OpenClaw 兼容模式显式且行为不变 | 任一被接进 queue/guard、变成只回复不学习，或新增第三个 legacy-direct callsite **[F5]** |

## 11. Wave

| Wave | 内容 | 交付门 |
| --- | --- | --- |
| Wave 0 | 冻结入口/raw sink/受保护 mutator 清单；把 deadlock、admission reservation、child-task permit、旁路与重试语义写成 RED tests | 清单覆盖 100%，F2/F3/F4 失败原因确定 |
| Wave 1 | 泛化 `DialogueLearnQueue` 为 typed 统一队列；queue-global anchor registry、Future、actual-Task permit、reentry/lifecycle；显式保留两处 legacy-direct 兼容 | 100 mixed jobs 单消费者；API 无隐式 detached fallback，CLI/OpenClaw 行为不变 **[F2][F4][F5]** |
| Wave 2 | 简化 SQLite receipt/effect 幂等；删除锁/lease/token/三段 executor；删除执行期 generation 推断 | 旧符号 0；七 crash gap 可重试，applied gap GET reconcile 通过 **[F7]** |
| Wave 3 | cards/pending-open/anchor/chat/probe/confusion/replay/legacy cutover；有限 202 轮询；护栏、旧测试改写、文档与全量门 | F1/F3/F6/F8/F9 与所有量化门全绿 |

Waves 0–1 可独立提交。Wave 2 的 schema/executor 删除与 Wave 3 的入口 cutover
属于同一个不可拆分的 runtime 迁移窗口：可按 Task 顺序开发，但不得发布只含
Wave 2 的中间状态；任何入口也不得同时 direct mutation 又 submit。

## 12. 已知限制

1. **不支持跨进程串行。** 两个 backend/多 Uvicorn worker 指向同一 data dir 时，
   各有自己的内存队列；SQLite winner 收据能限制 confirm/reject winner，但不能让
   JSON/anchor/profile 的全部副作用全局串行。支持部署仍是单 backend writer 进程。
2. **内存 job 可丢。** SIGKILL、机器掉电或强制 reload 可丢尚未执行/正在执行的
   queue item。已有 ref receipt 的 action 可由用户重试恢复；没有请求重试就不会
   自动完成。card UI 只承诺 30 秒后退出 processing 并允许重试，不承诺自动恢复。
   **[F6]**
3. **有界队头延迟仍存在。** 一个正在运行的 dialogue learn LLM job 不被抢占；
   后续 action 可能先收到 202，最终完成时间受 provider timeout 影响。
4. **跨任务极端并发不保证线性一致。** force_tick、avoidance、exploration、
   pipeline、OpenClaw、非对话 CLI writer 若被刻意安排在同一对象/文件的极端交错，
   本队列不提供全局 serializability；这不是重引入 owner/lease 的理由。
5. **legacy 只有幂等重试，没有 durable job polling。** 新 card UI 轮询 turn；
   deprecated insight endpoint 保持兼容 façade，不为它新增 job API。
6. **audit observer 可缺失。** audit ledger 写失败不回滚已完成业务对象；稳定
   effect key 只保证重试不重复，不把审计日志升级成强事务依赖。
7. **CLI/OpenClaw direct learning 仍是边界外 writer。** 两处显式
   `legacy_direct` 仅保兼容，不享受本队列的串行/receipt/worker guard 保证；本方案
   不把它们悄悄改成 queue client，也不宣称消除与 API 进程的极端交错。**[F5]**

## 13. 文档义务

本次 review revision 只修改本 spec 与 companion plan，不改变 runtime/API，因此
不改模块文档、changelog 或架构图。

实现 PR 必须按 `CLAUDE.md#documentation-requirements` 同步：

- `docs/modules/soul.md`：统一 queue、worker-only mutation、generation 冻结；
- `docs/modules/api.md`：action 的 200 fast path / 202 processing + turn polling；
- `docs/modules/storage.md`：简化 receipt schema、旧 lease 表迁移、effect 幂等；
- `docs/modules/runtime.md`：self-owned queue 的 start/pause/drain/shutdown；
- `docs/modules/llm.md`：对话 LLM 串行、provider timeout 与 HOL 指标；
- `docs/modules/extension.md`：仅在 processing 轮询行为有客户端改动时更新；
- `docs/modules/cli.md` 与 `docs/modules/integrations.md`：只记录 CLI/OpenClaw
  显式 `legacy_direct` 兼容 pin、行为不变且不受 queue 保证；**[F5]**
- `docs/changelog.md`：当前版本块增加一条；
- 数据流改变，必须同步 `docs/architecture.md`、`docs/spec.md` §3、
  `README.md` 与 `README_EN.md` 顶部架构图；
- CLI command/API 与 config 均不改变；仍因 `cli.py` compatibility-only 构造参数
  变化更新 `docs/modules/cli.md`，不扩写新命令。若实现超出本 spec，必须先修订
  spec。

这不是 release，不新增 README 版本 highlights。
