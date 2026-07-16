# Runtime Module

每个 **API daemon** runtime generation 只拥有一个 expression copy coordinator：8 条立即、尾批固定 3 秒、单轮最多 60，零进展退避 15 秒，60 秒仅作 safety wake。停止 generation 会取消 collector、gate waiter 与运行中的 provider callback；状态接口暴露 pending/state/deadline/last completed/error。OpenClaw direct composition 不启动 daemon loop，故不创建该 coordinator；它在 inline admission 后同步 drain 最多 4 条 durable copy，且不在同一交互请求内做 split retry：有效 subset 立即入 canonical pool，剩余行保持 pending 供下次请求处理，返回前没有遗留 provider/copy task。参数来自 2026-07-12 生产日志校准。

> API runtime 启动时从 `Config.models` 构造一个不可变 `RuntimeModelBundle(revision, models, chat_route, llm_service, embedding_service)`。完整 Chat/Embedding adapter、服务与所有 downstream consumer 都先在锁外构造；成功后才在同一个短、无 `await` 的 publication section 中切换 bundle 与 consumer graph。

`RuntimeContext` 只维护一个 current bundle 和一个 swap lock。新调用在 publication 后取得新 bundle；已经捕获旧 service/route 的在途调用继续安全完成。失败的 adapter/consumer 构造、gate 校验或配置事务不会发布部分 graph；rollback 恢复之前 bundle 及 Soul/Dialogue/Discovery/Recommendation/controller 等精确对象身份。

gate 属于 `RuntimeContext` 的稳定部分，容量只读取 `models.chat.concurrency`，并且只在 candidate 已完整构造、publication 即将成功时原地更新。旧、新 route 因此始终竞争同一个真实总上限；失败 candidate 不会提前改变容量或 inventory state。

模型 candidate 的 adapter 构造可以和普通配置写并行，但完整 consumer graph 不再携带构造开始时的全配置快照。`ModelConfigService` 进入 canonical writer、重读并完成模型 authority/revision 判定后，会调用同步 `restage_model_candidate()`，把已经构造好的 route/service 重新挂到当前 `RuntimeContext.config` 的完整配置图上，再写盘和短锁发布。因此普通 writer 在 candidate 等待期间提交的 scheduler/source 等非模型字段会同时保留在磁盘和 live controller；restage 失败时不写盘、不 swap。

guided-init 的 `InitPrereqs.chat_ready()` 只读取 production `OrderedLLMRoute.connections`，按配置顺序直接探测每个 connection adapter，第一条健康连接即通过；primary 不健康时会继续验证 fallback。生产与测试使用同一 ordered-route 协议，不再存在 vendor registry lookup 或单独的 fallback provider 构造分支。

`RuntimeContext.llm_registry` 仅是历史字段名兼容别名：它在每次 publication 时都指向当前 `RuntimeModelBundle.chat_route`，值的真实类型与语义是 `OrderedLLMRoute`。该名称背后没有旧 registry class、provider bucket、builder 或独立状态；新代码应优先读取 `model_bundle.chat_route`。

## 概述

