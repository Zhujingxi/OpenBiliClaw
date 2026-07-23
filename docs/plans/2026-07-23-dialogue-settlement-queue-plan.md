# 对话结算单队列 Implementation Plan

> **Spec:** [`2026-07-23-dialogue-settlement-queue-spec.md`](./2026-07-23-dialogue-settlement-queue-spec.md)
> **Baseline:** `feat/cognitive-profile-pipeline` @ `e16797ec`
> **Review:** adversarial review round 3 — F1–F9 retained；R2-2/R2-3 已闭环；
> REVISE findings R3-1/R3-2 incorporated **[R3-1][R3-2]**
> **Execution:** Wave 0 → 1 → 2 → 3；每个 Task 必须先 RED、再最小实现、最后 GREEN
> **Boundary:** 只实现 spec §2.1–2.2；禁止顺手收口
> force_tick/exploration/pipeline/OpenClaw/CLI。唯一例外是 F5 要求在既有 CLI/OpenClaw
> 构造点显式 pin `legacy_direct` 并补兼容测试；它们仍不接 queue/guard，行为不变。
> R2-1～R2-3 只收紧既有单进程、单 `asyncio` 队列的 admission registry 与 worker
> permit 生命周期，不增加 writer、queue、进程协调层、Wave 或 Task。
> **[R2-1][R2-2][R2-3]**
> R3-1/R3-2 继续严守同一边界：同 ref builder 各自拥有 reservation entry，且在
> 建锚 mutation 返回后、下一次 await/effect 前转正；不引入 coalescer、第二 worker、
> 其他 writer、新 Wave 或新 Task。**[R3-1][R3-2]**

## 1. 开工铁律

### 1.1 仓库与 Python 环境

所有命令从 worktree 根执行，只使用仓库 `.venv`，显式指定源码：

```bash
test "$PWD" = "/Users/white/workspace/OpenBiliClaw/.claude/worktrees/profile-analysis"
test "$(git branch --show-current)" = "feat/cognitive-profile-pipeline"
PYTHONPATH=$PWD/src .venv/bin/python -c \
  'import openbiliclaw; print(openbiliclaw.__file__)'
```

禁止调用系统 `python/pytest/ruff/mypy` 代替 `.venv/bin/*`。

### 1.2 `config.toml` 保护

本 worktree 当前真实配置 SHA-256：

```text
d23161b1e5f49359604c0c6dfe09c204ec8430c12e414cf5b996ccb12134ee3d
```

运行任何可能加载或保存配置的测试前，必须移走并在退出时恢复；不能用 git stash
代替，因为文件被 gitignore：

```bash
CONFIG_STASH="$(mktemp /tmp/openbiliclaw-dialogue-settlement-config.XXXXXX)"
test -f config.toml
test "$(shasum -a 256 config.toml | awk '{print $1}')" = \
  "d23161b1e5f49359604c0c6dfe09c204ec8430c12e414cf5b996ccb12134ee3d"
mv config.toml "$CONFIG_STASH"
restore_dialogue_config() {
  mv "$CONFIG_STASH" config.toml
  test "$(shasum -a 256 config.toml | awk '{print $1}')" = \
    "d23161b1e5f49359604c0c6dfe09c204ec8430c12e414cf5b996ccb12134ee3d"
}
trap restore_dialogue_config EXIT INT TERM
```

测试结束后必须看到原 SHA；不得提交 `config.toml`、临时 DB、log 或 coverage 产物。

### 1.3 TDD、并发与计时规则

每个 Task 固定执行：

1. 只写该 Task 的最小失败测试；
2. 跑列出的 focused command，确认失败来自缺失契约，而不是 import/fixture/错误 timeout；
3. 实现最小生产改动；
4. focused tests GREEN；
5. 跑 Wave gate 后再进入下一 Task。

并发测试使用 `asyncio.Event`、barrier、独立 SQLite connection 和明确 timeout；
不得用裸 `sleep` 猜交错。所有可能 hang 的复现必须有 0.1–5 秒测试上限。

### 1.4 静态质量门

```bash
.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
```

`pyproject.toml` 已启用 MyPy strict；不得通过新增宽泛 `Any`、`type: ignore` 或关闭规则
来过门。

只要 Wave 3 改到 shared/popup/desktop 的 processing 轮询，必须执行：

```bash
(cd extension && npm test && npm run typecheck && npm run build)
```

## 2. 目标文件与不新增的组件

计划中的核心文件：

```text
src/openbiliclaw/soul/dialogue_learn_queue.py       # 原地泛化现有骨架
src/openbiliclaw/soul/dialogue_settlement_guard.py  # actual worker Task-bound permit [F4]
src/openbiliclaw/soul/dialogue.py
src/openbiliclaw/soul/dialogue_anchor.py
src/openbiliclaw/soul/confusion.py
src/openbiliclaw/soul/engine.py
src/openbiliclaw/api/runtime_context.py
src/openbiliclaw/api/app.py
src/openbiliclaw/storage/database.py
src/openbiliclaw/cli.py                             # only explicit legacy_direct pin [F5]
src/openbiliclaw/integrations/openclaw/operations.py # only explicit legacy_direct pin [F5]
```

测试：

```text
tests/test_dialogue_settlement_queue.py
tests/test_dialogue_settlement_guard.py
tests/test_dialogue_learn_queue.py        # 移动/改写既有骨架测试
tests/test_dialogue_anchor.py
tests/test_confusion_lifecycle.py
tests/test_cognition_cycle.py
tests/test_database.py
tests/test_soul_engine.py
tests/test_api_app.py
tests/test_cli.py
tests/test_openclaw_adapter.py
extension/tests/dialogue-confirmation.test.ts
extension/tests/dialogue-confirmation-wiring.test.ts
```

明确**不新增**：

- `settlement_jobs` / durable inbox；
- data-dir owner / `.settlement-writer.lock`；
- coordinator package、scanner、lease、retry scheduler；
- force_tick/exploration/pipeline/OpenClaw queue adapter；
- 通用 job status API。

`src/openbiliclaw/cli.py` 与
`src/openbiliclaw/integrations/openclaw/operations.py` 不属于 cutover 文件；只允许
增加一个显式兼容 mode 参数。禁止把 queue、dispatcher、worker permit 或新 writer
注入这两处，禁止改 command/response contract。**[F5]**

## 3. Wave 0 — 冻结问题、入口与护栏

### Task 0.1 — 把关键交错与旁路变成确定性 RED

**Files**

- Create: `tests/test_dialogue_settlement_queue.py`
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_api_app.py`
- Read only: `src/openbiliclaw/storage/database.py`
- Read only: `src/openbiliclaw/soul/engine.py`
- Read only: `src/openbiliclaw/api/app.py`

**Steps**

1. 写 `test_file_fence_does_not_block_event_loop_while_owner_awaits`。旧实现放到受控
   子进程/线程交错中，heartbeat 有 2 秒总上限；当前代码应复现停顿，测试 RED，
   但不能挂死 pytest。
2. 写 `test_generation_zero_never_captures_future_anchor`：
   - 通过 legacy compatibility input 构造最终会规范化为 typed `absent` tombstone
     的 hypothesis settlement 请求；
   - 在实际 effect 前从非 admission-reservation 路径建立同 ref anchor；
   - 断言结果为 `stale_anchor`、不创建 receipt、新锚未被 release。
   当前 `engine.py:1089-1097` 应令测试 RED。
3. 写
   `test_queued_anchor_reservation_is_visible_to_later_settlement_admission`：**[F2]**
   - barrier 先卡住 worker；
   - submit `anchor.establish(A)`，不等待 completion；
   - 随后 submit `settle.hypothesis(A)`；
   - 捕获两份 immutable envelope 后才放行 worker；
   - 断言 settle 的 admission snapshot 引用 establish reservation，执行使用其实际
     generation；不能是空 ref/0，也不能在 handler 中重读 current。
   再写反向
   `test_no_anchor_tombstone_is_not_upgraded_by_later_establish_admission`：settle 先
   submit、establish 后 submit，前者始终保留 absent tombstone。两例都不用 sleep。
4. 写
   `test_card_discuss_reservation_is_visible_to_later_settlement_admission`：
   barrier 卡住 worker → submit `card.discuss(A)` → submit `settle.hypothesis(A)` →
   检查 immutable envelopes 后放行；settle 必须在 discuss 执行前已冻结
   `reserved(kind="hypothesis", ref=A, producer_kind="card.discuss")`，不能是
   absent/旧 generation，执行时使用 discuss 成功返回的实际 generation。
   另写表驱动
   `test_every_anchor_building_kind_reserves_before_enqueue`，覆盖
   所有 `anchor.establish`（明确含探针抛出、疑惑抛出、durable confusion ensure
   producer）、`card.discuss` 与
   `confusion.attribution.replay(needs_anchor=true)`；
   dispatcher 新增建锚 callsite 而 admission policy 未分类时 RED。两例都不用
   sleep。**[R2-1]**
5. 写
   `test_failed_reservation_advances_head_for_new_submit_and_gc_after_old_dependents_drain`：
   - builder 与两个依赖先入队，barrier 在 builder 失败已回报 registry、旧依赖尚未
     dispatch 的精确位置暂停；
   - 此时新 submit 必须冻结失败后的实际 `persisted/absent`，不得引用 failed entry；
   - 放行后，两个旧依赖均 `anchor_dependency_failed`、effect=0，最后一个引用归零
     立即 GC failed entry；
   - fresh retry 分配不同 reservation id 并可成功。
   测试同时覆盖失败后的 actual state 为 persisted 与 absent，不用 sleep。
   **[R2-2]**
6. 写两组同源 reservation RED：
   - `test_same_ref_double_builder_second_noop_resolves_own_head_for_later_settlement`：
     barrier 卡 worker，按序 submit 同 ref `B1`、`B2`、`settle S1`；断言 B1/B2 的
     reservation id 与 `(job_id, sequence)` owner 都不同，B2 是 head，S1 引用 B2。
     放行后 B1 persisted 只 resolve B1，B2 返回 `already_terminal/no_op` 仍只 resolve
     B2 为同一 persisted generation；随后 `settle S2` 冻结 B2 转正后的 persisted
     head，S1/S2 都完成且无悬空 entry。对 `anchor.establish`、`card.discuss`、重复
     replay builder 表驱动，不允许隐式 coalesce；**[R3-1]**
   - `test_anchor_reservation_promotes_before_followup_await_throw_or_replay_short_circuit`：
     fake 持久化 mutator 返回后，让复合 handler 卡在下一次 await/effect；此时并发
     submit 必须已看到 terminal head。再分别触发后续 throw、already-terminal
     short-circuit 与 duplicate replay no-op，断言 entry 不回退/二次 resolve；参数化
     `persisted/absent/already_terminal/no_op/superseded/failed`，每个 owner entry
     恰好一次离开 `reserved`。不用 sleep。**[R3-1][R3-2]**
7. 写 `test_generation_change_after_analysis_before_effect_writes_nothing`：
   barrier 卡在 post-LLM revalidation 后、首 effect 前，replace 同 ref anchor；
   旧代 event/object/derived/projection 均须为 0。当前实现应暴露窗口。
8. 写 `test_declared_dialogue_entries_submit_without_direct_mutation`，先覆盖 card action、
   legacy、pending-open anchor、普通 chat settle、probe/confusion durable side effect。
   对 queue.submit 和现有 direct mutator 同时放 spy；当前代码应至少出现 card/legacy/
   probe reply 旁路。
9. 每个 RED 都注明对应 spec Q/finding 编号；不永久保留 xfail。Task 分支若必须先提交测试，
   只允许 `xfail(strict=True)`，在对应 GREEN commit 中立即移除。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py \
  -k 'file_fence or future_anchor or anchor_reservation or card_discuss_reservation or anchor_building_kind or failed_reservation or same_ref_double_builder or promotes_before_followup or no_anchor_tombstone or generation_change or declared_dialogue_entries' \
  -q -rxX
```

