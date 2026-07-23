# 对话结算串行化重做 Spec —— 持久化单写者协调器

**Created:** 2026-07-23
**Status:** Proposed；替换已 REJECT 的「5 min lease + claim token + OS 文件锁 + 三段 CAS」方案
**Baseline:** `feat/cognitive-profile-pipeline` @ `0946ee2a`（行号均以此提交为准；后续漂移时以符号名为准）
**Plan:** [`2026-07-23-settlement-serialization-plan.md`](./2026-07-23-settlement-serialization-plan.md)

## 1. Goal

用一个由 SQLite durable inbox 驱动的**结算域单写者协调器**，替换当前分散在 API、`SoulEngine`、锚管理器、探针和 CLI 中的同步执行与并发补丁。目标不是再给现有临界区叠一层锁，而是从结构上消除：

- 同一 `data_dir` 同时存在多个结算 writer；
- 卡片、legacy、普通 chat settles、锚、探针、CLI 各自直接写业务状态；
- 一次结算跨 SQLite、JSON 状态、事件、派生假设、rebuild marker 时由多个执行者接力；
- HTTP 已返回成功但副作用尚未完成或只完成一半；
- 依赖 lease 到期、接管和 fencing 才能恢复崩溃中的执行。

这里的“唯一 writer”严格限定为**结算域拥有的业务状态**：假设/猜测兴趣/猜测避雷/疑惑的确认结算、对话锚及代次、卡片终态、探针反馈历史与由这些动作产生的 event/ledger/derived/rebuild 请求。推荐池、收藏同步、普通画像手工编辑本体等不在本 spec 中，不宣称整个应用只有一个 SQLite writer；但画像手工编辑若同步修改同一 speculation/avoidance 对象，该同步 effect 必须进入本 coordinator。

唯一允许入口线程执行的写操作是协调器的 **admission transaction**：原子写入 inbox、ref 收据预留、幂等键，以及卡片的 `processing + job_id` 接受态；它不得修改任何业务对象。除此之外，结算域 mutation 全部只能发生在 worker 上下文中。

### 1.1 量化完成门

| Gate | 必须达到的结果 |
| --- | --- |
| 单写者 | 同一 `data_dir` 启动 2 个 daemon/一次性 CLI worker，恰好 1 个取得 owner；失败方业务 mutation 次数为 0 |
| 并发仲裁 | 100 个并发请求、至少 4 类入口争用同一 ref：1 条唯一收据、1 个 winner job、每类副作用至多 1 次，全部请求在 5 s 测试上限内返回或收敛 |
| 接受语义 | 暂停 worker 后，所有新 mutation POST 均返回 `202`、`job_id`、`state=processing`；不得因 handler 快慢偶发返回 `200 applied` |
| 崩溃恢复 | 在 event/object/derived/ledger/rebuild/projection/anchor 每个边界注入崩溃并重启，最终均为 `applied` 或明示 `failed`，每个 effect 的 durable 计数恰为 1 |
| generation | 旧代 job 与之后建立的同 ref 新锚交错 100 次：旧代业务副作用始终为 0；payload 中 generation 从受理到终态字节不变 |
| 队头治理 | 阻塞分析器不影响 commit worker；单 job 最长 10 s、自动尝试最多 3 次，失败进入 durable `failed`，后续 ready job 可继续 |
| 护栏 | 受保护 mutator 从 worker 外调用 100% 抛 `SettlementMutationOutsideWorker`；AST/调用清单中允许目录外的直接调用数为 0 |
| 热重载 | worker 暂停且 inbox 非空时执行配置热重载，2 s 内返回；不调用 drain，重载后/重启后 job 仍可收敛 |
| 质量 | Python 全量测试通过、覆盖率不低于 70%；Ruff format/check、MyPy strict、扩展 npm test/typecheck/build 全绿 |

最终复现命令见本 spec §15 和 companion plan 的 Wave 5；所有 Python 命令必须使用仓库 `.venv` 与 `PYTHONPATH=$PWD/src`。

## 2. 决策摘要

1. 新增稳定生命周期组件 `SettlementCoordinator`；一个 `data_dir` 同时只有一个 owner。
2. SQLite `settlement_jobs` 是工作真相；`asyncio.Event` 只负责唤醒，不携带 job，也不决定是否还有工作。
3. worker 严格单消费者；只做本地、确定性、可重放的状态提交，不调用 LLM、不做网络 I/O、不启动 detached task。
4. LLM 对话学习和探针对话分类在 worker 外完成；结果冻结后再提交 `dialogue.analysis.apply` / `probe.*.apply` job。
5. `ref` 级 SQLite 唯一收据继续负责 winner 仲裁；删掉 lease、claim token、settlement OS 锁和三段 CAS。
6. 锚 generation 在请求/turn 受理时持久化，worker 只校验，不推断、不补抓当前锚、不升级到未来代次。
7. 新接受的 action 永远是异步契约：`202 accepted → poll → applied|failed`；只有已经落到终态的 ref 才同步返回 `200 already_settled`。
8. 热重载不替换 coordinator、不要求 drain；进程退出可直接取消 worker，SQLite 在下一次 owner 启动时恢复 `running` job。
9. Waves 0–3 可以独立开发验证但不得作为部分 cutover 发布；后端与 bundled clients 在 Wave 4 一起切换，禁止新旧 writer 双跑。

## 3. 术语与边界