`src/openbiliclaw/runtime/` 负责后端 daemon 的长期运行能力：后台刷新、账号同步、反馈批学习调度、运行时事件流、浏览器插件 presence gate、自动更新和任务生命周期管理。FastAPI 启动后会通过 `RuntimeContext` 持有这些 runtime 服务，配置热重载时重建可替换组件。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 扩展原生保存共享 runtime | ✅（6/6 executor + 真实账号验证） | 扩展已定义与后端一致的 `native_save` task/result allow-list、canonical HTTPS URL 规则、`NATIVE_SAVE_EXECUTE` / `NATIVE_SAVE_RESULT` 消息契约和 active-tab runner，并统一经过共享 MV3 recovery barrier。一般 runner 与 legacy dispatcher 共用 `globalThis` mutex 保护 tab 创建/加载，加载完成即释放；XHS 手动 native-save 使用 exact tokenized route + identity/control fence，可在没有精确复用页时越过后台 discovery mutex，且 alarm/runtime wake poll single-flight。执行中的任务按 task/tab 独立分桶，仍各自在单一绝对 deadline 内严格校验 final tab 与 sender URL、tab/ID/item/platform。`chrome.storage.session` 可同时记录多个 runner-owned tab，MV3 recovery 只关闭这些 ID；content 用 256 项 bounded outcome-promise cache。YouTube duplicate exact playlist 优先 checked proof，否则稳定复用一个；知乎 typed `question/answer/article` identity 适配 current `Favlists-item`；小红书适配 current `noteContainer/collect-wrapper`。2026-07-14 六个平台 favorite 与 watch-later/fallback 真实终态均为 `synced/already_synced`。 |
| 扩展原生保存 broker 与 adapter 注册 | ✅（6/6 executor + 真实账号验证） | `RuntimeContext.extension_native_save_broker` 是热重载不替换的 test-injectable 稳定实例；local/degraded construction 与 config rebuild 都注册六平台 adapter，service/router 会替换而 broker 不变。wake best-effort 发布 `<slug>_task_available`。broker poll/lease、native task/item heartbeat 与 terminal persistence 使用线程卸载的独立短连接并有界重试 SQLite lock；durable terminal state 在 heartbeat completion race 中优先。开发用 `/api/extension/reload` 返回 `delivered`，可确认至少一个 runtime-stream 订阅者收到热重载事件。 |
| 统一补货请求入口 | ✅ | `ContinuousRefreshController.request_replenishment(reason, force=False)` 收束补货触发：普通事件和反馈只排队 reason；初始化完成、用户手动刷新或推荐刷新后低库存用 `force=True` 进入手动补货。 |
| 后台刷新控制 | ✅ | `ContinuousRefreshController` 按 scheduler 配置补充候选池，并通过 source policy 计算各平台有效配比；后台定时 refresh 使用约 90% 的可换池低水位，库存只是略低于 `pool_target_count` 时不跑 discovery。async refresh / force-refresh / post-refresh 的 SQLite 原子库存维护统一经 `_enforce_pool_cap_async()` 放入 worker thread，维护再慢也不阻塞 API ping / runtime stream；启动前维护仍同步完成。注入 `DiscoveryCandidatePipeline` 后，B 站主补货会在现有 `_refresh_lock` 内按 `pending_eval + evaluating` 水位循环生产 raw candidates，直到待评估供给接近目标 batch 或达到预算；小缺口阶段先给 `search + related_chain` 配额，延后 `trending/explore`。统一关键词 planner 开启但 B 站关键词 store 暂空时，本轮只剔除 `search` 子策略，保留其它 B 站策略，避免回落到旧 `discovery.search.queries` LLM 生成。v0.3.149+ 当 `explore_refresh_hours` 到期或距到期不足一个 refresh tick，且 B 站平台族仍有补货空间时，controller 会允许 `KeywordPlanner` 在同一轮 merged keyword LLM 调用里请求 `explore_domains`，成功写入 B 站 `keyword_kind="explore"` query cache 后同步推进 `last_explore_refresh_at`；后续 `ExploreStrategy` 从该 explore 池 claim query。 |
| 启动优先的原子库存维护 | ✅ | `run_startup_maintenance()` 是每个 controller 幂等的 host 启动钩子：API daemon 的 `run_forever()` 与 OpenClaw direct bootstrap 都先调用它，才允许暴露 LLM operation 或启动后台 loop。钩子调用一次 `Database.maintain_pool_inventory()`，先零 LLM 恢复历史 `suppressed` 结果，再统一其它维护。 |
| Canonical 库存驱动的补货 admission | ✅ | `ContinuousRefreshController._pool_readiness_counts()`、原子维护结果、文案完成后的池状态和 API/OpenClaw candidate snapshot 都把 durable available 同步到共享 gate。低库存且 refill 排队时新 admission 保证两个 refill 后台槽并可借满三个；无 refill 时 maintenance 借用空槽；库存为零只 park 新 maintenance，不抢占已在 provider 中的请求。 |
| 连续候选评估协调器 | ✅ | API daemon 的 `CandidateEvalCoordinator` 是 live runtime 内唯一 claim owner：配置与构造器均硬限制为最多 3 个、每批最多 30 条，即最多 90 条 raw 在途；任一 worker 完成即补位。主协调任务串行持久化 token-owned 评分，再按 copy-aware headroom admission。B 站 refresh、抖音、YouTube、知乎、X 和 Reddit 都通过共享 `DiscoveryCandidatePipeline` enqueue；其单次 `on_candidates_enqueued` 回调立即唤醒协调器，managed refresh / producer 绝不再同步 `drain_pending()` 另领 claim。OpenClaw direct adapter 不启动 daemon loop，因此不挂接 dormant candidate / expression coordinator；其 `recommend(refresh_if_needed=True)` 的 controller refresh 专用首轮 source/evaluation wave 固定为 4（fetch oversample=1、min eval batch=4、inline evaluator=1），随后请求再补下一批。其 `on_candidates_admitted` callback 在 admission 的 DB commit 后 await `expression-copy(limit=4, max_extra_requests=0)`：首 batch 的有效 subset 先成为 canonical available，剩余行 durable pending 给下一请求，避免在 45 秒交互窗口内递归拆分。pipeline 把 callback ownership 作为 receipt 随 drain result 返回，controller 只有没有 owner 时才做 refresh 收尾 copy，故同一 admission 不会重复回调。独立/CLI 与 API daemon 保留原兼容路径（包括默认 split retry 与 API 的 60 条 copy drain）。projected 只等于 `available + admitted_pending_copy + evaluated_pending_admission`，raw pending/evaluating 不计入；超出 headroom 的达标结果保留为 durable `evaluated`。有效 worker 为 `min(candidate_eval_concurrency, max(1, models.chat.concurrency-1))`，60 秒仅作 API coordinator 的 safety wake。 |
| B 站扩展搜索兜底 producer | ✅ | `BilibiliExtensionSearchProducer` 在 B 站平台族低于 quota、`BilibiliAPIClient.search_cooldown_remaining()>0`、扩展 presence 在线且候选池未满时入队 `bili_tasks(type="search")`；扩展回传后仍进入 `DiscoveryCandidatePipeline` 统一评估。兜底关键词生成 prompt 已携带结构化画像，调用 `LLMService` 时会在支持路径上关闭额外 core memory 注入；统一关键词 planner 会把画像按 core / life / interests / style / recent 分层渲染，保护 prompt-cache 前缀。 |
| 候选池文案预计算状态同步 | ✅ | 独立 `_loop_pool_precompute()` 将 fresh 候选补齐 `pool_expression` / `pool_topic_label` 后，会同步更新 `last_replenished_count` 并推送 `refresh.pool_updated`；推荐文案 batch 默认 30 条、2 个 worker 并发生成，但仍受 `_expression_lock` 串行化多入口，避免重复消费同一批候选。推荐消费的 `pool_status='shown'` 仍在 detached event-loop task 中同步提交这个有界小批量 UPDATE，以保护响应时延并避免共享 SQLite 连接与未完成 worker 的关闭竞态；只有 durable write 成功后才调用 `RuntimeContext` 生命周期内唯一的 post-commit callback。该 callback 每次动态读取当前 controller/target，再通知 API 的稳定 event subscriber，因此多次热重载后旧 engine 的迟到任务也不会写回旧 target，新的 engine 仍会发布最终 `refresh.pool_updated`；写或 subscriber 失败均不阻断推荐响应。 |
| 候选池真实可换计数 | ✅ | `pool_available_count` 现在只表示后端当前可立即 `serve()` 的候选，并按默认每 `topic_group` 最多 3 条的候选窗口计数；runtime status / runtime stream 另带 `pool_raw_count`、`pool_pending_count`、`pool_pending_eval_count`、`pool_evaluated_pending_count` 区分素材库存、待评估和已评估待入池内容。API 的 runtime status 组装和推荐消费后的 pool snapshot 会通过 worker thread 读取这些 SQLite 计数，成熟库查询不会同步阻塞事件循环。 |
| embedding 后台预热 | ✅ | refresh 完成前只保证候选入池与文案可用；`prewarm_supergroup_embeddings()` / `prewarm_pool_mmr_embeddings()` 作为后台 task 运行，慢本地 embedding 后端不会占住 refresh lock 或让界面长时间停在“正在补货”。v0.3.124+（lever 4）：`prewarm_pool_mmr_embeddings()` 返回值区分良性冷启动与真故障——`-1`（无 embedding service / 空池，没东西可暖）让启动重试包装器 `_safe_prewarm_pool_mmr_embeddings` 平静跳过(不再每次装机刷 5 行 `warmed=0 — retry`)，`0`（有候选但全嵌入失败＝后端不可达）才重试到底并在放弃时打 WARNING 点名 embedding 后端不可达、MMR 降级。v0.3.148+ search / trending / explore / `KeywordPlanner` 的 query profile summary 也只通过 `EmbeddingService.lookup_cached()` 读取已缓存向量来保持 interest / dislike 多样性；缺缓存时按权重顺序降级，绝不在查询生成热路径新发 embedding 请求。 |
| YouTube 后台 discovery producer | ✅ | `YoutubeDiscoveryProducer` 独立运行 `yt_search` / `yt_trending` / `yt_channel`，只在 YouTube 平台族低于 quota 时由 `_loop_youtube_producer()` tick，按每日 ledger 和 `min_interval_minutes` 控制执行。 |
| X 后台 discovery producer | ✅ | `XDiscoveryProducer.produce_if_due()` 在 X 平台族低于 quota 且源健康就绪时，由独立 loop tick 触发 `search` / `feed`（For-You）/ `creator`（账号订阅）三个策略；按 `daily_*_budget` / `min_interval_minutes` / `request_interval_seconds` 节流，For-You 压到很低的每日频次并在连续失败后自动暂停。只 enqueue raw candidates 进 `discovery_candidates`，不写 `content_cache`、不调评估器。`enabled=false` 时是 no-op，不 import `twitter_cli`。 |
| Reddit 后台 discovery producer | ✅ | `RedditDiscoveryProducer.produce_if_due()` 在 Reddit 平台族低于 quota 且 `[sources.reddit].enabled=true` 时，默认通过随 OpenBiliClaw 安装的 `rdt-cli` 登录态命令后端触发 `search` / `hot` / `subreddit` / `related` 四个分支；已连接插件会同步 `reddit_session` 到 rdt-cli credential store，命令后端不可用或未登录时 fallback 到已安装浏览器插件的真实 `reddit.com` 登录态任务。四个分支各自有独立 daily budget，默认每类 300。producer 只 enqueue raw candidates 到 `discovery_candidates`，不写 `content_cache`、不同步跑 LLM 评估，正式 admission 由共享 evaluator 异步完成。 |
| X 源健康状态机 | ✅ | `storage/x_health.py` 的 `XSourceHealthStore` 持久化 `ok` / `missing_cookie` / `expired_cookie`(401) / `blocked`(403) / `rate_limited`(429) 五态；按 code 分别退避，429 带 `cooldown_until` 自愈，401/403/missing 须等用户重新登录 x.com 才恢复；连续 For-You 失败触发 `feed_allowed()=false` 自动暂停。状态经 `GET /api/sources/x/status` 暴露到插件设置页。 |
| 运行时频率配置 | ✅ | `refresh_check_interval_seconds`、行为触发阈值、trending / explore 间隔、单轮发现上限、惊喜队列加载数量、主动推送间隔和 speculator idle tick 都从 `[scheduler]` 读取，配置热重载后重建 runtime 生效。 |
| Durable 对话失败原子性 | ✅ | `/api/chat/turns` 在对话缺失、超时、provider/service 失败或空回复时持久化 `status="failed", reply="", error=<安全分类文案>`；真实回复生成后会先持久化 `completed + reply`，再 best-effort 运行情绪/投机/认知/事件副作用，副作用失败不得把已完成 turn 改为 failed；completed 持久化本身失败时也只记录并保留非 failed 状态供恢复。相同已终结 `turn_id` 保持幂等。 |
| 推荐反馈批学习调度 | ✅ | `FeedbackBatchScheduler` 挂在 FastAPI `app.state`，`/api/feedback` 每次只标记 dirty 并触发 5 秒 debounce；burst 内多条推荐反馈 coalesce 成一次 `SoulEngine.process_feedback_batch_if_needed()`，处理期间又有新反馈时会在本轮结束后再补跑一轮，避免每条反馈都启动画像重分析。 |
| 浏览器 presence gate | ✅ | `background_llm_work_allowed()` 结合 `scheduler.enabled` 与 `pause_on_extension_disconnect` 控制 daemon-owned 后台 LLM / embedding 工作。 |
| Runtime event stream | ✅ | `/api/runtime-stream` 向扩展推送状态、Cookie sync 请求、配置重载、候选池快照和 presence 事件；background 连接时会请求小红书 / 知乎立即回传一次本地 Cookie 存在性的布尔心跳，不打开或请求平台页面。`RuntimeEventHub.publish()` 会返回是否至少有一个订阅者接收，供一次性事件判断是否真正投递。 |
| WebSocket 运行时依赖 | ✅ | 默认安装显式携带 `websockets>=13`，PyInstaller spec 显式收集 `uvicorn.protocols.websockets.websockets_impl` 与 `websockets`，避免源码 / Docker / 桌面包只安装裸 `uvicorn` 时 `/api/runtime-stream` 缺协议实现。 |
| Activity feed 状态摘要 | ✅ | `/api/activity-feed` 聚合认知更新、反馈、推荐池补货和 live summary；未初始化且还没有推荐 / 可换池 / 补货产物时，普通 `/api/events` 不会新写入 pending signals，旧的 `pending_signal_events` 也不会抢占初始化提示。初始化后 pending 文案统一为“已记下 N 个新动作，下一轮补货会拿来参考”，表示 discovery refresh 水位，不表示画像待处理队列。 |
| 桌面 Web 推荐卡链接与元信息 | ✅ | `/web` 推荐卡、稍后再看 / 收藏卡、消息抽屉内容和惊喜推荐封面都改为真实 `<a href target="_blank" rel="noopener noreferrer">`；点击上报同时绑定 `click` 与中键 `auxclick`，但不阻止浏览器原生中键 / Ctrl 或 Cmd 点击 / 右键菜单行为。`RecommendationOut` 增量暴露 `duration`、`view_count`、`like_count`、`danmaku_count`、`up_mid`，桌面卡片展示视频时长徽标和播放 / 点赞 / 弹幕统计；字段为 0 或缺失时整段隐藏，不在 X / 小红书 / YouTube 等无数据卡片上显示空元信息。B 站且 `up_mid>0` 时 UP 主名跳到 `space.bilibili.com`，其它平台保持纯文本。 |
| 桌面 Web 首屏渐进水合 | ✅ | 首屏以 `/api/ping` 判断连接，推荐与 runtime 状态各自返回即各自渲染；health / init / profile / activity / config 等次级读取不会挡住推荐卡。推荐消费后仍独立复读 runtime 库存，失败沿用 1/2/4/8 秒的资源级恢复。 |
| 桌面 Web 封面请求优先级 | ✅ | 桌面推荐仅前 4 张封面使用 `eager/high`，后续封面使用 `lazy/low`；Delight 保持 `eager/high`。 |
| 桌面 Web 动效与布局稳定 | ✅ | 根滚动启用 `scrollbar-gutter: stable`，避免内容变长时顶栏横向抖动；消息 / 活动 / 手机二维码抽屉关闭进入 `.is-closing` 退出动画，快速开关会取消未完成 close；六个主分区切换使用短 `page-enter` 淡入。新增动效统一受 `prefers-reduced-motion: reduce` 保护。 |
| 桌面 Web 设置页密度与响应式布局 | ✅ | `/web` 设置页使用独立的 `1480px` 内容上限和更紧凑的标题、表单与分段控件，不再沿用推荐页的 `1120px` 上限；普通设置 panel、模型列表、inspector、连接类型和凭据区使用清晰的单层边框。模型列表 / inspector 依据侧栏挤压后的 `desktop-main` 容器宽度在 `940px` 切换为列表→详情，通用设置在 `720px` 收为单列，避免窗口仍宽但实际内容栏已不足时发生文字黏连和控件重叠。空 Chat / Embedding route 使用本地化短标题、按类型区分的添加按钮和紧凑空状态，不再混排英文内部结构名与冗长 fallback 说明。 |
| 桌面 Web 暗色模式 | ✅ | `/web` 支持 `auto` / `light` / `dark` 三态主题，顶栏按钮和设置页分段控件共享 `obc.theme` 本地键；`auto` 不写 `data-theme`，交给 `prefers-color-scheme`，手动浅色 / 深色写入 `:root[data-theme=...]`。暗色实现只覆盖 CSS token（暖暗背景、前景、边框、语义色、overlay、shadow），不为单个组件分叉硬编码颜色；`<meta name="color-scheme" content="light dark">` 让原生控件和滚动条跟随主题。 |
| 推荐/惊喜发布时间出口 | ✅ | `RecommendationOut`、`PendingDelightOut`、推荐列表/换批、pending delight 单条/批量、手动 delight 与 proactive runtime 事件均增量返回 `published_at` / `published_label`（默认空字符串）。API 不把发现时间或推荐生成时间改写为发布时间；桌面 Web 与移动 Web 精确时间优先、相对标签兜底、缺失隐藏，精确时间按本地时区显示并提供完整时间 tooltip。 |
| 桌面 Web 滚动自动加载 | ✅ | 首页推荐列表底部 sentinel 通过 `IntersectionObserver` 触发 `/api/recommendations/append`，默认开启并保留手动“继续追加”按钮。触发判定收敛在 `autoLoadBlockReason`（`shouldAutoLoadMore` 为其布尔包装）：需满足单飞、首页可见、列表有非骨架卡、加载按钮可见、`pool_available_count > 0`，最后才校验距上次自动加载至少 8 秒。候选池见底时自动续页暂停但手动按钮仍可用。**冷却自愈（issue #115）**：当唯一拦路项是冷却且哨兵仍在视口（用户停在底部、无更多滚动/相交事件）时，`armAutoLoadCooldownRecheck` 安排一次冷却到点后的一次性复查，避免停在底部时明明有货却卡到手动滚动/点按钮才继续；页面被新内容撑高、哨兵移出视口后自然停下。设置页开关写入 `openbiliclaw.webui.autoLoadOnScroll`，关闭或重建 observer 时断开并 `clearAutoLoadCooldownRecheck`。 |
| 桌面 Web 画像编辑即时反馈 | ✅ | 画像编辑的 chip 删除和添加按钮在请求 `/api/profile/edit` 前立即禁用并标记 `.is-pending`，让慢后端下也有置灰反馈；`state.profileEditState` 仍只接受服务端响应，成功 / 失败都通过重新渲染清除 pending 或恢复 chip，避免为低频编辑面引入复杂乐观回滚。 |
| 桌面 Web 可撤销即时反馈 | ✅ | 普通推荐卡和正向/避雷探针的非聊天动作先更新本地 UI，再由共享 pending-action coordinator 保留 10 秒提交屏障；点击撤销会取消定时器且不发 API 写请求，提交失败恢复原状态，`pagehide` 会以 keepalive 立即结清未提交动作。探针聊天和推荐评论需要服务端回复或文本语义，保持直接提交，不伪装成可撤销动作。 |
| 桌面 Web 探针反馈文案 | ✅ | 消息抽屉与画像页的正向/避雷探针共用一个 domain-aware feedback helper；inline 结果与 toast 使用同一条文本，明确显示经折叠空白且最长 24 字符的探针主题（超长以省略号收束），并通过 `textContent` / `showToast(text)` 写入，避免把主题插入 HTML。 |
| 三端 probe 反馈语义 | ✅ | 桌面、移动和插件的兴趣/避雷 probe 统一使用 confirm/defer/reject/chat 语义，所有操作均有可见文字；推荐区不新增画像或对话纠偏引导入口。 |
| 桌面 Web 前端偏好键 | ✅ | `/web` 的纯前端偏好继续走 `storageGet` / `storageSet`，不写 `config.toml`：`obc.theme` 保存主题三态，`openbiliclaw.webui.autoLoadOnScroll` 保存滚动自动加载开关；设置页保存状态行会同时回显主题、换一批忽略当前和滚动自动加载状态。 |
| 桌面 / 移动 Web / 浏览器插件权威模型编辑器 | ✅ | 三端「设置 → Models」使用独立 ES module 控制器和 DOM-free 状态模块：Chat/Embedding/Runtime tab、稳定 ID 有序列表、最多 10 项、选中项 inspector、descriptor-driven 分组搜索类型选择、Embedding 唯一共享 settings、拖拽/按钮/键盘排序、窄屏 list→detail、exact draft probe、migration resolution 与 revision 冲突提示。模型 `PUT /api/model-config` 与通用 `PUT /api/config` 完全分离；snapshot 与 descriptor 分别维护 latest-request generation，snapshot-only reload 可取代旧 snapshot 而保留仍有效的 descriptor，重叠的完整加载只允许最新 descriptor 安装或报错。保存期间锁定完整模型编辑面并拒绝重入，开始 PUT 会使此前已发出的 snapshot GET 与 probe generation 失效；GET 完成后重查 latest generation、dirty 与保存锁，迟到 GET 不覆盖新草稿而只提示远端更新，stale 成功/失败均丢弃，保存成功以响应 snapshot hydration、失败保留草稿，完成后由统一 operation state 恢复保存与 probe 控件。probe 完成只在 generation + revision + kind + stable ID + 精确 draft（Embedding 含共享 settings）仍匹配时挂回原记录，切换选择不会把 A 状态写到 B；干净 reload 保留仍存在的 tab/selection。Embedding Provider route 被 local override 锁定时，enabled 开关同步锁定而共享 settings 仍可独立保存。移动 Web 直接复用 `web/shared/model-config-state.js`，以 touch-friendly sequential list→detail、Back 和 Move Up/Move Down 呈现完整 route；Saved Sync 与 Models 分节且分别只写 `PUT /api/config` 与 revisioned `PUT /api/model-config`，移动端不提供一键 Ollama。浏览器插件使用自包含状态/控制器消费相同的 descriptor、snapshot、revisioned PUT 与 exact probe，并以 sequential list→detail 呈现同一 ordered route；通用 `/api/config` 保存不携带模型字段。一键本地 Ollama Embedding 只会创建或复用空的/单一 Ollama route，保留 Chat 与 credential action；仅在当前 snapshot/descriptor 都成功、加载期间没有编辑或其他 save 时发出 PUT，已配置非 Ollama/多 Provider route、脏草稿、override 或 revision 冲突均明确拒绝覆盖。 |
| 模型 route 的 Ollama 快捷动作范围 | ✅ | 只有浏览器插件提供「一键启用本地 Ollama」route 动作。桌面 Web 的 Embedding repair 只修复当前配置指向的运行时服务，不创建或改写模型 route；移动 Web 不提供 route 快捷动作。 |
| 移动 Models 资源与草稿收敛 | ✅ | `web/js/mobile-model-settings-controller.js` 分别跟踪 snapshot / descriptor readiness；先到的 config reload 不会跳过 descriptor，失败资源可单独重试，重叠请求结束但尚未 ready 时显示可恢复状态，两者都 ready 前锁定编辑器，迟到的胜出 reload 可自动完成收敛。锁定/inert 与 `aria-busy` 独立投影：首次/在途加载和重试才是 busy，settled-not-ready/error 继续锁定但 busy=false，ready 后解锁；实时状态与重试入口位于 inert 边界之外，销毁后的通知由 disposal guard 丢弃。所有会改变草稿的操作统一刷新列表、exact-probe health 与错误投影；纯 Back 导航保留服务端错误。Chat concurrency/timeout、每个 `num_ctx`、Embedding 输出维度/相似度在共享 payload serializer 之前执行严格数值校验，空输入保持为空，非法值阻止 PUT 与 exact probe；权威 hydration 重新推导本地错误。Pydantic 422 映射保持脱敏，按 prototype-safe 稳定 ID/字段建立索引，移动端读取时也逐级要求 own property，因此 `constructor` / `__proto__` / `name` 不会触发原型回退或遮住真实映射。关闭 overlay 会解析 shell 重绘后的 live opener。 |
| 扩展捕捉 E2E 控制事件 | ✅ | local-only `/api/extension/e2e/run` 会通过 runtime stream 投递 `extension_e2e_run`，要求已安装扩展在真实平台页执行白名单 DOM 操作；`/api/extension/e2e/result` 回收插件执行结果，后端再按运行窗口匹配 `/api/events` 中自然捕捉到的事件。 |
| 兴趣探针投递保护 | ✅ | `interest.probe` 只有成功投递到 runtime stream 后才写入 `probed_domains` / `probed_axes` / `probed_distance_bands` 冷却状态；事件 payload 会带 `probe_mode` 与 `challenge`，前端离线时不会消耗 active probe。普通 `near` 探针与挑战探针使用独立 active 额度，运行时选择时仍统一仲裁。 |
| 避雷探针投递与仲裁 | ✅ | `avoidance.probe` 与 `interest.probe` 共用 proactive push 循环；每轮最多投递一个 probe，并用 `last_probe_kind` 在正向/负向都有候选时轮流选择，避免探针频率翻倍。 |
| 图片代理 API | ✅ | `/api/image-proxy` 为移动 Web 和浏览器插件代理白名单 CDN 封面图，逐跳校验 redirect，并在返回前完成类型和 10MB 大小校验；成功封面写入 `data/image-cache/`（小红书 token 归一化），并按「已消费且未保存」定期清理、保护无法重抓的封面；多模态 discovery 评估也复用同一缓存，命中时不再重新请求 CDN。 |
| 自动更新 | ✅ | `AutoUpdateService` 检查 backend git tag，支持 `/api/update-status`、`/api/runtime-status` 更新摘要、手动 check/apply、apply 锁、可信 remote / dirty worktree / fast-forward guard，并通过 runtime stream 推送后端更新事件。dirty worktree guard 豁免 `uv.lock`、未跟踪文件、纯 index-only 条目和本地 `ollama-models/`；apply 前会重置 `uv.lock` 再快进。git 命令通过 `asyncio.create_subprocess_exec` 执行，避免 Windows 长时间运行后线程池 `subprocess.run` 卡死或异常返回；tag fetch 使用 `git fetch --force --tags origin`，避免本地旧 tag 被远端重打后卡在 `would clobber existing tag`。GitHub tags API 的 403/429 限流会先尝试 GitHub tags Atom feed 兜底，兜底失败才稳定上报 `github_rate_limited`，区别于真正网络不可达的 `github_unreachable`。`detect_install_mode()` 上报 `frozen / docker / git / unsupported` 安装形态，桌面冻结包与 Docker 容器据此在前端禁用自动更新开关。**可信 remote 按规范化形式比较**（大小写不敏感、`.git` 后缀可选、`https://` 与 `git@…:` / `ssh://` 等价），手动克隆少写 `.git` 后缀不再被 `untrusted_remote` 永久拦截；镜像/代理包装 URL 不会自动折算成官方地址，镜像用户需把镜像 URL 显式加入 `auto_update_allowed_remotes`。**守卫拒绝不再静默**：每条 guard 拒绝都 `logger.warning` 写明细（含实际 remote URL、脏文件列表等），并把原因写入 `last_error` 供状态卡展示。`untrusted_remote` 覆盖**可读但被拒**的远端（内嵌凭证 / 不在允许列表），把**脱敏后的实际远端地址 + 一键修复命令**（`git remote set-url origin …`）写入 `last_error`（`_guard_detail`），状态卡「最近错误」直接可自助排查，无需翻后端日志；`_redact_remote_url` 确保内嵌凭证不泄露到 UI。**`remote.origin.url` 读空单独归类 `origin_remote_unusable`（`_refuse_unreadable_origin`）**：旧逻辑把 `git config --get` 的任何非 0 返回都当「无 origin」并建议 `git remote add origin`，但 origin 缺 url 行 / 多 url / Windows dubious ownership 时照做会撞「remote origin already exists」（用户实测的卡片矛盾）；现在读空后补探 `git remote get-url origin` 分诊——dubious ownership → `git config --global --add safe.directory <root>`（须以运行后端的账户执行）、确无 origin（`No such remote`）→ `remote add`、url 空或不可解析 → `remote set-url`、其他 git 错误 → 透出首行真实 stderr，各自把对应修复命令写入状态卡。**冻结守卫**：apply 路径显式判 `install_mode == "git"`，冻结包即便与 git 检出共用目录也以 `unsupported_install_mode` 拒绝（Docker 容器同理以 `docker_install_mode` 拒绝），杜绝无限重启循环；冻结包后台改跑 check-only 提醒循环（无论开关状态），跟踪 `desktop-v*` 安装包 tag，发现新包时设置页提示并附「前往下载新安装包」直达链接 + toast 提醒；Docker 容器同样跑 check-only 循环（跟踪 `backend-v*`，镜像随后端版本发布），发现新版时设置页提示 `docker compose pull && docker compose up -d` 升级。桌面 Web 设置页提供「立即检查 / 立即应用」按钮并随 runtime stream 更新事件实时刷新状态行；配置保存重建服务时经 `adopt_status_from` 保留上次检查结果。降级模式（LLM 注册表不可用）放行 update-status / check / apply 并构建真实 `AutoUpdateService`，便于拉取修复版本恢复。 |
| 开机自启动管理 | ✅ | `runtime.autostart` 提供 macOS LaunchAgent、Windows HKCU Run + `.pyw`、Linux XDG autostart 三套当前用户作用域 manager；`/api/autostart-status`、`/api/autostart/apply`、`openbiliclaw autostart` 和插件设置页共用 env / shadow guard 与方向化 enable/disable 事务。 |
| Ollama 启动预检与生命周期 | ✅ | `runtime.ollama_supervisor` 统一提供 `ollama_required()`、endpoint 归一化、loopback 判定和 `_ollama_is_running()` / `_ollama_start_serve_background()`；`start` 仅在默认 `localhost:11434` 需要本机 Ollama 时尝试后台拉起，远端 / 自定义端口不强行 `serve`。托管启动会给子进程默认传入 `OLLAMA_KEEP_ALIVE=24h`（若用户已设置则保留用户值），减少 `bge-m3` / `llama-server` 在 UI 请求间隔中卸载再冷启动。Windows 模型路径编码故障自愈使用 `ollama_models_relocation_candidate()` 选 `%PROGRAMDATA%\OpenBiliClaw\ollama-models`（路径含非 ASCII 时放弃自动迁移），目录存在即视作 `managed_models_dir()` 持久迁移标记；后续托管启动用 `env.setdefault("OLLAMA_MODELS", managed_models_dir)`，显式用户环境变量优先。`restart_managed_ollama_with_models_dir()` 只重启本进程管理的 Ollama；若检测到外部启动的 daemon（运行中但没有 `_managed_proc`）则返回 `external_ollama`，避免杀掉用户自己开的官方 App / 服务。`_ollama_start_serve_background()` 现在记录**亲手拉起**的 `Popen` 句柄（复用外部已运行实例时句柄留空），`stop_managed_ollama()` 据此在退出时停掉整棵进程树（Windows `taskkill /T`、类 Unix 进程组 `SIGTERM`），对外部托管的 Ollama 一律不动 —— 桌面托盘「退出」经此调用，clean quit 不再遗留孤儿 `ollama serve` / `llama-server` runner。macOS 桌面包构建必须使用官方 `Ollama.app/Contents/Resources/ollama`，并同时打入同目录 `llama-server`、`llama-*`、`lib*.dylib`、`lib*.so` 和 `mlx_metal_*`；如果只发现 Homebrew 风格单独主程序或缺关键动态库，打包会失败，避免随包 daemon `/api/version` 正常但真实 embedding 500。 |
| Embedding 初始化进度单例 | ✅ | `runtime.embedding_progress` 是进程全局、线程安全的无依赖状态源，供桌面包首启自动拉取、guided init 自动拉取、API 一键修复和 Ollama supervisor 共享。各生产路径调用 `mark_pull_running()` / `report_pull()` / `mark_pull_done()`，`/api/init-status` 再把它合并到 `embedding_check="repairing"`、`embedding_repair_*` 和 `embedding_pull_status`；`_ollama_start_serve_background()` 同步报告 `ollama_phase` 为 `starting` / `ready` / `down`。`reset()` 仅供测试隔离进程级状态。 |
| 账号同步 | ✅ | `AccountSyncService` 同步 B 站账号历史、收藏和关注等信号；历史按 `view_at + 同秒 bvid 集合` 增量导入，收藏 / 关注只把新增 ID 转成画像事件，避免重放旧信号。画像分析默认受 360 秒墙钟上限保护，超时会取消并记录可见原因，不会把账号同步循环永久占住；文案明确模型服务 6 分钟未返回、Base URL / 模型名 / 网络 / 代理 / 响应过慢等常见原因，并经 `/api/init-status.detail` 同步给三端。 |
| 多源 bootstrap 去重 | ✅ | `/api/sources/{xhs,dy,yt}/task-result` 会用 `source_bootstrap_state.json` 过滤跨任务旧 identity key；任务结果仍完整保留，只有新增项进入 memory / profile pipeline。 |
| 扩展任务 claim / 复用 | ✅ | XHS / 抖音 / YouTube bootstrap 任务在扩展 poll 时用短生命周期 SQLite 连接标记 `in_progress`，CLI 默认复用 6 小时内近期任务，避免重复打开前台 tab 全量扫描，也避免 FastAPI 并发 poll 在共享 connection 上嵌套事务。 |
| Soul 画像自动 bootstrap | ✅ | `AccountSyncService` 首次成功写入账号行为并完成 `analyze_events()` 后，若 soul 画像仍为空，会自动调用 `build_initial_profile([])`；每进程生命周期最多尝试一次。 |
| 降级模式启动 | ✅ | 生产 `create_app()` 遇到 `RegistryBuildError` 时构造 degraded `RuntimeContext`，保留健康检查、配置读取/保存、runtime status、runtime stream、`/m` 移动静态壳与 `/favicon.ico`，方便用户从 popup 或手机入口识别并修复错误配置。 |
| 不可变模型 bundle 与事务恢复 | ✅ | `RuntimeContext.build_model_candidate()` 从 `Config.models` 构造完整 `RuntimeModelBundle` 和全部 consumer，不改变 live pointer；兼容用 `swap_model_candidate()` 原子发布并只在成功后发送带 revision 的 `config_reloaded`。稳定 `_background_lifecycle_lock` 让每个 public stop/restart 整段互斥，覆盖 slot clear、registry drain、loop creation 与 post-reload one-shot scheduling；`guided_init` 仍从 drain 排除。模型 API 写盘前用 `capture_model_runtime_task_state()` 等待 settled runtime/task 快照，再无事件 activate、串行重启新 graph loops、清除 degraded 并发布只对应最终 slots 的一次事件。等待快照时取消不改磁盘/runtime；写盘后取消则由 shielded rollback 重新取得 lifecycle ownership，恢复精确旧 normal/degraded graph 并按旧 ownership 重建等价 app loops（不保留原 `asyncio.Task` 对象）。已取消 detached 旧 one-shot 不会复活，旧在途同步调用保留旧 route，新调用读取新 bundle。 |
| 权威模型配置 API 与精确探测状态 | ✅ | `api/model_config_routes.py` 把 `ModelConfigService` 暴露为 revision-guarded GET/PUT、descriptor registry 和 exact draft probe。公开 snapshot 只含 credential 状态、迁移/override、按稳定 ID 绑定的 exact probe 与 live circuit 摘要；共享 Embedding 设置参与 probe fingerprint。probe 在 gate admission 后重查 init，取得短 path lock 后再次重查 init，再捕获 revision-bound draft/`keep` credential；等待 path lock 时启动的 init 会在 credential/network 前返回 `409`。网络期间不持配置锁，完成后重查 revision；stale 结果同样返回 `409` 且不写 history/circuit。成功探测当前持久化记录只关闭同 ID/capability/current-revision circuit，edited/unsaved draft 不改变 live circuit，也不覆盖已保存记录的最近探测摘要。 |
| 全局 ordered route 热重载 | ✅ | runtime、Soul、Dialogue、Discovery、Recommendation、health/Ollama 与 OpenClaw 都使用同一个全局 ordered route；模块 override 已删除。热重载后的正向兴趣和避雷 speculator tick 仍 detached 到 `BackgroundTaskRegistry`，不阻塞配置响应。 |
| 海外网络策略热更新 | ✅ | FastAPI 启动与 `PUT /api/config` 成功落盘后都会先把 `[network].mode + proxy` 镜像到 `openbiliclaw.network`，再构造 / 重建 LLM、YouTube、更新和 Codex OAuth 客户端；`POST /api/config/probe-service kind=network_proxy` 不落盘，按当前草稿的 direct/system/custom 策略真实发起 204 探测。Docker 启动器仅在容器内检测到代理变量且用户未显式选模式时补 `OPENBILICLAW_NETWORK_MODE=system`。 |
| 原生保存 service 热重载 | ✅ | `saved_sync_service` 是可替换组件：每次构造新 `BilibiliAPIClient` 时同步创建 router + 六平台 extension adapters + `BilibiliNativeSaveAdapter` + `SavedSyncService`。重载先取消旧 registry inflight；所有新组件构造成功后才原子发布，任一构造失败保留完整旧组件与稳定 broker。 |
| 原生保存 local-first 入口 | ✅ | 自动和手动同步都复用 `SavedSyncService.create_sync_task()` / `run_sync_task()`；`POST /api/saved/{list_kind}` 先提交本地 membership。`unsupported_adapter_missing` 可重试，`unsupported_content_type` 仍为终态；已接线的六个平台 executor 都可能因扩展离线进入 `extension_required`。 |
| 桌面包 SOCKS 代理兼容 | ✅ | 默认运行依赖使用 `httpx[socks]`，PyInstaller spec 显式收集 `socksio`；用户系统配置 `ALL_PROXY` / `HTTPS_PROXY=socks5://...` 时，冻结桌面包创建 OpenAI / 兼容 LLM 客户端不会因缺少可选 SOCKS 运行时依赖而在启动阶段崩溃。 |
| 运行时图像处理依赖 | ✅ | 默认安装显式携带 `Pillow>=10.0`，因为 `discovery.multimodal` 的封面压缩路径直接 import `PIL`；不再依赖 B 站 SDK 或打包 extra 的传递依赖碰巧提供 Pillow。 |
| 运行日志降噪 | ✅ | 全局 logging 初始化会把 `httpx` / `httpcore` / `openai` / `openai._base_client` logger 提升到 WARNING，避免文件日志在 DEBUG 模式下被连接细节和完整 LLM 请求体刷屏；业务模块仍按 `logging.file_level` 输出。 |