RED 门：原四类问题、F2 的两个 admission 交错、R2-1 discuss/建锚全集、R2-2
failed-head、R3-1 同 ref owner/no-op 与 R3-2 commit-point 交错都被精确复现；每个
测试单独运行 `<5s`；无无关失败。F2/R2/R3 registry 测试在 Wave 1 转 GREEN，
不等到 endpoint cutover。**[R2-1][R2-2][R3-1][R3-2]**

### Task 0.2 — 固定 entry/mutator inventory 与 worker guard 契约

**Files**

- Create: `src/openbiliclaw/soul/dialogue_settlement_guard.py`
- Create: `tests/test_dialogue_settlement_guard.py`
- Modify: `tests/test_dialogue_settlement_queue.py`
- Read only: `src/openbiliclaw/soul/dialogue_anchor.py`
- Read only: `src/openbiliclaw/soul/confusion.py`
- Read only: `src/openbiliclaw/api/app.py:2743-2906`
- Read only: `src/openbiliclaw/storage/database.py`

**Steps**

1. 在测试中冻结 spec §2.2 `ENTRY_INVENTORY`，每项记录 source symbol、job kind、
   protected mutation、是否同步等待。
2. 冻结 `PROTECTED_MUTATORS`：
   - hypothesis/confusion/speculation 的**对话来源** apply；
   - anchor establish/release/note_relation/expire；
   - confusion schedule/process/retry（仅对话调用面）；
   - card payload transition/projection/reconcile；
   - dialogue confirmation cooldown mutation；
   - probe/confusion durable reply side effect。
3. 实现最小 guard：
   - guard 实例登记实际 worker `asyncio.Task` + lifecycle nonce；
   - 私有 `ContextVar` 只可携带 nonce，不能单独授权；
   - `dialogue_settlement_worker(worker_task)` context manager；
   - `require_dialogue_settlement_worker()`；
   - 明确异常 `DialogueSettlementMutationOutsideWorker`。
   `require_*` 必须同时满足 `asyncio.current_task() is registered_worker_task` 与
   nonce 匹配。加 `test_worker_child_task_cannot_inherit_mutation_permit`：worker
   context 内直接调用成功，`asyncio.create_task(protected_mutator())` 的 child
   继承 ContextVar 后仍抛异常，worker 退出后直接调用也失败。**[F4]**
4. 加
   `test_old_worker_finally_cannot_clear_new_worker_permit_after_reload_handoff`：
   用 event 把 old task 卡在 `finally` 的 compare-and-clear 前；handoff 先以 old
   `(task, nonce)` 精确 revoke，再注册 new tuple。断言顺序固定为
   `new 已注册 → old finally 注销尝试 → new mutation 仍获授权`，并在 revoke 后、
   finally 前后都断言 old mutation 无授权。禁止 sleep，旧 `finally` 无条件 clear
   时测试必须 RED。**[R2-3]**
5. 先让 runtime guard 测试 RED：最终生产 runtime 组装出的受保护 façade 在 worker
   外调用必须 100% 抛异常。Wave 0 只实现 guard primitive、冻结 expected failures，
   **不提前把 guard 装到仍走旧入口的 runtime mutator**；安装点随 Wave 3 cutover
   一次转 GREEN，避免中间分支把现有 endpoint 全部打断。
   独立 low-level model/DAO 单测可显式注入 no-op guard，但 runtime 组装测试必须证明
   最终真实实例安装了 guard。
6. 加 AST/call-site 审计。只允许统一 dispatcher 与 worker 内 `_apply_*` 调用 protected
   façade；API endpoint、`SocraticDialogue`、12h hook 不在 allowlist。
   Out-of-scope avoidance/force_tick 符号必须显式排除，不能误报后把它们拉入改造。
7. 为 pending-open 单列 `RAW_SINK_INVENTORY`：**[F3]**
   - `ConfusionManager.schedule_ask`；
   - `Database.update_confusion` 的 ask-turn retarget 与 create-failure rollback；
   - `DialogueAnchorManager.establish`。
   记录 `api/app.py:2762-2782,2879-2891` 三个现有 direct callsite。最终 runtime
   spy 必须在每次调用时捕获 current task/permit；§2.2 pending-open 流程的 worker
   外 raw sink 调用数=0。通用 confusion TTL/独立 DAO 单测留在边界外，不得为追求
   全局 0 而迁入队列。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_guard.py \
  tests/test_dialogue_settlement_queue.py \
  -k 'inventory or guard or protected' -q
```

数值门：spec §2.2 entry 覆盖率 100%，`unclassified=[]`；guard primitive 的
10 个代表性 permit 外调用全部抛异常；worker child mutation 1/1 抛异常；
`RAW_SINK_INVENTORY` 三类无遗漏；production wiring 的 strict XFAIL 数量与未
cutover 入口清单相等；reload handoff 中 old revoke 后授权成功次数=0、new 在 old
finally 前后授权成功次数=2；out-of-scope 清单没有 queue/guard 改造要求。
**[R2-3]**

### Wave 0 Gate

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_dialogue_settlement_guard.py -q -rxX
```

保留的 strict XFAIL 只能是 Task 0.1 列明的原始 repro、F2/R2-1/R2-2 admission
repro、R3-1 same-ref owner/no-op、R3-2 commit-point repro、R2-3 reload cleanup repro
与 `ENTRY_INVENTORY` 中尚未 cutover 的 production-wiring case；各组数量都必须与
清单精确相等，不得出现 unclassified XFAIL/XPASS。
**[R2-1][R2-2][R2-3][R3-1][R3-2]**

## 4. Wave 1 — 泛化为唯一内存队列

### Task 1.1 — typed envelope、admission timeline、单 consumer 与 Task-bound permit

**Files**

- Modify: `src/openbiliclaw/soul/dialogue_learn_queue.py`（原地泛化，不为命名搬文件）
- Modify: `tests/test_dialogue_learn_queue.py`（保留既有 lifecycle/对话 wiring 回归）
- Modify: `tests/test_dialogue_settlement_queue.py`（扩充 typed queue 核心测试）
- Modify: `src/openbiliclaw/soul/dialogue_settlement_guard.py`

**Steps**

