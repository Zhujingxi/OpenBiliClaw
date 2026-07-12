# 库存安全的持续补货与全局额度设计

**日期：** 2026-07-12

**状态：** 用户已确认，待实施计划

**实施基线：** `codex/continuous-candidate-evaluation`

**关联设计：**

- `2026-07-12-work-conserving-candidate-evaluation-design.md`
- `2026-07-12-global-llm-reservation-expression-microbatch-design.md`

本设计扩展上述两份设计，补齐两份真实用户日志暴露的库存正确性、来源归一化、
补货额度保证和失败放大问题。若本设计与关联设计在库存维护或后台额度语义上冲突，
以本设计为准。

## 范围

本次解决：

1. 可用候选刚由评估/文案流程补出，随即被 topic、来源或 raw ceiling 维护重新
   `suppressed`，前端库存反复归零。
2. 知乎 `zhihu-creator`、`zhihu-hot`、`zhihu-feed`、`zhihu-related` 等来源没有统一
   归入 `zhihu` source family，无法命中配置的知乎份额。
3. 主推荐链路与 `SoulEngine` 各自创建 `LLMService` 和 semaphore，导致
   `llm.concurrency=N` 实际不是 runtime 全局上限。
4. 补货与画像、猜测、关键词等后台 LLM 工作竞争时没有容量保证；库存为零时仍可能
   被长维护请求挡住。
5. 候选评估存在固定轮询空档，文案小批触发和 transient failure 的拆批回退会放大
   延迟或请求数。
6. 已经被旧维护逻辑误 suppress 的合格候选没有自动恢复路径，升级后仍需重新花费
   LLM 请求补货。

## 非目标

- 不修改 Soul prompt、事件压缩、token 预算、调用成本估算或价格配置；相关成本优化由
  独立工作处理。
- 不改变候选评分阈值、delight 阈值、推荐排序、MMR 或质量门槛。
- 不放宽平台抓取频率、daily budget、Cookie/扩展在线 gate 或 B 站风控冷却。
- 不增加跨进程分布式队列；仍限定单 runtime 进程和单 SQLite 数据库。
- 不重写所有抓取、评估、文案和裁剪为一个新框架；在现有持续评估分支上分层修复。

## 生产证据

### 用户 A

- `15:26:18`：文案预计算令 `pool_available 0 -> 16`。
- `15:26:22`：topic 维护后只剩 8，raw `618 -> 600` 后库存归零。
- `18:59:09`：库存 `3 -> 12`，两秒后 raw trim 14 条，库存归零。
- `22:05:19`：库存 `0 -> 9`，随后 raw trim 12 条，库存归零。
- 同一故障窗口还出现 provider rate limit、连接失败、批量文案递归拆分和最长数分钟的
  evaluator fallback。

### 用户 B

- `13:37:11`：库存 `0 -> 2`；`13:38:29` raw trim 22 条小红书内容后归零。
- `13:44:24`：维护前已有 10 条可用；trim 12 条 `zhihu-creator` 后归零。
- `13:46:35`：库存 `0 -> 3`；`13:47:52` 正好 trim 三条知乎子来源后归零。
- 该日志没有 ERROR、429 或 provider failure，说明即使模型正常成功，库存维护本身也能
  稳定复现故障。

两位用户共同证明：评估速度与 provider 稳定性会放大症状，但首要根因是库存维护没有
守住可用库存底线。

## 已确认产品决策

1. `pool_target_count` 是可用库存底线。低于目标时，可用库存优先级高于来源比例和
   topic 多样性配额。
2. 任何池维护动作都不得把可用库存降到维护前值以下；库存已高于目标时，最多只能
   裁掉超过目标的余量。
3. 升级后自动恢复历史 `suppressed` 中仍然合格的候选，先复用已支付的评估和文案结果，
   不足时才调用 LLM。
4. 全局 LLM 并发为 4，交互保留 1，后台合计最多 3。
5. 有真实补货等待者时，补货保底两个后台槽并可借用第三个；没有补货工作可运行时，
   空槽允许其它后台任务借用。
6. 库存为零时不启动新的低优先级维护 LLM 工作；已进入 provider 的请求允许完成，
   不做危险抢占。
7. 文案达到 8 条立即启动，1–7 条最多等 3 秒；单 provider batch 不超过 30，文案
   batch fan-out 不超过 2。

## 备选方案

### 方案 A：只修库存裁剪

