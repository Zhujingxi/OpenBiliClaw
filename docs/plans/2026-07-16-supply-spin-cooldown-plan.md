# 候选补货空转冷却 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-16-supply-spin-cooldown-spec.md`](./2026-07-16-supply-spin-cooldown-spec.md)
> **Status:** r1,待对抗 review
> **Execution order:** Task 1 → 2 → 3(Wave A,可独立发布)→ Task 4(Wave B Phase 1)→ Task 5(Wave B Phase 2)→ Task 6(文档门)
> **Tech:** Python 3.11+(本机解释器用 `.venv/bin/python`,系统 python 无依赖包);测试 `.venv/bin/python -m pytest <file> -q`;lint `.venv/bin/python -m ruff check src/ tests/`;format `.venv/bin/python -m ruff format src/ tests/`;类型 `.venv/bin/python -m mypy src/`。

**Invariants that MUST hold — re-read before each task:**

- **源枯竭补货频率有界**:连续无产出补货按 30→60→120→300→600s 阶梯退避,任意模拟小时 ≤10 次补货调用。
- **唤醒响应性零回归**:任意 `notify()` 清当前冷却窗口(允许一次立即探测);`startup`/`manual_*`/`config_*` 额外清阶梯;既有快槽回填测试(`test_fast_worker_refills_under_one_second_*`)零修改全绿。
- **有产出即复位**:`refreshed=True` 的补货结果或任一 worker 成功入池(`last_cached>0`)把阶梯与冷却清零。
- **未知结果形状不受罚**:supply_callback 返回非 mapping 按有产出处理,不进入冷却。
- **纯状态机改动**:不新增常驻定时器/后台任务/线程;冷却全部由 `run_forever` 循环 + `_wait_for_activity` 超时实现;`asyncio.create_task` 调用点不新增。
- **阈值有出处**:阶梯与节流常量带校准注释(CLAUDE.md pitfall #3)。
- **诊断节流不吞首次与变化**:空计划全量诊断在首次 / 指纹变化 / 距上次 ≥300s 时必须 INFO 输出;节流期间单行 DEBUG 含被抑制计数,不允许完全静默。
- 既有协调器测试语义优先:与 no-progress 路径冲突时调整实现而非既有测试。

---

### Task 1: 补货结果分类 + 冷却状态机(核心退避)

**Files:** modify `src/openbiliclaw/runtime/candidate_eval.py`;test `tests/test_candidate_eval_coordinator.py`。

**Interfaces:** Consumes: `_settle_supply_task` 中 `task.result()`(现被丢弃,`candidate_eval.py:359-371`)、`run_forever` 补货分支(`:150-155`)。Produces: `_supply_streak`/`_supply_cooldown_until` 实例状态、`_SUPPLY_UNPRODUCTIVE_BACKOFF_SECONDS` 模块常量、`_supply_result_is_productive()` 分类函数、`state="supply_cooldown"`。

**Steps:**

- [ ] Write one focused failing test `test_unproductive_supply_backs_off_exponentially`:fake clock(沿用文件内 `time_fn=lambda: now[0]` 惯例),starved snapshot(available<target、pending_eval=0、无 worker),supply_callback 恒返回 `{"refreshed": False, "reason": "below_threshold"}` 并计数;驱动 `run_forever` + 推进模拟时钟 3600s,断言 supply 调用 ≤10 次且相邻调用间隔非降。
- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q -k unproductive_supply` and confirm FAIL for the intended missing behavior(现状会打出远超 10 次)。
- [ ] Add the minimal implementation:常量 + 分类函数(非 mapping→productive;`refreshed` truthy→productive;其余→unproductive;catch-all 异常按 productive 并 DEBUG)+ `_settle_supply_task` 记账(异常路径同样按 unproductive 记账)+ `run_forever` 分支冷却检查与 `min(safety_wake, 剩余冷却)` 等待。常量带校准注释:30s ≈ 空转单次耗时(0.3–0.5s)的 60–100 倍,兼顾源抖动快恢复;600s 封顶保证源恢复后 ≤10 分钟自愈。
- [ ] Rerun `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q -k unproductive_supply` and confirm PASS with no warnings.
- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q`(全文件回归,尤其 `test_fast_worker_refills_under_one_second_*`、`test_three_zero_cache_batches_trigger_supply_and_backoff` 零修改通过)+ `.venv/bin/python -m ruff check src/ tests/` + `.venv/bin/python -m mypy src/`。

**Acceptance:**

- Numeric gate: 模拟 3600s 内 supply 调用次数 ≤10(测试断言;失败含义=冷却阶梯未生效或可被绕过,即用户发热回归未修复)。
- Reproduce with `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q -k unproductive_supply`; record the result in the PR.

### Task 2: notify 穿透与阶梯复位语义

**Files:** modify `src/openbiliclaw/runtime/candidate_eval.py`(`notify()`、`_commit_finished_workers`);test `tests/test_candidate_eval_coordinator.py`。

**Interfaces:** Consumes: Task 1 的冷却状态、既有 `_resume_notification()`(`candidate_eval.py:455-458`)、`_commit_finished_workers` 的 `last_cached>0` 分支(`:286-288`)。Produces: notify 清冷却窗口(任意 reason)、`startup`/`manual_*`/`config_*` 清阶梯、productive 结果与成功入池清阶梯+冷却。

**Steps:**

- [ ] Write focused failing tests:`test_notify_pierces_supply_cooldown_once`(冷却中 `notify("candidate_commit")` → 下一迭代恰 1 次补货;仍无产出 → 阶梯继续不重置)、`test_manual_notify_resets_supply_ladder`(登顶后 `notify("manual_refresh")` → 下次冷却回 30s)、`test_productive_supply_resets_ladder`(`{"refreshed": True}` → streak/冷却清零)、`test_legacy_supply_result_shape_is_not_throttled`(返回 `None`/字符串 → 不进入冷却)。
- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q -k "pierces or resets_supply or legacy_supply"` and confirm FAIL for the intended missing behavior.
- [ ] Add the minimal implementation:`notify()` 内清 `_supply_cooldown_until=0`;`_resume_notification` 为真额外清 `_supply_streak=0`;worker 成功入池分支清两者。
- [ ] Rerun the focused tests and confirm PASS with no warnings.
- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q` + ruff + `.venv/bin/python -m mypy src/`。

**Acceptance:**

- Numeric gate: notify 后恰好 1 次立即补货(=1,非 ≥1;失败含义=要么唤醒失效要么穿透变成重开闸门);manual reset 后首个冷却窗口 =30s。
- Reproduce with `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q -k "pierces or resets_supply or legacy_supply"`; record the result in the PR.

### Task 3: Wave A 回归收口(可发布点)

**Files:** test only(无源码改动预期)。

**Interfaces:** Consumes: Task 1、2 的实现。Produces: Wave A 可发布判定。

**Steps:**

- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py tests/test_discovery_candidate_pipeline.py tests/test_refresh.py tests/test_refresh_runtime.py -q`。
- [ ] Run `.venv/bin/python -m ruff format src/ tests/ && .venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m mypy src/`。
- [ ] 确认 `asyncio.create_task` 调用点未新增:`git diff main -- src/openbiliclaw/runtime/candidate_eval.py | grep -c 'create_task'` 输出 0(纯状态机不变量)。

**Acceptance:**

- Numeric gate: 上述测试全绿、ruff/mypy 零报错、create_task diff 计数 =0。
- Reproduce with the commands above; record results in the PR. Wave A 至此可独立合并发布(修复用户发热报告)。

### Task 4: 空计划诊断节流(Phase 1)

**Files:** modify `src/openbiliclaw/runtime/refresh.py`(`_log_empty_refresh_plan_diagnostics`,`:2083-2140`);test `tests/test_refresh.py`。

**Interfaces:** Consumes: 既有 `self._now()` 时钟注入惯例、池维护指纹先例(`refresh.py:1020-1044`)。Produces: `_last_empty_plan_diag_at`/`_last_empty_plan_fingerprint`/`_suppressed_empty_plan_count` 状态、`_EMPTY_PLAN_DIAG_INTERVAL_SECONDS = 300.0` 常量(校准注释:与冷却阶梯封顶同数量级,保证每个枯竭事件 ≥1 条全量诊断)。

**Steps:**

- [ ] Write one focused failing test `test_empty_plan_diagnostics_throttled_by_fingerprint`:同指纹连续调用 100 次 → 全量 INFO 恰 1 次、其余为含抑制计数的 DEBUG、重统计(mock `_count_pool_available_candidates_by_source` 等)调用次数 =1;指纹变化 → 立即 INFO;`_now` 前推 301s → 立即 INFO 且行内含 `suppressed=99` 类计数后清零。
- [ ] Run `.venv/bin/python -m pytest tests/test_refresh.py -q -k empty_plan` and confirm FAIL for the intended missing behavior.
- [ ] Add the minimal implementation:先算轻量指纹(`pool_available` + 既有就绪计数,不新增 DB 查询),命中"首次/指纹变化/≥300s"任一才执行重统计 + INFO(附 `suppressed=%d` 并清零);否则计数 +1 并单行 DEBUG。
- [ ] Rerun `.venv/bin/python -m pytest tests/test_refresh.py -q -k empty_plan` and confirm PASS with no warnings.
- [ ] Run `.venv/bin/python -m pytest tests/test_refresh.py tests/test_refresh_runtime.py -q` + ruff + `.venv/bin/python -m mypy src/`。

**Acceptance:**

- Numeric gate: 同指纹 100 次连续调用 → 全量诊断恰 1 次、重统计 mock 调用 =1(失败含义=节流失效,DB 查询放大未消除或首次/变化被吞)。
- Reproduce with `.venv/bin/python -m pytest tests/test_refresh.py -q -k empty_plan`; record the result in the PR.

### Task 5: 枯竭可观测——status 字段 + 登顶 WARNING(Phase 2)

**Files:** modify `src/openbiliclaw/runtime/candidate_eval.py`(`status_payload`,`:178-194`;登顶 WARNING);test `tests/test_candidate_eval_coordinator.py`。

**Interfaces:** Consumes: Task 1 的 `_supply_streak`/`_supply_cooldown_until`。Produces: `status_payload` 新键 `candidate_eval_supply_streak`、`candidate_eval_supply_cooldown_until`;阶梯首次登顶 WARNING `candidate supply starved: ...`(每枯竭事件恰 1 条,复位后可再触发)。

**Steps:**

- [ ] Write focused failing tests:`test_supply_starvation_warns_once_per_episode`(登顶恰 1 条 WARNING;继续无产出不重复;复位后再登顶再 1 条)、status_payload 断言两个新键随冷却状态变化。
- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q -k starvation` and confirm FAIL for the intended missing behavior.
- [ ] Add the minimal implementation(登顶 flag 随阶梯复位一并清零)。
- [ ] Rerun and confirm PASS with no warnings.
- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q` + ruff + `.venv/bin/python -m mypy src/`。

**Acceptance:**

- Numeric gate: 单枯竭事件 WARNING 计数 =1(caplog 断言;失败含义=要么运维不可见要么 WARNING 刷屏)。
- Reproduce with `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py -q -k starvation`; record the result in the PR.

### Task 6: 文档门(Wave B 收口)

**Files:** modify `docs/modules/runtime.md`、`docs/changelog.md`。

**Interfaces:** Consumes: Task 1–5 的落地行为。Produces: CLAUDE.md 文档义务合规。

**Steps:**

- [ ] `docs/modules/runtime.md`:补协调器冷却状态机(阶梯常量与校准依据、notify 穿透/复位语义、`supply_cooldown` 状态)、status 新字段、空计划诊断节流行为。
- [ ] `docs/changelog.md`:当前版本块加 fix bullet(候选补货空转回归:源枯竭 2–3 Hz 热轮询 → 阶梯冷却 ≤10 次/时;空计划诊断 300s 节流;新增枯竭 WARNING 与 status 字段)。
- [ ] 确认无需更新:架构图(无跨模块接线/新依赖块)、CLI/config 文档(零新增配置)——在 PR 描述中显式声明。
- [ ] Run `.venv/bin/python -m pytest tests/test_candidate_eval_coordinator.py tests/test_refresh.py tests/test_refresh_runtime.py -q`(最终回归)。

**Acceptance:**

- Numeric gate: 预合并清单逐项勾选(模块 doc ✓、changelog ✓、架构/CLI/config 显式排除声明 ✓);最终回归全绿。
- Reproduce with `git diff --stat main -- docs/`; record in the PR.

## Verification after merge

- **实机观察(owner:white,时长 24h)**:在触发过回归的 Windows 桌面端(源仍处枯竭态:YouTube 拒连、xhs throttled)升级后运行一天,检查:(a) `grep -c 'refresh plan empty' <当日日志>` < 300;(b) 任务管理器中 serve-api 空闲 CPU < 5%、风扇不再持续满速;(c) `grep 'candidate supply starved' <日志>` 每个枯竭时段恰 1 条 WARNING。
- **响应性抽查**:枯竭态下点一次手动刷新/改一次配置,日志确认 1 个循环迭代内出现补货尝试。
- **回滚触发**:若出现池子长期(>30 分钟)填不满且日志显示补货被冷却卡住而源实际已恢复(源侧日志有成功产出但无补货请求),revert Wave A 提交并重开校准——该症状指向复位路径失效。

## Explicitly out of scope

- 内容源自身可用性(YouTube 代理拒连、xhs producer throttle、B站平台配额)——网络/源侧问题,另行处理。
- `refresh_if_needed` 计划构建逻辑与补货水位线阈值调整。
- 补货慢的候选杠杆(search/explore 共享升级冷却等既有分析)。
- 扩展/桌面/移动/CLI 的"源不可用"UI 提示面(仅后端 status 字段就绪)。
- 逐 commit 定位回归引入点(诊断已足够支撑修复;如需归因另开考古任务)。