- **producer / entry**：API handler、对话分析器、探针分类器、CLI 等，只能构造请求并调用 `submit()`。
- **admission transaction**：协调器在调用方任务上执行的短 SQLite 事务，只保存 job/唯一收据/接受态投影。
- **owner**：持有 `<data_dir>/.settlement-writer.lock` 的进程。owner lock 只做单实例生命周期仲裁，不参与某个 job 的 fencing。
- **worker**：owner 内唯一消费 `settlement_jobs` 的 task。
- **applier**：worker 内按 action 分派到 `_apply_*` 的确定性执行体。
- **effect**：一次 job 的 event、对象状态、派生假设、ledger、rebuild 请求、卡片投影或锚 mutation。
- **receipt**：ref 级 winner 记录；重试只重放 winner payload，不接受竞争请求的 payload。
- **analysis**：LLM/分类等慢、可能远程的计算；它生成不可变结果，但不修改结算域状态。

## 4. 现状诊断（D1–D9）

以下不是待继续修补的局部 bug，而是旧并发模型必须退役的证据。

### D1 — P0：OS 文件锁跨 `await`，事件循环可自锁

- `storage/database.py:90-102` 建立进程内 `_CARD_SETTLEMENT_LOCKS`；`:1325-1327` 为每库建立 settlement lock path。
- `storage/database.py:2526-2782` 实现 5 min claim、文件锁 fence、claim guard、三段标志及 publish CAS。
- `soul/engine.py:1184-1460` 在请求协程里运行完整 executor；`:1373-1401` 持 `card_settlement_claim_guard` 时 `await _apply_dialogue_settlement_object()`，`:1406-1439` 持同一 guard 时 `await mark_feedback_rebuild()`。

文件锁在事件循环线程取得，协程让出后另一个 task 可在同线程阻塞等待同一 OS lock；持锁 task 因事件循环被堵无法恢复释放。进程内 `RLock` 可重入不能使 OS `flock`/Windows byte lock 具备 asyncio 可重入语义。结论：删除这一整段并发栈，不再调整锁顺序。

### D2 — P0：generation 校验与业务提交之间仍有窗口

- `soul/engine.py:1767-1809` 在 LLM 后重读 generation。
- `soul/engine.py:2872-3037` 先 `note_relation(expected_generation=...)`，后续再调用 hypothesis/confusion settlement。
- 业务提交进入 `soul/engine.py:1184-1460` 后并没有把“generation 仍有效”与对象 mutation 放进同一个串行序列。

新锚可在 post-LLM 校验/`note_relation` 后、对象 effect 前被 release/re-establish。CAS 只能保护某一次锚文件写，不能跨 SQLite receipt、JSON 对象、derived、rebuild 和 projection 构成原子提交。新设计让锚 mutation 和 settlement effect 由同一 worker 按 durable sequence 执行；worker 开始应用时再次校验 frozen generation，之后不存在第二 writer 插入窗口。

### D3 — P0：执行时补抓 generation 会捕获“未来锚”

- `soul/engine.py:1089-1097` 在 `anchor_generation <= 0` 时读取执行当下的 current anchor，并把它升级为本次 settlement generation。

这会把一个在无锚状态下受理的旧请求错误归到之后才建立的同 ref 锚。必须删除该分支。generation 只能来自 admission 时持久化的快照；`0` 永远表示受理时无锚，worker 不得推断。

### D4 — P1：API 卡片/锚/探针仍有大量 writer 旁路

需要迁移到 `coordinator.submit_*` 的当前入口：

| 位置 | 当前直接 mutation | 目标 action |
| --- | --- | --- |
| `api/app.py:2324-2352` | confirmation cooldown JSON | `card.defer` / `confirmation.throw.mark` |
| `api/app.py:2632-2663` | GET/read-repair 时投影 settlement、修复 discussion | 显式 `card.reconcile` job；GET 保持纯读 |
| `api/app.py:2743-2906` | confusion claim/retarget、card/question 创建、anchor establish | `confirmation.open` |
| `api/app.py:2909-2958` | 系统卡附着与 cooldown claim | `confirmation.attach` |
| `api/app.py:7641-7674` | durable confusion turn 直接 schedule + 建锚 | `anchor.establish.confusion` |
| `api/app.py:7716-7868` | probe dialogue 直接改 speculator/avoidance/history/cognition | `probe.dialogue.apply` |
| `api/app.py:7901-8013` | 卡片 confirm/reject/defer/discuss 直接结算/建解锚 | `card.*` |
| `api/app.py:8051-8144` | turn/card/open handlers 直接触发上述函数 | 只做 admission + 返回 job envelope |
| `api/app.py:8216-8508` | interest probe button/chat 直接 mutation | `probe.interest.apply` |
| `api/app.py:8552-8805` | avoidance probe button/chat 直接 mutation/后台 writeback | `probe.avoidance.apply`；慢分析在队外 |
| `api/app.py:9038-9092` | legacy insight endpoint 直接 await executor | façade 入队并有界等待 |
| `soul/engine.py:875-906` | profile edit 直接同步 confirm/reject speculator | 当前 worker 内 nested apply，或提交 canonical probe/object action |
| `integrations/openclaw/operations.py:525-576` | OpenClaw avoidance feedback 直接 mutation + writeback | 注入 coordinator，复用 `probe.avoidance.apply` |

`asyncio.create_task()` 不是收口：例如 avoidance dislike writeback 仍是第二 writer，且进程退出时没有 durable ownership。

### D5 — P1：engine 内部 settle 与普通 chat settles 会绕过队列或递归排队