- 桌面侧栏是 flex 行内项：按钮的 `aria-expanded` 与侧栏的 `aria-hidden` 同步，内容宽度随
  312px 侧栏平滑让渡。Delight 以主内容实际 inline-size 响应，而非只看 viewport。
- Delight 的响应式布局用 `.delight` 网格的 `grid-template-areas`（thumb/body/actions/status）分级：
  宽栏操作行贴正文列下方；`@container desktop-main` ≤940px 时缩略图与正文仍并排、操作行下沉为跨整卡
  宽度独立一行（去看看/聊一聊靠右、反馈图标靠左，issue #115 修复标题与按钮被裁）；≤560px 整卡竖直堆叠，
  ≤430px 保留窄屏内联输入框。操作行与状态行是 `.delight` 直接子节点，能脱离正文列约束跨整卡展开。
- Delight 拖拽 10px 才进入拖动态，50px 才切换卡片；滚动自动加载仍使用 50px
  root margin。前者避免点击抖动，后两者分别控制明确切换与接近视口时加载。

## 公开 API

桌面静态入口把 `/web/shared/model-config-state.js` 作为可复用、无 DOM 的状态边界，并让 `/web/assets/js/model-settings.js` 只负责 descriptor/API/DOM 编排。两者与 desktop CSS/app shell 一起进入静态资源 hash，`/web` HTML 引用会附带同一 cache-busting revision；shared mount 位于通用 `/web` mount 之前，避免被 SPA 静态目录吞掉。

