# 对话结算单队列 Implementation Plan

> **Spec:** [`2026-07-23-dialogue-settlement-queue-spec.md`](./2026-07-23-dialogue-settlement-queue-spec.md)
> **Baseline:** `feat/cognitive-profile-pipeline` @ `e16797ec`
> **Execution:** Wave 0 → 1 → 2 → 3；每个 Task 必须先 RED、再最小实现、最后 GREEN
> **Boundary:** 只实现 spec §2.1–2.2；禁止顺手收口 force_tick/exploration/pipeline/OpenClaw/CLI

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
src/openbiliclaw/soul/dialogue_settlement_guard.py  # ContextVar worker permit
src/openbiliclaw/soul/dialogue.py
src/openbiliclaw/soul/dialogue_anchor.py
src/openbiliclaw/soul/confusion.py
src/openbiliclaw/soul/engine.py
src/openbiliclaw/api/runtime_context.py
src/openbiliclaw/api/app.py
src/openbiliclaw/storage/database.py
```

测试：

```text
tests/test_dialogue_settlement_queue.py
tests/test_dialogue_settlement_guard.py
tests/test_dialogue_learn_queue.py        # 移动/改写既有骨架测试
tests/test_dialogue_anchor.py
tests/test_confusion_lifecycle.py
tests/test_database.py
tests/test_soul_engine.py
tests/test_api_app.py
extension/tests/dialogue-confirmation.test.ts
extension/tests/dialogue-confirmation-wiring.test.ts
```

明确**不新增**：

- `settlement_jobs` / durable inbox；
- data-dir owner / `.settlement-writer.lock`；
- coordinator package、scanner、lease、retry scheduler；
- force_tick/exploration/pipeline/OpenClaw adapter；
- 通用 job status API。

## 3. Wave 0 — 冻结问题、入口与护栏

### Task 0.1 — 把四个真问题变成确定性 RED

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
   - 构造 generation=0 的 hypothesis settlement 请求；
   - 在实际执行前建立同 ref anchor；
   - 断言新锚未被 release、receipt payload 仍为 generation=0。
   当前 `engine.py:1089-1097` 应令测试 RED。
3. 写 `test_generation_change_after_analysis_before_effect_writes_nothing`：
   barrier 卡在 post-LLM revalidation 后、首 effect 前，replace 同 ref anchor；
   旧代 event/object/derived/projection 均须为 0。当前实现应暴露窗口。
4. 写 `test_declared_dialogue_entries_submit_without_direct_mutation`，先覆盖 card action、
   legacy、pending-open anchor、普通 chat settle、probe/confusion durable side effect。
   对 queue.submit 和现有 direct mutator 同时放 spy；当前代码应至少出现 card/legacy/
   probe reply 旁路。
5. 每个 RED 都注明对应 spec D/Q 编号；不永久保留 xfail。Task 分支若必须先提交测试，
   只允许 `xfail(strict=True)`，在对应 GREEN commit 中立即移除。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py \
  -k 'file_fence or future_anchor or generation_change or declared_dialogue_entries' \
  -q -rxX
```

RED 门：恰好 4 个预期问题被复现；每个测试单独运行 `<5s`；无无关失败。

### Task 0.2 — 固定 entry/mutator inventory 与 worker guard 契约

**Files**

- Create: `src/openbiliclaw/soul/dialogue_settlement_guard.py`
- Create: `tests/test_dialogue_settlement_guard.py`
- Modify: `tests/test_dialogue_settlement_queue.py`
- Read only: `src/openbiliclaw/soul/dialogue_anchor.py`
- Read only: `src/openbiliclaw/soul/confusion.py`

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
   - 私有 `ContextVar`；
   - `dialogue_settlement_worker()` context manager；
   - `require_dialogue_settlement_worker()`；
   - 明确异常 `DialogueSettlementMutationOutsideWorker`。
