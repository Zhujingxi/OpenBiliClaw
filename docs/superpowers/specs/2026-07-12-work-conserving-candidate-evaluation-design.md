# 持续候选评估与并发补货设计

**日期：** 2026-07-12

**范围：** `ContinuousRefreshController`、`DiscoveryCandidatePipeline`、候选评估调度、候选 claim 生命周期、运行时状态与配置

**不在范围：** 平台抓取协议、推荐准入分数、来源配比算法、推荐 serve 排序、初始化流程

## 问题

当前候选评估循环每次调用一次 `drain_pending()`，完成后固定等待
`refresh_check_interval_seconds`（默认 60 秒）再检查下一批。评估本身的耗时也会
叠加在这 60 秒之前，因此实际周期是“本轮耗时 + 60 秒”。新候选回写 API 会额外
创建一次 best-effort drain task，但外层 drain lock 已占用时，新触发只会返回，
不会保证当前批次完成后继续消费剩余积压。

单次 drain 当前最多 claim 90 条，由 `evaluate_content_batch()` 内部拆成最多两个
并行 LLM batch。该实现能限制瞬时并发，却不是 work-conserving worker pool：只要
一个 drain 整体结束，所有评估槽都会空闲到下一次轮询；当用户持续消费推荐或多个
平台持续写入 raw candidates 时，`pending_eval` 可能增长到 raw ceiling / 每来源
候选 cap，而 `pool_available_count` 长期追不上 `pool_target_count`。

直接把全局轮询从 60 秒改成 30 秒只能缩短空闲时间，同时会提高 refresh、画像、
平台 producer、预计算和其它共用循环的检查频率，不能解决锁冲突触发丢失、慢批次
拖住快批次和陈旧评估结果覆盖等并发问题。

## 目标

1. 只要可换库存未达到目标且存在可评估候选，评估 worker 就持续工作，不插入固定
   空闲等待。
2. 默认最多保持三个候选评估 batch 并发；任一 batch 完成后立即补入下一批，不等待
   同轮其它 batch 或下一次全局 refresh tick。
3. 数据库 claim、评估结果持久化和正式池 admission 具备清晰所有权；并发 worker
   不重复评估同一行、不把陈旧结果写回已重新领取的行，也不能越过可换池硬上限。
4. 候选入队、库存消费、初始化完成和配置恢复可以即时唤醒调度，且信号不会因锁竞争
   或 `Event.clear()` 时序丢失。
5. 429、鉴权失败、模型不可用、连续零产出和运行时热重载都有有界、可观测的退避或
   停止行为，不能形成忙循环或无限烧额度。
6. 交互聊天、推荐文案和其它高优先级 LLM 工作仍有容量；提高候选吞吐不能独占全局
   provider 并发。

## 非目标

- 不降低 `admission_min_score`，不以放宽质量门槛换库存数量。
- 不取消 B 站风控冷却、小红书/知乎插件任务间隔或其它平台请求预算。
- 不让多个进程共同消费同一 SQLite 候选队列；本阶段仍是单 runtime 进程内并发。
- 不把 `pool_target_count` 或 raw ceiling 改成软上限。
- 不在本阶段重构推荐排序、MMR、封面预取或画像 pipeline。

## 备选方案

### 方案 A：仅调配置

把 `refresh_check_interval_seconds` 改为 30、`discovery_limit` 改为 60，并提高
`llm.concurrency`。改动最小，但仍会在每个 drain 后空等，且共用 tick 会同时放大
其它后台循环。它适合作为旧版本的临时缓解，不作为正式方案。

### 方案 B：单一协调器 + 并行评估 + 串行提交（采用）

由一个候选评估协调器独占 claim 调度；三个 worker 只执行无数据库写入的 LLM 批评估；
一个 commit lane 按完成顺序校验 claim token、持久化结果并做 admission。任一 worker
空闲后立即领取下一批，库存达到目标或队列耗尽才停。