1. 先写失败测试：
   - 100 个 concurrent submit，严格 sequence、`max_active=1`；
   - fire-and-forget `learn` 与 request/response action 共用同一 queue；
   - handler 成功/异常都完成 Future，异常不杀 worker；
   - payload 入队后修改原 dict 不影响 worker；
   - worker 内调用 `submit_and_wait` 在 100 ms 内抛
     `DialogueSettlementReentryError`，后续 job 仍执行；
   - worker 内 direct protected mutation 成功；handler 中 `create_task()` child
     调同一 mutator 必须失败，且不杀 worker；**[F4]**
   - establish-reservation → later settle、card.discuss-reservation → later settle
     与 absent-tombstone → later establish 三个 barrier 交错按 Task 0.1 转 GREEN；
     表驱动建锚全集无漏项；**[F2][R2-1]**
   - 同 ref 双 builder 各有 owner-bound entry，第二个 `already_terminal/no_op` 也精确
     resolve 自己，夹在第二个执行前后的 settle 确定引用第二条 reserved/resolved
     head；六类 terminal 与 mutation-return commit-point barrier 按 Task 0.1 转 GREEN；
     **[R3-1][R3-2]**
   - builder 失败后 head 前移、new submit 不引用 failed、旧依赖排空后 GC、fresh
     retry 新 id 成功；旧依赖返回 `anchor_dependency_failed`、effect=0；
     **[R2-2]**
   - permit handoff 先 revoke old、再 register new；old `finally` 的
     compare-and-clear 不影响 new，old 撤权后始终失败；**[R2-3]**
   - pause/resume/pause_and_drain/shutdown 与 lazy start 保持现有语义。
2. 定义 typed `DialogueJobKind` 白名单：
   `learn`、`settle.hypothesis`、`settle.confusion`、`card.defer`、
   `card.discuss`、`card.reconcile`、`anchor.establish`、
   `probe.reply.apply`、`confusion.reply.apply`、
   `confusion.attribution.replay`、`confusion.open.sync`。
   后两者必须是枚举中的独立成员；dispatcher 若遗漏任一分支，exhaustiveness
   测试失败，不能用 `learn`/`confusion.reply.apply` 代跑。**[F1][F3]**
3. 定义 immutable envelope/result；`asyncio.Queue` 中只放 envelope，不放 callable。
   `DialogueJobResult` 为 probe 预留 typed
   `classification/classifier/resulting_action/exploration_intent` 字段，但此 Task
   不执行 exploration。envelope 另带由 `kind + immutable payload` 得出的 typed
   `anchor_transition`；dispatcher 不得在 admission policy 之外自行决定建锚。
   **[F8][R2-1]**
4. 在 queue 内实现唯一 `AnchorAdmissionRegistry`：
   **[F2][R2-1][R2-2][R3-1][R3-2]**
   - logical-state union 明确为 `persisted`、`reserved`、`failed`、`absent`、
     `not_applicable`；`failed` 不是旁挂 boolean；**[R2-2]**
   - 每次 builder admission 都创建不同 `reservation_id`，owner 固定为 envelope 的
     `(job_id, sequence)`；同 ref 不 coalesce，后发 entry 成为 head。实现
     `resolve_owned(ref, reservation_id, owner_job_id, owner_sequence, terminal)`（名称可
     等价）的单次 CAS，非 owner、二次 resolve、跨 entry resolve 全部 fail closed；
     **[R3-1]**
   - 建立 exhaustive `ANCHOR_TRANSITION_POLICY`（名称可等价）：直接
     `anchor.establish`（producer source 明确覆盖探针抛出、疑惑抛出、durable
     confusion ensure）、inline `card.discuss` 与
     `confusion.attribution.replay(needs_anchor=true)` 都在 submit 时先登记 opaque
     reservation，再入队；`confusion.open.sync` 禁止 inline 建锚，成功后必须提交
     前述 `anchor.establish`。任何 `may_establish` dispatcher 分支无 reservation
     时 fail closed；**[R2-1]**
   - 定义 typed `AnchorMutationTerminal`，完整覆盖
     `persisted/absent/already_terminal/no_op/superseded/failed`；no-op/terminal 必须
     带 authoritative `persisted/absent` post-state。本 Task 用 fake establish handler
     证明 mutator 返回后立即同步 `resolve_owned`，而不是等整个 handler completion；
     生产 handler 在 Task 2.2/3.1/3.2 接线；**[R3-1][R3-2]**
   - typed failed terminal 在同一无-await transition 中把 entry resolve 成
     `failed(reservation_id, cause)`，并在 head 仍指它时把 head 前移到 mutation 后
     实际 `persisted/absent`；新 submit 从该实际 head 冻结，禁止再引用 failed；
     **[R2-2]**
   - 每个 terminal 先 resolve owner entry；仅当 head 仍指该 id 时才把 head 折叠为
     effective actual state。若 logical head 已有更晚 reservation，较早 result 只
     resolve 自己，不能覆盖 head；`superseded` 依赖只解包 authoritative actual state
     并走既有 exact validation，失配沿用 `stale_anchor`；**[R3-1]**
   - no-anchor 保存 target kind/ref + tombstone epoch，不与 not-applicable 共用
     `("", 0)`；
   - reservation refcount 只包含已经冻结该 id 的 queued/running job；terminal entry
     在旧引用归零后 GC，`failed/superseded` 后不增新引用；retry 必须分配 fresh id；
     shutdown 全丢。**[R2-2][R3-1]**
5. `submit()` 在一个**不含 await**的 admission critical section 内：
   - 分配单调 sequence；
   - 复制 payload；
   - 先按 exhaustive policy 为每个建锚 transition 创建 owner-bound reservation，
     再从 registry 读取 logical anchor（包括尚未执行的 latest reservation），而不是
     只读 anchor provider；同 ref 也不能复用前一 builder 的 entry；
     **[R2-1][R3-1]**
   - 冻结 typed snapshot；
   - `put_nowait` 到 unbounded queue；
   - 可选创建 completion Future。
   并发 submit 的 snapshot/sequence/入队必须形成同一个 total order。
6. `_run()` 是唯一向 guard 登记 actual worker Task + lifecycle nonce 的地方；
   typed dispatcher 外不暴露 handler，ContextVar 不能作为身份替代。worker
   `finally` 只调用 `clear_if_current(task, nonce)`，不得无条件 clear；handoff API
   用精确 tuple revoke。**[F4][R2-3]**
7. 保留 `_QUEUE_DEPTH_WARN=10`，新增 structured log：
   `kind/sequence/depth/queue_wait_ms/run_ms/outcome`。不新增 durable metrics store。
8. dispatcher 的 builder wrapper 固定为
   `terminal = await anchor_mutator(...)` 后立即同步 `resolve_owned(terminal)`；两句之间
   以及 resolver 与其后的第一处 await/effect 之间不得插入业务 effect、release、
   completion 或 observer。mutator failure 分支也先同步 resolve failed/head，再传播；
   resolver 后的 handler throw 只失败 Future，不得二次 resolve。**[R3-2]**

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py -q
```

数值门：至少 24 个 queue 用例；100 mixed jobs 的 `max_active=1`、sequence 无缺口；
F2 anchor/absent 与 R2-1 discuss 三个交错各循环 100 次且 snapshot 错配=0；
建锚 policy `unclassified=[]`；failed reservation 100 次均 new-ref=0、旧依赖
effect=0、归零 GC=1、retry fresh-id=1；同 ref 双 builder 100 次 owner/id 冲突=0、
第二个 no-op 悬空=0、later-settle wrong-head=0；六类 terminal owner-resolve 恰好一次，
commit-point barrier 看到 reserved=0；较早 resolution 覆盖较晚 reservation=0；
child-task guard 绕过=0；old finally 清除 new permit=0；异常后至少 2 个后续 job
成功；reentry 测试 `<0.1s`。**[R2-1][R2-2][R2-3][R3-1][R3-2]**

### Task 1.2 — runtime 只装一个 dispatcher，拆分 API 与 legacy-direct 兼容模式 **[F5]**

**Files**

- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify (compatibility pin only): `src/openbiliclaw/cli.py:971`
- Modify (compatibility pin only):
  `src/openbiliclaw/integrations/openclaw/operations.py:300-306`
- Modify: `tests/test_dialogue_settlement_queue.py`
- Modify: `tests/test_soul_dialogue.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_openclaw_adapter.py`

**Steps**

1. 先写失败测试：
   - runtime 只构造一个 `DialogueSettlementQueue`；
   - API/runtime mode 的 `SocraticDialogue.respond` 只 submit `learn`，queue
     缺失时显式报配置错误，不 `create_task(learn_from_dialogue)`；
   - 显式 reply-only test-double mode 不学习；
   - CLI 与 OpenClaw 的显式 `legacy_direct` mode 各完成 reply 后仍调用既有
     `learn_from_dialogue` 恰 1 次、queue submit=0；测试必须等待/观察 learning
     call，不能只断言 reply 文本。具名新增
     `test_cli_dialogue_legacy_direct_mode_still_learns_without_queue` 与
     `test_openclaw_chat_legacy_direct_mode_still_learns_without_queue`；
   - hot reload 先 pause/drain old，精确撤销 old `(task, nonce)` 后才
     start/register/swap new；barrier 让 old 卡在迟到 `finally`，new 注册后 old
     cleanup 不得清除 new permit，old 撤权后不得 mutation；**[R2-3]**
   - rebuild 失败恢复 old queue；任何时刻 active worker 数 ≤1；
   - shutdown drain 后停止，Future 不悬空。
2. 把 `_build_dialogue_learn_handler` 改为 typed dispatcher builder；dispatcher 依赖
   `SoulEngine`、anchor manager 与 API side-effect façade，但不引用 HTTP Request。
3. runtime attribute 统一为 `dialogue_settlement_queue`。一次性迁移所有生产调用；
   不保留一个可独立运行的旧 `dialogue_learn_queue` alias。
4. `SocraticDialogue` 的无 queue 行为必须由显式 mode 决定，不再从 `None` 猜：
   - `queued`：正常 API runtime；queue 缺失即配置错误；
   - `reply_only_test`（或等价仅测试注入）：明确不学习；
   - `legacy_direct`：只允许 CLI/OpenClaw 两个生产构造点，执行 baseline 的
     direct learning；可保留其既有 detached 调度语义，但 mode 名、日志与测试必须
     明确，不能静默回退。
5. `cli.py:971` 与 OpenClaw `operations.py:300-306` 只增加
   `mode=legacy_direct`（或强类型等价参数），不得注入 queue、guard、API client，
   不改命令/adapter response。AST allowlist 要求生产 `legacy_direct` callsite
   **恰好 2 个**，新增第三处即失败。**[F5]**
6. API runtime 组装必须显式选择 `queued`；测试构造若省略 mode，要么注入 queue，
   要么显式选 test mode，不能靠宽松默认掩盖。
7. 热重载仍使用现有 30 s drain contract，不改成 stable coordinator，不增加 owner。
   成功 handoff 顺序固定为 `pause old → drain old → revoke_exact(old tuple) →
   register/start new → publish new`；old `finally` 只能
   `clear_if_current(old tuple)`。若 new 构造/注册失败，只能在 permit 单槽为空时以
   **新 nonce** 恢复已 drain 的 old queue，不能复活已撤销 nonce。**[R2-3]**

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_soul_dialogue.py \
  tests/test_cli.py \
  tests/test_openclaw_adapter.py \
  -k 'runtime or reload or shutdown or respond or legacy_direct' -q
```

