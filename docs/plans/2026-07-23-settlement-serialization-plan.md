# 对话结算串行化重做 —— Implementation Plan

> **Spec:** [`2026-07-23-settlement-serialization-spec.md`](./2026-07-23-settlement-serialization-spec.md)
> **Baseline:** `feat/cognitive-profile-pipeline` @ `0946ee2a`
> **Execution:** Wave 0 → 1 → 2 → 3 → 4 → 5；每个 Task 都先写失败测试、确认 RED，再实现 GREEN
> **Release rule:** Waves 1–3 只可 dark-build；不得让旧 writer 与新 coordinator 同时处理生产请求。后端 cutover 与 Wave 4 bundled clients 必须同一 release。

## 0. 开工前铁律

### 0.1 环境与 config 保护

所有命令从 worktree 根运行，只用仓库 `.venv`，显式指定源码：

```bash
test "$PWD" = "/Users/white/workspace/OpenBiliClaw/.claude/worktrees/profile-analysis"
PYTHONPATH=$PWD/src .venv/bin/python -c 'import openbiliclaw; print(openbiliclaw.__file__)'
```

本 worktree 的真实 `config.toml` 被 gitignore，当前基线为 5070 bytes、SHA-256：

```text
d23161b1e5f49359604c0c6dfe09c204ec8430c12e414cf5b996ccb12134ee3d
```

跑任何可能加载/保存配置的测试前必须移走原件，不能用 `git stash` 代替：

```bash
CONFIG_STASH="/tmp/openbiliclaw-settlement-config-$$.toml"
test "$(shasum -a 256 config.toml | awk '{print $1}')" = \
  "d23161b1e5f49359604c0c6dfe09c204ec8430c12e414cf5b996ccb12134ee3d"
mv config.toml "$CONFIG_STASH"
restore_settlement_config() {
  mv "$CONFIG_STASH" config.toml
  test "$(shasum -a 256 config.toml | awk '{print $1}')" = \
    "d23161b1e5f49359604c0c6dfe09c204ec8430c12e414cf5b996ccb12134ee3d"
}
trap restore_settlement_config EXIT
```

结束时必须执行 restore（或让 trap 执行）、再次校验 SHA，并确认没有测试生成的 DB/log/config 进入 git。

### 0.2 TDD 与改动纪律

每个 Task 固定顺序：

1. 只写该 Task 的最小失败测试；运行列出的 focused command，保存预期失败原因。
2. 确认失败来自缺失契约而非 import、fixture、环境或 timeout 写错。
3. 实现最小生产改动；focused tests 全绿。
4. 跑该 Wave 回归门；再进入下一 Task。
5. 不用 sleep 证明并发；用 barrier/event、独立连接、子进程、fake clock 和明确 timeout。
6. 不靠 monkeypatch 掩盖 worker 外 mutation；护栏失败必须改路由。

### 0.3 最终静态门

```bash
.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
```

扩展改动统一从 `extension/`：

```bash
(cd extension && npm test && npm run typecheck && npm run build)
```

## 1. 目标文件布局

新增包，避免继续把协调器塞进 `engine.py`/`app.py`：

```text
src/openbiliclaw/soul/settlement/
├── __init__.py
├── models.py       # action/job/result/frozen payload
├── guard.py        # writer permit + runtime guard
├── coordinator.py  # owner/admission/worker/recovery/wait
├── applier.py      # worker-only dispatch + nested _apply_*
└── analysis.py     # read-only dialogue/probe analysis handoff
```

测试新增：

```text
tests/test_settlement_coordinator.py
tests/test_settlement_recovery.py
tests/test_settlement_mutation_guard.py
tests/test_settlement_singleton.py
tests/test_settlement_api_contract.py
tests/test_settlement_entry_inventory.py
```

若实现时拆出 `api/settlement_routes.py` 或 storage DAO 文件可以，但 `Database` 初始化/迁移仍须有唯一入口，不能再造第二套 SQLite connection ownership。

---

## 2. Wave 0 — 固定基线、复现与入口清单

Wave 0 不改变运行时行为。它把本次 REJECT 的证据变成确定性测试，并冻结 cutover 清单。

### Task 0.1 — 五问题 deterministic repro

**Files**

- 新增 `tests/test_settlement_rejected_design_repros.py`
- 修改 `tests/test_soul_engine.py`（只抽复用 fixture，不改断言语义）
- 修改 `tests/test_database.py`（只抽 legacy settlement DB fixture）

**Steps**

1. 先写 5 个不带 xfail 的测试并确认 RED：
   - `test_file_fence_never_blocks_event_loop_while_owner_awaits`：旧实现放子进程，2 s timeout 应复现挂起而非挂死 pytest。
   - `test_generation_change_after_validation_before_effect_writes_nothing`：barrier 卡在 post-LLM validation 后，再 release/re-establish，同 ref 新 generation 不得被旧 job写。
   - `test_missing_generation_never_captures_future_anchor`：受理时 generation=0，执行前建同 ref 锚；旧实现会在 `engine.py:1089` 补抓。
   - `test_every_probe_and_cli_entry_submits_instead_of_mutating`：spy 当前 direct mutators，至少命中 API button、probe dialogue、CLI 三类旁路。
   - `test_crash_after_derived_ledger_before_segment_marker_does_not_duplicate`：在 derived ledger 后崩溃，恢复后当前实现会出现重复 effect。