- `soul/engine.py:947-1044` 的 compatibility feedback facade 仍能直接写 event/object/rebuild。
- `soul/engine.py:1811-1820` 直接调用 `_process_dialogue_settles()`。
- `soul/engine.py:2669-2803` 的 confusion recovery 同时做 expire、建解锚、重放、LLM 与 settle。
- `soul/engine.py:2872-3037` 的锚处理内部再次调用公开 `settle_*`。
- `soul/engine.py:3239-3310` 普通 chat 对 speculation/insight/confusion 逐项调用公开 settlement。
- `soul/cognition_cycle.py:267-289` 在 12 h cycle 直接 expire confusion并 await上述 replay/rebuild hook。

若只是把公开 `settle_*` 改成“入队并等待”，worker 处理 `dialogue.analysis.apply` 时会把子 settle 排回自己并等待，形成递归死锁。worker 内所有嵌套动作必须调用 `_apply_hypothesis/_apply_confusion/_apply_speculation`，共享当前 `ApplyContext`，不得 submit 或 await 自己的 job。

### D6 — P1：CLI 与非 HTTP integration 仍可成为独立 writer

- `cli.py:11708-11808` 自建 `SoulEngine`，直接调用 `user_reject_speculation()` / `user_confirm_speculation()`，confirm 后还内联 `force_tick()`。
- `integrations/openclaw/operations.py:525-576` 直接确认/拒绝 avoidance，并可能继续执行 dislike writeback。

daemon 运行时这些路径与 API 共写同一 data dir；daemon 不运行时也没有统一 crash receipt。CLI 必须优先经本地 API 提交；只有确认服务不可达且成功取得 data-dir owner lock 时，才可启动一次性 coordinator。输掉锁时不得 fallback 到直写。OpenClaw 运行时必须注入同一 coordinator；缺少 owner/coordinator 时明确失败，不得为了兼容退回 direct mutation。

### D7 — P2：derived 是幂等 upsert，但 ledger append 不是

- `soul/engine.py:1462-1479` 把 derived persistence 放在 object segment 内。
- `soul/engine.py:3039-3085` upsert 派生假设后逐条 append `anchor_revise_derived` ledger。
- object effect 与 `storage/database.py:2664-2686` 的 `seg_object` 标志不是同一存储事务。

若进程在 derived/ledger 已写、`seg_object` 尚未标记时崩溃，接管者重跑 object segment：假设文本可能仍是 upsert，但 ledger 会重复。新设计给每个 derived effect 稳定 `effect_key=job_id:derived:<content_hash>`，对象 upsert和 ledger 都按该 key 去重；generic settlement ledger 不再重复描述同一个派生写入。

### D8 — 内存 learn queue 把正确性绑定到 drain

- `soul/dialogue_learn_queue.py:35-181` 用 `asyncio.Queue` 保存唯一待处理副本，且 handler 同时包含 LLM 与 mutation。
- `soul/dialogue.py:194-226` 在 queue 不存在时退回 detached task。
- `api/runtime_context.py:539-581` 热重载必须 pause-and-drain，失败则整个 reload abort。
- `api/runtime_context.py:1287-1306,1345-1347` 每次重载创建/替换 learn queue。
- `api/app.py:5034-5040` shutdown 也依赖 30 s drain。

durable inbox 后，内存只作 wakeup。热重载无需证明旧内存队列已空；`running` job 在 owner 重启时恢复。

### D9 — 三端把 HTTP 返回当作业务已完成

- 共享卡片 helper `web/shared/dialogue-confirmation.js:82-127` 先乐观写终态，POST 返回后立即视为 settled。
- popup `extension/popup/popup.js:6702-6729`、桌面 `web/desktop/assets/js/app.js:5744-5771` 在一次 POST 后展示成功。
- 移动端 probe `web/js/views/chat.js:393-430`、`web/js/views/profile.js:424-450` 在 POST 后删除卡片；`web/js/api.js:296-321` 没有 job polling。
- popup 的 probe 路径 `extension/popup/popup.js:2610-2650,2722-2765,3661-3695` 同样即时提交成功 UI。

新契约必须让 processing 可跨刷新恢复，网络超时不能伪装为 failed，只有 job terminal 才能改变业务终态。

## 5. Design invariants（可证伪）

| ID | MUST | 可证伪条件 / 测试证明 |
| --- | --- | --- |
| I1 | 同一 data dir 的结算域恰有一个 worker owner | 两进程都进入 `_apply_*` 即失败；多进程测试断言 owner 数=1 |
| I2 | 入口只可 admission，不可直接 mutation | 任一 handler/CLI/analyzer 调受保护 mutator 未抛错即失败；AST allowlist 外调用数必须为 0 |
| I3 | 卡片、legacy、建锚/解锚、普通 chat settles、探针 API、探针对话、CLI、OpenClaw 及 manual-edit 同对象同步 effect 全收口 | entry inventory 少一项或 endpoint/integration spy 未观察到 job_id 即失败 |
| I4 | generation 在请求/turn 受理时固定 | job payload generation 与 admission snapshot 不同，或 worker 把 0 替换成 current generation，即失败；删除 `engine.py:1089` 推断分支 |
| I5 | SQLite inbox 是工作真相 | 清空进程内 event/重启后 job 丢失即失败；memory queue 不得存 payload |
| I6 | worker 嵌套 settle 调 `_apply_*` | worker job 内调用 `submit()`、等待自己的 job 或新增队列深度即失败 |
| I7 | worker 不做 LLM/网络 I/O | applier 内调用 LLM registry、HTTP client、`force_tick`、`asyncio.create_task` 即失败 |
| I8 | 队头不能无限阻塞 | job >10 s 未 timeout、>3 次自动执行、failed job 阻止后续 ready job 即失败 |
| I9 | ref 唯一收据保留 winner payload | 同 ref 产生两个 winner、重试采用 contender payload、已应用 ref 再次写 effect 即失败 |
| I10 | CLI 遵守 data-dir owner | daemon 可达时 CLI 自建 engine，或 API 不达且未取 owner lock仍写状态，即失败 |
| I11 | 每个 effect crash-replay 幂等 | 任一注入边界恢复后 event/derived/ledger/rebuild/projection/anchor 计数不等于 1 即失败 |
| I12 | 热重载不以 drain 为前置条件 | inbox 非空使 reload abort/等待队列清空即失败 |
| I13 | HTTP 契约不谎报完成 | 新 job 返回 200/applied，或 202 没有 job_id，或 failed 卡片未持久化 error/job_id，即失败 |
| I14 | GET 与 status 查询是纯读 | 读取 card/job 时触发 projection、repair、锚 release 或业务 mutation 即失败 |