增加知乎来源映射，并在 raw trim 时排除可用候选。改动小，能止住库存归零，但保留
60 秒 drain 空档、双 semaphore、补货无配额和 transient failure 请求放大。用户 A
仍可能长期缺货。

### 方案 B：分层完整修复（采用）

先做可独立发布的库存正确性提交，再在现有持续评估分支上完成真正的全局 gate、补货
额度和文案微批。每层可单独验证、发布和回滚，覆盖两位用户的共同根因与用户 A 的
并发放大问题。

### 方案 C：统一库存工作流重写

让一个新控制器拥有抓取、评估、文案和所有裁剪。长期边界更统一，但会同时改动平台
节流、来源策略和推荐维护，故障修复风险过高，本次不采用。

## 核心库存不变量

对 topic trim、source overflow trim、raw ceiling trim、under-quota reactivation 和自动
恢复组成的任意一次后台维护，必须满足：

```text
available_after >= min(available_before, pool_target_count)
```

该不变量只约束后台维护，不约束用户消费、reshuffle、append、反馈移除等正常库存下降。

### 候选保护层级

维护前使用与 `count_pool_candidates()` 完全相同的 canonical predicate 读取当前可用候选
ID，不能复制一套近似条件。候选按以下层级决定保留顺序：

1. **canonical available：** 当前前端真实可换；维护必须保护其中
   `min(available_before, target)` 条。若维护前可用数高于 target，按现有 serve 排序稳定
   选择被保护的 target 条，不能另造一套维护专用排序。
2. **ready reserve：** 文案、topic label、style、topic group、链接均齐全，只因全局 topic
   window、临时来源窗口等没有计入当前可换数。
3. **evaluated / pending copy：** 已经完成昂贵评分，等待正式准入或文案。
4. **raw pending：** 未评估、不可打开、缺少分类/文案字段；raw ceiling 首选牺牲对象。

层级优先于来源份额；同一层内部再按来源额度、相关度、评分时间、探索来源和稳定 ID
排序。被保护的候选仍计入其来源已占额度，避免保护规则成为来源无限扩张的漏洞。

### 统一维护事务

新增一个数据库级 pool maintenance 入口，使用短连接和 `BEGIN IMMEDIATE` 完成：

1. 读取 `available_before` 和 canonical protected IDs。
2. 可选恢复历史合格候选。
3. 仅从非保护集合选择 topic/source/raw trim victims。
4. 在同一事务内重新计算 `available_after`。
5. 若违反不变量，回滚整个维护事务并记录高优先级告警。

现有独立 trim 方法保留为内部选择器或兼容包装，但 runtime `_enforce_pool_cap()` 不再
逐个调用会分别 commit 的破坏性维护方法。这样不会出现 topic trim 已提交一半损失、
raw trim 才发现库存归零却无法回滚的状态。

raw ceiling 的 canonical 口径必须同时覆盖 `content_cache` 与 `discovery_candidates`，不能
继续只 trim 前者却把后者的 pending/evaluated 行留在 ceiling 之外。victim 选择遵守：

- 已被 worker claim、`status='evaluating'` 的行不参与维护；其所有权只属于 claim token。
- 未 claim 的 `pending_eval` raw 优先于已完成评分的 `evaluated` 行被清理。
- candidate 表被裁行进入可观测的 terminal trim 状态，而不是删除后失去来源/原因审计。
- 两张表的 source family、raw before/after 和 trim breakdown 使用同一 canonical 统计。

### 库存不足时的配额语义

- canonical available 永远不作为 victim。
- topic/source 配额可以临时超标；日志记录 deferred trim 数量。
- raw ceiling 仍是硬上限，但通过裁剪 ready reserve、evaluated pending 和 raw pending
  收敛，不能用 canonical available 换容量。
- 若异常配置使 canonical protected 数本身超过 raw ceiling，保留目标数量并记录配置
  不变量错误；正常配置的 raw ceiling 至少为 target 的两倍，不应进入该分支。

## 来源族规范化

当前 `_pool_source_family()` 手写各平台分支但遗漏知乎。改为一个集中、可枚举的来源族
映射，至少包含：

```text
bilibili, xiaohongshu, douyin, youtube, twitter, reddit, zhihu
```

知乎规则为：

- `source_platform in {"zhihu", "zh"}` -> `zhihu`
- `source` 以 `zhihu-`、`zhihu_` 开头 -> `zhihu`

