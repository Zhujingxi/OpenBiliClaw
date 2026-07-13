# 全局 LLM 槽位保留与文案微批设计

**日期：** 2026-07-12

**状态：** 已确认方案，待实施计划

**范围：** `LLMService` 全局并发、候选评估的本地并发上限、候选入池后的文案预生成调度、API/OpenClaw 运行时装配、相关配置与可观测性

**不在范围：** 模型或 provider 迁移、候选评分/准入阈值、推荐排序、平台抓取限流、跨进程分布式并发

## 背景与问题

持续候选评估已经改为 work-conserving worker pool：目标库存有缺口时，任一评估槽完成便立即补下一批，不再固定等待 60 秒。现有实现仍有四处容量边界没有统一：

1. API/runtime 的主推荐链路和 `SoulEngine` 会分别创建 `LLMService`，每个实例各有一个容量为 `llm.concurrency` 的 semaphore；dialogue 的无注入 fallback 还会再创建实例。现有值实际上是“每个 service 实例上限”，而不是整个 runtime 的全局上限。
2. 候选 worker 数按 `min(candidate_eval_concurrency, max(1, llm.concurrency - 1))` 计算，但这只限制候选评估自身；入池后的推荐文案预生成仍可占用“预留”的最后一个槽，因此交互请求没有严格容量保证。
3. API runtime 把 `DiscoveryConcurrencyController.llm_evaluation_concurrency` 固定为 2，OpenClaw 则固定为 4。即使配置为全局 4、候选评估 3，API 实际评估并发仍只有 2，真实吞吐由多个不一致的 gate 中最小值决定。
4. 文案预生成目前在第一条候选提交后立即启动。模型对很小的结构化输出批次并不一定更快：2026-07-12 的真实 provider 冒烟中，2 条和 6 条批次出现了结构化 JSON 失败并进入拆分/单条回退；同模型的 8 条手动批次表现更稳定。该观察样本较小，不能当作永久模型特性，但足以支持一个很短、可被数量提前打断的微批窗口。

因此本次不再增加彼此独立的“评估并发”和“文案并发”总量，而是在全局 LLM gate 前增加统一的后台额度，并把文案启动改为“满量立即、尾批短等”。

## 已确认参数

| 参数 | 采用值 | 含义 |
|---|---:|---|
| 全局 LLM 并发 | 4 | 同一 runtime 共享 gate 下最多四个 provider 请求在执行 |
| 后台共享并发 | 3 | 候选评估、文案预生成及其它后台 LLM 工作合计最多三个 |
| 交互保留槽 | 1 | 后台工作不能占用第四个槽；交互可在三个后台请求运行时立即取得剩余容量 |
| 候选评估并发 | 3 | 保持现有配置默认值，并受后台共享额度约束 |
| 文案触发阈值 | 8 条 | 待生成达到 8 条立即启动，不继续等时间窗 |
| 文案最大等待 | 3 秒 | 1–7 条最多聚合 3 秒，之后处理尾批 |
| 单次文案批大小 | 30 条 | 保持现有上限，不扩大单请求输出风险 |
| 文案批并发 | 2 | 保持现有引擎并发；同时仍受后台共享并发 3 限制 |

`8 条 / 3 秒` 是基于上述真实 provider 冒烟和交互补货时延之间的初始折中。它不是质量阈值。更换 provider/model 后，必须用结构化输出失败率、回退率、首条可服务延迟和 token 成本重新校准。

## 方案选择

### 方案 A：只提高全局并发

把 `llm.concurrency` 提高到 4，保留各模块现有 gate。改动小，但无法阻止三个评估请求加一个文案请求占满全部四槽；聊天只能排队。API 固定为 2 的发现层上限也仍会使配置失真。

### 方案 B：按优先级排队，不保留容量

继续只使用 `PrioritySemaphore`，把 `soul.dialogue*` 提升到最高优先级。它能让聊天在“下一个槽释放时”优先，但不能抢占已经运行的四个后台请求；慢请求期间仍可能长时间无交互容量。

### 方案 C：全局总 gate + 后台共享 gate（采用）