2. 每个测试必须有 2–5 s 上限，不能以无限 hang 表示 RED。
3. Wave 0 临时加 `pytest.mark.xfail(strict=True, reason="rejected settlement stack")`；后续对应 Wave 修复时逐个删除，禁止永久 xfail。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_rejected_design_repros.py -q -rxX
```

数值门：恰好 `5 xfailed`、`0 xpassed`、总时长 <15 s。

### Task 0.2 — action、entry、mutator inventory

**Files**

- 新增 `src/openbiliclaw/soul/settlement/models.py`
- 新增 `tests/test_settlement_entry_inventory.py`
- 读取但暂不改：`api/app.py`、`soul/engine.py`、`soul/dialogue.py`、`soul/dialogue_anchor.py`、`soul/confusion.py`、`cli.py`、`integrations/openclaw/operations.py`

**Steps**

1. 先写失败测试，冻结 action 白名单、payload version 与 `receipt_policy=terminal_once|version_once|job_only`。至少包含 card 四动作、confirmation open/attach、anchor establish/release、dialogue analysis apply、interest/avoidance/probe-dialogue apply、reconcile；manual profile edit/OpenClaw 复用相同 canonical action，不另开直写口。
2. 建 `ENTRY_INVENTORY`，逐项记录当前 symbol、目标 action、owner state；必须覆盖 spec D4–D6、`engine.py:875-906` 的 manual edit speculator sync、`integrations/openclaw/operations.py:525-576`。
3. 建 `MUTATOR_INVENTORY`：数据库 settlement writer、hypothesis feedback、speculation/avoidance user action、confusion settle/replay、anchor establish/release/relation、derived、rebuild、card projection、probe history/cognition。
4. 测试用明确 symbol import/AST 定位；行号只作诊断文本，不作脆弱匹配。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_entry_inventory.py -q
```

数值门：entry inventory 对 spec 列出的入口覆盖率 100%，`unclassified=[]`；action/payload JSON round-trip 100% deterministic。

### Wave 0 Gate

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_rejected_design_repros.py \
  tests/test_settlement_entry_inventory.py -q -rxX
```

必须仍为 5 个 strict XFAIL，inventory 全绿。

---

## 3. Wave 1 — Durable inbox、owner 与 worker 基座

Wave 1 的 coordinator 尚不接生产入口；旧行为保持，避免双 writer。

### Task 1.1 — SQLite schema、迁移与 DAO

**Files**

- 修改 `src/openbiliclaw/storage/database.py`
- 修改 `tests/test_database.py`
- 新增 `tests/test_settlement_coordinator.py`

**Steps**

1. 先写失败测试：fresh schema；从旧 `card_settlements` 最小表、Wave-A 表、当前 lease/segment 表三种迁移；重复 initialize 幂等。
2. 新增 `settlement_jobs`、简化后的 ref receipt、`settlement_effects`、`rebuild_requests`、SQLite `dialogue_anchor_state`。
3. 用 table rebuild 移除旧 claim/segment 语义；把旧 raw ref迁成 namespaced `receipt_ref`，每行派生稳定 migration job/idempotency key。`applied=1` 合成 applied job+receipt；缺 kind 的 applied行保存为 `legacy:<raw_ref>` terminal alias，不重执行。`applied=0` 只凭合法 stored winner payload生成 pending recovery job；坏/缺 kind payload进入 failed receipt/job，不猜 verdict。
4. 实现 admission/queue DAO：
   - `submit_settlement_job()`：idempotency + receipt + card processing CAS同事务；
   - `claim_next_ready_job()`；
   - `complete_settlement_job()` / `retry_or_fail_settlement_job()`；
   - `recover_running_settlement_jobs()`；
   - `get_settlement_job()` / `retry_failed_settlement_job()`；
   - effect/rebuild once helpers。
5. 所有 payload/result deterministic JSON、payload ≤256 KiB、result ≤64 KiB；非法 state/action拒绝。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_database.py -k 'settlement_job or settlement_receipt or card_settlement_schema' -q
```

数值门：fresh + 3 legacy shapes + double-init 至少 8 个用例；同 idempotency key 并发 50 次只生成 1 job；同 ref 相反 action 并发 50 次只生成 1 receipt/1 winner job。

### Task 1.2 — data-dir owner、durable scanner、restart recovery

**Files**

- 新增 `src/openbiliclaw/soul/settlement/coordinator.py`
- 新增 `src/openbiliclaw/soul/settlement/__init__.py`
- 新增 `tests/test_settlement_singleton.py`
- 扩充 `tests/test_settlement_coordinator.py`

**Steps**

1. 先写失败测试：两个进程对同一 temp data dir 非阻塞争 owner，恰一成功；输家不启动 worker。
2. 复用 generic `exclusive_file_lock(..., blocking=False)`，lock file 固定且不 unlink；新增 lifecycle owner object。不得复用/保留 card settlement per-job fence。
3. worker loop 每次扫描 SQLite；`asyncio.Event` 只 wake。测试清除/漏掉 event 后，周期 scanner 仍发现 durable pending job。
4. coordinator start 后先 `running→pending` recovery，再处理；shutdown 直接 cancel，不 join/drain。
5. claim 只取 ready job 最小 seq；10 s apply timeout；transient backoff 1 s/5 s；第三次后 failed；permanent validation一次 failed，domain stale一次 applied/no-op。
6. 提供 `submit/get/wait/retry/start/stop`；`wait()` 只观察 DB，不执行 job。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_coordinator.py tests/test_settlement_singleton.py -q
```

数值门：

- 2 个进程、20 轮竞争，每轮 owner=1、loser mutation=0；
- 100 个 no-op job 按 seq applied，5 s 内完成；
- 预置 10 个 running 后重启，10 个全恢复且没有第 11 个副本；
- 丢 10 次 wakeup 仍全部收敛；
- hang job 3 次后 failed，后续 sentinel applied。

### Task 1.3 — writer permit 与运行时护栏

**Files**

- 新增 `src/openbiliclaw/soul/settlement/guard.py`
- 新增 `src/openbiliclaw/soul/settlement/applier.py`（先放 dispatch skeleton）
- 新增 `tests/test_settlement_mutation_guard.py`

**Steps**

1. 先写失败测试：无 permit、伪 coordinator id、不同 task、不同 thread、child task继承 ContextVar都必须拒绝。
2. permit 绑定 `coordinator_id/job_id/owner_task_id/thread_id`；worker 进入 apply 时创建，离开必清理。
3. 提供 `@settlement_mutation` / `require_settlement_writer()`；异常固定为 `SettlementMutationOutsideWorker`。
4. `SettlementApplier.apply()` 白名单 dispatch；未知 action permanent failed，不进入 retry storm。
5. 禁止 applier `asyncio.create_task` 与 mutation `to_thread` 的静态测试。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_mutation_guard.py tests/test_settlement_coordinator.py -q
```