该方案把并发放在最耗时的 provider 调用上，同时保留数据库状态转换和池上限检查的
单一顺序，吞吐与一致性边界最清晰。

### 方案 C：抓取、评估、预计算统一闭环控制器

把所有来源 producer、评估和文案生成都纳入同一动态 worker graph。理论吞吐最高，
但会扩大平台风控、来源配额和失败恢复的耦合面。本次只让评估成为持续消费者；raw
不足时复用现有 replenishment / producer 机制。

## 总体架构

新增 `runtime/candidate_eval.py::CandidateEvalCoordinator`，由
`ContinuousRefreshController` 创建、启动、停止和热重载。协调器只负责调度与状态，
候选格式、评分、准入策略仍属于 `DiscoveryCandidatePipeline`。

```text
候选入队 / 库存消费 / 周期兜底 / 配置恢复
                    │ wake(generation += 1)
                    ▼
          CandidateEvalCoordinator（唯一 claim owner）
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   eval worker 1 eval worker 2 eval worker 3
        │           │           │
        └───────────┴───────────┘
                    │ completed result queue
                    ▼
          单一 commit / admission lane
                    │
                    ├─ conditional persist by claim_token
                    ├─ 串行检查 pool target / franchise quota
                    ├─ cached / rejected / evaluated
                    └─ 更新运行时状态并决定继续、补 raw 或退避
```

协调器不并行调用现有 `drain_pending()`。实现需要把 pipeline 当前合在一个锁内的
“claim → evaluate → persist → admit”拆成明确阶段：

1. `claim_batch()`：短事务领取一批行并生成 claim token。
2. `evaluate_claim()`：worker 只调用 LLM，不写 SQLite。
3. `complete_claim()`：commit lane 校验 token，持久化评估并串行 admission。
4. `release_claim()`：失败、取消或关闭时仅释放仍由该 token 持有的行。

CLI 等显式单次调用可以继续通过兼容 `drain_pending()` 使用同一四阶段方法；runtime
后台改由协调器驱动，避免维护两套候选生命周期语义。

## 调度状态机

协调器状态为：

- `idle`：库存已满或没有 pending；等待事件，另保留 60 秒安全唤醒。
- `running`：持续 claim / evaluate / commit。
- `waiting_supply`：库存有缺口但 pending 与 evaluating 都为空；只请求一次
  replenishment，然后等待新候选事件或安全唤醒。
- `backoff`：遇到 provider 限流、暂时不可用或连续无产出。
- `paused`：scheduler gate、extension presence gate、画像未就绪或配置热重载中。
- `stopping`：不再 claim 新行，等待或取消 worker，释放属于本协调器的 claim。

每次 commit 后重新读取持久化状态，按以下顺序决策：

1. `pool_available_count >= pool_target_count`：停止 claim；已在执行的 batch 允许完成
   评分并写成 `evaluated`，commit lane 不越过 pool target，剩余高分行留待消费后优先
   admission。
2. 后台工作 gate 关闭或画像不存在：进入 `paused`。
3. 有 `pending_eval` 且有空 worker：立即 claim 下一批。
4. 只有 `evaluated`：commit lane 先尝试 admission，不发 LLM 请求。
5. raw 队列为空且库存有缺口：合并一次 `request_replenishment(reason="candidate_supply")`，
   进入 `waiting_supply`，不忙轮询平台。

## 并发模型与同步边界

### 单一 claim owner

同一 runtime 中只有一个 `CandidateEvalCoordinator` 可以 claim。API 回写、refresh、
producer 和周期任务只调用 `notify()`，不创建独立 drain task。这样锁竞争不会把一次
“新候选到了”降级成无后续保证的 no-op。

### Claim token

`discovery_candidates` 新增 nullable `claim_token TEXT`。每个 batch claim 使用不可预测的
唯一 token，并在同一事务中把目标行从 `pending_eval` 改为 `evaluating`。以下操作都必须
同时匹配 `id + status + claim_token`：