同一 runtime 创建一个共享 gate，并把它注入所有 `LLMService` 实例。所有请求经过该 gate 中容量为 4 的全局优先级 semaphore；后台请求在此之前还必须取得容量为 3 的共享后台 permit。候选评估、文案预生成、画像、关键词等后台任务竞争同一个后台池，而交互请求只经过全局 gate。

这样既保留现有优先级排队，又从容量上保证后台最多占用 `N - 1` 个全局槽。它也把 API 和 OpenClaw 的运行时差异收敛到同一派生规则。

## 并发架构

```text
                         同一 runtime 的 LLMConcurrencyGate
后台请求 ── acquire background gate (3) ──┐
                                         ├── acquire global PrioritySemaphore (4) ── provider
交互请求 ─────────────────────────────────┘
       ▲                 ▲                 ▲
 主 LLMService     SoulEngine service   dialogue/fallback
```

### 两层 gate 的职责

- **全局 gate** 是所有 provider 请求的最终上限，并继续按 caller priority 决定等待者顺序。
- **后台 gate** 只限制后台执行中和等待全局槽的请求总数，默认容量为 `max(1, llm.concurrency - 1)`。
- **gate 的所有权属于 runtime composition root**，不属于单个 `LLMService`；service 只持有引用。热重载创建一个新 gate，并在旧 runtime task 收敛后释放旧对象。
- 当 `llm.concurrency >= 2` 时，后台不能占满全部全局容量；交互请求不需要后台 permit。
- 当用户明确配置 `llm.concurrency = 1` 时，后台容量退化为 1，无法严格保留交互槽。系统不制造零容量死锁，但配置文档和状态需明确这一退化语义。

后台请求的获取顺序固定为“后台 gate → 全局 gate”，释放顺序相反。任何路径都不能先持有全局槽再等待后台 permit；取消、超时和 provider 异常必须通过同一上下文管理路径释放两个 permit。数据库事务、candidate claim commit 和文案缓存写入均不得跨 gate 等待持有。

### 流量分类

caller 标签是容量分类和优先级的唯一依据，避免调用点各自维护 semaphore：

- **交互：** `soul.dialogue`、`soul.dialogue.tools`、`soul.dialogue.tool_followup`、`api.sentiment`。
- **后台：** `discovery.*`、`recommendation.*`、除 dialogue 外的 `soul.*`、`sources.*`、`runtime.*`、`yt_search.*`、`pool_purge.*`、`eval.*`。
- **未知或未标记 caller：** 为了不让新调用静默绕过后台预算，开发/测试环境记录 warning；容量上默认按后台处理。新增用户交互入口必须显式加入交互白名单。

`soul.dialogue*` 的优先级提升为最高级；后台中现有推荐文案、发现评估等相对优先级可以保留。优先级解决“谁先得到下一个槽”，后台 gate 解决“后台最多占几个槽”，两者职责不能互相替代。

### 严格保留的边界

“保留一个交互槽”表示：在单个 runtime、共享同一 `LLMConcurrencyGate`、`llm.concurrency >= 2` 且所有调用都经过注入该 gate 的服务的前提下，后台 provider 请求同时运行数不超过 `N - 1`。它不表示请求可抢占 provider 已接受的调用，也不覆盖另一个进程或绕过服务直接调用 provider 的代码。

测试必须枚举仓库中的 caller 标签，确保所有已知调用都被明确分类；运行时 warning 用于发现后续新增的漏标路径。

### Service 实例收敛

- API runtime 和 OpenClaw bootstrap 各自只创建一个 `LLMConcurrencyGate`，主 `LLMService` 与 `SoulEngine` 内部 service 注入同一对象。
- `SocraticDialogue` 已注入 service 时继续复用；无注入 fallback 改为优先复用 `SoulEngine` 的 service，不再按一次会话新建独立 semaphore。
- CLI 的一次性 composition 可以创建自己的 gate，但同一命令内构造出的 recommendation、soul 和 dialogue service 必须共享它。
- `LLMService` 仍允许在单元测试或独立库用法中未传 gate；此时为该孤立实例创建私有 gate，保持构造兼容。正式 runtime 装配测试必须断言关键 service 引用的是同一个 gate。

## 运行时并发收敛

增加统一派生函数：