数值门：至少 6 类非法上下文全部抛指定异常；worker 正向 20 次全部通过；child task 0 次获得写权限。

### Wave 1 Gate

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_coordinator.py \
  tests/test_settlement_singleton.py \
  tests/test_settlement_mutation_guard.py \
  tests/test_database.py -k 'settlement or card_settlement_schema' -q
```

此时不得在 `RuntimeContext` 启动新 coordinator，不得改 endpoint response。

---

## 4. Wave 2 — 核心 applier、effect 幂等与旧并发栈删除

### Task 2.1 — effect-once primitives

**Files**

- 修改 `src/openbiliclaw/storage/database.py`
- 修改 `src/openbiliclaw/soul/ledger.py`
- 修改 `src/openbiliclaw/soul/speculator.py`
- 修改 `src/openbiliclaw/soul/avoidance_speculator.py`
- 修改 `src/openbiliclaw/soul/confusion.py`
- 修改 `src/openbiliclaw/soul/dialogue_anchor.py`
- 修改 `src/openbiliclaw/soul/engine.py`（derived/rebuild primitives）
- 新增 `tests/test_settlement_recovery.py`

**Steps**

1. 先写失败测试，在以下“effect 已写、job 尚未 terminal”边界硬崩溃：event、object、derived、derived ledger、rebuild outbox、card projection、anchor；另测既有 `dialogue_anchor_state.json` 一次性迁入 SQLite、重复启动不增 generation。耗尽重试的部分完成 job必须列出 `completed_effects/pending_effects`，retry只补后者。
2. event + effect receipt 同 SQLite transaction；ledger 增 stable effect key unique contract。
3. hypothesis/confusion 改目标态 set；speculation/avoidance 持久化 `object_version` + last effect：legacy按 kind/domain/created_at确定性补一次，新对象/复燃生成新 version，字段编辑不重算；禁止裸 `count += 1` 重放。
4. derived 按 normalized hash upsert，`anchor_revise_derived` ledger 用 `<job>:derived:<hash>`；generic settlement ledger不得重复描述该 derived change。
5. rebuild 改写 durable `rebuild_requests`；现有 consumer 合并 refs并 ack，重复 effect no-op。
6. card projection只接受 payload.job_id 当前值；anchor state迁 SQLite，并保存 last_job_id。
7. probe feedback history/cognition append 携带 effect key并去重。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_recovery.py \
  tests/test_speculator.py tests/test_avoidance_speculator.py \
  tests/test_confusion_lifecycle.py tests/test_dialogue_anchor.py -q
```

数值门：7 类 crash boundary × 至少 2 种动作；每次 restart 后 effect count=1、job terminal；derived ledger count=1；rebuild request count=1。

### Task 2.2 — `_apply_*` 与 nested settle

**Files**

- 修改 `src/openbiliclaw/soul/settlement/applier.py`
- 修改 `src/openbiliclaw/soul/settlement/models.py`
- 修改 `src/openbiliclaw/soul/engine.py`
- 修改 `src/openbiliclaw/soul/confusion.py`
- 修改 `tests/test_soul_engine.py`
- 扩充 `tests/test_settlement_recovery.py`

**Steps**

1. 先写失败测试：hypothesis confirm/reject/revise、speculation confirm/reject/defer、avoidance confirm/reject/defer、confusion answer/defer、anchor establish/release/relation。
2. 从 `engine.py` 提取 worker-only `_apply_hypothesis/_apply_speculation/_apply_avoidance/_apply_confusion/_apply_anchor_*`；每个入口要求 permit。
3. `dialogue.analysis.apply` parent 内逐项调用 `_apply_*`。测试把 coordinator.submit monkeypatch 为“调用即失败”，确保 nested 不排队。
4. `dialogue.analysis.apply` admission 已按 ref排序预留全部 nested receipts；当前 ApplyContext只核对 frozen reservation map并 apply。测试构造 seq 更早的 parent、seq 更晚的 card，确认 parent仍是 winner；禁止到 worker 才首次 reserve。重放 parent 对冲突 ref返回 already_settled，不重写 effects。
5. worker 开始 action 时校验 frozen anchor ref+generation；不符返回 applied result `outcome=stale`、业务 effects=0。
6. 删除 `settle_hypothesis()` 中执行时抓 current generation 的逻辑；generation=0 保持 0。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_soul_engine.py -k 'settle or anchor_generation or nested' \
  tests/test_settlement_recovery.py -q