## 6. 目标架构

```text
popup / desktop / mobile / legacy / CLI / chat analyzer / probe analyzer
                              |
                              | validate + freeze payload/generation
                              v
                  SettlementCoordinator.submit()
                  [short admission transaction]
                     | inbox + ref receipt +
                     | processing/job_id projection
                     v
             SQLite settlement_jobs (source of truth)
                     |
            asyncio.Event only wakes scanner
                     v
            one owner / one commit worker
                     |
           SettlementApplier._apply_*()
          /       |        |       |       \
      event    object   derived  rebuild   anchor/card
       once     once      once    outbox    projection
                     |
                     v
        job applied/failed + durable result
                     |
          GET /api/settlement-jobs/{job_id}
```

### 6.1 生命周期

- coordinator 属于 `RuntimeContext` 的稳定、不可热替换组件；配置热重载只更新它读取的 runtime dependency provider。
- startup 非阻塞尝试 data-dir owner lock。daemon 取锁失败必须拒绝启动 writer，并给出明确 owner conflict；不得静默降级成第二 writer。
- owner lock 由生命周期对象持有至进程退出，竞争者只做非阻塞 acquire。它不在 request/job 临界区内反复申请，不让事件循环线程阻塞等待，也不承担 effect fencing。
- shutdown 取消 worker、释放 owner lock即可，不 drain。若取消发生在 effect 中，job 保持 `running`，下一 owner 恢复。

## 7. Durable data model

字段名是实现契约；迁移可以用 table rebuild，但最终语义不得保留 lease/claim。

### 7.1 `settlement_jobs`

| Column | Contract |
| --- | --- |
| `seq INTEGER PRIMARY KEY AUTOINCREMENT` | durable 接受顺序 |
| `job_id TEXT UNIQUE NOT NULL` | 对外稳定 ID（UUID） |
| `idempotency_key TEXT UNIQUE NOT NULL` | 同一入口重试返回同一 job |
| `action TEXT NOT NULL` | 白名单 action |
| `ref TEXT NOT NULL DEFAULT ''` | 无 ref action 可为空 |
| `state TEXT NOT NULL` | 仅 `pending/running/applied/failed` |
| `payload TEXT NOT NULL` | deterministic JSON；UTF-8 最大 256 KiB |
| `result TEXT NOT NULL DEFAULT '{}'` | terminal deterministic JSON；UTF-8 最大 64 KiB |
| `attempts INTEGER NOT NULL DEFAULT 0` | 每次 pending→running 加一 |
| `max_attempts INTEGER NOT NULL DEFAULT 3` | 自动尝试硬上限 3 |
| `available_at/accepted_at/started_at/finished_at` | UTC timestamps |
| `last_error_code/last_error_message` | 对外 safe error；不存 traceback/secret |
| `manual_retry_count INTEGER NOT NULL DEFAULT 0` | 用户显式 retry 的审计计数 |

索引至少包含 `(state, available_at, seq)` 和 `(ref, state)`。JSON 一律 `ensure_ascii=False, sort_keys=True`。

### 7.2 ref 唯一收据

保留现有 `card_settlements.ref PRIMARY KEY` 的业务契约，但迁移为简单 winner receipt：

- `ref` 改存 canonical receipt ref，并新增/保留 `object_kind/object_ref/verdict/turn_id/payload/created_at`；public raw ref不能直接当主键查询；
- 新增/使用 `job_id`（普通索引，**不可 UNIQUE**：一个 dialogue parent job 可以预留多个 ref）、`state=processing|applied|failed`、`result`、`updated_at/applied_at`；
- 删除语义上的 `apply_claim_at/apply_claim_token/seg_event/seg_object/seg_marker`；
- admission 在同一事务内 reserve receipt 与插入 winner job。冲突时不创建 loser job，而是返回已有 winner 的 job/result；
- `applied` receipt 永不被相反 action 覆盖；`failed` receipt 只能显式重试其**原冻结 payload**，不能让 contender 借重试改 verdict。

收据不是“payload 里只要有 ref 就永久占坑”。action model 必须显式声明 receipt policy：

| Policy | Actions | Receipt ref |
| --- | --- | --- |
| `terminal_once` | hypothesis/card confirm、reject、revise；confusion answer/terminal settle；普通 chat 中同类 settle | namespaced `hypothesis:<identity>` / `confusion:<id>:<generation>` |
| `version_once` | speculation/avoidance 当前 probe version 的 confirm/reject/defer | `speculation:<object_version>` / `avoidance:<object_version>`；新一代对象得到新 version |
| `job_only` | hypothesis card defer/discuss、confirmation open/attach、anchor establish/release/relation、reconcile | 不写永久 ref receipt，只依赖 job idempotency/effect keys |