模型 runtime bundle 与 coordinator 边界：

```python
bundle = build_runtime_model_bundle(
    config.models,
    revision,
    memory=memory_manager,
    usage_sink=database,
    concurrency_gate=runtime_gate,
)
candidate = await context.build_model_candidate(models, revision)
previous = await context.swap_model_candidate(candidate)
await context.restore_model_candidate(previous)
state = context.capture_model_runtime_state()
state, active = await context.capture_model_runtime_task_state(app)
previous = await context.activate_model_candidate(candidate)
await context.restore_model_runtime_state(state)
await context.stop_background_tasks(app)
await context.restart_background_tasks(app)
result = await context.probe_model_draft(connection_or_provider, embedding_settings)
closed = context.record_model_probe_success(connection_id, capability, revision)
```

- `build_runtime_model_bundle()` 构造全部 adapter、`OrderedLLMRoute`、ordered Embedding service、usage recorder 与主 `LLMService`；任一构造失败时不返回部分 bundle。
- `build_model_candidate()` 进一步 staging 全部依赖 consumer，不交换生产引用，也不发起探测请求。
- `current_model_candidate` 返回新请求将取得的不可变 bundle 身份；兼容用 `swap_model_candidate()` / `restore_model_candidate()` 在短异步锁内替换指针及完整 consumer graph。模型 HTTP lifecycle 使用 `capture_model_runtime_task_state(app)` 等待稳定 lifecycle ownership 并原子捕获 runtime token 与三个 settled slot-active flag，再用无事件 `activate_model_candidate()` 与 `restore_model_runtime_state()` 保存/恢复 normal 或 degraded 的完整 graph。同步 `capture_model_runtime_state()` 继续供已经自行持有生命周期边界的调用方使用。
- `probe_model_draft()` 构造只含目标记录的 route，Chat 使用 exact connection 调用，Embedding 使用 exact provider 与共享 settings；不遍历 fallback、不落盘。DeepSeek Chat 的探测调用无论草稿是 `high` 还是 `max` 都传入空 `reasoning_effort`，因此 adapter 发送 `thinking.type=disabled` 的 8-token 最小请求，草稿本身及正式 runtime 配置保持不变。adapter/route 归一化的 `LLMProviderError` 转成不含上游详情的 `ModelConfigProbeResult`，未知程序错误不被伪装成探测失败。
- `stop_background_tasks()` 与 `restart_background_tasks()` 都取得同一稳定 lifecycle lock；restart 通过 private unlocked stop helper 在一次 ownership 内完成 drain、loop replacement 与 post-reload scheduling，不会递归取锁，也不会让 guided-init finally restart 与模型 cutover 各自留下一个 loop set。直接 `swap_model_candidate()` 保持既有行为：成功 publication 后发布 `{"type": "config_reloaded", "revision": ...}`。模型 HTTP lifecycle 则在无事件 publication、旧 registry drain、新 app loop 重启和 degraded 清理全部完成后发布一次；`guided_init` 不参与 drain。restore/rollback 不发布成功事件，旧 app loops 按先前 ownership 重建为等价的新 task 对象；shielded restore 会重新取得 lifecycle ownership，已经取消的 detached 旧 graph one-shot 不会重建。

扩展共享原生保存基础（6/6 executor 已接、fixture 全覆盖，并于 2026-07-14 完成 favorite + watch-later/fallback 真实账号验证）：

```typescript
isNativeSaveTask(payload)
sanitizeNativeSaveResult(result)
runNativeSaveTask(task, platformSlug, authenticatedPostResult)
installNativeSaveExecutor(platform, executor)
createXBrowserEnvironment(root?, currentUrl?)
createYouTubeBrowserEnvironment(root?, currentUrl?)
createXiaohongshuBrowserEnvironment(root?, currentUrl?)
createDouyinBrowserEnvironment(root?, currentUrl?)
```

runner 只通过调用方注入的已认证 closure 回传结果；它自身不创建后端 fetch。busy mutex 只覆盖 tab create/load，加载完成立即释放；每个 executor 仍从调用起使用自己的 absolute deadline，timeout 固定回传 `failed/native_save_timeout`，迟到的 tab-create success 也会被回收。所有 listener/tab/mutex cleanup 独立 guarded。content 的 once fence 仅保证当前 256 项 recent outcome window（含 in-flight）内不重复执行，不是永久 task ledger。

```python
from openbiliclaw.runtime.updater import AutoUpdateService

service = AutoUpdateService(enabled=False, check_interval_hours=6)
backend = await service.check_now()
status_code, apply_payload = await service.request_apply(tag="backend-v0.3.92")
```

核心调用：

- `check_now()`：立即检查 GitHub tags，只刷新后端更新状态，不自动应用。
- `request_apply(tag="backend-vX.Y.Z")`：先检查安装形态为 `git`（`frozen` / 其他以 `unsupported_install_mode` 拒绝、`docker` 以 `docker_install_mode` 拒绝——见下）、git repo、可信 `origin`（按 `_canonicalize_remote_url` 规范化比较：大小写不敏感、`.git` 后缀可选、`https://` 与 `git@…:` / `ssh://` 等价；镜像包装 URL 不折算，需显式加入 allowlist）、worktree clean（仅 `uv.lock` 改动豁免——发布 tag 携带过期 lock 时安装侧 `uv sync` 必然改写它，不能因此永久阻塞更新）、未 merge/rebase、目标 tag 存在且当前 HEAD 可 fast-forward，再返回 `202/applying` 并在后台执行 `git checkout -- uv.lock`、`git merge --ff-only <tag>`、依赖同步和 `os.execv` 重启。任何守卫拒绝都会 `logger.warning` 写明细（含实际 remote URL / 脏文件列表）并把原因写入 `last_error`。
- `check_and_update_if_due()` / `check_and_update_now()`：供后台调度使用；只有 `scheduler.auto_update_enabled=true` 时才会定时自动应用。冻结桌面包与 Docker 容器走 check-only 分支：**无论开关状态**都按间隔检查（`_background_loop_enabled()` 对 frozen / docker 恒真）——frozen 跟踪 `desktop-v*` 安装包 tag，docker 跟踪 `backend-v*`（镜像随后端版本发布），发现新版置 `update_available` 并推 `backend_update_available` 事件提醒用户下载新安装包 / 拉取新镜像，但永不进入 apply——`request_apply` 的非 git 守卫独立兜底，后台循环不可能 fast-forward 共享目录里的 git 检出。
- `adopt_status_from(other)`：配置保存触发热重载、本服务被重建时，由 `rebuild_from_config` 调用以携带上一实例的检查结果（版本 / tag / 上次检查时间总是携带；`update_available` / `up_to_date` / `blocked` 等已结算状态也携带，瞬态 `checking` / `applying` 不携带）。否则设置页状态行会从「发现新版本」回退到「尚未检查更新」直到下个检查周期。
- `detect_install_mode()`（模块级函数）：上报安装形态——`frozen`（PyInstaller 桌面包，结构上无法 git 自更新）、`docker`（容器内运行，代码烧在镜像里；经 `docker_runtime.is_running_in_container()` 判定：`OPENBILICLAW_IN_CONTAINER` 环境变量（Dockerfile 已内置）或 `/.dockerenv` / `/run/.containerenv` 标记）、`git`（installer / agent / dev 克隆）、`unsupported`（其他）。**安全守卫**：冻结桌面包可能与 AI / 一键安装共用 `~/OpenBiliClaw` 目录（`entry.py` 把 `OPENBILICLAW_PROJECT_ROOT` 指向它，目录里是真实 git 检出），此时磁盘上有 `.git` 但仍必须拒绝自更新——否则会改写他人源码 + venv 而冻结包重启后仍跑捆绑旧码，形成无限重启循环。故 apply 路径显式判 `install_mode == "git"`，不只依赖 `.git` 是否存在；`docker` 判定优先于 `git`，容器里即便挂载了 git 检出也不会误入自更新路径（快进检出改不了运行中的镜像代码）。
- **更新通道**：git 安装与 Docker 容器跟踪 `backend-v*` 源码 tag（legacy `v*` / 裸 semver 兜底；GHCR 镜像随 backend tag 发布，同一版本号）；冻结桌面包跟踪 `desktop-v*` 安装包 tag（`_parse_desktop_candidate`，无 legacy 兜底——两类 tag 不总是同步发布，桌面用户只关心有没有新安装包）。`_fetch_latest_candidate(channel=...)` 按 `check_now` 里的安装形态选通道。
- `get_update_status()`：返回 `/api/update-status` 使用的 backend 状态对象，含 `install_mode`。
- `get_runtime_status()`：返回 `/api/runtime-status` 合并用的自动更新摘要，包含当前版本、最新远端版本、上次检查、错误、状态原因和 `install_mode`。

### ContinuousRefreshController

```python
controller.candidate_eval_coordinator.notify("candidate_enqueued:bilibili")
```

核心调用：