```

数值门：action 矩阵所有合法分支至少 1 用例；nested submit 调用=0；旧/新 generation 交错 100 次，旧代 effect=0；missing generation 捕获未来锚=0 次。

### Task 2.3 — 删除 lease/claim/file-lock/三段 CAS

**Files**

- 修改 `src/openbiliclaw/storage/database.py`
- 修改 `src/openbiliclaw/soul/engine.py`
- 修改 `tests/test_database.py`
- 修改 `tests/test_soul_engine.py`
- 修改 `tests/test_api_app.py`（只移除旧 fixture 依赖，API 契约 Wave 4 改）

**Steps**

1. 先让新 coordinator recovery tests覆盖旧 takeover/fencing tests想保护的 outcome。
2. 删除：
   - `database.py:90-102` card settlement lock globals/helper；
   - `database.py:1325-1327` lock path；
   - `database.py:2526-2782` claim/fence/segments/complete stack；
   - `engine.py:1184-1460` 三段 executor；
   - `engine.py:1089-1097` future generation推断。
3. `try_create/get/project` 改为新 admission/receipt/projection DAO，不保留同名的双语义 compatibility shim。
4. 删除/替换旧测试：`test_card_settlement_claim_fences_paused_old_executor_after_takeover`、`test_card_settlement_segment_and_applied_writes_require_current_fence`、`test_card_settlement_ledger_failure_cannot_roll_back_marker_or_block_apply`、`test_claim_takeover_fences_object_derived_and_ledger_side_effects` 等。
5. generic `memory/json_state.py::exclusive_file_lock` 保留给其他模块及 data-dir owner；只删除 settlement per-job 用法。

**Acceptance**

```bash
test "$(rg -n 'claim_card_settlement|card_settlement_claim_guard|_card_settlement_fence|_CARD_SETTLEMENT_LOCKS' src/openbiliclaw | wc -l | tr -d ' ')" = "0"
test "$(rg -n 'await self\._apply_dialogue_settlement_object|await self\.mark_feedback_rebuild' src/openbiliclaw/soul/engine.py | wc -l | tr -d ' ')" = "0"
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_database.py tests/test_soul_engine.py tests/test_settlement_recovery.py -q
```

数值门：旧 runtime stack 符号命中 0；focused tests 全绿；Wave 0 的 D1/D2/D3/D7 xfail 去标后 4 个变 PASS。旧列名只可出现在 legacy schema 迁移识别/fixture 中，不得被运行时 claim/fencing 代码读取。

### Wave 2 Gate

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_coordinator.py \
  tests/test_settlement_recovery.py \
  tests/test_settlement_mutation_guard.py \
  tests/test_database.py tests/test_soul_engine.py -q
```

仍不得对生产 endpoint 启用新 worker；Wave 3 会一次性收口入口。

---

## 5. Wave 3 — 全入口收口、分析/提交拆分、CLI 单实例

### Task 3.1 — card/open/attach/anchor API 只 admission

**Files**

- 修改 `src/openbiliclaw/api/app.py`
- 修改 `src/openbiliclaw/api/models.py`
- 可新增 `src/openbiliclaw/api/settlement_routes.py`
- 修改 `src/openbiliclaw/api/runtime_context.py`
- 修改 `tests/test_api_app.py`
- 修改 `tests/test_settlement_entry_inventory.py`

**Steps**

1. 先写失败 endpoint spy：handler 中只有 `coordinator.submit()`；任何 anchor/confusion/card/cooldown/project mutator direct call均触发 guard。
2. 迁移 spec D4 的 `app.py:2324-2352,2632-2663,2743-2958,7641-7674,7901-8144`。
3. card confirm/reject/defer/discuss 均提交 job；discuss applied 结果才建立锚，失败 card 持久化 failed。
4. pending confirmation open 返回 job；worker创建 card/question、claim confusion、建立 frozen generation anchor。自动 attach 按 durable seq保持“卡片在用户 turn 前”的显示顺序；必要时给 `chat_turns` 增 `display_seq`，不要靠插入完成时间。
5. GET chat/card/job 纯读；删除 `_reconcile_chat_card_row()` 中 projection/repair mutation，改显式 startup `settlement.reconcile` job。
6. coordinator 成为 RuntimeContext 稳定组件；hot reload只更新 dependency provider，不重新 acquire owner。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_api_app.py -k 'hypothesis_card or pending_confirmation or discuss or anchor' \
  tests/test_settlement_entry_inventory.py -q
```

数值门：card 四动作 + open + attach 至少 6 个 endpoint都只产生 job；GET mutation spy=0；同 ref/session 50 并发 open只创建 1 display turn；跨 session按既有产品契约各 1。

### Task 3.2 — dialogue LLM 队外化、普通 chat settles 内嵌 apply

**Files**

- 修改 `src/openbiliclaw/soul/dialogue.py`
- 删除或降级 `src/openbiliclaw/soul/dialogue_learn_queue.py` 为 durable analyzer scanner（不得保留 payload `asyncio.Queue`）
- 修改 `src/openbiliclaw/soul/engine.py`
- 修改 `src/openbiliclaw/soul/cognition_cycle.py`
- 修改 `src/openbiliclaw/soul/settlement/analysis.py`
- 修改 `src/openbiliclaw/api/runtime_context.py`
- 修改 `tests/test_dialogue_learn_queue.py`
- 修改 `tests/test_dialogue_context.py`
- 修改 `tests/test_soul_engine.py`

**Steps**

1. 先写失败测试：LLM analyzer barrier未释放时提交 100 个 no-LLM commit job，sentinel必须 <500 ms applied。
2. turn admission 时把 anchor snapshot写进 durable chat payload；不是 dialogue reply生成后才抓。
3. 拆 `analyze_dialogue_learning()`（只读/LLM）和 `_apply_dialogue_analysis()`（worker-only）。completed turn若无 analysis receipt，startup scanner重做分析；相同 turn/version只产生 1 job。同一 history按 durable display seq串行分析，单次≤120 s、最多2次，失败后放行下一 turn，但不占 commit worker。
4. 迁移 `engine.py:1811-1820,2669-2803,2872-3037,3239-3310`；普通 chat settles与锚 relation作为 parent job nested `_apply_*`，不调用 public submit。
5. `cognition_cycle.py:267-289` 只提交/唤醒 durable reconcile；confusion TTL/anchor expire/replay的确定性 mutation进 worker，必要 LLM重分类仍在 analyzer。
6. 候选 merge、anchor relation、confusion replay、settles若属于同一逻辑 mutation，统一由 parent job effects处理；分析失败只标 analysis failure，不产生半 mutation。
7. 删除 runtime hot reload 的 old learn queue pause/drain ownership；此 Task 先保证不丢，Wave 5删最后 lifecycle残骸。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_learn_queue.py tests/test_dialogue_context.py \
  tests/test_soul_engine.py -k 'dialogue or settle or anchor' -q
```