数值门：10 次成功 reload 和 10 次失败 rollback 中 `max_active_workers=1`、
`max_authorized_workers=1`；每次 new 注册后触发 old finally，new mutation
成功=1、old mutation 成功=0；**[R2-3]**
API queued mode 的 direct learn=0；CLI/OpenClaw 各 direct learn=1、queue submit=0；
production `legacy_direct` callsite=2；旧 `DialogueLearnQueue` 名称命中 0。不得用
“CLI/OpenClaw 只回复”让测试变绿。

### Task 1.3 — LLM 在线内串行，先不拆 analyze/apply

**Files**

- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `tests/test_dialogue_settlement_queue.py`
- Modify: `tests/test_soul_engine.py`

**Steps**

1. 先写失败测试：两个 learn job 的 fake LLM 同时提交，LLM `max_active=1`；
   第一项 await fake LLM 时 event-loop heartbeat 仍至少运行 20 次/秒。
2. dispatcher 的 `learn` 分支在现有 `_background_admission_bypass` 中直接 await
   `learn_from_dialogue`；不得交给 task registry 或 detached task。
3. 沿用 provider 的有限 timeout；不在整段 `learn_from_dialogue` 外加会在本地
   mutation 中途取消的 timeout。
4. 记录 queue wait/run duration；测试可注入 slow handler，不读取真实 provider。
5. 明确不抽 read-only analyze DTO、不新增 snapshot digest/CAS。把生产阈值
   `202 ratio >1%` / p95 `>5s` 写入模块文档 TODO，而不是预埋第二队列。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_soul_engine.py \
  -k 'llm or heartbeat or serial' -q
```

数值门：2 个 blocked dialogue LLM job 的 `max_active=1`；heartbeat 在 0.5 s
窗口内 `>=10`；本 queue 对 force_tick/exploration/OpenClaw dispatcher 的调用数
全部为 0。runtime-wide provider gate 只作为共享资源上限，不成为对话 settlement
writer/调度队列。

### Wave 1 Gate

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_dialogue_settlement_guard.py \
  tests/test_soul_dialogue.py \
  tests/test_cli.py \
  tests/test_openclaw_adapter.py \
  -k 'dialogue or queue or guard or legacy_direct or chat_delegates' -q
```

所有 Wave 0 queue/lifecycle XFAIL（含 R2-1 全建锚预约、R2-2 failed head/GC、
R3-1 same-ref owner/no-op、R3-2 commit-point、R2-3 reload permit handoff）在本
Wave 移除；尚未 cutover 的 endpoint repro 可以继续 strict XFAIL。CLI/OpenClaw
compatibility tests 必须 GREEN，且不要求二者接入 queue。
**[F5][R2-1][R2-2][R2-3][R3-1][R3-2]**

## 5. Wave 2 — 删除旧锁栈，保留轻量 receipt/effect 幂等

### Task 2.1 — 简化 `card_settlements` schema 与迁移

**Files**

- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `tests/test_database.py`

**Steps**

1. 先写三种 migration RED：
   - 最早 `ref/verdict/turn_id/applied` 表；
   - 当前 `payload + apply_claim_* + seg_*` 表；
   - fresh schema + double initialize。
   具名改写 `tests/test_database.py:90`
   `test_card_settlement_schema_migrates_wave_a_table`：保留“最早 schema 数据不丢”
   的业务意图，但新断言是 winner ref/verdict/turn_id/applied/payload/event identity
   正确，且 fresh `PRAGMA table_info` **不存在**
   `apply_claim_*`/`seg_*`；不能继续断言这些旧列默认值。**[F9]**
2. 用单一具名 private migration routine 做 SQLite table rebuild，得到 spec §7.1
   的简化表。最终 schema/runtime 不含
   `apply_claim_at`、`apply_claim_token`、`seg_event`、`seg_object`、
   `seg_marker`；event 恢复改用单一稳定 `event_id`（或等价字段），不承担 claim。
3. 迁移语义：
   - applied receipt 保持 terminal；
   - unapplied winner payload/verdict/turn_id 保持；
   - `seg_event=1` 映射到最小 event-recorded 字段/关联，避免重复 event；
   - 不根据旧 lease 时间接管，不把 contender payload 覆盖 winner。
4. 删除：
   - `_CARD_SETTLEMENT_LOCKS` / `_card_settlement_process_lock`；
   - `_card_settlement_lock_path`；
   - settlement 对 `exclusive_file_lock` 的 import/调用；
   - `claim_card_settlement`、`_card_settlement_fence`、
     `card_settlement_claim_guard`、`mark_card_settlement_segment`。
5. 实现 worker 使用的最小 DAO：
   - `try_create_card_settlement` / `get_card_settlement`；
   - `record_card_settlement_event_once`（event + receipt 同事务）；
   - `complete_card_settlement`（无 token）；
   - 既有 projection。
   其他 effect 直接使用各目标存储已有/补齐的 deterministic set/upsert，不新增
   通用 effect table/helper。
6. event/ledger stable key 的长度、字符集、ref 构造要有 validation；不能接受调用方
   任意 SQL column/name。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_database.py \
  -k 'card_settlement or card_projection or profile_ledger' -q
```

数值门：fresh + 2 legacy shapes + double-init 至少 8 用例；50-way 同 ref
`INSERT OR IGNORE` 只有 1 winner；event/effect helper 重放 10 次仍各 1 行。
点名的 `test_card_settlement_schema_migrates_wave_a_table` 必须仍存在（可重命名为
`test_card_settlement_schema_rebuilds_wave_a_table_without_claim_columns` 并在映射表
记录），不得以删除测试代替 migration 证明。**[F9]**

静态门：

```bash
test "$(rg -n \
  'claim_card_settlement|card_settlement_claim_guard' \
  src/openbiliclaw tests | wc -l | tr -d ' ')" = "0"
test "$(rg -n \
  'apply_claim_token|apply_claim_at|seg_event|seg_object|seg_marker' \
  src/openbiliclaw --glob '!storage/database.py' | wc -l | tr -d ' ')" = "0"
PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_database.py \
  -k 'legacy_card_settlement_columns_are_migration_only' -q