- `request_replenishment(reason=..., force=False)`：补货请求的统一入口。`force=False` 只记录触发原因，等待定时 `refresh_if_needed()` 统一检查池子缺口；`force=True` 用于初始化完成、用户手动刷新和推荐刷新后低库存路径，会启动手动补货并消费已排队的 reason。
- `refresh_if_needed()` / `force_refresh()`：按 pool available 缺口、source share 和 raw-material headroom 构建补货计划；如果正式可换池已经达到 `pool_target_count`，返回 `pool_at_cap` 并跳过 discovery。后台 `refresh_if_needed()` 还会应用约 90% 的 replenishment low-watermark：略低于 target 时只维护状态，不触发 discovery；`force_refresh()` 是显式用户动作，仍按 source 缺口尝试补货。注入 `DiscoveryCandidatePipeline` 后，refresh 会优先调用 `ensure_pending_supply()`，按实际新增 `pending_eval` 数补足 Evo 供给，而不是只跑一次 discover；API daemon 已有 coordinator 时，pipeline 的一次 enqueue callback 立即唤醒唯一 owner，refresh 不会再同步 `drain_pending()`，从而保持 durable `evaluating <= 3×30`。没有 coordinator 的 composition 可选 `one_shot_inline_eval_limit`；OpenClaw bootstrap 将它固定为 4，使这次 refresh 的 source supply 与 inline drain 都不超过 4，fetch oversample=1、min eval batch=4、inline evaluator=1，后续 OpenClaw 请求再补下一批。该值是 integration 内部策略，不是 `config.toml` 字段；API runtime 不设置它，仍保持 4× supply oversample 与 coordinator worker 波次。完整 B 站四策略补货在小缺口阶段只给 `search + related_chain` 配额，`trending/explore` 到更深缺口再跑。当待评估水位已足够时不会再 claim B 站搜索关键词，避免空跑关键词被误标失败；当统一关键词 planner 已启用但 B 站关键词 store 暂空时，会从本轮策略组移除 `search`，而不是传 `queries=None` 触发旧 `discovery.search.queries`。池子低于 target 但 plan 为空时会打 INFO 诊断，包含 `pool_available/raw/pending/source_available/source_raw/source_targets/raw_targets/requested_by_source`。
- `drain_discovery_candidates_once(..., reason=...)`：runtime 已有 coordinator 时退化为耐久 `notify(reason)`，不再创建一次性 drain task；没有 coordinator 的 CLI / 兼容 runtime 仍通过相同 staged pipeline 执行一次 drain。
- `run_init_backfill(profile, target_pool_count, *, fully_parallel=True)`：图形化引导初始化（gui-init）stage 4 的发现补池。持 `_refresh_lock` 与连续 refresh 串行，绝不与之争 `content_cache`；`async with` 在 `CancelledError` 时释放锁。不查 `_llm_work_allowed()`，因此 init 期间后台门控暂停不会自锁 init 自己的补池。
- `_pool_count_payload()`：统一生成 runtime status / runtime stream 的池子字段，包含 pending eval 与 evaluated pending 拆分。
- `_update_llm_inventory_state(available)`：把 canonical durable available 与 `pool_target_count` 同步到共享 gate；不接受 Task 6 的 projected/transient count。
- `_enforce_pool_cap()` / `_enforce_pool_cap_async()`：同步实现把 target、跨表 raw ceiling、available/raw source quotas、topic/explore cap、stale age 与 XHS 本人昵称一次传给 storage 原子维护入口；异步 refresh 路径只通过 `asyncio.to_thread()` 包装调用。成功返回 `result.at_target`，post-snapshot rollback 时记录 ERROR 并按事务前 availability 决策。若 BEGIN / snapshot 尚未取得就失败，storage 抛出专用异常，runtime 重新调用 canonical `count_pool_candidates()` 决策，绝不信任默认零值。每个有结果的维护轮只输出一条包含 `PoolMaintenanceResult` 全字段的汇总日志。

`run_startup_maintenance()` 把生命周期固定为“原子维护/历史恢复 → 暴露服务或启动后台工作”，并用 controller 内部完成标记避免同一 host lifecycle 重复维护。API daemon / 热重载由 `run_forever()` 先调用该钩子，再执行 delight、candidate 与 background loops；OpenClaw 不运行 `run_forever()`，因此 direct bootstrap 在返回 adapter services 前同步调用同一钩子，并保持 one-shot inline candidate evaluation。`_enforce_pool_cap()` 每次先清空 success signal，只有拿到 `rolled_back=False` 的 `PoolMaintenanceResult` 才置为成功；snapshot/DB 异常即使被 fallback bool 吞掉、或事务返回 rollback，都不会完成 startup 标记，后续 host 调用仍会重试。

`_run_refresh_plan()` 在 durable admission 与文案完成后只调用这一个入口；不再组合 `trim_topic_group_overflow()`、`trim_explore_cluster_overflow()`、`evict_stale_pool_items()`、source trim 或 raw trim，因此不会留下“前半段已提交、后半段才发现库存归零”的中间状态。旧数据库 trim 方法仍保留给兼容测试和手动工具。

### CandidateEvalCoordinator

- `notify(reason)`：generation 递增并 set event；等待前会重读 durable snapshot，避免 clear/wait 边界丢唤醒。
- `run_forever()` / `stop()`：管理 claim owner、worker task map、串行完成 lane、退避和取消清理；停止时按 token 释放所有未完成 claim。
- `post_commit_callback`：首次成功缓存后立即启动，后续完成批次在任务运行期间只标记一次 rerun；它与 worker 并行，不阻塞第四批即时补位，停止时由 coordinator 统一取消并 gather。
- `status_payload()`：返回 `candidate_eval_state/workers/in_flight/pending/backoff_until/last_error/last_batch_seconds/last_cached/last_rejected`，由 runtime status 与 pool event 合并发布。
- `on_admitted(count)`：同步、返回 `None` 的轻量通知接口；协调器不 await 文案工作，因此 admission 通知不会占住串行 commit lane。Task 7 的文案协调器通过该接口接入，本任务不改变其微批状态机。
- `candidate_evaluation_owned_by_coordinator`：仅 API daemon 在 coordinator attach 后，对会同步 drain 的 Douyin / YouTube / Zhihu producer 置为 `True`。B 站 refresh、X 与 Reddit 同样走共享 pipeline；X 不再直接写数据库，因而所有 managed source 都经 pipeline 的一次 callback `notify("candidate_enqueued:pipeline")` 立即唤醒 coordinator，且不得再调用 `drain_pending()`。OpenClaw direct adapter 不启动该 owner，故其 producer 保持 `False` 并走有 90 条硬上限的 inline drain；独立/CLI 也保持此兼容路径。

### FeedbackBatchScheduler

```python
from openbiliclaw.runtime.feedback_scheduler import FeedbackBatchScheduler

scheduler = FeedbackBatchScheduler(soul_engine, debounce_seconds=5.0)
scheduler.schedule()
```

核心调用：

- `schedule()`：标记当前有新反馈待处理；若没有活跃任务，创建一个后台任务，先等待 debounce 窗口，再调用 `SoulEngine.process_feedback_batch_if_needed()`。
- `drain()`：测试辅助，等待当前调度任务结束。
- `close()`：关闭 API 时取消还没跑完的调度任务。

调度语义：

- 多个 `/api/feedback` 请求在 debounce 窗口内只会合并成一次批处理。
- 批处理执行中再次收到反馈，会把 dirty 标志重新置位；当前处理结束后再等待一个 debounce 窗口并补跑一次。
- 该调度器只解决 API 层的 burst coalesce；Soul 层仍有 `process_feedback_batch_if_needed()` single-flight，防止其它入口绕过 API 时并发重放同一游标。

### InitCoordinator + InitPrereqs（引导初始化）

`InitCoordinator`（`runtime/init_coordinator.py`，惰性挂在 `RuntimeContext.init_coordinator`）是图形化引导初始化的生命周期所有者：`init_runs` 持久化状态机、单写者进度事件（`_write_lock` 串行化，并行 stage 3/4 的 `sequence` 不丢更新）、`BEGIN IMMEDIATE` 单飞预定、启动 `reconcile_on_boot()`（崩溃残留 `starting/running` 判失败）、协作取消、bootstrap task 归属（供写者门控放行 init 自己的 task-result）。`InitPrereqs`（`runtime/init_prereqs.py`）提供 TTL 缓存 + 单飞的 `chat_ready()` / `bilibili_check()` / `enabled_platforms()` 前置探测。共享流水线 `cli.run_guided_init`、`/api/init*` 端点和 init 期间写者门控详见 [init 模块文档](init.md)。

### Embedding Progress

```python
from openbiliclaw.runtime import embedding_progress

embedding_progress.mark_pull_running("bge-m3")
embedding_progress.report_pull("downloading", completed=240_000_000, total=568_000_000)
snapshot = embedding_progress.snapshot()
embedding_progress.mark_pull_done(ok=True, error="")

embedding_progress.report_ollama_phase("starting")
phase = embedding_progress.ollama_phase()

# 仅测试隔离：清空拉取态并把 Ollama phase 置回 ready
embedding_progress.reset()
```

`snapshot()` 返回 `{running, model, completed, total, status_text, done, ok, error, started_monotonic}`。`reset()` 会同时清空拉取状态并把 `_ollama_phase` 置为 `ready`，因此仅用于测试前后隔离；生产调度失败的回滚必须用 `mark_pull_done(False, error)`，以保留真实 Ollama phase。该模块不能 import API / config / registry，避免桌面入口、API app 和 supervisor 之间形成循环；所有环境判断仍留在调用方。`/api/embedding/repair` 的 `not_running` 自愈也复用 `runtime.ollama_supervisor.ollama_required()`、`is_loopback()` 与 `_is_default_ollama_endpoint()`，只在 `autostart.manage_ollama=true` 且 endpoint 是默认 loopback `11434` 时尝试拉起托管 Ollama。

### Degraded RuntimeContext

`build_runtime_context()` 仍然保持严格：原生 ordered Chat / Embedding route 无法构建时直接抛出 `RegistryBuildError`，方便测试和 CLI 调用方快速失败。FastAPI 生产入口 `create_app()` 会单独捕获这个错误并调用 `build_degraded_runtime_context()`。

降级模式下可用接口：

- `GET /api/health`：返回 `status="degraded"`、兼容 reason code `llm_registry_unavailable` 和 blocking issues；该 reason 只是稳定 API 字符串，不代表 runtime 仍有 legacy registry 类型。当 `SoulEngine` 可用时会额外返回可选字段 `profile_ready`，表示 soul 画像是否已生成。v0.3.95+ 额外返回 `embedding_ready`（bool）。v0.3.137+ 该同一 live probe 也被 `/api/init-status` 复用：若原生 `[models.embedding]` 已启用且 Provider 列表非空，初始化前置清单会下发 `embedding_required=true`，`can_start` 与 `POST /api/init` 都必须等真实 probe 通过；关闭 route 或 Provider 列表为空则可降级初始化。v0.3.97+ 这是一次**实时探活**而非「服务是否构建」：经 `EmbeddingService.probe()` 绕过缓存真打一次 provider，探测缓存保存 `ready / failed / timed_out` 原始三态而非调用方布尔值，并由 `_EMBEDDING_PROBE_TIMEOUT_SECONDS`（默认 15s）上限兜住。普通 `/api/health` 仅把 loopback Ollama 的 `timed_out` 解释为冷加载中的乐观可用，避免外部 Homebrew / 官方 Ollama 默认 5 分钟卸载后让插件横幅误报停服；远程 Ollama 或非 Ollama provider 超时仍为 `false`。成功沿用 `_EMBEDDING_READY_TTL_SECONDS`（默认 30s），明确失败与超时使用 8s 短 TTL 重探；single-flight 锁继续让并发 health/init 共享同一次真实 probe，但各入口独立解释结果。provider 现已 404/500（如 `bge-m3` 没拉、Ollama 停了、随包缺 `llama-server`）、返回空向量或抛出异常仍会如实报 `false`，修好后下次探活即翻 `true`；服务对象不存在仍 `false`，老/无 `probe()` 的服务回退「构建即就绪」。`false` 表示语义去重 / MMR 多样性降级（可能刷到换皮重复内容），插件 popup 据此显示「一键启用本地 Ollama」横幅。
- `GET /api/config`：返回完整配置、`degraded=true` 和同一组 issues。
- `PUT /api/config`：允许保存修复配置，但跳过热重载并返回 `restart_required=true`。
- `GET /api/model-config`、`PUT /api/model-config`、`GET /api/model-connection-types` 与 `POST /api/model-config/probe`：保持模型修复、迁移决定、descriptor 与精确探测入口可达；请求仍受 revision/init guard 与 secret-safe validation 保护。
- `GET /api/runtime-status` 与 `/api/runtime-stream`：用于 popup 展示降级状态；stream 会先发送 `{type:"degraded", ...}` 并保持连接。
- 所有非 `/api/` 的前端页面与静态资源继续可达，包括桌面 Web `/`、`/web...`、首次设置 `/setup...`、移动 Web `/m...` 与 favicon；用户可以先打开完整页面，再通过上述模型配置 API 修复路由。远程访问仍照常经过 API auth 门禁。