数值门：阻塞 20 个 analyzer时 commit sentinel p95 <500 ms；重启扫描 20 个 completed turn只产 20 个 job；nested queue depth增量=0；turn generation从 admission 到 result不变。

### Task 3.3 — interest/avoidance probe API、probe dialogue、OpenClaw

**Files**

- 修改 `src/openbiliclaw/api/app.py`
- 修改 `src/openbiliclaw/soul/settlement/applier.py`
- 修改 `src/openbiliclaw/soul/settlement/analysis.py`
- 修改 `src/openbiliclaw/integrations/openclaw/operations.py`
- 修改 `tests/test_api_app.py`
- 修改 `tests/test_openclaw_adapter.py`
- 修改 `tests/test_openclaw_proactive_e2e.py`
- 修改 `tests/test_settlement_entry_inventory.py`

**Steps**

1. 先写失败 guard tests覆盖：interest confirm/reject/defer、avoidance confirm/reject/defer、两类 probe chat、profile surface、OpenClaw avoidance feedback。
2. 迁移 `app.py:7716-7868,8216-8508,8552-8805`。button action只 submit；probe chat先在队外生成 reply/classification，再 submit frozen classification。
3. 删除 avoidance confirm 的 detached `_apply_confirmed_avoidance` writer；topic expansion/LLM 队外，确定性 dislike apply进 job effects。
4. `_record_probe_feedback_history/_record_probe_cognition/_publish_probe_event` 由 applier effect key驱动。事件发布失败不回滚业务终态，但进入可重放 outbox/明确 observer error，不匿名重发。
5. OpenClaw 使用注入的 coordinator；没有 owner/协调器时返回 adapter error，不 direct fallback。
6. `engine.py:875-906` manual edit对 speculator 的同步也提交 canonical action或由已有 worker context nested apply，不能绕 guard。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_api_app.py -k 'interest_probe or avoidance_probe or probe_chat' \
  tests/test_openclaw_adapter.py tests/test_openclaw_proactive_e2e.py \
  tests/test_settlement_entry_inventory.py -q
```

数值门：至少 8 类 probe entry mutation direct count=0；每 entry返回/关联 1 job；同 object version双击 20 次 effect各 1；LLM call在 worker context内=0。

### Task 3.4 — CLI API-first + 一次性 worker

**Files**

- 修改 `src/openbiliclaw/cli.py`
- 修改 `tests/test_cli.py`
- 修改 `tests/test_settlement_singleton.py`

**Steps**

1. 先写失败测试：daemon可达时 `probe` 只做 HTTP submit/query，`_build_soul_engine` 调用即失败。
2. 提取共享 loopback client：POST probe action、读取 job Location/ID、按 250 ms→2 s poll；applied exit 0、failed exit 1、Ctrl-C不取消 job。
3. daemon不可达时尝试同一 data-dir owner lock；成功构建最小 one-shot coordinator并处理到 terminal，失败绝不 direct mutate。
4. 两个 CLI 子进程并发对同 data dir：一个 one-shot owner；另一个退出 1并给“owner/API不可达”诊断。
5. CLI confirm后 `force_tick` 改为队外可恢复调度；不能在 commit worker里 await LLM。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_cli.py -k 'probe or settlement_job' \
  tests/test_settlement_singleton.py -q
```

数值门：API path direct engine builds=0；one-shot单进程 terminal；2 CLI并发 owner=1、effect=1；failed exit code=1；Ctrl-C后 DB job仍存在。

### Wave 3 Gate — worker 外 mutation 护栏正式启用

**Files**

- 完成 `tests/test_settlement_mutation_guard.py`
- 完成 `tests/test_settlement_entry_inventory.py`

**Steps**

1. 给 inventory 中所有低层 mutator启用生产 guard。
2. AST allowlist只允许 `soul/settlement/applier.py` 与受控 migration helper直接调用；endpoint/analyzer/CLI/OpenClaw允许数为 0。
3. 删除 Wave 0 的旁路 xfail 标记，确认第 5 个 repro转 PASS。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_mutation_guard.py \
  tests/test_settlement_entry_inventory.py \
  tests/test_settlement_rejected_design_repros.py -q
```

硬门：`unclassified=[]`、allowlist 外 direct mutation=0、5 个原 repro 全 PASS。此门不通过禁止进入前端 cutover。

---

## 6. Wave 4 — 202/job 契约与 popup/桌面/移动/CLI

### Task 4.1 — 统一 API job envelope、query/retry、legacy bounded wait

**Files**

- 修改 `src/openbiliclaw/api/models.py`
- 修改 `src/openbiliclaw/api/app.py` 或 `api/settlement_routes.py`
- 新增 `tests/test_settlement_api_contract.py`
- 修改 `tests/test_api_app.py`

**Steps**

1. 先写失败契约测试并暂停 worker：所有新 card/open/probe POST必须 `202 + Location + Retry-After + job_id + state=processing`。
2. 新增 `GET /api/settlement-jobs/{job_id}` 与 `POST .../{job_id}/retry`；内部 pending/running映射 public processing + phase。显式 retry 原子执行 failed→pending、attempts清零、manual_retry_count+1、card failed→processing，payload hash/verdict不变。
3. 固定语义：新接受永不 `200 applied`；同 ref processing返回 202 winner job；applied ref返回 200 already_settled；failed返回 durable error、`completed_effects/pending_effects`并只允许原 payload retry。
4. legacy `/api/insights/feedback` 只 submit，最多 wait 1.0 s。applied返回旧 fields；超时202；failed不 fallback。
5. 卡片 admission同事务持久化 processing/job_id；failed/applied由 worker投影。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_api_contract.py \
  tests/test_api_app.py -k 'card or insight_feedback or pending_confirmation or probe' -q
```