同一映射同时供 pool share accounting、raw count、trim breakdown、viewed content identity
和 URL platform inference 使用，避免不同函数各自维护平台清单。测试从启用平台注册表
参数化生成，覆盖每个平台的 canonical 名、别名和所有已注册策略来源；新增平台若没有
family mapping，测试必须失败。

## 历史库存自动恢复

在 runtime 启动、热重载完成以及库存有缺口的维护周期中，先运行零 LLM 的恢复：

### 可恢复条件

- `pool_status = 'suppressed'`
- 未出现在 recommendations 历史中
- 非 dislike / purged / shown
- 未被当前用户看过
- 满足统一 admission 分数
- 文案、topic label、style、topic group 均齐全
- 来源链接当前可打开
- 通过小红书本人内容排除和现有 delight guard

### 恢复策略

1. 最多恢复到 `pool_target_count`，不制造可用库存过量。
2. 先补当前来源缺口；来源份额只作排序，不阻止其它来源填满全局缺口。
3. 同来源内按相关度、最近评分时间和稳定 ID 排序。
4. 与 trim 在同一维护事务中执行；恢复导致 raw 超 ceiling 时，随后只裁非保护候选。
5. 操作幂等；没有合格 suppressed 行时为廉价 no-op。

恢复完成后才允许候选评估或文案请求，避免对已存在结果重复付费。

## 全局 LLM 容量

沿用并扩展已确认的 `LLMConcurrencyGate`：

```text
同一 runtime：total gate = 4
├── interactive：只经过 total gate，保留 1
└── background gate = 3
    ├── refill traffic：有等待者时保证至少 2，可借到 3
    └── maintenance traffic：补货活跃时最多占 1；库存为 0 时不新启动
```

### Gate 所有权

- API runtime、OpenClaw 和单次 CLI composition 各自创建一个 runtime-owned gate。
- 主 `LLMService`、`SoulEngine` 内部 service 和 dialogue fallback 注入同一对象。
- 单元测试或孤立库调用未注入 gate 时仍可创建私有兼容 gate；正式 runtime 装配测试必须
  断言对象身份相同。
- `bypass_semaphore=True` 只允许绕过后台分类限制，不能绕过 total provider 上限。

### 流量分类

- **interactive：** `soul.dialogue*`、`api.sentiment`。
- **refill.expression：** 推荐文案预生成，补货最高优先级。
- **refill.evaluation：** 候选批评估。
- **refill.supply：** 库存缺口直接触发的关键词/原料生成。
- **maintenance：** 其它 `soul.*`、非缺货型 discovery、delight、speculation、purge 等。
- 未分类 caller warning 一次并默认为 maintenance，不能静默绕过额度。

### 补货额度语义

补货“保底 2、最多 3”不是额外 provider pool，而是 background=3 内的 admission 规则：

- `available < target` 且 refill waiters 非空时，新 maintenance provider 请求最多一个。
- refill 没有可运行任务（无 raw、平台节流、provider backoff）时，maintenance 可借用空槽，
  保持 work-conserving。
- `available == 0` 时，新 maintenance LLM 请求 park；refill 可使用三个后台槽。
- 已在 provider 中的 maintenance 不抢占。它完成后，等待中的 refill 优先获得释放槽。
- “保底两个槽”约束新 admission；从健康态切换时已经运行的 maintenance 不被计入可
  抢占资源，保证在这些旧调用自然释放后生效。
- 文案等待者优先于下一批 evaluator，因此首个评估 batch commit 后，释放槽先完成
  “评估结果 -> 可服务文案”闭环，再补评估 fan-out。

## 持续评估与端到端补货

保留现有分支的单一 `CandidateEvalCoordinator`、claim token、并行 evaluate 和串行 commit
设计，补货决策顺序调整为：

```text
canonical inventory snapshot
  -> recover eligible suppressed rows
  -> admit already-evaluated rows without LLM
  -> generate pending expression copy
  -> continuously evaluate pending raw
  -> request new platform supply only when projected inventory is insufficient
```

### 评估 worker

- 配置上限 3，每个 provider batch 最多 30 条，总 in-flight raw 不超过 90。
- 任一 worker 完成后，commit lane 条件写回 claim token，并在 projected inventory 仍不足时
  立即领取下一批；不等待其它慢 worker。
- 60 秒只作遗漏通知的 safety wake，不是正常吞吐节拍。
- commit/admission 串行，provider 调用并行；任何 SQLite 事务都不得跨 LLM gate 等待。

### Projected inventory

调度使用 durable 计数：

```text
projected = available
          + admitted_pending_copy
          + evaluated_pending_admission
```