其他 `/api/` 业务接口在降级模式下返回 503，避免在缺少可用模型 route、数据库/运行时组件不完整时继续执行推荐、发现或画像链路。对外 `degraded_reason="llm_registry_unavailable"` 作为一版兼容值保留，不代表旧 `LLMRegistry` 仍存在。

降级状态下成功 `PUT /api/model-config` 会构造并发布完整 runtime graph，启动新 graph 的后台任务，清除 `RuntimeContext` 与 app 两层 degraded 标记，再发一次 `config_reloaded`；调用方无需重启 daemon。构造、publication 或任务启动失败时，文件和完整 degraded runtime state 都恢复，且不发送成功事件。

### Runtime Status Pool Counts

`GET /api/runtime-status` 和 runtime stream 中的池子字段语义如下：

- `pool_available_count`：真实可换数量，只统计 fresh、未 dislike、未进入推荐历史、未近期看过、已有 `pool_expression` / `pool_topic_label`、已有 `style_key` / `topic_group` 且来源可打开的候选，并按默认每 `topic_group` 最多 3 条的候选窗口计数。
- `pool_raw_count`：fresh、未 dislike、未进入推荐历史的 `content_cache` 素材库存 + `discovery_candidates` 中尚未缓存的 raw candidates，用于诊断池子里是否还有原料。
- `pool_pending_count`：未近期看过、但仍缺文案 / 分类 / 可打开链接等 readiness 条件的 `content_cache` 素材数，加上待评估 / 已评估待入池候选；不会用 `raw - available` 近似，避免把 recently viewed 内容误算为待整理。
- `pool_pending_eval_count`：`discovery_candidates.status IN ('pending_eval', 'evaluating')` 的数量，表示已经找到但还没完成统一 LLM 评估的内容。
- `pool_evaluated_pending_count`：`discovery_candidates.status='evaluated'` 的数量，表示已经完成评估但尚未 admission 到 `content_cache` 的内容。
- `last_discovered_count`：最近一轮 refresh 新入队的 raw candidates 数；已评估待入池候选的 retry / admission 不会冒充“新发现”。
- `pending_signal_events`：`discovery_runtime.last_processed_event_id` 之后新增的 discovery-trigger 行为事件数，只用于判断是否触发 `search + related_chain`，不表示画像 pipeline backlog。普通 `/api/events` 会用独立 `last_profile_pipeline_event_id` 把旧 pending 行为事件补喂给画像 pipeline，但不会推进 discovery 水位；补货执行由已排队的 replenishment reason、定时 tick 或用户刷新后的低库存检查统一触发。
- `recent_pool_topics`：最近一轮实际 admission 到推荐池的内容主题；retry-only admission 可以更新该字段，但不会增加 `last_discovered_count`。

前端凡是显示“可换”都必须只读取 `pool_available_count`。`pool_pending_count` / `pool_pending_eval_count` / `pool_evaluated_pending_count` 只能用于“正在整理成可换内容”等辅助文案和诊断。

`refresh.pool_updated` 不只来自后台补货和文案预计算。`GET /api/recommendations` 在无历史推荐时会从池子 bootstrap，一旦这一步或 `reshuffle` / `append` 把 fresh 候选标记为 shown，API 会立刻重新读取同一组 runtime pool 字段并向 `/api/runtime-stream` 发布快照。已打开的插件、移动 Web 和桌面 Web 应用该快照刷新库存数字、底部可换提示和空态文案，但不得因此重拉 `/api/recommendations` 替换当前列表。

### Activity Feed

`GET /api/activity-feed` 返回 popup、移动 Web 和桌面 Web 共用的轻量动态摘要：

- `live_summary`：当前 runtime 摘要；优先显示手动补货中的 `manual_refresh_message`，否则根据 discovery signal 水位或可换池库存生成短文案。
- `headline`：最新动态条目的摘要；没有动态条目时回退到 `live_summary`。
- `items`：认知更新、反馈记录和推荐池补货等最近动态。

首启 / setup 阶段要优先保护初始化入口：当 `initialized=false`，且 `recommendation_count`、`pool_available_count`、`pool_pending_count`、`last_replenished_count`、`last_discovered_count` 都为 0 时，普通 `/api/events` 会以 `not_initialized` 拒收，不会写入 memory 或制造新的 `pending_signal_events`；`live_summary` 也会提示用户点击「开始初始化」，不会因为历史残留 pending signal 显示“已记下 N 个新动作”。一旦已有推荐或候选池产物，上述 pending signal 文案会按初始化后的正常运行状态展示。这里的 `pending_signal_events` 是 discovery refresh 触发水位，不是画像待处理队列；画像增量由 `/api/events` accepted 事件进入 `ProfileUpdatePipeline`，同时用 `last_profile_pipeline_event_id` 兜底补喂旧 pending 行为事件，再由 pipeline / cognition cycle 按各自节奏更新。事件入口不会同步执行补货，只通过 `request_replenishment(reason="event_ingest")` 排队，交给定时 tick 或用户刷新后的低库存检查统一处理。

### Runtime Status Update Fields

`GET /api/runtime-status` 会保留自动更新摘要字段，供插件和 Web 前端在统一 runtime 状态对象中读取：

- `auto_update_enabled`：当前后台定时自动更新是否开启；关闭时仍允许手动检查和手动 apply。
- `install_mode`：安装形态（`frozen` / `docker` / `git` / `unsupported`）。桌面 Web 设置页在非 `git` 时禁用自动更新开关，并按形态提示升级方式（frozen → 下载新安装包，docker → `docker compose pull`）。
- `current_version`：本地后端版本。
- `latest_remote_version`：最近一次检查得到的后端远端版本。
- `last_update_check_at`：最近一次检查时间。
- `last_update_error`：最近一次检查或 apply 的稳定错误原因。
- `backend_update_state` / `backend_update_reason`：更新状态和原因，语义与 `/api/update-status.backend.state/reason` 对齐。

### RuntimeEventHub

`RuntimeEventHub.publish(event)` 会把事件 fan-out 到当前 `/api/runtime-stream` 订阅者队列，并返回布尔值：

- `True`：至少一个订阅者队列接收了事件。
- `False`：当前没有订阅者，或所有订阅者队列都未接收事件。

`ContinuousRefreshController._publish_probe_if_available()` 使用这个返回值保护主动探针：只有 `interest.probe` 或 `avoidance.probe` 实际进入至少一个 runtime stream 后，才会把本次 domain / axis / probe distance 写入 `discovery_runtime.json` 的短期去重状态，并更新 `last_probe_kind`。这些写入走 `MemoryManager.update_discovery_runtime_state()` 的原子读改写，和 API 反馈历史、短期探索 buffer 合并，避免后台循环用旧状态覆盖用户刚点击过的探针反馈。普通状态事件仍可忽略返回值。

主动探针仲裁规则：

- 每轮 proactive push 最多发布一条 probe；惊喜推荐仍走独立 `delight.candidate` 逻辑。单条 pending、批量 rehydrate 与 runtime 事件统一透传 canonical `item_key`、raw `content_id`、`source_platform`、`content_url`、`content_type`。
- 正向和负向都有候选时，根据上一次成功投递的 `last_probe_kind` 反向优先，形成 `interest -> avoidance -> interest` 的轮转。
- 发布失败（例如没有订阅者）时不写 `last_probe_kind`，也不消耗 `probed_domains` / `probed_avoidance_domains`。
- runtime 只会投递 `status="active"` 的正向/负向探针；已经确认、拒绝或过期的旧候选即使仍残留在某次内存快照中，也不会再次进入 `interest.probe` / `avoidance.probe` 事件流。
- `interest.probe` 正向探针还会记录 `probed_distance_bands`，并在下一次选择时优先尝试没在冷却窗口内问过的 `near/lateral/bridge/wildcard` 档位。
- `interest.probe` runtime event 暴露 `probe_mode` 和 `challenge`，移动 Web、桌面 Web、插件 inbox 与 OpenClaw 都可以把挑战探针和普通确认区分开；`near` 普通池最多 5 条，`lateral/bridge/wildcard` 挑战池另有 3 条 active 额度。
- `avoidance.probe` 选取会避开近期 `probed_avoidance_domains` / `probed_avoidance_axes`，并读取 `avoidance_probe_feedback_history` 中用户否认过的方向。

### Extension E2E API

`POST /api/extension/e2e/run` 是本机 trusted-local 调试端点，用来验证已安装扩展的真实捕捉链路。它不会直接写事件，也不会让后端伪造采集结果；后端只发布一次 `extension_e2e_run` runtime event，并等待扩展回传执行结果。

典型响应字段：

- `run_id`：本轮运行 ID，贯穿 runtime event、插件 result 和后端匹配。
- `token`：一次性结果回传 token，仅用于 `/api/extension/e2e/result` 鉴权。
- `observed`：后端在运行窗口内从 `events` 表匹配到的真实捕捉事件。
- `matched`：`observed` 是否满足本轮平台 / 动作要求。

约束：

- 端点只允许可信本机调用；局域网或远程请求会被拒绝。
- 同一后端进程一次只允许一个 E2E run，避免多个真实浏览器标签页互相污染匹配窗口。
- 如果 `RuntimeEventHub.publish()` 返回 `False`，端点会快速失败为 `extension_runtime_unavailable`，不空等超时。
- 默认禁止会改变平台状态的动作；调用方必须显式设置 `allow_state_changing=true` 才能执行 `like/favorite/follow/comment/repost` 这类操作。

### Image Proxy API

`GET /api/image-proxy?url=<encoded_url>` 只代理明确白名单内的 HTTP(S) 图片 URL，用于移动 Web `/m/` 和浏览器插件 side panel 的推荐、惊喜推荐和消息封面图。白名单按域名边界匹配，当前包含 `hdslb.com`、`xhscdn.com`、`pstatp.com`、`douyinpic.com`、`douyinvod.com`、`ytimg.com` 和 `ggpht.com`，会拒绝非 HTTP(S)、缺 hostname、userinfo 和非白名单域名。

代理不使用自动跳转；`301/302/303/307/308` 最多手动跟随 3 次，每一跳都会重新校验目标 URL。上游响应必须是 2xx 且 `Content-Type` 为 `image/*`。若 `Content-Length` 超过 10MB 会立即返回 413；缺失或伪造长度时，响应体会先流式写入 `SpooledTemporaryFile(max_size=1MB)`，实际读取超过 10MB 同样返回 413，避免在下游响应头已发送后才发现超限。

成功响应会带 `Cache-Control: public, max-age=86400` 和 `X-Content-Type-Options: nosniff`，并写入本地图片缓存。缓存回退只用于上游网络失败、超时或 5xx 类上游错误；URL / redirect 白名单失败、非图片 Content-Type、超过 10MB 等校验类错误会保留 403 / 400 / 413 等明确状态，不会被统一折叠成 502。该接口按本地单用户后端设计，默认只应暴露在 `127.0.0.1` 或用户可信局域网；若用 `--host 0.0.0.0` 对外监听，应在反向代理层自行加访问控制。

### Boot Autostart API

```python
from openbiliclaw.runtime import autostart

state = autostart.status()
autostart.register(config)
autostart.unregister()
```

核心对象：

- `AutostartStatus(supported, registered, platform, mechanism, reason, detail)`：API、CLI 和插件 UI 共享的状态模型。`mechanism` 固定为 `launchd` / `windows_run` / `xdg_autostart` / `none`。
- `build_launch_spec(config)`：生成登录项执行命令，固定为当前 Python 解释器执行 `-m openbiliclaw.cli start`，并注入 `OPENBILICLAW_PROJECT_ROOT`；如果能找到 `ollama`，会把其目录加入登录项 `PATH`。
- `active_env_managed_inputs(config)`：检测会在桌面登录会话里丢失的环境变量来源（`OPENBILICLAW_*`、provider API key env、抖音 Cookie env），用于拒绝开启自启动。
- `autostart_shadowed(intended)`：写后 reload effective config，检测 `config.local.toml` 或环境变量是否覆盖了 `[autostart].enabled`。

公开接口：

- `GET /api/autostart-status`：远程可读、降级模式可读，返回固定字段集；只展示 `enabled`、`registered`、`supported`、`can_manage`、`reason` 等状态，不包含 Cookie / API Key 等敏感配置。
- `POST /api/autostart/apply {"enabled": bool}`：本机 trusted-local 可写；非本机返回 `403 local_only`，不支持平台返回 `409 unsupported_*`，env / shadow 命中返回 `409`。开启时先写 config 后注册 OS，关闭时先注销 OS 后写 config，并在失败时尽量回滚 OS 与 config 到操作前状态。

平台实现都只写当前用户作用域：