```

AST 测试必须证明 `database.py` 中旧列名只出现在具名 private migration routine，
其余只可见于 legacy fixture/测试输入；fresh schema 与 runtime DAO 不得读取。

### Task 2.2 — worker-only settlement apply，删除 future-anchor 推断

**Files**

- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/soul/dialogue_anchor.py`
- Modify: `src/openbiliclaw/soul/confusion.py`
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_dialogue_anchor.py`
- Modify: `tests/test_confusion_lifecycle.py`

**Steps**

1. 先写失败测试：
   - public/worker façade 在 permit 外调用抛 guard；
   - worker handler 内 `create_task()` child 调 protected façade 仍抛 guard；
   - worker 内 hypothesis/confusion/普通 chat nested settle 不增加 queue depth；
   - absent tombstone 执行前出现非预约未来锚，receipt/新锚保持 0 副作用；
   - 已入队 `anchor.establish` 与 `card.discuss` reservation 都被后续 settle
     admission 捕获并 resolve；探针/疑惑抛出/补建锚的表驱动 producer 同样无漏项；
     **[F2][R2-1]**
   - 同 ref 两个 builder 各自持有/resolve 不同 entry；第二个 no-op 后其前后 settle
     都稳定使用第二条 head；mutator 返回后卡在复合 handler 的下一 await，再 submit
     时 head 已转正，后续 throw/replay short-circuit 不回退；**[R3-1][R3-2]**
   - builder 失败时旧依赖不回退为 absent/current；失败后的新 submit 冻结实际
     persisted/absent，旧依赖排空后 failed entry GC，fresh retry 成功；
     **[R2-2]**
   - 旧 generation 被 replace 后，anchor/object/profile 均 0 副作用。
2. 删除 `settle_hypothesis:1089-1097` 的 current anchor 推断，参数缺失默认 0，
   不允许 `None` 表示“稍后推断”。内部兼容参数的 0 只能在 producer 被迁移前作为
   typed `absent(target_kind, target_ref, tombstone_epoch)` 输入，最终 handler 只接收
   `AnchorAdmissionSnapshot`；worker 解析 owner 已 resolve 的 `reserved`、校验
   `persisted/absent`，
   不读取 current 来改变 snapshot。非预约未来锚返回 `stale_anchor`；failed
   reservation 返回 `anchor_dependency_failed`，两者全部业务 effect=0。**[F2]**
   failed resolve 只面向失败前已受理依赖；新 submit 不得拿到 failed snapshot，
   registry head 前移/GC 契约沿用 Task 1.1。**[R2-2]**
   `superseded` dependency 解包 authoritative state 后走既有 exact validation，失配
   返回 `stale_anchor` 且 effect=0；
   `already_terminal/no_op` 必须解包 mutator 返回的 authoritative state，不能执行期
   再读 current。**[R3-1]**
3. 把 `_settle_dialogue_object` 改为 worker-only `_apply_dialogue_settlement`：
   - 先读 existing receipt；存在时 contender 总是采用 stored winner payload；
   - 在任何新 receipt/effect 前校验 authoritative frozen generation；
   - 校验通过后才创建缺失的 winner receipt；`stale_anchor` 不占 ref；
   - 按 event → object/derived → rebuild marker → receipt applied →
     projection → anchor publication 的固定顺序执行；
   - applied receipt 的 retry/reconcile 总是幂等补跑 projection/anchor，因此
     `applied=1` 后崩溃也可由显式 retry 补齐；
   - 无 claim、lease、fence、`asyncio.to_thread`。
4. 普通 chat `_process_dialogue_settles` 与
   `_process_dialogue_anchor_decision` 只调用内部 apply；禁止 submit 自己。
5. anchor manager runtime 实例安装 actual-Task-bound worker guard。read-only
   `current/snapshot/validate_snapshot` 不要求 permit；mutation 要求 permit。
   `ContextVar` nonce 匹配但 current task 不是已登记 worker 也必须失败。**[F4]**
6. card discuss 不再使用 attempt token/5min fencing：
   admission 先为 `card.discuss` 登记 reservation，worker 内再执行
   pending→discussing、establish。`establish` 持久化 mutation 返回后必须立即同步
   resolve 该 job 自己的 entry，随后才能 await/release/完成 card effect；重复 discuss
   的 `already_terminal/no_op` 也 resolve 自己为 authoritative persisted/absent。
   mutator 自身失败的异常补偿先 resolve failed 并推进 head；转正后的后续异常不得
   二次 resolve failed；orphan reconcile 立即回 pending。
   **[R2-1][R2-2][R3-1][R3-2]**
7. confusion 的 dialogue `schedule_ask/process_anchor_settlement/retry` 通过 guarded
   façade 调用；通用 TTL 等 out-of-scope 路径不被错误迁入队列。
8. 所有建锚 façade 返回统一 typed terminal；wrapper 严格执行
   `terminal = await persist_anchor_mutation(...)` → 无 await/effect 的
   `resolve_owned(terminal)`。用于日志、card transition、replay receipt、Future
   completion 的代码都排在 resolver 之后。**[R3-2]**

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_soul_engine.py \
  tests/test_dialogue_anchor.py \
  tests/test_confusion_lifecycle.py \
  tests/test_dialogue_settlement_guard.py \
  -k 'settle or anchor or generation or guard or discussion' -q
```

数值门：F2 establish/tombstone 与 R2-1 card.discuss 三个交错各循环 100 次，
snapshot 错配与错误 mutation=0；建锚 policy 漏项=0；failed reservation
old-effect=0、new-reference=0、GC/retry 各成功 100 次；worker nested settle queue
depth 增量=0；同 ref 双 builder 第二个 no-op 后 dangling=0、wrong-head=0；六类
terminal owner-resolve-once=100%，commit-point 后 reserved 可见次数=0、later throw
demotion=0；permit 外 10 个 mutation与 inherited-context child mutation全抛。
**[F2][F4][R2-1][R2-2][R3-1][R3-2]**

### Task 2.3 — 七个 crash gap、applied boundary 与幂等重试

**Files**

- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_api_app.py`

**Steps**

1. 参数化故障点：
   `after_event`、`after_object`、`after_derived`、`after_rebuild_marker`、
   `after_applied_before_projection`、`after_projection`、`after_anchor_release`。
   `after_applied_before_projection` 必须位于 durable `applied=1` commit 已完成、
   任一卡片 projection 尚未调用的精确边界。**[F7]**
2. 每个故障第一次执行必须留下可解释的 unapplied receipt 或已应用终态；不得写
   lease/timestamp 让测试“等 5 分钟”。
3. 以相同 ref 重试：
   - 使用原 winner payload，不用 contender payload；
   - event=1；
   - object terminal mutation=1 个语义结果；
   - derived 每 content hash=1；
   - rebuild trigger 集合不重复且不刷新原 set_at；
   - 每 session card 同终态；
   - frozen generation 的 anchor 至多 release 1 次。
   对已经 `applied=1`、但 projection/anchor 未完成的故障点，也必须走 publication
   replay，不得因 terminal receipt 直接 early-return。
4. 专门写
   `test_get_reconcile_projects_applied_receipt_without_reapplying_object_semantics`：
   在 `after_applied_before_projection` 退出后，断言 object/derived/rebuild 已各 1、
   card 仍 pending；随后调用既有 GET，让 GET **只 submit**
   `card.reconcile` 且 request direct write=0。等待 queue drain 后第二次 GET 看到
   跨 session card 终态一致，object/derived/rebuild 调用计数仍各 1。**[F7]**
5. 具名改写 `tests/test_soul_engine.py:3257`
   `test_rebuild_marker_write_failure_blocks_settlement_publication_and_cleans_tmp`：
   保留 marker 写失败会阻止 `applied/projection`、清理 `.tmp`、同 ref 显式 retry
   最终完成的断言；删除 `seg_*` 与手改 `apply_claim_at`，改断言 stable event/object
   幂等计数和 winner payload 不变。**[F9]**
6. audit ledger 故障继续不阻止业务 `applied=1`；恢复后相同 stable key 不重复。
7. 明确不写“restart 自动扫描恢复”测试。重建 runtime 后必须由显式 retry 才继续，
   以证明没有偷偷实现 durable inbox。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_database.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py \
  -k 'crash_gap or idempotent_effect or receipt_retry or ledger_failure' -q
```

数值门：7 个故障点 × confirm/revise/confusion 代表路径至少 11 个用例；
每个 mandatory effect 最终计数恰为 1；applied gap 的 GET reconcile 后对象语义
计数仍为 1；点名 rebuild-marker 旧测试业务意图完整保留；无 sleep/lease clock
manipulation。**[F7][F9]**

### Wave 2 Gate

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_database.py \
  tests/test_soul_engine.py \
  tests/test_dialogue_anchor.py \
  tests/test_confusion_lifecycle.py \
  tests/test_dialogue_settlement_queue.py \
  tests/test_dialogue_settlement_guard.py -q

test "$(rg -n \
  'claim_card_settlement|card_settlement_claim_guard' \
  src/openbiliclaw tests | wc -l | tr -d ' ')" = "0"
test "$(rg -n \
  'apply_claim_token|apply_claim_at|seg_event|seg_object|seg_marker' \
  src/openbiliclaw --glob '!storage/database.py' | wc -l | tr -d ' ')" = "0"
PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_database.py \
  -k 'legacy_card_settlement_columns_are_migration_only' -q
```

Wave 0 的 deadlock/future-anchor repro 必须在此转 GREEN；F2 establish/tombstone、
R2-1 discuss/全建锚 policy 与 R2-2 failed head/GC/retry 交错已在 Wave 1 GREEN
并继续回归；R3-1 双 builder owner/no-op 与 R3-2 mutation-return commit-point 在本
Wave 的真实 anchor façade 上 GREEN；F7 的 applied-gap GET reconcile 在本 Wave
GREEN。**[R2-1][R2-2][R3-1][R3-2]**

## 6. Wave 3 — 全入口 cutover、按需 202、护栏与交付

### Task 3.1 — cards、pending-open、reconcile 与 legacy 全部 submit

**Files**

- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/soul/confusion.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_dialogue_settlement_guard.py`

**Steps**

1. 先写 endpoint RED：
   - card confirm/reject/defer/discuss 都观察到一个 typed submit；
   - direct engine/anchor/card-state spy 为 0；
   - `card.discuss` submit 的 reservation 在 queue put 前可见；用 barrier 固定
     discuss 入队、settle 随后入队、worker 尚未执行的顺序，后者必须冻结
     reserved 而非 absent/旧锚；**[R2-1]**
   - 同 ref 两次 discuss/establish 都分配独立 owner entry；第二个执行时即使 card/锚
     已 terminal 而 no-op，也先 resolve 自己，再让其后 settle 读取第二条 resolved
     head；第一条 resolution 不得覆盖第二条 head；**[R3-1]**
   - pending-open confusion 的 schedule、retarget、turn-create failure rollback 都提交
     `confusion.open.sync`，raw `schedule_ask`/`Database.update_confusion` spy 只在
     worker task 内触发。具名
     `test_pending_open_confusion_schedule_retarget_rollback_only_in_worker`；**[F3]**
   - pending-open 探针/疑惑抛出与 durable confusion question 的 anchor 建立经
     exhaustive admission helper，reservation 都先于 queue put；**[R2-1]**
   - GET card reconcile 只 submit 或纯读，不直接 mutation；
   - legacy submit 的 source=`legacy_endpoint`，deprecated headers 不变。