数值门：所有新接受 `200 applied` 次数=0；暂停 worker时 100 个 POST p95 <250 ms；legacy wait wall time ≤1.1 s；未知 job 404；failed retry不改变 payload hash/verdict。

### Task 4.2 — 共享 polling helper + popup/桌面

**Files**

- 修改 `src/openbiliclaw/web/shared/dialogue-confirmation.js`
- 同步 `extension/popup/shared/dialogue-confirmation.js` 的 build/source流程（不手工制造第三份逻辑）
- 修改 `extension/popup/popup-api.js`
- 修改 `extension/popup/popup.js`
- 修改 `src/openbiliclaw/web/desktop/assets/js/app.js`
- 修改 `extension/tests/dialogue-confirmation.test.ts`
- 修改 `extension/tests/dialogue-confirmation-wiring.test.ts`
- 修改/新增桌面浏览器 E2E fixture

**Steps**

1. 先写 node失败测试：POST 202 后 UI为 processing，不是 optimistic terminal；poll applied后更新；poll failed后显示 retry；network error保持 processing。
2. shared helper新增 job URL/normalizer/poll state machine（250/500/1000/2000 ms，前台上限30 s）。
3. popup/桌面 hydrate发现 payload.job_id + processing时自动续 poll；同 ref already_settled用权威 result覆盖本地 action。
4. discuss/open只有 applied 后聚焦聊天；failed不卡死按钮。
5. 扩展 shared 文件继续沿用当前 copy/build约定，测试两份字节/版本一致，不能手修漂移。

**Acceptance**

```bash
(cd extension && npm test -- --test-name-pattern='settlement|dialogue confirmation')
(cd extension && npm run typecheck && npm run build)
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_desktop_web_issue_98_e2e.py -k 'settlement or processing or failed' -q
```

数值门：popup + desktop 各覆盖 accepted、reload-resume、applied、failed-retry、offline-resume 5 态；任何 202 后成功 toast=0。

### Task 4.3 — 移动端 processing/轮询/失败恢复

**Files**

- 修改 `src/openbiliclaw/web/js/api.js`
- 修改 `src/openbiliclaw/web/js/views/chat.js`
- 修改 `src/openbiliclaw/web/js/views/profile.js`
- 修改 `src/openbiliclaw/web/js/view-models.js`
- 修改 `src/openbiliclaw/web/js/state.js`（如需 durable view state）
- 修改 `tests/test_mobile_web_view_models.py`
- 修改 `tests/test_mobile_web_probe_delight_e2e.py`
- 修改 `tests/test_mobile_web_delight_layout.py`
- 新增 `tests/test_mobile_dialogue_confirmation.py`

**Steps**

1. 先写失败 E2E：hypothesis card confirm 与 probe POST 202 时卡片保持且 `aria-busy=true`；只有 poll applied后进入终态/删除；failed 恢复按钮并显示错误。
2. mobile API新增 pending confirmation open、card action、fetch/retry job；复用与 shared helper相同 public state语义和 backoff常量（可提共享纯函数 fixture，不能复制漂移表）。
3. mobile chat/view-model渲染 structured hypothesis card 的 processing/applied/failed，open/discuss等到 applied才进入对话；不得以“当前移动端卡片较少”为由保留同步分支。
4. profile probe、消息 overlay probe、inline probe chat都关联 job；chat reply completed不等于 probe mutation applied。
5. reload/stream reconnect根据后端 card payload/pending probe + job状态恢复；网络错误不调用 `forgetHandledProbe` 造成错误重现/消失。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_mobile_web_view_models.py \
  tests/test_mobile_web_probe_delight_e2e.py \
  tests/test_mobile_web_delight_layout.py \
  tests/test_mobile_dialogue_confirmation.py -q
```

数值门：mobile hypothesis card + 三个 probe入口（profile/overlay/inline chat）均走统一 job client；card 与每类 probe至少覆盖 processing→applied / processing→failed；reload-resume至少 1；HTTP 202 即宣告终态或删除卡片次数=0。

### Task 4.4 — CLI terminal UX 与契约补测

**Files**

- 修改 `src/openbiliclaw/cli.py`
- 修改 `tests/test_cli.py`
- 修改 `docs/modules/cli.md`（实现 PR 内）

**Steps**

1. 先写失败 snapshot/exit-code tests：accepted打印 job id/处理中；applied打印权威 action；failed打印 error code + retry提示；timeout只停止等待。
2. CLI query使用统一 response model，不把 HTTP 202 当成功终态。
3. `questions` 维持只读；新增 test确保它不启动 coordinator。
4. one-shot和 daemon path输出语义一致。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_cli.py -k 'questions or probe or settlement' -q
```

数值门：accepted/applied/failed/interrupted 4 态 exit/output全覆盖；daemon path与one-shot path各 1；CLI direct mutator调用=0。

### Wave 4 Gate

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_api_contract.py tests/test_api_app.py tests/test_cli.py \
  tests/test_desktop_web_issue_98_e2e.py \
  tests/test_mobile_web_view_models.py \
  tests/test_mobile_web_probe_delight_e2e.py \
  tests/test_mobile_dialogue_confirmation.py -q