- macOS：`~/Library/LaunchAgents/com.openbiliclaw.daemon.plist`，不执行 `launchctl bootstrap`，下次登录由 launchd 读取。
- Windows：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` + `data/autostart/openbiliclaw-autostart.pyw`，优先用 `pythonw.exe`。
- Linux：`~/.config/autostart/openbiliclaw.desktop`，使用 XDG autostart。

#### 封面磁盘缓存与清理

成功抓取的封面以 `sha256(归一化 URL)` 为键写入 `data/image-cache/`（键与清理逻辑集中在 `openbiliclaw.runtime.image_cache`，由 `api.app` 复用，保证单一真源）。小红书 `sns-webpic-qc.xhscdn.com/{timestamp}/{token}/{path}` 这类带轮换 token 的 URL 会先剥掉 `{timestamp}/{token}` 前缀再算键，因此 token 过期重新生成后仍命中同一份缓存——这是小红书封面在签名失效后仍能展示的关键。

`cleanup_image_cache` 负责按消费状态清理：启动时全量执行一次，运行时由 `RefreshRuntime._loop_image_cache_cleanup` 每 6 小时增量执行。清理规则为「已消费且未保存」——`content_cache.pool_status` 属于 `shown / feedbacked / stale / purged_by_dislike`、且 bvid 不在 `favorites` / `watch_later`（经 `Database.iter_cover_lifecycle` 联表判定）的封面会被删除；`fresh` / `suppressed`（待展示 / 可能复活）以及任一被收藏或加入稍后再看的封面始终保留。B 站等 URL 稳定、可随时重抓的来源安全释放空间（实测可回收数百 MB）；而带过期 token、删除后无法重抓的小红书封面默认受保护不删（缓存是其唯一副本），可用 `protect_unrefetchable=False` 关闭。无任何 `content_cache` 行引用、且文件超过 30 天的孤儿封面会作为增长上限兜底被移除（降级模式下数据库不可用时仅执行这条规则）。

#### 发现即缓存（封面预取）

白名单 / redirect / 大小 / 类型校验的抓取核心 `fetch_cover_bytes` 是唯一真源，由 proxy 路由和预取共用；失败抛 `CoverFetchError`（携带 400/403/413/502/504），proxy 路由再映射回对应 HTTP 状态。v0.3.153+：抓取按主机分流代理——国内 CDN（hdslb / xhscdn / pstatp / douyinpic / douyinvod）恒直连（`trust_env=False`，代理出口 IP 易被风控，与 B站 登录探测同因），境外 CDN（ytimg / ggpht）保持继承环境 / 系统代理，需要代理才能拉 YouTube 封面的用户不受影响。`get_or_fetch_cover_bytes` 是缓存优先入口：先按同一白名单边界校验 URL，再读取 `data/image-cache/` 的非空文件，未命中才调用 `fetch_cover_bytes` 并写回缓存。多模态 discovery evaluator 使用这个入口，因此小红书已缓存头图即使原 CDN token 过期，也能继续参与封面图评估。

`RefreshRuntime._loop_cover_prefetch` 每 60 秒做一次「发现即缓存」：从 `Database.iter_servable_cover_urls` 取最近 12 小时内、仍可展示（`fresh / shown / suppressed` 或已保存）的封面（最新优先），`select_prefetch_targets` 过滤掉非白名单和已缓存项、把**无法重抓的小红书封面排在最前**，每轮最多抓 40 张写入缓存。这修复了此前封面只在「展示时」才懒加载、而小红书签名 token 早已过期导致 502 破图的问题——预取趁 token 新鲜时就把图落盘；最近窗口也避免对 token 已死的旧内容反复重试。预取按 `content_cache.cover_url` 原始值（可能是 `//` 或 `http://`）归一化后再抓，落盘 key 与 proxy 查找一致，故预取的封面 proxy 能直接命中。

### AccountSyncService

```python
from openbiliclaw.runtime.account_sync import AccountSyncService

service = AccountSyncService(
    memory_manager=memory,
    bilibili_client=bilibili_client,
    soul_engine=soul_engine,
    profile_analysis_timeout_seconds=360.0,
)
result = await service.sync_now()
```

`sync_now()` 会拉取最近一批 B 站历史、收藏夹和关注列表，但只有新增信号会进入 `memory.propagate_event()` 与 `soul_engine.analyze_events()`：

- 历史记录：使用 `last_history_view_at`、`last_history_bvid` 和 `history_bvids_at_last_view_at` 跳过已经处理过的同秒历史项。
- 收藏夹：使用稳定排序后的 `favorite_signature` 和 `favorite_bvids`，签名变化时只导入新增 bvid。
- 关注列表：使用 `following_signature` 和 `following_mids`，签名变化时只导入新增 mid。

`analyze_events()` 失败（对话模型不可用：本地模型未拉取 → 404、网关鉴权 → 401、超时）时，`sync_now()` 会把原因写入 `last_sync_error`（`画像分析失败：<原因>`，供 `/api/init-status` 与账号同步状态读取），但**不推进任何游标、不打 `last_account_sync_at` 时间戳**——整个 tick 回滚，下一次 `sync_if_due` tick 重试同一批事件，也不会被 `sync_interval_hours` 节流锁死或消耗一次性的 auto-bootstrap 机会，随后重新抛出交给 `run_forever` 分类记日志。Issue #113 收口后，`profile_analysis_timeout_seconds` 默认 360 秒（受控调用可传 `<=0` 关闭），到期会取消 Soul/provider coroutine，并写入固定的安全排查文案：模型服务在 6 分钟内没有结果，常见原因是 Base URL / 模型名 / 网络 / 代理配置或服务响应过慢，下一步到模型设置测试后重试；`GET /api/init-status` 已真正消费这条错误，三端会优先显示 detail，不再只在 runtime status 中存在。外部 `CancelledError` 继承自 `BaseException`，不会被失败捕获，因此热重载 / 重启打断的取消语义不变。

### YoutubeDiscoveryProducer

```python
from openbiliclaw.runtime.youtube_producer import YoutubeDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

`produce_if_due()` 返回 `{"discovered": int, "reason": str, ...}`。注入 `DiscoveryCandidatePipeline` 时，`discovered` 表示本轮已入待评估池或已被 drain 处理的候选量；未注入时沿用直接 `ContentDiscoveryEngine.discover()` 缓存路径。常见 `reason`：

- `ok`：至少完成了一轮可运行策略；结果已通过候选 pipeline 或直接 discovery 路径进入统一评估 / 缓存链路。
- `throttled`：距离上次执行未达到 `min_interval_minutes`。
- `budget_exhausted`：当天 `yt_search` / `yt_trending` / `yt_channel` 的执行 ledger 已耗尽。
- `disabled` / `no_profile` / `error`：分别表示配置关闭、画像不可用或所有策略失败。

### XDiscoveryProducer

```python
from openbiliclaw.runtime.x_producer import XDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

X (Twitter) 的 steady-state discovery 走服务端 cookie 重放（对标抖音 direct，但用 `twitter-cli` 取代 XBogus 签名）。`produce_if_due()` 在 `[sources.twitter].enabled=true`、X 平台族低于 quota、源健康就绪、距上次执行已过 `min_interval_minutes` 时，依次跑三个策略：

- `search`：从 Soul 画像生成关键词，调 `XClient.search()`。
- `feed`：拉推荐流 For-You（`XClient.for_you()`）。这是最高曝光、最易被注意的行为，被压到很低的每日频次，并在连续失败后由 `XSourceHealthStore.feed_allowed()` 自动暂停。
- `creator`：对 `x_creator_subscriptions` 里到期的订阅逐个调 `XClient.user_tweets(handle)`，按 `creator_refresh_hours` 控制刷新节奏。

每条推文经 `discovery.x_normalize.normalize_tweet()` 映射为 `DiscoveredContent`（`content_type ∈ {tweet, thread}`、`body_text` 带全文），API runtime 通过共享 `DiscoveryCandidatePipeline.enqueue_candidates()` 写入 `discovery_candidates` 待评估池；pipeline 的单次 callback 会立即唤醒 coordinator，不再等 60 秒 safety wake，也不会双重通知。producer **只 fetch，不写 `content_cache`、不调评估器**，由共享混源 evaluator 完成 admission；脱离 API 的 isolated caller 仍可使用 direct-database fallback。runtime 的平台族统计会把 `x` / `x-*` / `twitter` 归一到 `twitter`，避免 X 配额、过滤 tab 和 pool 状态被拆成不同来源。每个策略 run 都把成功 / 失败结果回写 `XSourceHealthStore`（成功 `record_success()`，失败 `record_error(exc)` 按 401/403/429 落对应健康态）。预算护栏：`daily_search_budget` / `daily_feed_budget` / `daily_creator_budget`（`0` = 不设上限）+ 两次请求间 `request_interval_seconds` 间隔。`enabled=false` 时整条路径 no-op，绝不 import `twitter_cli` / `curl_cffi`。

X 客户端 `XClient`（`sources/x_client.py`）封装默认运行时依赖 `twitter-cli`，全程只读，方法用 `asyncio.to_thread` 包成 async；底层 `TwitterAPIError` / `AuthenticationError` 映射为 `XMissingCookieError` / `XAuthError`(401) / `XBlockedError`(403) / `XRateLimitError`(429)，供源健康状态机分流退避。`openbiliclaw[x]` 仍保留为兼容旧脚本的安装别名。

### RedditDiscoveryProducer

```python
from openbiliclaw.runtime.reddit_producer import RedditDiscoveryProducer

result = await producer.produce_if_due(limit=20)
```

Reddit 的 steady-state discovery 默认走 `rdt-cli` 登录态命令后端。`produce_if_due()` 在 `[sources.reddit].enabled=true`、Reddit 平台族低于 quota、距上次执行已过 `min_interval_minutes` 时，按 `[sources.reddit].source_modes` 调度四类分支：

- `search`：优先 claim 统一关键词 store；关键词池为空时回退 Soul 画像兴趣。
- `hot`：默认拉 `r/all` 的热门内容，也可由 smoke 命令传指定 subreddit。
- `subreddit`：优先复用近期 Reddit 结果里的 subreddit；没有历史种子时回退画像兴趣。
- `related`：优先复用近期 Reddit 内容 URL 或同轮 search / hot / subreddit 结果作相关扩展。

默认 `backend="rdt"`：producer 先检查 `rdt-cli` 命令和 `~/.config/rdt-cli/credential.json`，避免状态探测隐式触发浏览器 Cookie 提取；已连接插件会通过 `/api/sources/reddit/cookie` 把 `reddit_session` 写入该 credential store，凭据存在时再跑 `rdt status --json`，并用 `rdt search --json` / `rdt all --json` / `rdt sub <name> --json` / `rdt read <id> --json` 拉取候选。显式 `backend="extension"`，或命令后端状态不是 `ready` 且后端可写入 `reddit_tasks` 时，后端会改入队插件任务，唤醒真实 `reddit.com` 登录态 tab 并通过同源 `.json` endpoint 读取 posts / comments，再 POST `/api/sources/reddit/task-result` 回写。init 期 `bootstrap_events` 仍固定使用插件读取 saved / upvoted / subscribed。每条内容经 `reddit_items_to_contents()` 映射为 `DiscoveredContent(source_platform="reddit", source_strategy="reddit-<mode>")`，posts / comments 会保留 `body_text` 与 `content_type ∈ {"post", "comment"}`，前端因此按无封面文字卡展示。

producer **只 fetch，不写 `content_cache`、不同步调用 evaluator**。注入 `DiscoveryCandidatePipeline` 时，候选只进入 `discovery_candidates(pending_eval)`，后续由共享混源 evaluator 批量评分、admission 和文案预生成。这样 `openbiliclaw discover --source reddit` 的真实插件 E2E 只验证 Reddit 取数和入池，不会被本地 LLM 评估时延拖到超时。预算护栏是 `daily_search_budget` / `daily_hot_budget` / `daily_subreddit_budget` / `daily_related_budget` 四个独立 ledger，默认每类 300；`0` 表示不设上限，负数表示禁用该分支。

### BilibiliExtensionSearchProducer

```python
from openbiliclaw.runtime.bilibili_producer import BilibiliExtensionSearchProducer

result = await producer.produce_if_due(limit=5)
```

B 站扩展搜索 producer 是 API 搜索的兜底，不是常驻主发现路径。`produce_if_due()` 只在以下条件同时满足时入队：

- `[sources.bilibili].enabled=true` 且 `[scheduler].enabled=true`。
- B 站 API search 正在进程级冷却中（`search_cooldown_remaining()>0`）。
- 浏览器扩展 presence 在线或仍处于 `extension_disconnect_grace_seconds` 宽限窗口。
- B 站平台族低于 source share quota，且 `DiscoveryCandidatePipeline.pool_full()` 为 false。
- `bili_tasks` 中近期没有 pending / in-progress / completed search 任务，避免同一冷却窗口反复打开搜索页。

统一关键词 planner 开启时，producer 会通过 `KeywordFetchCoordinator` claim B 站 regular 关键词并把 `source_keyword_id` 写进任务 payload；扩展收到 `bili_task_available` 后打开真实 B 站搜索页并抓渲染后的 DOM 卡片，`/api/sources/bili/task-result` 再把视频转换成 `source_platform="bilibili"`、`source_strategy="bili-extension-search"` 的 raw candidates，并触发一次候选 drain。terminal `ok` 会把关键词标记 used，失败或空结果标记 failed。关键词合并 prompt 复用共享画像分层缓存，画像核心和兴趣层没变时不会重新渲染前置 profile block。若 refresh 口径判断 explore 已到期 / 即将到期，且 B 站还有 real deficit，本轮 prompt 会额外带 `<explore_domains>`；返回的 domain queries 会作为探索性 B 站 pending keywords 写入 `keyword_kind="explore"` 池，供 `ExploreStrategy` claim 消费。只有实际插入了 query 才会把 runtime state 的 `last_explore_refresh_at` 推进，避免空响应浪费 explore 周期。

### Source Bootstrap Task Results

XHS / 抖音 / YouTube 的插件任务桥保留两层去重：