`receipt_ref` 与对外 object `ref` 分开保存并带 kind namespace，避免 hypothesis hash、confusion id、speculation domain 偶然碰撞。speculation/avoidance state新增持久 `object_version`；新对象/复燃为新一代时生成，legacy 行按 `kind + normalized_domain + created_at` 确定性派生一次后落盘，之后不得随字段编辑重算。旧 hypothesis receipt 迁移为 `hypothesis:<old_ref>`。hypothesis defer 不得让未来 confirm 永久返回 already_settled；probe defer只终结当前 object version，不封死未来再生成的一代。

旧库迁移：每条旧 receipt用 canonical receipt ref派生稳定 migration job/idempotency key；`applied=1` 合成 terminal applied job + receipt，`applied=0` 用已持久化 winner payload 合成 pending recovery job，忽略旧 claim 时间/token。已经 applied但缺 kind 的老行保留成 `legacy:<raw_ref>` terminal receipt并保留 raw object ref，兼容查询把它视为“该 raw ref 已结算”，绝不重执行；unapplied 且 payload坏/缺 kind才合成 failed job，不猜 action。迁移必须幂等，且不得执行 contender 请求。

### 7.3 `settlement_effects` 与 rebuild outbox

`settlement_effects(effect_key PRIMARY KEY, job_id, effect_kind, target_ref, applied_at, metadata)` 是 effect receipt。仅写一行还不足以解决跨存储 crash gap，因此各 effect 还必须符合 §10 的动作级幂等规则。

rebuild 不再由 job 直接“碰一下 JSON marker 后认为完成”；写入 `rebuild_requests(effect_key UNIQUE, trigger_refs, state)` durable outbox。现有 rebuild consumer 合并 trigger refs 并确认 request，重复投递不重复增加逻辑请求。

### 7.4 锚状态

把 `dialogue_anchor_state.json` 的 scheduling truth 迁入 SQLite 单行 `dialogue_anchor_state`（`id=1`、全局 generation、kind/ref/origin_turn_id/counters/established_at/last_job_id）。原因：admission 必须在同一个 SQLite transaction 中读取 generation 并写 job，才能定义“请求受理时”的精确顺序。

首次升级仅在 SQLite anchor row 尚未初始化时导入规范化 JSON；若两者同时有值，SQLite 胜并记录 warning，绝不合并/递增两次。导入 commit 后把 JSON保留为只读迁移备份一个版本，但不得继续成为 writer。generation 单调递增，不回收。

### 7.5 卡片 payload

卡片 payload 至少持久化：

```json
{
  "state": "processing",
  "job_id": "job-...",
  "requested_action": "confirm",
  "last_stable_state": "pending",
  "error_code": ""
}
```

状态图：

```text
pending/discussing --admission--> processing
processing --job applied--> confirmed/rejected/deferred/discussing
processing --job failed--> failed
failed --explicit retry(same frozen job)--> processing
```

跨 session 的同 ref 卡片由 worker 在 applied receipt 后统一投影。GET 只读 payload/receipt，不做 read-repair mutation。

## 8. Admission、顺序与 generation

### 8.1 原子 admission

每次 `submit()` 在一个短 `BEGIN IMMEDIATE` 中：

1. 校验 action/payload/大小/idempotency key；不调用业务 mutator。
2. 读取 SQLite anchor row，写入冻结的 `{anchor_ref, anchor_generation}`。调用方显式传入的快照必须与其 durable turn 一致；不得 fallback 到 current。
3. 仅对 `terminal_once/version_once` action 尝试 reserve canonical `receipt_ref`；`job_only` 不占永久 ref。`dialogue.analysis.apply` 等含多个 frozen settles 的 parent 必须在本 admission 事务按规范化 receipt ref 排序并**预留全部 nested ref**，把每个 ref 的 winner/conflict写回 payload。不得等 worker 执行 parent 时才第一次仲裁，否则更晚的 card admission 会抢先成为 winner。若单 ref 已存在，按 §11 返回已有 winner；parent 则只跳过该冲突 ref，继续处理自己已预留的 ref及非 settle effects。
4. 插入 `settlement_jobs(pending)`。
5. 若来自卡片，同事务 CAS payload 为 `processing + job_id`；失败则整个事务回滚。
6. commit 后 `wakeup.set()`。丢失 wakeup 无损，因为 worker 每次被唤醒/周期 tick 都扫描 SQLite。

admission 不等待 worker，所以不会因为 object/rebuild I/O 延长 HTTP handler。

### 8.2 顺序

- worker 每次原子 claim 最小 `seq` 的 ready pending job，只有一个 running apply task。
- 默认无优先级，避免锚/卡片动作被重排；`seq` 是唯一业务顺序。
- transient 失败的 job 按 1 s、5 s backoff 重新变为 pending；等待 backoff 的 job 不阻止更晚但 ready 的 job。
- 同 ref 后续 job 由 receipt 指向同一 winner；依赖锚 generation 的后续 job因 frozen snapshot 不匹配会成为无副作用的 applied/stale result，而不是偷用未来状态。
- 每 job apply wall timeout 10 s；permanent validation error 直接 `failed` 且不重试；合法但过时代次是成功的 no-op，记 `applied` + `outcome=stale`；transient 最多 3 次，之后 `failed`（即 dead-letter）。

### 8.3 慢分析在队列外