- 写回评分与分类；
- 释放回 `pending_eval`；
- 标记低分 / 最近看过；
- 转成 `evaluated`；
- 处理取消和超时。

claim 完成进入 terminal 状态或 `evaluated` 后清空 token。旧 worker 即使在热重载、
超时回收或重新 claim 后迟到，其 token 已不匹配，只能记录 stale completion，不能覆盖
新结果。

### 评估 worker

- 默认目标并发为 3。
- 每个 worker 每次处理 30 条以内；并发三批时总 in-flight raw 上限为 90，与当前
  `_EVALUATE_BATCH_HARD_CAP=90` 的风险边界一致，不扩大单轮最大评估量。
- worker 不执行 admission、不改 `pool_status`，只产生带 claim token 的结果对象。
- worker 完成后把结果交给 commit lane；该 claim 完成条件提交并重新读取 canonical
  available 后，协调器才为这个空闲槽领取下一批。提交是短 SQLite 操作，不等待其它
  慢 worker，同时避免 commit 尚未落库时按旧库存过量 claim。
- 结果队列容量等于 worker 数，避免 commit lane 暂时变慢时无限堆积内存结果。

30 条批大小来自现有 90 条 hard cap 除以三个 worker；该值不是质量阈值，也不随 provider
静默变化。未来若 provider/model 改变，应通过日志中的 batch latency、结构化输出失败率
和 admission yield 重新校准，而不是只提高 hard cap。

### Commit / admission lane

- 始终单 worker；所有 SQLite 结果转换和正式池 admission 在此串行发生。
- 每个 claim 的评估结果先以条件事务持久化，再按当前库存余量逐条 admission。
- 库存达到目标后，同批剩余合格行保持 `evaluated`，不误标 rejected，供后续消费后直接
  入池且无需再次评分。
- 现有 franchise quota、最近看过、cache admission 和 source-aware pool guards 保持不变。
- runtime status 与 `refresh.pool_updated` 在 commit 后读取 canonical DB 计数，不从
  worker 的预估数量推导。

### 全局 LLM 容量

新增 `[discovery].candidate_eval_concurrency`，合法范围 `1..8`，默认 `3`。有效评估 worker
数为：

```text
min(candidate_eval_concurrency, max(1, llm.concurrency - 1))
```

当 `llm.concurrency >= 2` 时，为聊天、推荐文案、关键词或画像任务保留至少一个全局
LLM slot；若用户明确把全局并发设为 1，候选评估仍可使用唯一槽位，但无法同时预留
交互容量。高吞吐推荐配置为
`llm.concurrency=4`、`candidate_eval_concurrency=3`；默认 `llm.concurrency=3` 时自动使用
两个评估 worker，不会因为升级突然把 provider 并发打满。所有调用继续经过现有
`PrioritySemaphore` 和 `DiscoveryConcurrencyController.chat_active` gate。

## 唤醒协议与不丢信号

协调器使用“generation counter + `asyncio.Event`”的 level-triggered 协议：

1. `notify(reason)` 先递增 generation，再 `event.set()`。
2. 协调器醒来记录 generation，清除 event，运行一次或连续多次调度。
3. 准备休眠前重新读取 durable pending / evaluated / available 状态。
4. 如果 generation 已变化、event 已重新 set，或 durable 状态仍可继续，直接进入下一轮；
   否则才 `await event.wait()`。

即使通知恰好发生在 `event.clear()`、状态读取或准备 await 之间，generation 或 event
至少有一个会保留变化；60 秒安全唤醒只用于防御未接入通知的旧路径，不承担正常吞吐。

以下路径必须在事务提交后调用 `notify()`：

- B 站、小红书、抖音、YouTube、X、知乎、Reddit 候选入队；
- refresh/discovery 批量 enqueue；
- 推荐 bootstrap、reshuffle、append 或反馈使 available 下降；
- 初始化完成、scheduler/config 恢复、extension presence gate 重新打开；
- runtime 启动释放 orphaned `evaluating` claims 后。