只有已经通过评分/准入语义的行可以计入 projected；普通 `pending_eval` 不计入，避免低
通过率时虚报库存。projected 达到目标后停止新 claim/抓取；已在执行的评分可写成
`evaluated`，但 commit lane 不越过目标。

### 文案微批

- pending copy >= 8：立即启动。
- 1–7：从首条进入 pending 起最多等 3 秒；重复通知不延长 deadline。
- provider batch <= 30，batch fan-out <= 2，并继续受 refill/background gate 限制。
- 同一 runtime 只有一个 collecting timer 和一个 running copy task；运行期间的通知合并为
  一次 durable rerun 检查。
- 文案不阻塞 evaluator worker 补位，但当文案和下一批评估同时等待后台槽时，文案优先。

## 失败分类与退避

### Transient provider failure

429、provider cooldown、连接错误、超时和 5xx 不因 batch 大小而改善：

- 不递归拆成单条请求。
- 释放或保留可恢复 claim 状态，按 provider Retry-After 或 15/30/60/120/300 秒退避。
- 退避期间不忙循环，也不把候选记成低分。

### 成功响应但结构损坏

只有 provider 成功返回、但 JSON/schema 缺项时才允许拆批：

- 仅重试缺失项，不重跑已经成功解析的候选。
- 拆分深度和总额外请求数有上限。
- 单条仍损坏时保留 pending copy/evaluated 状态并进入有界重试，不回到候选重新评分。

### 零进展

- 文案一轮成功写入数为 0 时至少等待 15 秒。
- 连续成功评分但零 admission 使用现有 60/120/300 秒供给退避，不降低质量阈值。
- auth/config 错误 park 到配置恢复；热重载通过 token 条件释放旧 claim。

## 生命周期

### 启动

1. 释放上次进程遗留的 evaluating claim。
2. 创建一套共享 LLM gate。
3. 执行历史库存恢复与安全维护。
4. 启动候选协调器和文案微批调度。
5. 根据 canonical inventory 决定 idle、refill 或等待 supply。

### 热重载/关闭

- 先阻止旧协调器 claim 新行。
- 取消 collecting timer 和未进入 provider 的等待任务。
- 用 claim token 条件释放未完成行，等待 commit lane 收敛。
- 旧 gate 不与新 runtime 共享；新 runtime 仅在旧任务停止后开始。
- 迟到结果 token 不匹配时只记录 stale completion，不能覆盖新状态。

## 可观测性

### Pool maintenance 日志

每轮一个结构化汇总：

- `available_before` / `available_after` / `target`
- `protected_available`
- `recovered_suppressed`
- `trimmed_ready_reserve` / `trimmed_evaluated` / `trimmed_raw`
- `trimmed_by_source`
- `deferred_topic_trim` / `deferred_source_trim`
- `raw_before` / `raw_after` / `raw_ceiling`
- `rolled_back` 与脱敏原因

不再只记录 `raw_trimmed=N`，避免无法判断是否误伤可用库存。

### Runtime 状态

- `pool_available_count` / `pool_target_count`
- `candidate_eval_state` / workers / in-flight / pending
- `expression_pending_count` / batch state / deadline
- `llm_total_concurrency = 4`
- `llm_background_concurrency = 3`
- `llm_refill_active` / `llm_refill_waiting`
- `llm_maintenance_active`
- `inventory_priority_state = healthy | refill | empty`

状态来自 gate 和数据库真实计数，不从配置值或任务数推测。

## 配置与兼容

- 默认 `llm.concurrency=4`；显式旧值不改写。
- `candidate_eval_concurrency=3` 保持现有分支设计。
- 补货保留数 2、后台总数 3、文案 8/3/30/2 先作为带校准注释的内部常量，避免增加
  用户必须理解的相互约束配置。
- 本设计新增的 source family 与库存修复不需要破坏性迁移；来源在读取/维护时规范化，
  历史行无需批量重写。持续评估分支已有的 `claim_token` additive migration 保持不变。
- 自动恢复只改变 `pool_status='suppressed'` 且当前仍合格的行，绝不复活 shown、purged 或
  disliked 行。
- API 新增状态字段保持向后兼容，旧客户端忽略未知字段。

## 提交与发布顺序

在 `codex/continuous-candidate-evaluation` 同步最新 `main` 后，按可独立验证的顺序提交：