4. 先让 runtime guard 测试 RED：最终生产 runtime 组装出的受保护 façade 在 worker
   外调用必须 100% 抛异常。Wave 0 只实现 guard primitive、冻结 expected failures，
   **不提前把 guard 装到仍走旧入口的 runtime mutator**；安装点随 Wave 3 cutover
   一次转 GREEN，避免中间分支把现有 endpoint 全部打断。
   独立 low-level model/DAO 单测可显式注入 no-op guard，但 runtime 组装测试必须证明
   最终真实实例安装了 guard。
5. 加 AST/call-site 审计。只允许统一 dispatcher 与 worker 内 `_apply_*` 调用 protected
   façade；API endpoint、`SocraticDialogue`、12h hook 不在 allowlist。
   Out-of-scope avoidance/force_tick 符号必须显式排除，不能误报后把它们拉入改造。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_guard.py \
  tests/test_dialogue_settlement_queue.py \
  -k 'inventory or guard or protected' -q
```

数值门：spec §2.2 entry 覆盖率 100%，`unclassified=[]`；guard primitive 的
10 个代表性 permit 外调用全部抛异常；production wiring 的 strict XFAIL 数量与
未 cutover 入口清单相等；out-of-scope 清单没有生产改动要求。

### Wave 0 Gate

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_dialogue_settlement_guard.py -q -rxX
```

保留的 strict XFAIL 只能是四个真问题 repro 与
`ENTRY_INVENTORY` 中尚未 cutover 的 production-wiring case；两组数量都必须与
清单精确相等，不得出现 unclassified XFAIL/XPASS。

## 4. Wave 1 — 泛化为唯一内存队列

### Task 1.1 — typed envelope、单 consumer、Future 与 reentry 防护

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
   - pause/resume/pause_and_drain/shutdown 与 lazy start 保持现有语义。
2. 定义 typed `DialogueJobKind` 白名单：
   `learn`、`settle.hypothesis`、`settle.confusion`、`card.defer`、
   `card.discuss`、`card.reconcile`、`anchor.establish`、
   `probe.reply.apply`、`confusion.reply.apply`。
3. 定义 immutable envelope/result；`asyncio.Queue` 中只放 envelope，不放 callable。
4. `submit()` 在 `put` 前：
   - 复制 payload；
   - 读取一次 anchor provider；
   - active anchor 与 payload target kind/ref 匹配时冻结 ref/generation；
   - target 已知但无匹配锚时冻结 target ref + generation `0` tombstone，供 worker
     识别同 ref future anchor；完全不涉锚才用 `("", 0)`；
   - 分配单调 sequence；
   - 可选创建 completion Future。
5. `_run()` 是唯一设置 worker permit 的地方；typed dispatcher 外不暴露 handler。
6. 保留 `_QUEUE_DEPTH_WARN=10`，新增 structured log：
   `kind/sequence/depth/queue_wait_ms/run_ms/outcome`。不新增 durable metrics store。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py -q
```

数值门：至少 12 个 queue 用例；100 mixed jobs 的 `max_active=1`、sequence 无缺口；
异常后至少 2 个后续 job 成功；reentry 测试 `<0.1s`。

### Task 1.2 — runtime 只装一个 dispatcher，删除 detached fallback

**Files**

- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `tests/test_dialogue_settlement_queue.py`
- Modify: `tests/test_soul_dialogue.py`

**Steps**

1. 先写失败测试：
   - runtime 只构造一个 `DialogueSettlementQueue`；
   - `SocraticDialogue.respond` 只 submit `learn`，queue 缺失时不
     `create_task(learn_from_dialogue)`；
   - hot reload 先 pause/drain old，成功后才 start/swap new；
   - rebuild 失败恢复 old queue；任何时刻 active worker 数 ≤1；
   - shutdown drain 后停止，Future 不悬空。
2. 把 `_build_dialogue_learn_handler` 改为 typed dispatcher builder；dispatcher 依赖
   `SoulEngine`、anchor manager 与 API side-effect façade，但不引用 HTTP Request。
3. runtime attribute 统一为 `dialogue_settlement_queue`。一次性迁移所有生产调用；
   不保留一个可独立运行的旧 `dialogue_learn_queue` alias。
4. `SocraticDialogue` 缺 queue：
   - 正常 runtime 视为配置错误并记录 ERROR；
   - 明确的无学习测试 double 可只返回 reply；
   - 任何路径都不得 detached mutation。
5. 热重载仍使用现有 30 s drain contract，不改成 stable coordinator，不增加 owner。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_settlement_queue.py \
  tests/test_soul_dialogue.py \
  -k 'runtime or reload or shutdown or respond' -q
```