2. card/legacy 使用 `asyncio.shield(completion)` 等待 1.0 s：
   - 完成返回现有 200 body；
   - 超时不 cancel job，返回 202 processing；
   - worker 异常不得被包装成 200；
   - legacy 保持 `InsightFeedbackResponse` 与 deprecation headers，不增加
     outcome/job 字段。
3. confirm/reject 的 endpoint task 连 receipt 也不得预留；只构造 immutable
   envelope。worker 按 queue sequence 创建/读取 winner receipt，并完成对象、
   event、card payload 与 anchor 处理。
4. defer/discuss 依赖 `turn_id + action + card state` 幂等；`card.discuss` admission
   为每个 job 创建 owner-bound reservation，不能等 handler 运行才补记，也不能与
   同 ref 前一 job coalesce。两个重复 queued job 串行后，第二个返回 already
   terminal/no-op，不延长 cooldown、不重建锚，但必须用 authoritative anchor state
   resolve 自己的 entry；后续 settle 引用第二条 head。建锚 mutator 返回后先同步
   转正，才能继续 cooldown/card effect 或 return；其后异常不回退。mutator 自身失败
   的补偿按 R2-2 先 resolve failed/head 前移，不让后续 submit 粘住失败 entry。
   **[R2-1][R2-2][R3-1][R3-2]**
5. 实现 `confusion.open.sync` 的唯一 dispatcher handler：**[F3]**
   - `operation=schedule` 调 guarded `ConfusionManager.schedule_ask`；
   - turn 创建异常后 endpoint 只可 submit+await `operation=rollback`；
   - SQLite 去重返回不同 canonical turn 时只可 submit+await
     `operation=retarget`；
   - operation 白名单外立即拒绝；
   - 三条分支复用同一 handler/guard，不各造 command kind。
   endpoint 的 `_prepare_confusion_confirmation` 不再保留 direct fallback；原始
   `Database.update_confusion` 只能从 handler 调用链出现。
6. pending-open 的 turn 创建沿用 `(ref, session)` SQLite 去重。探针/疑惑抛出凡会
   建锚的分支，必须调用同一个无-await
   `reserve_then_put_nowait(anchor.establish)` helper；reservation 成为 logical
   head 后才允许 producer 提交后续 learn/settle。产品路径仍 await completion 后再
   生成需归属该锚的 reply；同时保留 F2/R2-1 确定性测试，证明 correctness 不靠
   “大家都记得 await”。direct interest/avoidance probe button 不改。
   **[F2][R2-1]**

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_api_app.py \
  tests/test_dialogue_settlement_guard.py \
  -k 'DialogueConfirmationCards or pending_confirmation or legacy_feedback or reconcile' -q
```

数值门：入口 spy 覆盖 card 4 + open + reconcile + legacy 共 7 类；
pending-open schedule/retarget/rollback 三分支各至少 1 例；§2.2 流程 worker 外
raw confusion/anchor sink=0；card.discuss→settle barrier 100 次 reserved=100、
absent/旧锚=0；同 ref 双 discuss 第二个 no-op owner-resolve=100、dangling/wrong-head=0；
mutation-return 后下一 effect 前 reserved 可见=0；探针/疑惑抛出建锚 policy 漏项=0；
空队列 100 次 action 全为 200；每次本地完成 `<1s`。
**[F3][R2-1][R3-1][R3-2]**

### Task 3.2 — chat/anchor cutover、typed replay 与 probe→exploration handoff

**Files**

- Modify: `src/openbiliclaw/soul/dialogue.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/soul/cognition_cycle.py`
- Modify: `tests/test_dialogue_context.py`
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_confusion_lifecycle.py`
- Modify: `tests/test_cognition_cycle.py`

**Steps**

1. 先写 RED：
   - plain chat settles hypothesis/confusion/speculation while queue
     `max_active=1`，不 nested submit；
   - anchor support/contradict/revise/answer/ambiguous/unrelated 全在 worker；
   - `scope=probe` durable reply 的 interest side effect submit 后才 mutation；
   - weak-positive classification 在 worker 恰 1 次，completion result 携带
     exploration intent；exploration helper 在原 producer task、无 permit 时调用
     1 次，worker/child 内调用 0。具名
     `test_probe_result_handoff_runs_exploration_outside_worker_once`；**[F8]**
   - `scope=confusion` visible cognition/attribution submit 后才 mutation；
   - 12h confusion replay hook 只 submit
     `confusion.attribution.replay`；dispatcher 观察到专属 handler，`learn` 与
     `confusion.reply.apply` handler 调用数均为 0。具名
     `test_confusion_attribution_replay_dispatches_dedicated_kind`；**[F1]**
   - replay payload 表明缺锚、其 handler 将补建锚时，reservation 必须在 replay
     envelope 入队前成为 logical head；不能进 worker 后才补预约；**[R2-1]**
   - 同 replay identity 连续提交两个 needs-anchor job 时，各自拥有 reservation；首个
     建锚后，第二个命中已有 terminal result/no-op 仍 resolve 自己，后续 settle 引用
     第二条 head；首个 mutator 返回后的下一 await barrier 上 submit 已见 persisted；
     **[R3-1][R3-2]**
   - 同 `(confusion_id, turn_id, replay_id)` submit 10 次，gap analyzer 与对象语义
     各 1 次，返回同一 terminal result。具名
     `test_confusion_attribution_replay_is_idempotent`；**[F1]**
   - `scope=avoidance_probe`、delight、direct probe button 未被误迁入；
   - weak-positive 的 exploration buffer/promotion 未被装进
     `probe.reply.apply`。
2. `SocraticDialogue.respond` 提交 learn envelope 时冻结 anchor snapshot；删除 producer
   或 engine 在执行时重新补抓 generation 的任何路径。
3. 把 `_ensure_confusion_dialogue_anchor` 改成 async queue façade：先 await
   由同一 admission helper 先 reserve+enqueue 的 `anchor.establish` 本地
   completion，再调用会生成 reply/提交 learn 的 `SocraticDialogue.respond`，
   保证 learn admission 已持有返回的 generation。前序 LLM 拥塞时 durable turn
   保持 pending，不允许 execution-time inference。**[R2-1]**
4. `_complete_durable_chat_turn` 在 reply 持久化后，为 probe/confusion side effect
   submit typed job；保持 turn completion 与结算错误可分别诊断。
5. 拆开当前 probe side-effect handler：speculation/hypothesis settlement、
   feedback history、visible cognition/event 进入 job；exploration buffer/promotion
   保持 out-of-scope 路径，不因复用 helper 被 worker permit 覆盖。
   `probe.reply.apply` 返回 immutable
   `ProbeReplyApplyResult(classification, classifier, resulting_action,
   exploration_intent)`；`_complete_durable_chat_turn` await completion 后，才在自身
   task 消费 optional intent。队外代码禁止再次调用 classifier；非 weak-positive
   intent=None、exploration 调用=0。**[F8]**
6. 对同一 durable turn 使用稳定 `turn_id` 做 observation/effect key；classification
   result 写进现有 chat-turn payload/effect receipt（不新建表），duplicate job
   直接重建 result、classifier=0。exploration intent 固定
   `evidence_id=turn_id`，队外重复 handoff 由既有 buffer 语义去重。
7. 为 `confusion.attribution.replay` 实现独立 dispatcher 分支与 handler：**[F1]**
   - payload 至少有 `confusion_id/turn_id/replay_id` 与 admission anchor snapshot；
   - payload/admission policy 必须在缺锚且 handler 会补建时声明
     `anchor_transition=establish(confusion, ref)` 并先创建 reservation；
     **[R2-1]**
   - 先读现有 replay head/turn payload/settlement receipt；已有 result 时，若 envelope
     持有 reservation，必须从 authoritative anchor state 同步 resolve 本 job 的
     `already_terminal/no_op` entry，之后才可直接返回；**[R3-1][R3-2]**
   - classification gap 才调用 analyzer，一次写入既有 durable replay/turn receipt
     后再 apply；
   - handler 的 anchor mutator 返回 typed terminal 后立即 `resolve_owned`，再做 replay
     receipt/settle/release/下一次 await；转正后的后续 throw 不改 reservation；
     **[R3-2]**
   - handler 可调用内部 settle/anchor apply，但不得 public submit 自己。
8. cognition replay hook 只做 read-only candidate enumeration，逐个投递
   `confusion.attribution.replay`；worker 内仍按现有 FIFO confusion replay 语义
   处理。不得把整个 cognition cycle 放入队列，不得复用 `learn` 或
   `confusion.reply.apply` 偷渡。**[F1]**

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_context.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py \
  tests/test_confusion_lifecycle.py \
  tests/test_cognition_cycle.py \
  -k 'settles or anchor or durable_chat or probe_chat or confusion or attribution_replay or exploration_handoff' -q