## 供给与背压

评估协调器只加速已存在 raw 的消费，不直接绕过各平台 producer 节流。

- 当 `available < target` 且 `pending_eval + evaluating + evaluated` 低于一个完整 worker
  wave 时，协调器合并一次补货请求。
- `request_replenishment` 继续受 refresh lock、来源配比、raw ceiling、关键词 backpressure、
  平台 min interval 和 daily budget 约束。
- 同一 `waiting_supply` 周期只允许一个补货请求；候选入队或请求完成后才允许重新判断。
- raw ceiling 继续作为 trim guard，不把低 available 的真实缺口算成零。

## 退避与无进展保护

### Provider 限流

- 优先采用 provider `Retry-After`；没有时按 15、30、60、120、300 秒指数退避，最大
  5 分钟。
- 退避期间不 claim 新行，现有 claim token 行释放回 `pending_eval`。
- 配置保存、provider 恢复通知或用户手动刷新可以提前唤醒重新探测，但同一时刻仍只跑
  一个受控 probe batch。

### 鉴权或不可恢复配置错误

进入 `paused`，等待 config reload；不按秒重试，不消耗候选 attempts。

### 连续零 admission

连续三个已成功评分的 batch 都 `cached=0` 时，说明问题更可能是低通过率、重复内容、
franchise quota 或来源质量，而不是 worker 不够。协调器：

1. 记录各 rejection reason；
2. 请求一次新的 source/keyword supply；
3. 暂停 claim 60 秒；
4. 新候选到达后恢复；若仍连续零产出，退避按 60、120、300 秒封顶。

该护栏只延迟下一轮，不降低准入门槛，也不把已拒绝候选重新评估。

## 生命周期、取消与热重载

- runtime 启动先把上个进程遗留的 `evaluating` 行重置为 `pending_eval` 并清空 token，
  然后启动一个协调器。
- `claim_token` 通过现有容错 schema migration 增加；旧数据库升级后现有行默认为 NULL，
  orphan reset 与新 claim 不依赖离线迁移脚本。
- 热重载先把旧协调器置为 `stopping`，禁止新 claim；取消 worker 后用各自 token 条件释放
  未完成行；等待 commit lane 退出，再构建新 runtime，不能让新旧协调器重叠。
- 正常 shutdown 使用同一路径；进程崩溃则由下次启动的 orphan reset 恢复。
- 达到 pool target 不强制取消已发出的 provider 请求。它们完成后只持久化为 `evaluated`，
  admission 由单 lane 决定，避免浪费已产生的模型结果。
- worker 或 commit task 的未处理异常必须向协调器汇报并触发 claim release；顶层 task 不用
  `suppress(Exception)` 永久吞掉后静默死亡。

## 运行时状态与日志

`/api/runtime-status` 与 `refresh.pool_updated` 增加兼容字段：

- `candidate_eval_state`: `idle|running|waiting_supply|backoff|paused|stopping`
- `candidate_eval_workers`: 当前有效 worker 上限
- `candidate_eval_in_flight`: 当前执行 batch 数
- `candidate_eval_pending`: `pending_eval` 数
- `candidate_eval_backoff_until`: 无退避时为空
- `candidate_eval_last_error`: 脱敏后的最近错误摘要
- `candidate_eval_last_batch_seconds`
- `candidate_eval_last_cached`
- `candidate_eval_last_rejected`

每个 completed batch 记录结构化 INFO：claim 数、耗时、cached、各 rejection reason、
pending 前后变化和 available 前后变化。状态转换、stale token completion、退避、零产出
保护和 orphan reset 记录一次 WARNING；空闲安全 tick 保持 DEBUG，避免日志膨胀。

## 配置

新增：

```toml
[discovery]
candidate_eval_concurrency = 3
```