数值门：10 次成功 reload 和 10 次失败 rollback 中 `max_active_workers=1`；
`rg -n 'asyncio\.create_task\(_background_learn|DialogueLearnQueue' src/openbiliclaw`
命中 0。

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
  tests/test_soul_dialogue.py -q
```

所有 Wave 0 queue/lifecycle XFAIL 在本 Wave 移除；尚未 cutover 的 endpoint repro
可以继续 strict XFAIL。

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
   - worker 内 hypothesis/confusion/普通 chat nested settle 不增加 queue depth；
   - generation=0 执行前建立未来锚，receipt/新锚保持 0 副作用；
   - 旧 generation 被 replace 后，anchor/object/profile 均 0 副作用。
2. 删除 `settle_hypothesis:1089-1097` 的 current anchor 推断，参数缺失默认 0，
   不允许 `None` 表示“稍后推断”。generation=0 envelope 必须保留 target ref；
   worker 若发现同 ref future anchor，返回 `stale_anchor` 并丢弃全部业务 effect。
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
5. anchor manager runtime 实例安装 worker guard。read-only
   `current/snapshot/validate_snapshot` 不要求 permit；mutation 要求 permit。
6. card discuss 不再使用 attempt token/5min fencing：
   worker 内 pending→discussing、establish，异常补偿；orphan reconcile 立即回 pending。
7. confusion 的 dialogue `schedule_ask/process_anchor_settlement/retry` 通过 guarded
   façade 调用；通用 TTL 等 out-of-scope 路径不被错误迁入队列。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_soul_engine.py \
  tests/test_dialogue_anchor.py \
  tests/test_confusion_lifecycle.py \
  tests/test_dialogue_settlement_guard.py \
  -k 'settle or anchor or generation or guard or discussion' -q
```

数值门：future-anchor 两个交错各循环 100 次，错误 mutation=0；worker nested settle
queue depth 增量=0；permit 外 10 个 mutation 全抛。

### Task 2.3 — 六个 crash gap 的幂等重试

**Files**

- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_api_app.py`

**Steps**

1. 参数化故障点：
   `after_event`、`after_object`、`after_derived`、`after_rebuild_marker`、
   `after_projection`、`after_anchor_release`。
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
4. audit ledger 故障继续不阻止业务 `applied=1`；恢复后相同 stable key 不重复。
5. 明确不写“restart 自动扫描恢复”测试。重建 runtime 后必须由显式 retry 才继续，
   以证明没有偷偷实现 durable inbox。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_database.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py \
  -k 'crash_gap or idempotent_effect or receipt_retry or ledger_failure' -q
```

数值门：6 个故障点 × confirm/revise/confusion 代表路径至少 10 个用例；
每个 mandatory effect 最终计数恰为 1；无 sleep/lease clock manipulation。

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

Wave 0 的 deadlock/future-anchor repro 必须在此转 GREEN。

## 6. Wave 3 — 全入口 cutover、按需 202、护栏与交付

### Task 3.1 — cards、pending-open、reconcile 与 legacy 全部 submit

**Files**

- Modify: `src/openbiliclaw/api/app.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_dialogue_settlement_guard.py`

**Steps**

1. 先写 endpoint RED：
   - card confirm/reject/defer/discuss 都观察到一个 typed submit；
   - direct engine/anchor/card-state spy 为 0；
   - pending-open 与 durable confusion question 的 anchor 建立经 queue；
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
4. defer/discuss 依赖 `turn_id + action + card state` 幂等；两个重复 queued job 串行后
   第二个返回 already terminal，不延长 cooldown、不重建锚。