- `learn_from_dialogue` 拆成 read-only `analyze_dialogue_learning()` 与 worker-only `_apply_dialogue_analysis()`。
- completed `chat_turns` 是 durable analysis source：turn 受理时保存 anchor snapshot与 `analysis_state=pending`；分析器使用 `pending/running/submitted/failed` 小状态机，重启把遗留 running恢复 pending，扫描“completed 且 analysis 未产出 job”的 turn，生成冻结结果后以 `analysis:<turn_id>:<version>` 幂等键 submit并写 `analysis_job_id`。
- 同一 dialogue history 的 analyzer 按 durable turn/display sequence 串行取数，避免后一句 relation 先于前一句提交；单次分析最长 120 s、自动最多 2 次，耗尽后标 analysis failed并放行下一 turn。这个慢 lane不持有 commit worker，也不阻止 card/probe/legacy job提交与应用。
- probe chat 的 reply/sentiment classification 同理；分类完成前不进入 commit inbox。
- `force_tick`、avoidance topic expansion 等 LLM 工作只可在 job applied 后由独立 durable analysis/scheduler 触发；它们不能是 settlement job effect，也不能在 worker 中 detached 执行。

## 9. Worker 与 mutation 护栏

### 9.1 结构

- `SettlementCoordinator`：owner、submit、wake、scan、recovery、wait/query。
- `SettlementApplier`：`apply(job, ApplyContext)` 和 action 白名单。
- `_apply_hypothesis/_apply_confusion/_apply_speculation/_apply_anchor_*`：只接受 frozen payload 与当前 context。
- 公开入口没有 `settle_*` 的“既可提交又可执行”双重语义；public method 只 submit，内部 method 只 apply。

`dialogue.analysis.apply` 中的 `settles[]` 必须直接调用上述 `_apply_*`，不得创建子 job。一个 parent job 的 result 列出每个 nested ref 的 `applied/already_settled/stale`；worker只消费 admission 已冻结的 reservation map并读取每个 ref receipt，不在执行时首次 reserve。重放 parent 不重复 effect。

### 9.2 “worker 外 mutation 直接失败”护栏

生产护栏而非只靠 review：

1. worker 进入 apply 时建立私有 `SettlementWriterPermit(coordinator_id, job_id, owner_task_id, thread_id)`。
2. 所有结算域低层 mutator 加 `require_settlement_writer(permit)` 或 `@settlement_mutation`；只检查 ContextVar 不够，必须同时验证当前 asyncio task 与 thread，防止 ContextVar 被 child task 继承。
3. worker 禁止 `create_task`；mutation 不得 `to_thread`。migration/recovery 也由 worker 用明确的 system job 执行。
4. endpoint/analyzer/CLI 的唯一允许写 API 是 `coordinator.submit()`；DAO admission 方法不暴露业务 mutation。
5. `tests/test_settlement_mutation_guard.py` 做三层检查：受保护方法逐个直接调用均抛错；endpoint spy 观察 permit；AST/符号 inventory 只允许 `settlement_applier.py` 与 migration helper 调受保护 mutator。

## 10. Crash recovery 与 effect 幂等

startup 在取得 owner 后把所有 `running` job 改回 `pending`，保留 attempts/last error 并立即扫描；不判断“lease 是否过期”。每个 effect 使用稳定 key：

| Effect | Stable key | Crash-safe 规则 |
| --- | --- | --- |
| feedback/event | `<job_id>:event:<type>` | effect receipt 与 event INSERT 同一 SQLite transaction，`INSERT OR IGNORE` |
| hypothesis/confusion terminal object | `<job_id>:object:<ref>` | 设置目标状态而非增量；对象保存 `last_settlement_effect` 或以 terminal/ref receipt 判重，重复 apply 是 no-op |
| speculation/avoidance counters | `<job_id>:object:<object_version>` | payload 必带对象 version；JSON 原子写同时记录 last effect，禁止无 receipt 的 `count += 1` |
| derived hypothesis | `<job_id>:derived:<normalized_hash>` | 按 normalized hash upsert；derived ledger 同 effect key unique，重放不 append 第二条 |
| profile ledger | `<job_id>:ledger:<semantic_write>` | SQLite unique effect key；parent summary 与 child derived 各有明确语义，不重复记同一变化 |
| rebuild | `<job_id>:rebuild` | `rebuild_requests.effect_key UNIQUE`；consumer 合并 refs、确认 outbox |
| card projection | `<job_id>:card:<turn_id>` | 仅当 payload.job_id 等于本 job 时 CAS processing/failed→terminal；跨 session 枚举稳定 |
| anchor | `<job_id>:anchor:<generation>` | anchor row 与 job/effect receipt 尽量同 SQLite transaction；expected generation 不符返回 stale、零业务 effect |
| probe history/cognition | `<job_id>:probe-history` / `:cognition` | history item 带 effect key 并去重；禁止匿名 append |

“先写 effect receipt 再写外部文件”会丢 effect，“先写文件再写 receipt”会重放；因此非 SQLite effect 必须本身可凭 stable key 幂等，不能把 `settlement_effects` 当成跨存储事务魔法。

每个 action 把 deterministic `effect_plan` 固定在 payload version中；重试读取 effect receipts并只补未完成项，不再用三列 `seg_*` 和 claim CAS。跨存储没有假装成一次原子事务：job 达到 `failed` 时可能已有部分 effect，status result必须列出 `completed_effects/pending_effects`，ref receipt仍锁定原 winner，卡片显示“未完整结算、可重试”，不得回滚成 pending或接受相反 verdict。显式 retry沿同一 plan续做；不做不可靠的补偿回滚。

## 11. 异步 HTTP / CLI 契约

### 11.1 新接受与已有终态

适用于 card action、confirmation open/attach、interest/avoidance probe button、以及能直接产生 mutation 的内部 façade。

新接受（即使 worker 在 response 发出前已跑完，也固定返回 202）：