```

数值门：spec §2.2 的 chat/anchor/probe/confusion/replay 入口覆盖 100%；typed replay
同 identity 重放 10 次 analyzer=1、effect=1；weak-positive classifier=1、
exploration=1 且 exploration worker-permit=false；out-of-scope direct probe
button/avoidance/exploration writer 的行为与 baseline 一致，没有被接入 queue；
replay 缺锚补建 admission 100 次 reservation-before-put=100、late-reserve=0。
同 replay 双 builder 100 次 distinct-owner=100、第二个 no-op resolved=100、
later-settle wrong-head=0；commit-point barrier reserved-visible=0、throw demotion=0。
**[F1][F8][R2-1][R3-1][R3-2]**

### Task 3.3 — 按需 202 与 card turn 轮询

**Files**

- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/web/shared/dialogue-confirmation.js`
- Modify: `extension/popup/popup-api.js`
- Modify: popup/desktop action wiring only if shared helper 需要 poll callback
- Modify: `extension/tests/dialogue-confirmation.test.ts`
- Modify: `extension/tests/dialogue-confirmation-wiring.test.ts`
- Modify: `tests/test_api_app.py`

**Steps**

1. 先写 backend RED：fake learn job 占住 worker，card action 在 1.5 s 内返回
   `202/outcome=processing`；HTTP return 不 cancel queued Future；释放 worker 后
   `GET /api/chat/turns/{turn_id}` 读到 terminal。
2. 先写 shared frontend RED：
   - 200 继续一次请求直接终态；
   - 202 进入 processing，不回滚成 pending；
   - 通过既有 `fetchChatTurn` 以 1/2/5 秒退避轮询；
   - 远端 pending 时维持本地 processing；
   - 从 action 202 起 30 秒达到 deadline 后停止 timer，转本地
     `retryable_error`、重新启用原 action；durable payload 仍是 pending；
   - `confirmed/rejected/deferred/discussing` 停止；
   - page abort 停止并保留“可刷新重试”状态；
   - opposite `already_settled` 仍覆盖 optimistic state。
3. 写
   `test_processing_job_lost_on_restart_can_be_resubmitted`：**[F6]**
   - fake worker 被 barrier 占住，action 得到 202；
   - 模拟 backend/runtime 重启，明确丢弃未执行内存 queue item，不复制 job；
   - 同一 SQLite turn 仍为 pending，GET 不会凭空终态；
   - fake timer 推到 30 秒，客户端进入 retryable 状态；
   - 用户再次 POST 同一 action，新 runtime 正常受理并到终态。
   分别覆盖“无 receipt”“`applied=0` winner”“`applied=1` 只缺 publication”三种
   retry 分支；后两者不覆盖 winner payload、不重做对象语义。
4. 30 秒常量带校准注释：覆盖多轮 1/2/5 秒本地 publication poll，主要防止
   non-durable restart 永久 spinner；不等待 provider 300 秒，也不以加 job 表修复。
   **[F6]**
5. 不新增通用 job endpoint，不给 mobile 增 card UI。popup/desktop 复用 shared helper；
   legacy 仍靠 ref 重试。
6. 所有 timer 测试使用 fake timer/注入 poll，不真实等待 5/30 秒。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_api_app.py \
  -k 'processing or queue_wait or card_action or lost_on_restart or resubmit' -q

(cd extension && npm test && npm run typecheck && npm run build)
```

数值门：blocked-worker 请求 `<=1.5s` 返回 202；释放后 `<=2s`（fake handler）
读到终态；restart-lost-job 后第二次提交成功；deadline 前永久 spinner=0；前端
200/202/already_settled/error/retryable-timeout 五分支全覆盖。**[F6]**

### Task 3.4 — 旧测试改写、最终 mutation 护栏与文档

**Files**

- Modify: `tests/test_database.py`
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_dialogue_anchor.py`
- Modify: `tests/test_dialogue_settlement_guard.py`
- Modify documentation listed below

**Steps**

**旧测试清单**

删除旧机制测试，但用新业务意图测试替代：

| 旧测试 | 处理 | 新证明 |
| --- | --- | --- |
| `tests/test_database.py:90::test_card_settlement_schema_migrates_wave_a_table` | Task 2.1 具名改写/可重命名 | 最早 schema 的 winner 数据保留；fresh schema 不含 `apply_claim_*`/`seg_*`，不再断言旧列默认值 **[F9]** |
| `test_card_settlement_insert_or_ignore_arbitrates_across_connections` | 保留 winner 业务断言、改用新 `try_create` payload | 两 connection 仍只有一个 immutable winner；不含 claim ownership |
| `test_card_settlement_claim_fences_paused_old_executor_after_takeover` | 删除 | 100 mixed jobs 单 consumer + retry original winner |
| `test_card_settlement_segment_and_applied_writes_require_current_fence` | 删除 | worker permit + event/effect once |
| `test_claim_takeover_fences_object_derived_and_ledger_side_effects` | 删除 | 七 crash gap + no second executor |
| `test_unapplied_conflict_reports_processing_then_stale_takeover_applies_winner` | 改写 | unapplied winner 重试立即继续，无 5min clock/takeover |
| `test_fault_injection_resumes_each_settlement_segment_once` | 改写 | per-effect stable key/idempotent retry，无 `seg_*` 断言 |
| `test_confirm_and_reject_apply_fenced_settlement` | 重命名/改写 | serialized settlement + unique receipt |
| `test_card_settlement_ledger_failure_cannot_roll_back_marker_or_block_apply` | 保留业务断言、删除 token/segment fixture | observer failure 不阻塞 applied，stable key 不重复 |
| `tests/test_soul_engine.py:3257::test_rebuild_marker_write_failure_blocks_settlement_publication_and_cleans_tmp` | 具名改写 | marker failure 时 applied=0/card pending/tmp 清理；显式 same-ref retry 以 stable effect 恢复，不改 lease/`seg_*` **[F9]** |
| `test_discussion_attempt_token_is_cleared_by_stale_repair_and_fences_resume` | 改写 | orphan discussing 无锚立即 reconcile→pending |
| `tests/test_api_app.py:11464::test_discuss_cas_builds_anchor_and_failure_rolls_back` | 具名改写/可重命名 | `card.discuss` worker 内 pending→discussing→anchor；anchor failure 同 worker 补偿 pending，payload 无 attempt token，endpoint direct sink=0 **[F9]** |
| `test_stale_discussion_read_repair_clears_token_and_rejects_old_resume` | 具名改写 | GET 只 submit `card.reconcile`；orphan discussing 无锚立即回 pending，无 5min/attempt token |
| `test_legacy_feedback_forwards_to_common_settlement_with_deprecated_source` | 保留、改 queue spy | legacy typed submit、deprecated headers/source 不变，空队列仍 200 |

表中“删除”只删除对 takeover/fence 私有机制的断言；同一行的业务意图必须由右栏
列出的新测试承接。三个带 file:line 的 review 点名测试不得遗漏、合并成一句泛称或
直接删除。**[F9]**

同步/异步契约不一刀切：

- 原本空队列 200 测试继续断言 200；
- 只新增“worker 被 LLM 占用”时 202 + poll；
- 删除依赖 stale lease 才出现 202 的测试；
- 前端不得把所有 200 预期改成 202 来掩盖本地慢 action。

**Guard steps**

1. 先把最终 AST/runtime guard、旧符号 0 命中与 200/202 contract 写成 RED；
   分别确认 failure 指向尚存旁路/旧测试，而不是放宽 allowlist 或删除业务断言。
2. AST inventory 最终扫描 production：
   endpoint、API-mode `SocraticDialogue`、cognition hook 对 protected mutator 的
   直接调用=0；pending-open exception/retarget 分支对 `schedule_ask`、
   `Database.update_confusion`、anchor mutator 的 direct 调用=0。**[F3]**
3. 在本 Task 才把 guard 安装到 production runtime façade；runtime spy 分别跑
   card 4 动作、legacy、open、plain chat settle、anchor、
   probe/confusion durable reply、attribution replay；每条 mutation 都由登记的
   actual worker Task 发起。另在 worker handler 中 create child task，证明 inherited
   ContextVar 仍不能 mutation。**[F1][F4]**
   热重载 guard spy 另固定 `revoke old → register new → old finally`，证明任一时刻
   authorized tuple 恰好 0 或 1，new 不被迟到 cleanup 清除、old 撤权后 mutation=0。
   **[R2-3]**
4. “旁路对象矛盾只限已知限制”测试：
   - 单 backend、只用 declared entries，100 次交错无矛盾；
   - 单独的 `known_limit` 测试可显式构造 out-of-scope writer，但只记录当前不保证，
     不让它成为拉入新 coordinator 的失败门。
5. F5 compatibility allowlist 独立于 worker guard：API runtime 之外只允许
   CLI/OpenClaw 两个构造点显式 `legacy_direct`；它们 direct learn 的 baseline
   测试必须通过，但不计入 declared-entry queue coverage。生产第三个
   `legacy_direct` callsite 或任一 CLI/OpenClaw queue submit 都失败。**[F5]**

**Documentation steps**

按 spec §13 更新：