高吞吐实例建议：

```toml
[llm]
concurrency = 4

[discovery]
candidate_eval_concurrency = 3

[scheduler]
pool_target_count = 600
discovery_limit = 60
pause_on_extension_disconnect = false
```

`refresh_check_interval_seconds` 保持 60；新协调器通过事件与连续 worker 消除评估空转，
无需把所有后台循环一起提速。平台 `min_interval_minutes`、`request_interval_seconds` 和
daily budgets 不随高吞吐配置自动改变。

## 测试

实现先写失败测试，至少覆盖：

1. pending 足够时启动三个 worker，最大并发不超过有效 worker 数。
2. 一个 worker 先完成并由 commit lane 落库后立即领取下一批，不等待其它慢 worker。
3. available 达到 target 后不再 claim；已在执行的结果只进入 `evaluated`，池不超上限。
4. 三个 worker 同时完成时 commit lane 仍串行，SQLite 不出现嵌套事务或重复 admission。
5. 每个候选最多被一个 claim token 持有；陈旧 token 的完成、失败和取消都不能改写新 claim。
6. 热重载取消旧协调器并释放未完成 claim，新旧协调器不重叠。
7. 候选恰在 `Event.clear()` / 状态检查 / await 边界入队时不会丢 wake-up。
8. 多个 API/task-result 同时 notify 会合并唤醒，不创建多个 drain owner。
9. 429 遵循 Retry-After / 指数退避，退避期间不 claim、不忙循环。
10. auth/config 错误 park 到 config reload；普通 transient failure 可恢复。
11. 连续三个零 cached batch 触发 supply 请求和有界退避，不降低 threshold。
12. `llm.concurrency=3`、候选配置 3 时有效 worker 为 2；全局 4 时为 3。
13. 聊天 active 时新评估不抢占保留 slot；聊天结束后 worker 自动续跑。
14. runtime status 和实时事件报告 canonical DB 数与协调器状态。
15. raw ceiling 已满但 available 不足时仍可评估、admit 和请求新的有效供给。

除针对性单元测试外，运行候选 pipeline、refresh runtime、API task-result、配置 round-trip、
LLM priority semaphore、真实 SQLite 并发回归，以及 `ruff check`、`mypy src/` 和全量
`pytest`。

## 文档影响

实现时同步更新：

- `docs/modules/runtime.md`：协调器状态机、生命周期和 runtime status。
- `docs/modules/discovery.md`：claim/evaluate/commit/admit 四阶段数据流与吞吐语义。
- `docs/modules/storage.md`：`claim_token` 字段和条件状态转换。
- `docs/modules/config.md` 与 `config.example.toml`：候选评估并发配置和高吞吐建议。
- `docs/changelog.md`：持续评估、并发安全与可观察性。
- `docs/architecture.md`、`docs/spec.md`、`README.md`、`README_EN.md`：把候选评估从
  60 秒轮询 drain 更新为事件驱动协调器 + worker/commit lane。

CLI 命令和平台外部协议不变，因此无需新增 CLI 文档或安装步骤。

## 验收标准

- 在 pending 持续充足、provider 无退避且 available 低于 target 时，任一评估 worker
  完成后 1 秒内领取下一批，不再出现固定 60 秒空档。
- 配置为 `llm.concurrency=4`、candidate eval concurrency 3 时，候选评估最多三批并发，
  同时为其它 LLM 工作保留至少一个全局 slot。
- 持续消费和持续入队压力下，池子始终不超过 target，候选无重复 claim/admission，
  SQLite 无 transaction/locking 错误。
- 429、配置错误、热重载、进程退出和陈旧 worker completion 均不会造成忙循环、候选
  永久卡在 `evaluating` 或旧结果覆盖新结果。
- raw 缺货时平台抓取仍遵守现有来源节流；本功能的吞吐提升来自消除评估空转，而不是
  放宽平台风控或推荐质量门槛。