```text
background_llm_concurrency = max(1, llm.concurrency - 1)
candidate_eval_workers = min(candidate_eval_concurrency, background_llm_concurrency)
```

API runtime 和 OpenClaw bootstrap 都使用 `background_llm_concurrency` 构造 `DiscoveryConcurrencyController.llm_evaluation_concurrency`，删除当前分别硬编码 2 和 4 的行为。发现层 semaphore 仍作为本模块 fan-out 防线，但不得比全局后台额度更低而无配置依据。

默认配置从 `llm.concurrency = 3` 调整为 4，`candidate_eval_concurrency = 3` 保持不变。对应的配置 dataclass、API schema、示例配置、桌面端/插件默认展示、配置文档和测试需同步。已有用户显式保存的并发值不被迁移或覆盖；只有缺省值采用 4。

## 文案微批调度

### 触发规则

候选 commit 后仍不等待文案生成完成，评估 worker 可以立即补位。协调器只负责发出/合并文案预生成请求：

1. `committed_pending_copy >= 8`：立即启动一次预生成。
2. `1 <= committed_pending_copy < 8`：建立一个从首条待生成候选开始的 3 秒 deadline。
3. deadline 内新 commit 使数量达到 8：取消剩余等待并立即启动。
4. deadline 到期仍不足 8：处理当前尾批，不能无限等待“凑满”。
5. `committed_pending_copy == 0`：不启动 timer，不调用 LLM。

批次交给现有 recommendation engine 后，仍以单批最多 30 条、最多两个并行批执行。微批阈值 8 只决定何时启动，不把 engine 的实际 batch size 固定成 8；积压 30 条时仍可一次处理 30 条，积压更多时由现有分批和并发逻辑处理。

### 单飞、合并与不丢唤醒

同一 runtime 最多存在一个文案调度 timer 和一个正在执行的 post-commit copy task：

- copy 执行中收到新 commit，只设置 `rerun_requested` 并更新 generation，不再启动第二个 copy task。
- copy 完成后重新读取 durable pending copy 数，而不是信任旧计数；仍有积压则重新应用 `>= 8` 或“最多等到原/新 deadline”的规则。
- 3 秒 deadline 以 monotonic clock 记录。重复通知不能把同一尾批的 deadline 不断向后延长。
- `Event + generation` 协议覆盖 timer 建立、event clear、copy 完成和准备休眠之间的竞态，确保一次 commit 至少留下可观察的数量变化或 generation 变化。
- runtime stop、热重载或初始化失败必须取消 timer/copy task，等待取消收敛并清理状态；迟到的旧任务不能在新 runtime 上重复写入。

### 库存计算

`available + committed_pending` 继续作为候选补货的 projected inventory。文案短等最多 3 秒不应触发重复候选评估；只有 durable copy 失败、候选失效或 cache 写入未成功时，后续 canonical 计数才重新暴露缺口。

文案生成失败沿用现有拆分/单条回退和日志，不把候选退回 `pending_eval`。失败后的重试由有界 backoff 控制，不能在 `rerun_requested` 下形成零间隔循环。

## 状态与可观测性

运行时状态至少暴露或记录以下派生值，便于确认真实生效并发而不是只看配置：

- `llm_total_concurrency`
- `llm_background_concurrency`
- `candidate_eval_workers`
- `expression_pending_count`
- `expression_batch_state`：`idle | collecting | running | backoff`
- collecting 状态的 deadline / remaining（日志可用毫秒，API 可用秒）

日志需包含 batch item count、等待聚合时长、结构化输出回退次数、provider latency 和最终成功写入数。不得记录 prompt、API key、Cookie 或完整用户画像。

## 配置与兼容性

- 不新增用户必须理解的“后台并发”配置；它由 `llm.concurrency - 1` 派生，避免出现全局 4、后台 7 之类互相矛盾的组合。
- 文案阈值 8、最大等待 3 秒和现有批上限 30 / 并发 2 先作为内部命名常量，并用校准来源注释。若后续真实指标证明需要用户调节，再通过独立设计暴露配置。
- `candidate_eval_concurrency` 继续允许 `1..8`，但状态/API 显示派生后的实际 worker 数。
- 老配置显式写 `llm.concurrency = 3` 时，后台额度为 2、候选实际 worker 最多 2；兼容行为可预测，不强制改写用户文件。
- 单次 CLI/测试若构建独立 `LLMService`，仍遵守其自身的总/后台 gate；mock provider 不需要实现额外接口。