5. pending-open 的 turn 创建沿用 `(ref, session)` SQLite 去重。需要由该新锚归属的
   后续 learn job，必须 await `anchor.establish` completion，拿到
   `established_generation` 后才可 admission；不得只靠 FIFO 后在 worker 中补抓。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_api_app.py \
  tests/test_dialogue_settlement_guard.py \
  -k 'DialogueConfirmationCards or pending_confirmation or legacy_feedback or reconcile' -q
```

数值门：入口 spy 覆盖 card 4 + open + reconcile + legacy 共 7 类；direct mutation=0；
空队列 100 次 action 全为 200；每次本地完成 `<1s`。

### Task 3.2 — ordinary chat、anchor handler、probe/confusion durable reply cutover

**Files**

- Modify: `src/openbiliclaw/soul/dialogue.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/soul/cognition_cycle.py`
- Modify: `tests/test_dialogue_context.py`
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_api_app.py`

**Steps**

1. 先写 RED：
   - plain chat settles hypothesis/confusion/speculation while queue
     `max_active=1`，不 nested submit；
   - anchor support/contradict/revise/answer/ambiguous/unrelated 全在 worker；
   - `scope=probe` durable reply 的 interest side effect submit 后才 mutation；
   - `scope=confusion` visible cognition/attribution submit 后才 mutation；
   - 12h confusion replay hook 只 submit；
   - `scope=avoidance_probe`、delight、direct probe button 未被误迁入；
   - weak-positive 的 exploration buffer/promotion 未被装进
     `probe.reply.apply`。
2. `SocraticDialogue.respond` 提交 learn envelope 时冻结 anchor snapshot；删除 producer
   或 engine 在执行时重新补抓 generation 的任何路径。
3. 把 `_ensure_confusion_dialogue_anchor` 改成 async queue façade：先 await
   `anchor.establish` 的本地 completion，再调用会生成 reply/提交 learn 的
   `SocraticDialogue.respond`，保证 learn admission 已持有返回的 generation。
   前序 LLM 拥塞时 durable turn 保持 pending，不允许 execution-time inference。
4. `_complete_durable_chat_turn` 在 reply 持久化后，为 probe/confusion side effect
   submit typed job；保持 turn completion 与结算错误可分别诊断。
5. 拆开当前 probe side-effect handler：speculation/hypothesis settlement、
   feedback history、visible cognition/event 进入 job；exploration buffer/promotion
   保持 out-of-scope 路径，不因复用 helper 被 worker permit 覆盖。
6. 对同一 durable turn 使用稳定 `turn_id` 做 observation/effect key，retry 不重复。
7. cognition replay hook 只投递当前持久 replay heads；worker 内仍按现有 FIFO
   confusion replay 语义处理。不得把整个 cognition cycle 放入队列。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_dialogue_context.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py \
  tests/test_confusion_lifecycle.py \
  -k 'settles or anchor or durable_chat or probe_chat or confusion' -q
```

数值门：spec §2.2 的 chat/anchor/probe/confusion 入口覆盖 100%；同 turn 重放 10 次
effect=1；out-of-scope spy mutation 行为与 baseline 一致。

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
   - `confirmed/rejected/deferred/discussing` 停止；
   - page abort/timeout 停止并保留“可刷新重试”状态；
   - opposite `already_settled` 仍覆盖 optimistic state。
3. 不新增通用 job endpoint，不给 mobile 增 card UI。popup/desktop 复用 shared helper；
   legacy 仍靠 ref 重试。
4. 所有 timer 测试使用 fake timer/注入 poll，不真实等待 5 秒。

**Acceptance**

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_api_app.py -k 'processing or queue_wait or card_action' -q

(cd extension && npm test && npm run typecheck && npm run build)
```