- 单任务内：`merge_result()` 合并 partial / final payload 时按 scope + 平台原生 ID / URL / title 去重，只把本次新增项返回给 API 传播。
- 跨任务：API 在传播 bootstrap 事件前读取 `source_bootstrap_state.json`，跳过已经进入事件路径的 `xhs_seen_note_keys` / `dy_seen_video_keys` / `yt_seen_item_keys`。这样 `fetch-*`、`init` 或近期任务复用重复返回同一批收藏 / 历史时，不会再次写入 memory 或触发增量画像分析。

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `scheduler.auto_update_enabled` | `false` | 是否启用后台自动更新检查。 |
| `scheduler.auto_update_check_interval_hours` | `6` | 自动更新检查间隔。 |
| `scheduler.auto_update_allow_prerelease` | `false` | 是否允许 `backend-vX.Y.Z-rc/beta/dev` 预发布 tag 进入候选。 |
| `scheduler.auto_update_allowed_remotes` | OpenBiliClaw GitHub HTTPS / SSH | 允许自动更新快进的 `origin` allowlist；按规范化形式比较（`.git` 后缀可选、HTTPS/SSH 拼法等价、大小写不敏感），带凭据 URL 或未匹配的 remote（含镜像包装 URL——镜像用户把镜像地址加进来即可）会被拒绝。 |
| `scheduler.enabled` | `true` | 后台 LLM / embedding 总开关。 |
| `scheduler.pause_on_extension_disconnect` | `false` | 浏览器插件断开后是否暂停后台 LLM / embedding 工作。 |
| `scheduler.extension_disconnect_grace_seconds` | `90` | 插件断开后的宽限秒数。 |
| `scheduler.refresh_check_interval_seconds` | `60` | `ContinuousRefreshController` 主循环轮询间隔。 |
| `scheduler.signal_event_threshold` | `6` | 累计多少条 discovery-trigger 新行为事件后触发 `search + related_chain`；该计数只表示 discovery refresh 水位，不表示画像待处理队列。 |
| `scheduler.trending_refresh_hours` | `3` | `trending` 策略最小刷新间隔。 |
| `scheduler.explore_refresh_hours` | `12` | `explore` 策略最小刷新间隔；统一关键词 planner 会复用这条 refresh plan 时钟，在到期或距到期不足一个 `refresh_check_interval_seconds` 且 B 站有补货空间时，把探索 query 生成合并进当轮关键词调用。 |
| `scheduler.discovery_limit` | `30` | 单轮 discovery wave 候选上限，最大 `60`。 |
| `scheduler.delight_queue_limit` | `20` | 惊喜推荐队列默认加载数量；桌面 Web、移动 Web 和浏览器插件默认共享，范围 `1..100`。 |
| `scheduler.proactive_push_interval_seconds` | `120` | 主动推荐 / probe 推送循环间隔。 |
| `scheduler.speculator_idle_interval_minutes` | `30` | 画像 pipeline 空闲时检查猜测兴趣生命周期的间隔。 |
| `scheduler.avoidance_speculation_interval_minutes` | `10` | 不喜欢领域探针生成间隔。 |
| `scheduler.avoidance_speculation_ttl_days` | `3` | 不喜欢领域探针存活天数。 |
| `scheduler.avoidance_speculation_cooldown_days` | `7` | 不喜欢领域探针被否认或过期后的冷却天数。 |
| `scheduler.avoidance_speculation_confirmation_threshold` | `3` | 自动确认不喜欢领域所需显式负向信号数。 |
| `scheduler.avoidance_speculation_max_active` | `5` | 最多同时活跃的不喜欢领域探针数。 |
| `autostart.enabled` | `false` | 是否期望登录系统后自动拉起 `openbiliclaw start`。 |
| `autostart.manage_ollama` | `true` | `start` 是否在需要本机默认 Ollama 时尝试后台拉起 `ollama serve`。 |

## 设计决策

### Auto-update release contract

后端自动更新只认 backend source tag：

- backend 源码更新发布为 git tag：`backend-vX.Y.Z`，这是唯一 canonical 后端 tag。
- legacy 安装仍 fallback 兼容 `vX.Y.Z` 和裸 semver `X.Y.Z`，但只在没有稳定 `backend-v*` 候选时使用；远端同时存在 `backend-v0.3.89` 和 `v0.3.90` 时选择 `backend-v0.3.89`。
- 浏览器扩展 release 使用 `extension-vX.Y.Z`，必须被后端自动更新忽略。
- GitHub `/releases/latest` 是面向用户的 `openbiliclaw-v*` 聚合发布页，会同时挂最新插件 zip、桌面安装包和后端源码入口；它不是后端自动更新的 canonical source。`AutoUpdateService._fetch_latest_version()` 直接查询 `/tags`，分页过滤 backend tag 后选择最高版本。GitHub tag API 默认保留 TLS 校验；仅遇到证书校验类错误时降级重试一次，兜底 Windows 打包环境缺证书链的问题；REST API quota 耗尽的 403/429 会先读 `https://github.com/whiteguo233/OpenBiliClaw/tags.atom` 兜底，仍失败才单独返回 `github_rate_limited`，避免和 DNS / 断网 / GitHub 不可达混在一起。
- 默认忽略 prerelease；若只有更新的 `backend-vX.Y.Z-rc/beta/dev`，状态上报 `up_to_date` + `prerelease_ignored`。
- 浏览器插件更新不由 `AutoUpdateService` 管理：Chrome Web Store / Edge Add-ons / AMO 版本交给浏览器原生更新，GitHub zip / sideload 用户按插件 release 文档手动下载和重新加载。
- **版本 bump 必须重新 lock**：发布提交除 `pyproject.toml` / `openbiliclaw.__version__` 外必须同步运行 `uv lock`（或 `uv sync`）并提交 `uv.lock`。tag 携带过期 lock 时，安装侧首次 `uv sync` 会改写 `uv.lock` 把 worktree 弄脏，历史上曾让所有 git 安装的自动更新永久卡在 `dirty_worktree`。`tests/test_release_consistency.py` 断言三处版本一致；updater 守卫额外豁免 `uv.lock`、未跟踪文件、纯 index-only 条目和本地 `ollama-models/` 作为存量安装兜底，仍会阻止已跟踪文件的工作区修改。

这样可以避免后端 `0.3.64` 把 `extension-v0.3.24` 解析成 `(0,)` 并误报 "Already up-to-date"。

### Config recovery boundary

配置恢复是 runtime 和 API 的交界。普通 `/api/config` 事务只负责非模型字段，并保留磁盘上原始模型 authority；模型字段即使出现在 legacy payload 也会被忽略并返回 warning。只有 `PUT /api/model-config` 可以权威修改模型配置：它先从原生 records 构造完整 ordered routes 与 consumer graph，再原子写盘和发布；正常与 degraded runtime 都走同一 candidate swap，成功后无需重启 daemon。失败会恢复事务前文件和完整 normal/degraded runtime identity，并以 `rollback_applied` 告知调用方。

原生模型配置使用阶段 7 的 model-scoped 串行锁与全配置 canonical path boundary，并由 `PUT /api/model-config` 暴露为唯一权威 HTTP 写路径：首次读取后执行 revision guard、credential merge、migration resolution、validation，并在 canonical boundary 外构造 candidate；提交前再与 auth admin、guided-init source opt-in、autostart apply、`PUT /api/config` 共用同一路径事务，先检查 guided-init precommit guard，再立即重读 base/local。guided init 的 `try_start` reservation 也在同一 canonical writer 内完成，因此 init 与模型 commit 只能有一方先取得 writer。普通字段并发变化会从最新原字节 rebase，模型 authority 的来源、内容或 local provenance 变化会 conflict。restage 后、创建 backup 与替换文件之前，app coordinator 优先使用 async capability 调用 `capture_model_runtime_task_state()`；它取得稳定 lifecycle lock，等正在执行的 public stop/restart 完整结束后一起捕获 runtime token 与三个 active flag。此时 canonical writer 已持有，但 lifecycle 代码从不反向取得 writer，锁顺序保持单向；等待快照时取消会原样传播且不改磁盘/runtime。bounded 同步 disk gate 从不跨 `await`；完成快照后的异步所有权覆盖 legacy backup、原子替换、无事件 graph publication、旧 registry drain、新 app loop 重启和 rollback，另一 task 的同步 writer 在 swap 窗口快速失败。所有 public stop/restart 由同一 lock 整段串行；restart 使用 private unlocked stop helper，在一次 ownership 中先清空三个 app slot、并发取消/收集其结果，同时调用 registry-wide `cancel_all(exclude={"guided_init"})`，再创建新 loops 并安排 post-reload one-shot。child failure/cancellation 属于 cleanup 结果，外层 caller cancellation 不会被吞。swap/restart 异常或 `CancelledError` 都会先按事务前快照恢复文件和完整 normal/degraded runtime graph；shielded restore 重新取得 lifecycle ownership 后按旧 ownership 重建等价 app loops，已清退 detached 旧 one-shot 保持取消，caller cancellation 随后原样传播。probe 的 init guard 除 gate 后检查外，还在取得 model path lock 后、读取 revision/credential 前再次执行，因此不会在慢保存排队期间越过 init reservation，且网络仍完全位于锁外。该流程仍使用独立 pre-model-refactor backup，不复用普通 `/api/config` 的 `.bak` 与整套 component rebuild。协调范围仅限当前进程且没有跨进程文件锁：即时重读只能发现读取前已可见的外部变化，外部 writer 仍可在重读到替换之间的窄窗口竞争。

热重载成功后，所有可替换 LLM 入口都会拿到同一 `RuntimeModelBundle.chat_route`。稳定 gate 的 proposed target/inventory 直到全部新组件构造成功并进入 atomic publication 后才更新；晚期构造失败保留旧 target/state，不会让仍在运行的旧 runtime 提前进入新配置的 refill 模式：

- 主 runtime 的 discovery / recommendation / XHS producer 共用 `ctx.llm_service`。
- SoulEngine 内部的 preference / awareness / insight / profile_builder / speculator / dialogue_insight 使用同一 ordered route、usage recorder 与 gate。
- SocraticDialogue 使用 bundle 的 `LLMService`；fallback 也从 SoulEngine 当前 route 构造，不再存在模块专属 Provider/model。

`restart_background_tasks()` 在启动后置 one-shot 时通过 `_safe_post_reload_speculate()` 分别调度正向兴趣 speculator 和避雷 speculator，不会 await 两者的 `force_tick()`。正向路径读取 `probe_feedback_history`，避雷路径读取 `avoidance_probe_feedback_history`，让热重载后的首次生成继续避开近期已否认方向。这保证 popup 保存配置的 HTTP 响应不被一次画像猜测卡住；调度本身写 debug 日志，helper 内部吞掉异常，下一轮正常调度仍会继续。

同一后置 one-shot 还通过 `_safe_post_reload_precompute()` 调度一次 `precompute_pool_copy(profile=...)`（v0.3.124+，lever 2a）：`rebuild_from_config()` 的 `cancel_all` 会连带取消正在跑的 classify_pool_backlog / 文案预计算 / delight 评分，若不补一脚，冷启动期反复保存配置的用户会看到候选池迟迟不填（每次保存都把进度清零、最坏要等到下一个 `refresh_check_interval_seconds` tick）。`precompute_pool_copy` 内部会 detached 再启 classify 与 delight，因此一次调用即在新引擎上重启整条 classify→文案→delight drain；其自带的 `_expression_lock` 保证与 refresh loop 周期 drain 不抢同批，刷新轮询仍是兜底。helper 吞掉异常、不影响 `/api/config` 响应。

刷新调度不使用 `scheduler.discovery_cron`。该字段仅保留为旧配置兼容；实际触发由 `refresh_check_interval_seconds` 轮询、候选池低水位（约 `pool_target_count * 0.9`）、`signal_event_threshold`、`trending_refresh_hours`、`explore_refresh_hours` 和 `discovery_limit` 共同决定。`KeywordPlanner` 的探索 query piggyback 不另起时钟：它只读取 controller 暴露的 explore 到期 / 即将到期口径，并在成功插入 B 站 query cache 后由 controller 更新同一个 `last_explore_refresh_at`。

`ContinuousRefreshController.run_forever()` 当前并行启动 refresh、`CandidateEvalCoordinator`、pool precompute、soul pipeline、各来源 producer 和 proactive push 等 loop。协调器 worker 只执行 LLM evaluation，不持有 SQLite drain lock；claim、完成提交、重试 admission 与补位由单一协调任务管理。限流按 15/30/60/120/300 秒退避（尊重更长 `Retry-After`），缺 provider / 鉴权失败暂停后只接受精确 `startup` 或 `config_*` / `manual_*` 唤醒，连续 3 个成功但零缓存 batch 触发 60/120/300 秒无进展退避和一次补货。热重载只取消 registry 中的父 `refresh_loop`；父任务 gather 协调器子任务、子任务归还所有未完成 token 后，`RuntimeContext` 才构造新 runtime。

Expression copy 与 candidate evaluation 对 rate-limit、timeout、connection、5xx 使用同一条 15/30/60/120/300 秒 transient ladder；provider 提供更长 `Retry-After` 时优先采用。鉴权失败或无 provider 进入 `paused`，只由 startup、manual_* 或 config_* 通知恢复；成功但零写入至少等待 15 秒，避免 malformed singleton 紧循环。