1. `fix: normalize pool source families including zhihu`
2. `fix: preserve available inventory during pool maintenance`
3. `fix: recover eligible suppressed pool inventory`
4. `feat: share runtime-wide llm concurrency gate`
5. `feat: reserve background capacity for refill traffic`
6. `perf: microbatch expression copy and bound transient retries`
7. 文档、状态字段和真实验证记录

前三个正确性提交可以优先发布或 cherry-pick；后续调度提交失败时可回滚而不丢失库存
正确性修复。

## 测试

### 库存正确性

1. 用户 A 形状：available `0 -> 16`、raw 618、ceiling 600；topic/source/raw 全部维护后
   available 至少为 16。
2. 用户 B 形状：10 条知乎 ready + 超限 `zhihu-*` raw；维护后 ready 不归零，所有知乎
   子来源归入 `zhihu`。
3. 小红书、B 站、知乎以及混合来源分别验证 protected IDs 先于 source quota。
4. available > target 时只允许裁 surplus，结果不得低于 target。
5. 人为制造不变量违反，维护事务 rollback，原 pool_status 全部保持。
6. 自动恢复排除 viewed/recommended/disliked/purged/shown/self-XHS/unlinkable，并按缺口封顶。
7. 所有启用平台和策略来源 family mapping 参数化覆盖。

### 并发与调度

1. 总 provider active <= 4，background active <= 3。
2. 两个不同 `LLMService` 共用 gate 时不能各跑满。
3. 三个后台槽有 refill waiters 时 maintenance 新请求最多一个；refill 至少取得两个。
4. 没有可运行 refill 时 maintenance 可借空槽；新的 refill 到达后优先取得下一个释放槽。
5. available=0 时新 maintenance park，三个 refill 可进入。
6. 文案等待者优先于下一批 evaluator。
7. 一个 evaluator 完成后立即 refill，不等待其它慢 worker或 60 秒 tick。
8. 取消、异常、热重载不泄漏 total/background/class permits。

### 错误处理

1. 429、timeout、connection、5xx 不触发递归 single fallback。
2. malformed JSON 只重试缺失项，额外请求有界。
3. 文案零进展等待至少 15 秒。
4. claim token stale completion 不写回。

### 真实环境

使用临时 SQLite 和临时 Memory：

1. 只读抓取最多 8 条公共 B 站候选。
2. 真实 provider 执行评估 -> commit -> 文案 -> canonical available。
3. 人为添加 raw 令其超过 ceiling，再运行统一维护事务。
4. 断言 available 不下降、raw 回到 ceiling、知乎/小红书 family 统计正确。
5. 持续消费并持续入队，记录 worker 补位间隔、首条可用耗时、文案 batch size、真实并发
   峰值和 transient retry 数。
6. 不读取或修改用户生产数据库，不进行关注、收藏、点赞或历史写入。

最后运行 Ruff format/check、MyPy、相关 focused tests、完整 `pytest`，以及扩展端 npm test、
typecheck 和 build。并发/取消测试做至少 50 轮 deterministic soak。

## 验收标准

1. 两个生产日志形状均无法再让维护动作把库存清零。
2. 所有维护满足 `available_after >= min(available_before, target)`；违反时原子回滚。
3. 升级已有数据库后，合格 suppressed 候选在发起新 LLM 请求前自动恢复。
4. `zhihu-*` 在所有 pool 统计、trim 和日志中均显示为 `zhihu`。
5. pending 持续充足时，任一 evaluator 提交后 1 秒内补下一批，没有固定 60 秒空档。
6. provider 总并发不超过 4；补货活跃时至少得到两个后台槽，空库存时可用三个。
7. 三个慢后台请求运行时交互仍可取得保留槽。
8. 文案 8 条立即、尾批最迟 3 秒；单批 <=30、fan-out <=2。
9. transient provider failure 不再演化为递归单条请求风暴。
10. 不包含 Soul token/cost 行为改动，不与独立成本优化工作重叠。

## 文档影响

实施同步更新：

- `docs/modules/storage.md`：维护事务、保护层级、恢复和来源族。
- `docs/modules/runtime.md`：库存优先状态和补货额度。
- `docs/modules/llm.md`：共享 gate 与流量分类。
- `docs/modules/discovery.md`：持续评估和 projected inventory。
- `docs/modules/recommendation.md`：文案微批和失败语义。
- `docs/modules/config.md`、`config.example.toml`：默认并发和派生额度。
- `docs/architecture.md`、`docs/spec.md`、README 中英文图示。
- `docs/changelog.md`：库存正确性、知乎归一化和补货保障。