## 测试策略

### 单元测试

1. 三个后台请求可同时进入，第四个后台请求等待；此时一个交互请求仍可取得第四个全局槽。
2. 任意时刻 provider 活跃请求不超过 4，后台活跃不超过 3。
3. 交互等待者优先于后台等待者获得新释放的全局槽。
4. 后台请求在等待全局 gate 或 provider 执行时被取消，两个 permit 都能释放；异常路径同样验证。
5. `llm.concurrency = 1` 无死锁并呈现退化语义。
6. 两个不同 `LLMService` 共享同一 gate 时仍满足总活跃 4 / 后台活跃 3，不能各自跑满。
7. API/OpenClaw/CLI composition 中主 service、SoulEngine 和 dialogue 共用同一 gate；dialogue fallback 不另建私有 gate。
8. 所有仓库内 caller 标签被分类；未知 caller 记录 warning 并按后台处理。
9. API runtime 与 OpenClaw 在全局 4 时均把发现层和候选 worker 设为 3；显式全局 3 时均为 2。
10. pending 达到 8 时立即执行；1–7 条用 fake monotonic clock 验证恰好在 3 秒尾批执行。
11. 收集期间达到 8 会提前唤醒；重复 commit 不延长最初 deadline。
12. copy 执行期间的多次 commit 合并为一次后续判断，且没有并行 copy task。
13. stop/reload 取消 collecting timer 和 running copy，不遗留 task 或重复 cache 写入。
14. 文案失败/回退后有界重试，不形成忙循环；projected inventory 计数保持一致。

### 集成与真实请求验证

- 用可控慢 provider 运行“3 后台 + 1 交互”，记录开始/结束时间，证明交互不等待后台完成且总活跃数不超过 4。
- 用临时 SQLite/Memory 跑候选评估 → commit → 微批文案 → cache 的完整链路，验证 8 条立即、尾批 3 秒、claim token 归零和可服务数量。
- 使用本机真实 provider 做小规模手动 smoke，至少记录实际评估并发、交互首 token/首响应等待、文案 batch size、结构化输出回退和总耗时。真实调用失败不得用 mock 结果冒充通过。
- 真实 B 站源测试只读公共候选并写临时数据库，不修改账号、Cookie、收藏或线上数据。

## 文档同步范围

实施提交必须同步：

- `docs/modules/llm.md`：全局/后台双 gate、caller 分类与优先级。
- `docs/modules/discovery.md`、`docs/modules/recommendation.md`：实际候选 worker 和文案微批状态机。
- `docs/modules/config.md`、`config.example.toml`：默认并发 4 与派生后台额度。
- `docs/architecture.md`、`docs/spec.md`、`README.md`、`README_EN.md`：跨模块 LLM 容量与候选到文案的数据流。
- `docs/changelog.md`：本 PR 的性能与响应性变化。

CLI 命令、安装流程、产品定位均不改变，因此 `docs/modules/cli.md`、安装器文档和 README 版本亮点无需因本次内部调度单独变更；若实施中实际触及这些表面，再按仓库文档清单补齐。

## 验收标准

1. 缺省配置为全局 4、后台共享 3、候选实际 worker 3；API 与 OpenClaw 一致，runtime 内所有 LLM service 共用同一个 gate。
2. 三个慢后台 LLM 请求运行时，交互请求可以使用保留槽，且第四个后台请求不能进入 provider。
3. 候选持续到达时评估无固定空等；文案不阻塞候选 worker 补位。
4. 文案待生成达到 8 条立即启动；不足 8 条的尾批最迟在首条等待 3 秒后启动。
5. 文案单批上限仍为 30、引擎批并发仍为 2，所有后台 LLM 合计不超过 3。
6. 取消、异常、热重载和结构化输出失败均不泄漏 permit、不遗留 task、不重复 claim/cache，也不产生忙循环。
7. 自动化测试、真实 provider smoke 与关键状态指标共同证明“配置值、派生值、真实活跃数”一致。