(cd extension && npm test && npm run typecheck && npm run build)
```

这是第一个允许发布的 cutover 候选；仍需 Wave 5 韧性门才能合并。

---

## 7. Wave 5 — Crash/restart、热重载、指标、文档与总验收

### Task 5.1 — 无 drain 的 shutdown/hot reload

**Files**

- 修改 `src/openbiliclaw/api/runtime_context.py`
- 修改 `src/openbiliclaw/api/app.py`
- 删除/收缩 `src/openbiliclaw/soul/dialogue_learn_queue.py` 旧 lifecycle API
- 修改 `tests/test_dialogue_learn_queue.py`
- 修改 `tests/test_api_app.py`
- 新增/扩充 `tests/test_settlement_recovery.py`

**Steps**

1. 先写失败测试：worker barrier挂住且 pending>0，`rebuild_from_config()` 2 s 内完成，不调用 `pause_and_drain/shutdown(timeout=...)`。
2. 删除 `runtime_context.py:539-581` 的 reload drain/rollback resume、`:1287-1306,1345-1347` 的可替换内存 learn queue；coordinator留在 stable components。
3. 删除 `api/app.py:5034-5040` shutdown drain；cancel worker并释放 owner。
4. 子进程在每 effect边界 `os._exit`，新进程取得 owner并恢复；测试旧 owner仍活时新进程绝不接管。
5. runtime dependency swap用原子 provider snapshot；running job要么用 job开始时 snapshot完成，要么失败重试，不跨半旧半新组件。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_recovery.py \
  tests/test_api_app.py -k 'hot_reload or shutdown or settlement' \
  tests/test_dialogue_learn_queue.py -q
```

数值门：hot reload 20 轮均 <2 s；0 次 drain调用；7 crash边界全部恢复；旧 owner活着时 takeover=0；进程退出后新 owner取得率=100%。

### Task 5.2 — dead-letter、metrics 与负载门

**Files**

- 修改 `src/openbiliclaw/soul/settlement/coordinator.py`
- 修改现有 health/diagnostics 暴露点（优先 `api/app.py`/runtime diagnostics，不新增未认证 debug endpoint）
- 新增 `tests/test_settlement_observability.py`
- 扩充 `tests/test_settlement_coordinator.py`

**Steps**

1. 先写失败测试：pending/running/applied/failed、oldest age、attempt、recovered、effect replay、singleton conflict、dead-letter 指标。
2. structured log统一 `job_id/action/ref/attempt`，payload全文/secret/traceback不得出现。
3. 100 mixed jobs：card、ordinary chat nested settle、probe API、CLI/legacy模拟；并发 producer，单 worker。
4. worker暂停测 admission p95；恢复后测 terminal latency与 exact-once counts。
5. malformed payload >256 KiB、result >64 KiB、unknown action、permanent error、transient 3 failures分别进入稳定结果。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_observability.py \
  tests/test_settlement_coordinator.py -q
```

数值门：100 mixed job admission p95 <250 ms、5 s 内收敛；writer=1；每 ref receipt=1；每 effect=1；unknown/malformed不重试；transient attempts=3后failed；后续 ready job不阻塞。

### Task 5.3 — 旧测试改写与删除清单收口

**Files**

- `tests/test_api_app.py`
- `tests/test_database.py`
- `tests/test_soul_engine.py`
- `tests/test_dialogue_anchor.py`
- `tests/test_confusion_lifecycle.py`
- `tests/test_cli.py`
- `extension/tests/dialogue-confirmation.test.ts`
- `extension/tests/dialogue-confirmation-wiring.test.ts`
- `tests/test_desktop_web_issue_98_e2e.py`
- `tests/test_mobile_web_view_models.py`
- `tests/test_mobile_web_probe_delight_e2e.py`
- `tests/test_mobile_dialogue_confirmation.py`

**Steps**

按 §8 的专表逐项完成；删除旧 lease/takeover/fencing 语汇与 fixture，保留其业务意图并改成 owner/inbox/effect recovery 测试。不得只删断言降低覆盖。

**Acceptance**

```bash
test "$(rg -n 'lease-takeover|paused-owner|claim_card_settlement|card_settlement_claim_guard' tests | wc -l | tr -d ' ')" = "0"
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_database.py tests/test_soul_engine.py tests/test_api_app.py \
  tests/test_dialogue_anchor.py tests/test_confusion_lifecycle.py tests/test_cli.py -q
```

数值门：旧 takeover/claim 行为测试与调用命中 0；旧列名仅允许出现在具名 legacy migration fixture；新 202/poll/failure/restart测试每端齐全；focused suites全绿。

### Task 5.4 — 强制文档同步

**Files**

- `docs/modules/storage.md`
- `docs/modules/soul.md`
- `docs/modules/api.md`
- `docs/modules/runtime.md`
- `docs/modules/cli.md`
- `docs/modules/extension.md`
- `docs/architecture.md`
- `docs/spec.md`
- `README.md`
- `README_EN.md`
- `docs/changelog.md`
- 条件触发：`docs/modules/config.md`、安装/部署文档

**Steps**

1. 按 spec §17 更新 public API、schema、module boundaries、data flow、CLI、三端 UX。
2. 四份架构图同步 single-writer/inbox/job polling；README CN/EN图必须同构。
3. changelog 写用户可见行为：action 变 202、processing可恢复、failed可重试、CLI owner规则。
4. 若没有 config/dependency/install变化，PR说明明确标 `N/A`；若有则补对应文档，不得默认跳过。
5. 更新原 `2026-07-22-dialogue-confirmation-entry-*` 的 status/引用，标明旧并发段已由本 spec supersede，避免两份权威文档冲突。

**Acceptance**

```bash
rg -n "settlement_jobs|settlement-jobs|single.writer|单写者|202" \
  docs/modules docs/architecture.md docs/spec.md README.md README_EN.md docs/changelog.md