- `docs/modules/soul.md`
- `docs/modules/api.md`
- `docs/modules/storage.md`
- `docs/modules/runtime.md`
- `docs/modules/llm.md`
- `docs/modules/extension.md`（客户端 polling 实际改动后）
- `docs/modules/cli.md`（只写 CLI chat 显式 legacy-direct、行为不变）
- `docs/modules/integrations.md`（只写 OpenClaw chat 显式 legacy-direct、行为不变）
- `docs/changelog.md`
- `docs/architecture.md`
- `docs/spec.md`
- `README.md`
- `README_EN.md`

逐项核对 `CLAUDE.md#documentation-requirements`。本次不是 release，不加 README
highlights；CLI command/config/installer contract 未改，不新增命令或配置。因
`cli.py` 有 F5 compatibility-only 构造参数变化，仍按强制清单更新
`docs/modules/cli.md`；不得借此扩写 CLI queue 接入。

**Acceptance**

```bash
test "$(rg -n \
  'claim_card_settlement|card_settlement_claim_guard|paused-owner|lease-takeover' \
  src/openbiliclaw tests | wc -l | tr -d ' ')" = "0"
test "$(rg -n \
  'apply_claim_token|apply_claim_at|seg_event|seg_object|seg_marker' \
  src/openbiliclaw --glob '!storage/database.py' | wc -l | tr -d ' ')" = "0"
PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_database.py \
  -k 'legacy_card_settlement_columns_are_migration_only' -q

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
```

数值门：旧 takeover/fencing runtime/test 词命中 0；declared entry coverage=100%；
§2.2 worker 外 protected/raw-sink mutation=0；inherited-context child 绕过=0；
typed replay branch 有覆盖；建锚 admission policy 漏项=0；failed entry 新引用=0、
旧引用归零后残留=0；同 ref builder owner 冲突/越权 resolve/dangling entry=0，
already-terminal/no-op/superseded 均终结自己的 entry；建锚 mutation 返回后下一
await/effect 前 reserved-visible=0，later throw demotion=0；reload old-finally 清 new
permit=0；CLI/OpenClaw legacy-direct callsite=2 且 queue submit=0。
**[F1][F3][F4][F5][F9][R2-1][R2-2][R2-3][R3-1][R3-2]**

### Wave 3 / Final Gate

恢复并校验 `config.toml` 后执行：

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q
PYTHONPATH=$PWD/src .venv/bin/python -m pytest --cov=openbiliclaw \
  --cov-report=term-missing

.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/

(cd extension && npm test && npm run typecheck && npm run build)

git diff --check
git status --short
```

最终必须满足：

- pytest 0 failed；
- coverage `>=70%`；
- Ruff/MyPy/extension 全绿；
- `config.toml` SHA 恢复为
  `d23161b1e5f49359604c0c6dfe09c204ec8430c12e414cf5b996ccb12134ee3d`；
- 只出现计划内代码、测试和强制文档差异；
- 没有 durable inbox、owner lock、force_tick/exploration/pipeline 改动；
- CLI/OpenClaw 只有两处 `legacy_direct` compatibility pin、对应测试/文档，queue
  adapter/submit/guard 改动为 0；**[F5]**
- 所有建锚 kind/路径 admission-before-put，failed reservation 可排空/GC/retry，
  同 ref builder 各自 owner-only resolve 且 mutation-return 即转正，reload permit
  始终单槽且迟到 old finally 不影响 new。
  **[R2-1][R2-2][R2-3][R3-1][R3-2]**

## 7. Rollout 与回滚

1. Wave 0–1 可独立提交且保持现有入口。Wave 2 的 schema/executor 删除与 Wave 3
   的入口 cutover 是一个不可拆分的 runtime 迁移窗口：可按 Task 顺序开发和跑
   focused gate，但不得发布只含 Wave 2、仍让 endpoint 调旧 executor 的版本。
2. Wave 3 用一次 atomic cutover 切全 §2.2 入口并安装 guard；不得 endpoint 同时
   direct + submit。之后每次 hot reload 都必须先 drain/revoke old permit，再
   register new；old `finally` compare-and-clear 不得清 new。bundled popup/desktop
   polling 与 backend 同版本交付。**[R2-3]**
3. 上线 7 天观察：
   - queue depth / oldest age；
   - action queue-wait p50/p95；
   - 202 比例；
   - worker job error；
   - stale-generation drop；
   - receipt retry 次数。
4. 触发 follow-up analyze/apply 设计的门：连续 7 天 action 202 `>1%` 或
   action-to-applied p95 `>5s`。未触发前不增加第二分析线。
5. 回滚必须整组 revert Wave 2 schema/executor + Wave 3 cutover；不能只恢复某个
   endpoint direct mutation，否则会重新形成双 writer。旧 schema migration 必须
   forward-compatible，回滚前先在副本验证旧版本能否读取；不能用 destructive
   DB reset。

## 8. Review round 1 finding → RED/GREEN 追踪

| Finding | RED 所在 | GREEN/最终 gate | 不允许的“修法” |
| --- | --- | --- | --- |
| F1 | Task 1.1 dispatcher exhaustiveness；Task 3.2 replay call spies | typed `confusion.attribution.replay` 重放 10 次 analyzer/effect=1 | 借 `learn` 或 `confusion.reply.apply` |
| F2 BLOCKER | Task 0.1 两个 barrier admission 交错 | Task 1.1 registry + Task 2.2 authoritative apply，各循环 100 次 | execution-time current-anchor 推断、第二队列 |
| F3 | Task 0.2 raw sink inventory；Task 3.1 三分支 endpoint spies | schedule/retarget/rollback 同一 handler，worker 外 raw sink=0 | 把全部 confusion/TTL writer 纳入 queue |
| F4 | Task 0.2/1.1 inherited-context child test | actual worker Task + lifecycle nonce 双校验 | 只检查 ContextVar boolean/token |
| F5 | Task 1.2 CLI/OpenClaw learning spies | 两个显式 legacy-direct callsite，各 learn=1/queue=0 | 接入 queue，或静默只回复 |
| F6 | Task 3.3 fake timer + restart-lost-job | 30 秒 retryable；第二次 POST 完成 | job table、无限 spinner、扩大 wait |
| F7 | Task 2.3 exact checkpoint fault | GET submit reconcile，projection 补齐且 object semantics=1 | scanner、重做对象语义 |
| F8 | Task 3.2 classifier/permit/exploration spies | typed result handoff，classifier=1、队外 exploration=1 | 把 exploration writer 放进 worker |
| F9 | Task 2.1 + Task 3.4 具名映射 | 三个 file:line 点名测试及全部旧机制意图有新证明 | 删除测试或保留旧字段凑绿 |

## 9. Review round 2 finding → RED/GREEN 追踪

| Finding | RED 所在 | GREEN/最终 gate | 不允许的“修法” |
| --- | --- | --- | --- |
| R2-1 BLOCKER | Task 0.1 `card.discuss→settle` barrier + anchor-builder policy exhaustiveness；Task 3.1/3.2 producer spies **[R2-1]** | Task 1.1 reservation-before-put；Task 2.2 authoritative apply；discuss/throw/replay 交错各 100 次无 absent/旧锚误冻 | 只给 `anchor.establish` 预约；worker 内迟补 reservation；迁入 direct probe/exploration |
| R2-2 BLOCKER | Task 0.1 failure→new submit→old drain→GC/retry barrier **[R2-2]** | Task 1.1 union/head/refcount；Task 2.2 old dependency effect=0、新引用=0、GC/fresh retry 各 100 次 | failed 留作 logical head；新 submit 继续引用；复用 failed id 或加 durable retry scheduler |
| R2-3 MAJOR | Task 0.2 `new registered→old finally→new mutation` deterministic guard test **[R2-3]** | Task 1.1 exact revoke/compare-clear primitive；Task 1.2 10 次成功/失败 reload 均 `max_authorized_workers=1` | 到 finally 才撤旧权；无条件 clear；引入 coordinator/第二 worker |

## 10. Review round 3 finding → RED/GREEN 追踪

R2-2/R2-3 保持已闭环；下表只收紧同一 registry 的 entry ownership 与
mutation-return 线性化点。**[R3-1][R3-2]**

| Finding | RED 所在 | GREEN/最终 gate | 不允许的“修法” |
| --- | --- | --- | --- |
| R3-1 BLOCKER | Task 0.1 `same-ref B1→B2→settle` + 第二个 no-op + 六 terminal owner test；Task 3.1/3.2 duplicate discuss/replay spies **[R3-1]** | Task 1.1 owner-bound entry/CAS；Task 2.2 authoritative terminal apply；B1/B2 各 resolve 一次、later settle 固定 B2 head、dangling/越权 resolve=0 | 同 ref 共享未声明 reservation；no-op 直接 return；较早 resolution 覆盖后发 head；新增 coalescer/第二 writer |
| R3-2 MAJOR | Task 0.1 mutator-return→next-await barrier + later throw/replay short-circuit **[R3-2]** | Task 1.1 synchronous resolver wrapper；Task 2.2/3.1/3.2 真实 handler 上 reserved-visible=0、later-throw demotion=0 | 等整个 handler 成功才转正；在 resolve 前 await/release/effect；用 completion callback 补转正 |

Wave 数仍为 **4**，Task 数仍为 **12**（2 + 3 + 3 + 4）；本轮只把
R3-1/R3-2 落进既有 Task，不新增 queue、coalescer、进程协调层或
force_tick/exploration/pipeline/OpenClaw/CLI writer；R2-2/R2-3 状态机不改。
**[R3-1][R3-2]**