```http
HTTP/1.1 202 Accepted
Location: /api/settlement-jobs/job-123
Retry-After: 1

{
  "ok": true,
  "outcome": "accepted",
  "job_id": "job-123",
  "state": "processing",
  "poll_after_ms": 250
}
```

同 ref 已有 processing winner：返回 `202`，指向 **winner job_id**，不创建 contender job。已应用：

```json
{
  "ok": true,
  "outcome": "already_settled",
  "job_id": "job-winner",
  "state": "confirmed",
  "result": {}
}
```

HTTP 为 `200`。不得返回 `200 applied`。

### 11.2 统一状态端点

`GET /api/settlement-jobs/{job_id}`：

- internal `pending/running` 对外均为 `state=processing`，另给 `phase=pending|running`；
- terminal 为 `state=applied|failed`；
- applied result 包含 domain `outcome/verdict/card_state/turn`；
- failed 包含稳定 `error_code`、安全 message、`retryable=false|true`，不含 traceback；
- 未授权/另一 data dir 的 job 按现有 API auth 规则不可见。

显式 `POST /api/settlement-jobs/{job_id}/retry` 只允许 failed winner 原 payload，返回 202；同一事务把 job 改为 pending、`attempts=0`、`manual_retry_count += 1`、card failed→processing，并清 safe error。它不会改 payload hash/verdict，也不会绕过 ref receipt。自动重试每轮上限仍为 3，手工 retry 另记审计。

### 11.3 卡片

- admission 同事务把所有可定位 card payload 写为 `processing/job_id`；刷新后客户端看到该状态继续 poll。
- worker applied 后投影最终 state；failed 后投影 `failed/job_id/error_code`。
- 网络断开只代表 poll 未完成，客户端不得回滚为 pending；重新 hydrate 后按 job_id 续查。
- discuss/open 必须等 job applied 后才聚焦输入框，因为锚此前尚未建立。

### 11.4 legacy `/api/insights/feedback`

端点保留 `Deprecation`/`Link` header，内部只入队。为旧客户端做最长 **1.0 s** 的有界 wait：

- 1 s 内 applied：`200`，返回旧 `InsightFeedbackResponse` 字段并附可选 `job_id/outcome=already_settled`；
- 未终态：`202` job envelope；
- failed：返回稳定失败响应，不回退到直接 `update_from_feedback()`。

此 façade 是兼容等待，不是第二执行器。

### 11.5 三端与 CLI

popup、桌面、移动端对各自支持的**所有**结算入口统一状态机；移动端不得只迁 probe 而给 hypothesis card/open 保留同步特例：

```text
POST accepted
  -> persist/render processing
  -> poll 250 ms, 500 ms, 1 s, then max 2 s
  -> applied: render authoritative terminal
  -> failed: render durable error + explicit retry
  -> network/offline: keep processing, resume after reconnect/reload
```

UI 单次前台 poll 30 s 后可停止主动轮询，但不能改业务状态；下一次 hydrate/stream 事件继续。probe card 只有 applied 后才能从 inbox/profile 删除。

CLI `probe`：

1. 优先 POST 配置端口的 loopback API，打印 job id并 poll；applied exit 0，failed exit 1，Ctrl-C 只停止等待、不取消 durable job。
2. API 明确不可达时尝试 data-dir owner lock；成功才启动一次性 coordinator，submit 并跑到 terminal；失败则报告“已有 owner 但 API 不可达”，exit 1。
3. CLI 不再 `_build_soul_engine()` 后直接 mutation；`force_tick` 不在 commit worker 中执行。

OpenClaw adapter 复用同一 submit/query 语义；为了维持其调用形状可做有界 wait，但超时必须返回 processing/job identity 或明确 adapter error，不能执行同步 fallback。

普通 durable chat 的 reply polling 契约保持；其学习 mutation 由 analyzer 生成 settlement job。probe chat/card 只有关联 job terminal 后才宣告业务反馈完成。

## 12. Wave 0–5

| Wave | 独立产物 | 独立验证门 |
| --- | --- | --- |
| Wave 0 — 基线与红测 | 5 个问题的确定性 repro、入口/mutator inventory、冻结 API schema | 5 个 strict xfail 全部 XFAIL 且无 XPASS；inventory 无遗漏 |
| Wave 1 — durable 基座 | inbox/receipt/effect schema、owner、worker、recovery、status DAO、writer permit | 100 no-op jobs 顺序完成；10 个 running 重启恢复；两进程恰一 owner |
| Wave 2 — 核心 apply | hypothesis/confusion/speculation/anchor `_apply_*`、effect 幂等、删除旧 lease/lock/CAS executor | 7 类 crash 点逐一恢复且 effect=1；旧 claim/guard 符号 `rg` 为 0 |
| Wave 3 — 全入口收口 | cards/legacy/open/attach/chat settles/probe API/probe dialogue/CLI/OpenClaw/manual sync effect 全 submit；LLM 与 commit 拆分；护栏启用 | allowlist 外 mutation=0；阻塞 LLM 时 commit sentinel <500 ms；generation 100 次交错零错写 |
| Wave 4 — 异步客户端 | 202/job endpoint/legacy wait；popup/desktop/mobile/CLI accepted→poll→terminal/failed | 三端 processing reload、terminal、failed retry 各至少 1 E2E；新 action 200 applied 次数=0 |
| Wave 5 — 韧性与交付 | hot reload、kill/restart、指标、dead-letter、全量文档/测试、删除兼容残骸 | §1.1 全门通过；全量 pytest/coverage/Ruff/MyPy/npm 全绿 |