数值门：blocked-worker 请求 `<=1.5s` 返回 202；释放后 `<=2s`（fake handler）
读到终态；前端 200/202/already_settled/error 四分支全覆盖。

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
| `test_card_settlement_claim_fences_paused_old_executor_after_takeover` | 删除 | 100 mixed jobs 单 consumer + retry original winner |
| `test_card_settlement_segment_and_applied_writes_require_current_fence` | 删除 | worker permit + event/effect once |
| `test_claim_takeover_fences_object_derived_and_ledger_side_effects` | 删除 | 六 crash gap + no second executor |
| `test_unapplied_conflict_reports_processing_then_stale_takeover_applies_winner` | 改写 | unapplied winner 重试立即继续，无 5min clock/takeover |
| `test_fault_injection_resumes_each_settlement_segment_once` | 改写 | per-effect stable key/idempotent retry，无 `seg_*` 断言 |
| `test_confirm_and_reject_apply_fenced_settlement` | 重命名/改写 | serialized settlement + unique receipt |
| `test_card_settlement_ledger_failure_cannot_roll_back_marker_or_block_apply` | 保留业务断言、删除 token/segment fixture | observer failure 不阻塞 applied，stable key 不重复 |
| `test_discussion_attempt_token_is_cleared_by_stale_repair_and_fences_resume` | 改写 | orphan discussing 无锚立即 reconcile→pending |
| API stale discussion fencing test | 改写 | queue 顺序 + compensating rollback，无 5min/attempt token |

同步/异步契约不一刀切：

- 原本空队列 200 测试继续断言 200；
- 只新增“worker 被 LLM 占用”时 202 + poll；
- 删除依赖 stale lease 才出现 202 的测试；
- 前端不得把所有 200 预期改成 202 来掩盖本地慢 action。

**Guard steps**

1. 先把最终 AST/runtime guard、旧符号 0 命中与 200/202 contract 写成 RED；
   分别确认 failure 指向尚存旁路/旧测试，而不是放宽 allowlist 或删除业务断言。
2. AST inventory 最终扫描 production：
   endpoint、`SocraticDialogue`、cognition hook 对 protected mutator 的直接调用=0。
3. 在本 Task 才把 guard 安装到 production runtime façade；runtime spy 分别跑
   card 4 动作、legacy、open、plain chat settle、anchor、
   probe/confusion durable reply；每条 mutation 都携带 worker permit。
4. “旁路对象矛盾只限已知限制”测试：
   - 单 backend、只用 declared entries，100 次交错无矛盾；
   - 单独的 `known_limit` 测试可显式构造 out-of-scope writer，但只记录当前不保证，
     不让它成为拉入新 coordinator 的失败门。

**Documentation steps**

按 spec §13 更新：

- `docs/modules/soul.md`
- `docs/modules/api.md`
- `docs/modules/storage.md`
- `docs/modules/runtime.md`
- `docs/modules/llm.md`
- `docs/modules/extension.md`（客户端 polling 实际改动后）
- `docs/changelog.md`
- `docs/architecture.md`
- `docs/spec.md`
- `README.md`
- `README_EN.md`

逐项核对 `CLAUDE.md#documentation-requirements`。本次不是 release，不加 README
highlights；CLI/config/installer 未改，不碰对应文档。

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
  tests/test_database.py \
  tests/test_soul_engine.py \
  tests/test_api_app.py -q
```

数值门：旧 takeover/fencing runtime/test 词命中 0；declared entry coverage=100%；
worker 外 protected mutation=0。

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
- 没有 durable inbox、owner lock、force_tick/exploration/pipeline/OpenClaw/CLI 改动。

## 7. Rollout 与回滚

1. Wave 0–1 可独立提交且保持现有入口。Wave 2 的 schema/executor 删除与 Wave 3
   的入口 cutover 是一个不可拆分的 runtime 迁移窗口：可按 Task 顺序开发和跑
   focused gate，但不得发布只含 Wave 2、仍让 endpoint 调旧 executor 的版本。
2. Wave 3 用一次 atomic cutover 切全 §2.2 入口并安装 guard；不得 endpoint 同时
   direct + submit。bundled popup/desktop polling 与 backend 同版本交付。
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