```

数值门：CLAUDE Documentation Requirements 每个适用项都有文件或明确 N/A；CN/EN架构图节点与边数一致；旧 spec不再声称 lease/claim是现行设计。

### Task 5.5 — 最终总验收

**Files**

- 不预设新的生产文件；若总门发现问题，回到拥有该行为的前述 Task 修复并重跑其 RED/GREEN 门
- 只读核对全仓、git index、原 `config.toml` 与测试/构建产物

**Steps**

1. 恢复并校验原 `config.toml` SHA。
2. 跑 format check、lint、types、全量 pytest、coverage、extension全门。
3. 跑两进程 owner与 kill/restart focused gate第二次，排除全量测试顺序污染。
4. `git diff --check`；确认无 `.db/.lock/.tmp/config.toml/dist/node_modules` 入库。
5. 记录测试命令、时长、数值门结果到 PR。

**Acceptance**

```bash
.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q --tb=short
PYTHONPATH=$PWD/src .venv/bin/python -m pytest --cov=openbiliclaw --cov-fail-under=70
(cd extension && npm test && npm run typecheck && npm run build)
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_settlement_singleton.py tests/test_settlement_recovery.py -q
git diff --check
```

最终数值门：coverage ≥70%；100 concurrent gate满足 spec §1.1；7 crash effects各=1；hot reload <2 s；new action `200 applied`=0；工作树只含本实现与强制文档，无运行产物。

---

## 8. 旧测试改写清单（不可漏）

| 旧测试/断言 | 处理 | 新契约 |
| --- | --- | --- |
| `test_api_app.py::test_confirm_and_reject_apply_fenced_settlement` | 改写 | POST=202；card payload=`processing/job_id`；poll applied后才断言对象/event/card terminal |
| `test_defer_persists_cooldown_without_creating_settlement` | 改写 | defer进 `job_only` job但不占 terminal ref receipt；POST不直接写 cooldown；poll后断言 deferred/outbox，未来 confirm仍可受理 |
| `test_discuss_cas_builds_anchor_and_failure_rolls_back` | 改写 | POST=202；applied后anchor存在；failed后payload=`failed/job_id`，显式 retry恢复 |
| `test_fault_injection_resumes_each_settlement_segment_once` | 删除替换 | 不再断言 `seg_*`；改为每 effect边界 kill/restart、stable effect key计数=1 |
| `test_unapplied_conflict_reports_processing_then_stale_takeover_applies_winner` | 删除替换 | 无 stale takeover；同 ref contender得到 winner job id；owner restart恢复原 winner |
| `test_applied_conflict_is_already_settled_and_refreshes_other_session` | 保留改写 | 第一次202→poll；第二次200 already_settled；跨 session投影一致 |
| `test_legacy_feedback_forwards_to_common_settlement_with_deprecated_source` | 改写 | façade submit + ≤1 s wait；快 applied=200旧 shape，paused worker=202 job |
| `test_database.py::test_card_settlement_claim_fences_paused_old_executor_after_takeover` | 删除替换 | two-process singleton + running recovery |
| `test_card_settlement_segment_and_applied_writes_require_current_fence` | 删除替换 | writer permit + effect receipt exact-once |
| `test_card_settlement_ledger_failure_cannot_roll_back_marker_or_block_apply` | 改写 | ledger observer/outbox error可见；不依赖 marker CAS；重放不重复 |
| `test_soul_engine.py::test_claim_takeover_fences_object_derived_and_ledger_side_effects` | 删除替换 | 单 worker无 takeover；derived crash gap恢复后 object/ledger各1 |
| generation post-LLM/CAS 两测试 | 改写加强 | generation admission冻结；worker序列校验；100次旧/新代交错零 effect |
| 普通 chat settle“调用 public settle”测试 | 改写 | parent job nested `_apply_*`；submit spy=0 |
| interest/avoidance probe endpoint tests | 全部改写 response phase | POST accepted；poll terminal后才断言 active/cooldown/history/cognition |
| probe chat tests | 拆两阶段 | LLM/classifier在队外；chat reply完成后mutation job仍可processing；poll后断言 |
| `desktop_web_issue_98_e2e` 即时/undoable probe断言 | 改写 | processing不删卡；applied删除；failed恢复 |
| `mobile_web_probe_delight_e2e` 与 view-model probe busy | 补齐 | reload processing、poll applied、failed retry、offline resume |
| `test_mobile_dialogue_confirmation.py`（新增） | 新增 | mobile card/open 同样 accepted→poll→applied/failed，不留同步例外 |
| `extension/tests/dialogue-confirmation.test.ts` optimistic terminal | 改写 | optimistic只到 processing；authority只来自 job result |
| `tests/test_cli.py` probe 现有空白 | 新增 | daemon API path、one-shot owner、failed exit、Ctrl-C durable、输锁不直写 |

`test_api_app.py` 中所有“请求返回即已落库”的 card/probe/anchor断言都必须通过搜索复核：

```bash
rg -n 'status_code == 200|outcome.*applied|payload.*state.*confirmed|user_confirm|user_reject' \
  tests/test_api_app.py tests/test_cli.py
```

逐项判断并改写，不能只处理表中已知名字。

## 9. 每 Wave 独立交付记录模板

每个 Wave 的 PR/commit 说明至少记录：

```text
Wave:
Files:
RED command + expected failure:
GREEN command + result:
Numeric gates:
Mutation inventory delta:
Schema/API compatibility:
Docs updated or N/A:
Config SHA restored: d23161b1...
```

若任一硬门未达到，不以“后续 Wave 会修”宣告该 Wave 完成；唯一例外是 Wave 0 明确登记的 5 个 strict XFAIL，且它们必须在 Wave 3 Gate 前全部转 PASS。