Wave 1–3 不允许启用“双轨 shadow writer”；它们可在开发分支独立验证，但生产 cutover 必须连同 Wave 4 bundled clients 同一 release 完成。

## 13. Observability

至少暴露日志/health diagnostics（命名可映射现有 metrics 设施）：

- `settlement_jobs{state}`：pending/running/applied/failed 数；
- `settlement_oldest_pending_age_seconds`；
- `settlement_job_latency_seconds`（accepted→terminal）；
- `settlement_job_attempts_total{action,outcome}`；
- `settlement_recovered_running_total`；
- `settlement_effect_replay_total{effect_kind}`；
- `settlement_singleton_conflict_total{caller=daemon|cli}`；
- `settlement_dead_letter_total{action,error_code}`；
- structured log 必含 `job_id/action/ref/attempt`，不得记录完整用户文本或 secret payload。

告警基线：oldest pending >30 s、任一 failed 新增、queue depth >100、单 job 达到第 3 次 attempt。指标本身是观察面，不得决定 winner 或恢复正确性。

## 14. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 单 writer 吞吐下降 | commit job 禁止 LLM/网络；单 job 10 s timeout；批量 nested settle 在一个 parent 内但逐 ref 幂等；先用 queue age/p95 决定是否需要未来分片 |
| 严格顺序与 retry backoff 冲突 | ready jobs 按 seq；backoff job暂时跳过。依赖旧 generation 的后续 job会 stale，不会错误升级 |
| 外部 JSON effect crash gap | 每个非 SQLite mutator接受 stable effect key并原子记录；增量 counter 改成 object-version + effect 去重 |
| owner lock 残留 | OS advisory lock 随进程死亡释放；lock file inode不 unlink；竞争者非阻塞失败，不按时间偷锁 |
| daemon 活着但本地 API 不通 | CLI 输掉 owner lock即失败，不直写；错误提示引导恢复 daemon/API |
| failed receipt 永久挡住相反 action | 明示 failed + 原 payload手工 retry；不得让相反 action接管。需要人工放弃 winner 属未来管理功能 |
| 老客户端不理解 202 | legacy façade 1 s wait；bundled 三端与后端同 release；部署文档标明不支持版本组合 |
| payload/错误泄密 | payload/result大小上限；状态 API沿用 auth；safe error白名单；日志不打印全文 |
| 迁移中旧 unapplied 行语义不清 | 只用已持久化 winner payload生成 recovery job；迁移前备份；未知/坏 payload进入 failed 而不是猜测 |

## 15. 验证命令

实现完成后的最小定向门：

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_coordinator.py \
  tests/test_settlement_recovery.py \
  tests/test_settlement_mutation_guard.py \
  tests/test_settlement_singleton.py -q

PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_database.py tests/test_soul_engine.py tests/test_api_app.py tests/test_cli.py \
  -k 'settlement or card or anchor or probe or legacy_feedback or hot_reload' -q

PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_desktop_web_issue_98_e2e.py \
  tests/test_mobile_web_probe_delight_e2e.py \
  tests/test_mobile_web_view_models.py \
  tests/test_mobile_dialogue_confirmation.py -q

(cd extension && npm test -- --test-name-pattern='settlement|dialogue confirmation|probe')
```

最终门：

```bash
.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q --tb=short
PYTHONPATH=$PWD/src .venv/bin/python -m pytest --cov=openbiliclaw --cov-fail-under=70
(cd extension && npm test && npm run typecheck && npm run build)
```

并执行 plan Wave 5 的多进程/kill/restart gate，确认：100 并发、1 writer、每 effect 1 次、无 200 applied、hot reload <2 s。

## 16. Out of scope

- 把整个 OpenBiliClaw 的所有 SQLite/JSON 写入都迁进这个 coordinator；本 spec 只拥有结算域。
- 修改推荐卡 like/dislike、delight view/dismiss、saved sync、画像手工编辑本体等互不共享结算收据的业务契约；若它们同步写 speculation/avoidance 等结算域对象，该同步 effect 仍须纳入 inventory。
- 改变 LLM prompt、归属判断模型、锚 TTL/ambiguous 产品规则、确认阈值。
- 多锚、分布式多主、远程 worker、Kafka/Redis；v1 是单机 data-dir 单 owner。
- 自动覆盖/放弃 failed winner 的管理 UI；v1 只允许重试原 payload。
- 用优先级重排 mutation；v1 以 durable seq 为序。若指标证明需要优先级，另立 spec并给出有界公平算法。

## 17. 文档义务

本次文件只是未来实现 spec/plan，不改变运行时接口；实现 PR 必须按 `CLAUDE.md#documentation-requirements` 同步：

- `docs/modules/storage.md`：inbox/receipt/effect schema与迁移；
- `docs/modules/soul.md`：single-writer、锚 generation、analysis/apply 分层；
- `docs/modules/api.md`：202、job query/retry、legacy façade、probe contract；
- `docs/modules/runtime.md`：稳定 coordinator、owner、restart/hot reload；
- `docs/modules/cli.md`：API-first + one-shot owner；
- `docs/modules/extension.md`：popup processing/poll/failure恢复；
- `docs/architecture.md`、`docs/spec.md`、`README.md`、`README_EN.md`：跨模块数据流图同步；
- `docs/changelog.md`：用户可见异步结算与恢复语义；
- 若未新增 config 字段，不改 `docs/modules/config.md`；若实现擅自增加开关/timeout 配置，则必须补齐 config 示例与文档；
- installer/依赖若未变化可不改；如新增运行依赖或启动锁行为影响部署，则同步 `scripts/install.sh`、`docs/agent-install.md`、`docs/docker-deployment.md`。
