# 存储层

## 概述

`src/openbiliclaw/storage/` 负责本地 SQLite 数据库、schema 初始化、候选池计数和高频读写路径。它不理解 runtime state 或用户画像，只提供确定性的持久化 API。

本模块当前承担四类边界：

- 行为、推荐、候选池、聊天和鉴权状态的 SQLite 表结构管理。
- 推荐池 `content_cache` 的可换 / raw / pending 计数口径。
- discovery 待评估池 `discovery_candidates` 的生命周期管理。
- 跨平台收藏 / 稍后再看的 canonical 本地 membership、元数据快照、native sync 状态和独立任务快照持久化。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| SQLite schema 初始化 | ✅ | `Database.initialize()` 自动创建核心表和索引，支持旧库增量补列 / 补索引。 |
| 规范化保存存储 | ✅ | `saved_items` 以 canonical key 保存跨平台元数据快照，`saved_memberships` 独立表达收藏 / 稍后看归属，`native_save_states` 持久化当前逐项同步状态；`native_save_tasks` / `native_save_task_items` 独立持久化每次请求的 UUID、不可变成员集合和 task-scoped 结果。旧 `watch_later` / `favorites` 由带 marker 的单次事务迁移导入。 |
| 扩展原生保存 job ledger 与旧状态迁移 | ✅ | `extension_native_save_jobs` 保存脱敏后的六平台扩展任务；partial unique index 保证 `(platform, item_key, requested_action)` 只有一个 pending/in-progress row。命名迁移只把六个 canonical 平台的旧 `unsupported`/空 error code 改为 `unsupported_adapter_missing`，绝不改 Bilibili、未知平台或 `unsupported_content_type`。 |
| 推荐链路 canonical identity | ✅ | `content_cache.item_key` 唯一索引、`recommendations.item_key` 普通索引；初始化按平台 + raw `content_id` 回填旧行，并在建唯一索引前确定性合并 canonical 重复行（优先 canonical storage key、填补非空元数据、重定向 recommendation 引用）。若 loser 仍被旧 `watch_later` / `favorites` 引用，consolidation 会先为真实 legacy schema 补 additive `item_key` 并写入 canonical key；后续 normalized saved migration 在 exact `bvid` 不存在时用该稳定键 join keeper，既保留 membership，也不绕过 Task 2 的单次 marker / no-resurrection 语义。B 站 `bvid` 主键保持 raw BV 兼容，非 B 站 `bvid` 存储键使用 namespaced identity，API 继续从独立字段输出 raw ID 与 authoritative URL。 |
| 推荐池 readiness 计数 | ✅ | `count_pool_readiness()` 返回 `available/raw/pending/pending_eval/evaluated_pending`，供 runtime status 和补货判断使用。 |
| 来源 raw material 统计 | ✅ | `count_pool_raw_material_by_source()` 合并 `content_cache` raw rows 和 `discovery_candidates` 待评估候选，供 raw ceiling headroom 使用。 |
| discovery 待评估池 | ✅ | 新增 `discovery_candidates` 表，支持 mixed-source enqueue / claim / evaluation update / cached mark / rejection status，并持久化 `score_threshold`、`eval_attempts` 与 batch 级 `batch_eval_attempts`。 |
| discovery 历史候选查询 | ✅ | `get_existing_discovery_candidate_keys()` 与 `get_existing_content_cache_ids()` 支持 pipeline 在 enqueue 前过滤历史候选和已缓存内容，避免重复 raw 占住 Evo 前供给窗口。 |
| discovery 状态恢复 | ✅ | 启动初始化会释放过期 `evaluating` 行；terminal 状态有 status guard，避免 stale update 改写 cached / rejected 结果。 |
| discovery keyword store | ✅ | `discovery_keywords` 用 `keyword_kind` 区分常规 search 词与 explore 词；默认 `regular`，`explore` 词只供 `ExploreStrategy` 专用 claim，不会被普通 B 站 search 消费。 |
| discovery inspiration cache | ✅ | 新增 `discovery_inspiration_probe_cache`、`discovery_inspiration_expansion_cache`、`discovery_inspiration_axis` 与 `discovery_interest_selection_ledger`，持久化搜索探针证据、可复用 inspiration 轴、旧横向扩展缓存、yield 反馈计数和二级兴趣抽中事件；`search_local_inspiration_evidence()` 从 `content_cache` 抽取 local-first grounding evidence；`upsert_inspiration_axes()` / `list_inspiration_axes()` 管理轴库复用和轮转；`backfill_inspiration_axis_yield()` 用 trailing-window 全量重算（SET，幂等）把轴的 `yield_score` 从恒 0 变成由真实 `admissions` / `window_uses` 驱动，`apply_inspiration_axis_lifecycle()` 落库 stale / retired 状态迁移并物理清理 90 天陈旧行；`list_inspiration_axes_by_source()` 按 `source`（非 interest_label）过滤 + `min_yield` 高产筛 + 镜像生命周期排序，供跨域 explore 通道复用 `source='explore'` 的高产轴；`get_keyword_interest_coverage_snapshot()` 归一化汇总 keyword / raw candidate / admitted pool 覆盖和 recent selection count，用于下轮二级兴趣抽样降权；`get_keyword_cohort_stats()` 输出 inspiration / merged cohort 对比、local-first stub 字段和 replace 门禁指标。 |
| keyword interest label migration | ✅ | `migrate_keyword_interest_labels()` 根据画像整理产生的重命名 mapping 迁移 `discovery_keywords.source_interest` 和 `discovery_interest_selection_ledger.source_interest`，降低画像标签漂移造成的 coverage / selection cooldown 死桶。 |
| 最近已看过滤 | ✅ | 可换、raw 和评估路径复用 `source_platform:content_id` 与旧 BVID key，避免已看内容重复入池。 |
| 统一 admission 分数门 | ✅ | 推荐池读取、raw/headroom 统计、topic/franchise 分布、suppressed 复活、delight 候选和历史推荐读取都会应用统一最低分；初始化会清理旧低分 `content_cache` / `recommendations` 脏数据。 |
| 惊喜通道占位排除 | ✅ | `get_pool_candidates()` / `count_pool_candidates()` 统一排除被惊喜通道认领的行（`delight_notified=1`，或 delight 分数达动态阈值且 reason/hook 非空即当前惊喜队列候选），普通推荐与惊喜推荐不再重复出同一条内容；`dynamic_delight_threshold()` 以 `0.70` 为默认底线，候选池样本不少于 20 条时抬高到正式池 Top 10% 分数边界；delight backfill 会重新领取旧 `delight_score` 与当前 `relevance_score` 不一致的行，包括 `shown` 历史行。 |
| serve 平台保底查询 | ✅ | `get_pool_candidates_for_platform(platform, limit=5)` 复用 `get_pool_candidates()` 同一 servable WHERE / guards / 排序，追加 `COALESCE(NULLIF(source_platform,''),'bilibili')` 平台过滤，供推荐 serve 对窗口内缺席平台补拉；`list_servable_pool_platforms()` 返回当前可服务候选的去重平台 token（复用 `_load_available_pool_candidate_rows` 的同口径守卫）。 |
| `style_key` 历史值迁移 | ✅ | `Database.initialize()` 会把 `content_cache` / `discovery_candidates` 中已知旧内容风格 key 迁移到新的观看模式 key；写入 `cache_content()` 和 `update_discovery_candidate_evaluations()` 时也会归一化已知旧值。 |
| 封面粘性保护 | ✅ | `cache_content()` upsert 对 `cover_url` 用 `COALESCE(NULLIF(excluded,''), 现值)`——带空封面的重摄入（如互动数据刷新、事件驱动 related-chain）不再抹掉已有好封面，与 `author_name` / `body_text` 同一保护策略（v0.3.162+）。 |
| 保存内容封面生命周期 | ✅ | `iter_cover_lifecycle()` / `iter_servable_cover_urls()` 以 `content_cache.item_key` 关联 normalized `saved_memberships`，跨平台本地保存内容不会因缺少 legacy BVID 行而被漏预取或误清理；旧 `favorites` / `watch_later` 仍作为兼容 fallback。`saved_memberships(item_key)` 独立索引支持该关联。 |

## 公开 API

### Saved Memberships And Native State

```python
from openbiliclaw.saved_sync.models import SavedItemInput

item = SavedItemInput(
    source_platform="youtube",
    content_id="video-123",
    content_url="https://www.youtube.com/watch?v=video-123",
    title="Example",
)
membership = db.upsert_saved_membership("favorite", item, note="稍后整理")
native = db.ensure_native_save_state("favorite", item.item_key, "favorite")
current = db.get_saved_membership("favorite", item.item_key)
rows = db.list_saved_memberships("favorite", limit=50, offset=0)

task_rows = db.create_native_sync_task_snapshot(
    "favorite", [item.item_key], "task-id", "manual_selected"
)
if db.claim_native_sync_task_runner("task-id", "runner-id") and db.claim_native_save_item(
    "favorite", item.item_key, "task-id", "runner-id", "execution-id"
):
    db.update_native_save_claim_route(
        "favorite", item.item_key, "task-id", "execution-id",
        "favorite", "OpenBiliClaw",
    )
    db.heartbeat_native_save_claim(
        "favorite", item.item_key, "task-id", "execution-id"
    )
    db.heartbeat_native_sync_task("task-id", "runner-id")
task_rows = db.list_native_sync_task_items("task-id")
removed = db.remove_saved_membership("favorite", item.item_key)
```

存储契约：

- `saved_items.item_key` 是平台 canonical identity；不同平台可安全复用相同裸 `content_id`。
- `content_cache` 与 `recommendations` 用同一 canonical `item_key` 做跨源关联；新推荐写入会随历史记录持久化该键，读取不再依赖可能跨平台碰撞的裸 ID。
- `saved_memberships` 以 `(list_kind, item_key)` 为主键，同一内容可同时属于 `favorite` 与 `watch_later`。无 `native_save_states` 行时，membership 查询返回 `sync_status="pending"`。
- 封面预取和清理读取以 `content_cache.item_key → saved_memberships.item_key` 判断是否已保存，不依赖 legacy 表是否有同 BVID 行；初始化会为反向关联补 `saved_memberships(item_key)` 索引，并保留旧表 join 作为兼容 fallback。
- `native_save_states` 以同一联合键引用 membership；状态写入在启用外键的事务内先验证本地 membership，未本地保存的 key 会抛出 `ValueError`，不会留下 orphan state。所有 DAO 写入只接受显式 `NativeSaveStatus` 集合；新建表还有等价 `CHECK`。`ensure_native_save_state()` 使用 `INSERT OR IGNORE` 并在同一事务返回 effective row，任何已存在的 pending / claimed / syncing / retryable / terminal 状态都不会被本地重复保存降级或清空 owner。兼容用 `upsert_native_save_state()` 只能插入 / 刷新无 owner 的 pending 或写允许的 terminal 快照：传入未知 / 带空白状态、`execution_id`、`status='syncing'`、带 `task_id` 的 pending，覆盖已有 active owner，或把 terminal 降回 pending 都会拒绝；它不能建立 / 改写 task ownership。`complete_native_save_claim()` 只接受 terminal 状态，`pending/syncing/unknown` 不会清空 execution owner。
- `native_save_tasks` 以 UUID 为主键；`native_save_task_items` 以 `(task_id, item_key)` 为主键并保存请求顺序、requested/resolved action、target、status/error 与 `is_live`。task/item 集合不引用 membership，因此本地删除后轮询快照仍存在。`create_native_sync_task_snapshot()` 在一个 `BEGIN IMMEDIATE` 中写 task/items 并领取 eligible 的 live owner；缺失、terminal、已有 owner 与零 eligible 都形成可查询快照。
- `extension_native_save_jobs` 是与 native task ledger 分离的浏览器执行 ledger。`create_or_reuse_extension_native_save_job()` 在 `BEGIN IMMEDIATE` 中原子复用 active row；`claim_extension_native_save_job()` 按平台领取最老 pending job；`owns_extension_native_save_job(job_id)` 检查全局 namespace，传 `platform_slug` 时进一步限制 exact source；`complete_extension_native_save_job()` 只接受匹配 platform slug + job UUID + item key 的 in-progress row；`mark_unclaimed_extension_native_save_job_extension_required()` 与 `cancel_unclaimed_extension_native_save_job()` 只更新 pending；`expire_stale_extension_native_save_jobs()` 把不确定的 claimed write 固定完成为 `failed/extension_task_timeout`，绝不重放。所有读取返回新的 `dict` copy。
- 扩展 ledger 的 URL 只接受六平台 allow-listed HTTPS host，去 fragment、token 与 tracking query；YouTube 仅保留身份字段 `v`。结果 code 使用显式集合，result message 只从后端 status/code 映射生成，拒绝 Unicode category-C 输入，因此 Cookie、token、HTML 或平台响应正文不会进入 SQLite。
- 当前 task ledger 采用数据库生命周期保留：已返回任务没有 TTL、容量上限或自动删除，只有 starter 注册失败且未返回的 ledger 会回滚删除。未来若引入 bounded pruning，必须先定义轮询保留窗口、容量阈值以及 active/recent task 保护；该策略当前延期，不能假定存在后台清理。
- `claim_native_sync_task()` 保留为底层兼容 owner 入口；生产 service 使用上述快照 DAO 原子建立 ledger 与 ownership。执行前 `claim_native_sync_task_runner(task_id, runner_id)` 原子取得唯一 runner lease；fresh 的其它 runner 返回 `False`，stale lease 才允许接管。task heartbeat、item claim 与 pending release 都要求 runner token 匹配。runner 正常 / 取消退出释放余项；崩溃由 poll / manual-create 在 5 分钟后回收。所有 task / runner 边界拒绝空白 ID，公开 runner ID 还拒绝 `__openbiliclaw_` 保留前缀。
- `claim_native_save_item()` 还要求当前 `task_runner_id` 匹配，用 `execution_id` 原子执行 `pending → syncing`；`update_native_save_claim_route()`、`heartbeat_native_save_claim()`、`complete_native_save_claim()` 要求 `(list_kind, item_key, task_id, execution_id, status='syncing')` owner 完整匹配，旧 worker 无法刷新或完成新 owner。`reconcile_stale_native_save_claims(task_id)` 供轮询恢复一个已知 task；`reconcile_stale_native_save_claims_for_list(list_kind, item_keys)` 供普通手动创建在 eligibility selection 前恢复匹配的崩溃遗留项。两者只把超过 5 分钟无 item heartbeat 的 `syncing` 写成 `failed/interrupted`。
- `list_native_sync_eligible()` 是只读诊断 / selection 视图；`list_native_save_states_by_task()` 只用于 live runner 工作集，durable polling 必须使用 `native_sync_task_exists()` + `list_native_sync_task_items()`。claim、route、complete、membership 删除和 stale/cancel recovery 都在同一事务同步更新 task item 快照。
- 初始化只在 `saved_sync_migrations` 缺少 `legacy_saved_tables_v1` 时迁移旧表。迁移用当时的 `content_cache` 恢复平台、内容 ID 与元数据；身份字段不完整时按兼容语义回落 `bilibili:<legacy bvid>`。解析出的 canonical key 同时写入旧 `watch_later.item_key` / `favorites.item_key`，之后的状态和删除不再依赖可变或可清理的 `content_cache`。marker 在两个列表都复制成功后写入，避免已删除的 normalized membership 下次启动复活；`legacy_saved_item_keys_v2` 只为此前已迁移数据库补稳定关联，不重新导入 membership。
- 旧 `add/remove/list/count/status` Bilibili wrappers 继续维护兼容表及其 stable `item_key` link，但用户可见读取以 normalized membership 为准。状态 / 移除 wrapper 会优先匹配 Bilibili key，否则只在裸 `content_id` 唯一对应一个 normalized membership 时解析跨平台 key；移除时按旧行已持久化的 `item_key` 同步清理迁移来源行。多个非 Bilibili 平台共享该裸 ID 时状态返回 `False`、移除也返回 `False`，不删除任何一侧。
- 平台 adapter、platform-neutral HTTP API，以及插件 side panel / 桌面 Web / 移动 Web
  保存与同步 UI 已接入同一 normalized store。Bilibili 保持 direct adapter，六平台已注册 extension-backed adapter；
  stable runtime broker wiring 也已完成。其它来源的本地 membership 仍可正常保存、列出和删除，
  手动同步会进入 durable extension job ledger。Tasks 4–8 继续负责 extension executors 和经验证的
  真实账号写入；这些任务完成前不能宣称六平台账号写入已经闭环。

`native_save_states` 完整字段如下：

| 字段 | 语义 |
|------|------|
| `list_kind`, `item_key` | 联合主键，同时外键引用 `saved_memberships`。 |
| `requested_action` | 用户请求的 `favorite` / `watch_later`。 |
| `resolved_action`, `resolved_target` | capability router 决定且由 execution owner fence 写入的平台动作 / 目标。 |
| `status` | `pending`、`syncing` 或逐次尝试的 terminal 状态。 |
| `task_id` | 当前 live batch owner ID；空串表示尚未被任务领取。durable polling 的 UUID 与结果位于独立 task ledger。 |
| `execution_id` | 单次 adapter 调用 owner token；仅 `syncing` 生命周期非空。 |
| `task_claimed_at` | task 领取时间，供“已领取但 runner 未启动”保护窗判断。 |
| `task_started_at` | runner 首次开始时间；非空后不会走 never-started 回收。 |
| `task_heartbeat_at` | batch runner 最近心跳；保护尚未逐项 claim 的后排 pending，崩溃后作为 5 分钟回收租约。 |
| `task_runner_id` | 当前唯一 batch runner token。升级时，已有 `task_id + task_started_at` 的 active 旧行写入保留 legacy sentinel，并在缺 heartbeat 时补 fresh lease，防 rolling upgrade 立即抢走旧 runner；lease stale 后新 runner才可接管。 |
| `last_error_code`, `last_error_message` | 安全归一化错误，不存平台响应正文或异常正文。 |
| `last_attempt_at` | execution claim / heartbeat 最近时间，供 5 分钟 stale 判定。 |
| `synced_at` | 最近一次 `synced` / `already_synced` 完成时间。 |

其父表字段：`saved_items(item_key, source_platform, content_id, content_url, content_type, title, author_name, cover_url, created_at, updated_at)` 保存 canonical 内容快照；`saved_memberships(list_kind, item_key, note, added_at)` 保存本地列表归属；`saved_sync_migrations(name, applied_at)` 保存 legacy migration marker。旧 `watch_later` / `favorites` 继续保留 `bvid, added_at, note, item_key` 兼容字段。

### Discovery Candidates

```python
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite

count = db.enqueue_discovery_candidates(
    [
        DiscoveryCandidateWrite(
            candidate_key="youtube:abc123",
            source_platform="youtube",
            source_strategy="yt_search",
            content_id="abc123",
            title="A YouTube deep dive",
            score_threshold=0.60,
        )
    ],
    max_pending_per_source=420,
)

rows = db.claim_discovery_candidates_for_eval(limit=30)
updated = db.update_discovery_candidate_evaluations(
    [
        {
            "candidate_id": rows[0]["id"],
            "status": "evaluated",
            "relevance_score": 0.82,
            "relevance_reason": "匹配用户最近的深度解释偏好。",
        }
    ]
)
ready = db.get_evaluated_discovery_candidates_for_admission(limit=30)
if ready:
    db.mark_discovery_candidate_cached(ready[0]["id"])

db.reset_discovery_candidates_to_pending([rows[0]["id"]], reason="temporary LLM outage")
db.reset_stale_discovery_candidate_evaluations(max_age_minutes=30)
known_candidate_keys = db.get_existing_discovery_candidate_keys(["youtube:abc123"])
known_content_ids = db.get_existing_content_cache_ids(["BV1xx411c7mD"])
```

行为说明：

- `enqueue_discovery_candidates()` 用 `candidate_key` 去重；重复发现只刷新 `last_seen_at`。传入 `max_pending_per_source` 时，会按来源用总行数判断 cap、删除时保护 `evaluating` 行，并优先删除 terminal rows，避免长期满池时 candidate table 无界增长。
- `claim_discovery_candidates_for_eval(limit=...)` 只领取 `pending_eval`，并按 `source_platform` round-robin 选取 mixed-source batch；运行中不会回收其他 in-flight evaluator 的 claim。
- `update_discovery_candidate_evaluations()` 将 evaluator 输出回写到候选行，常用状态为 `evaluated`；只更新仍处于 `evaluating` 的行。
- `get_evaluated_discovery_candidates_for_admission(limit=...)` 读取已完成评估但尚未写入 `content_cache` 的行，供池子从满池降回目标以下后重试 admission。
- `reset_discovery_candidates_to_pending([...], reason=..., max_attempts=5, max_batch_attempts=50, increment_attempts=True)` 释放 evaluator failure 中被 claim 的行；`increment_attempts=True` 时连续失败达到上限后进入 `failed_eval`。pipeline 对 batch 级 LLM/provider transient 会传 `increment_attempts=False`，不消耗单条候选预算，但会递增 `batch_eval_attempts`；达到较高 `max_batch_attempts` 后进入 `failed_eval`，避免永久坏 provider 让同一批候选无限 churn。
- `reset_stale_discovery_candidate_evaluations(max_age_minutes=...)` 将崩溃遗留的旧 `evaluating` 行释放回 `pending_eval`。
- `mark_discovery_candidate_cached()` / `reject_discovery_candidate(..., status=...)` 只改写 `evaluating` / `evaluated` 行；terminal rows 不会被 stale caller 复活或覆盖。常见 rejection status 包括 `rejected_low_score`、`rejected_duplicate`、`rejected_cache_admission`、`rejected_recently_viewed`、`rejected_franchise_quota`。
- `count_discovery_candidates_by_status()` 与 `count_discovery_candidates_by_source_status()` 用于诊断待评估池生命周期分布。
- `get_existing_discovery_candidate_keys(keys)` 返回任意 lifecycle status 下已经出现过的 `candidate_key`；`get_existing_content_cache_ids(ids)` 返回已经进入正式 `content_cache` 的 BVID / `content_id`。两者用于 `DiscoveryCandidatePipeline` 在 enqueue 前过滤历史重复，而不是等 SQLite `INSERT OR IGNORE` 静默吞掉后才发现供给不足。

### Discovery Keywords

```python
db.insert_pending_keywords("bilibili", ["AI 科普"], digest)
db.insert_pending_keywords(
    "bilibili",
    ["城市 声音 采样 纪录片"],
    digest,
    keyword_kind="explore",
    metadata_by_keyword={
        "城市 声音 采样 纪录片": {
            "aspect_id": "interest:field-recording",
            "inspiration_backend": "exa",
            "inspiration_id": "urban-soundscape",
            "expansion_id": "ambient-documentary",
            "angle_id": "craft-analysis",
            "grounding_source": "local_cache",
            "generation_reason": "从搜索预览里的城市声音采样横向扩展。",
        }
    },
)

regular = db.claim_keywords("bilibili", 5)
explore = db.claim_keywords("bilibili", 5, keyword_kind="explore")
coverage = db.get_keyword_interest_coverage_snapshot()
db.record_keyword_interest_selection(["独立游戏叙事"], query_kind="regular")
stats = db.get_keyword_cohort_stats(window_days=14)
evidence = db.search_local_inspiration_evidence("独立游戏 机制", limit=5, lookback_days=365)
db.migrate_keyword_interest_labels({"AI 工具": "AI 工程化"})
```

行为说明：

- `keyword_kind="regular"` 是默认值，供普通平台 search / producer 消费。
- `keyword_kind="explore"` 是 `KeywordPlanner` 写入的 B 站探索 query 候选池，只有 `ExploreStrategy` 的 planner-backed 分支会 claim。
- 在途唯一约束包含 `(platform, keyword, profile_kw_digest, keyword_kind)`；同一个 query 可分别作为 regular 与 explore 生命周期存在，互不抢占。
- `history_keywords()` 与 `recycle_oldest_used()` 也默认只读 `regular` 池；需要查看 / 回收探索池时必须显式传 `keyword_kind="explore"`。
- `pending → claimed → used/failed/executing` 状态机保持不变；租约回收和失败回滚对两类 keyword 都生效。
- `metadata_by_keyword` 是可选溯源字段，不参与唯一约束；同一个 in-flight query 的去重仍只看 `(platform, keyword, profile_kw_digest, keyword_kind)`。当前支持记录 `aspect_id`、`inspiration_backend`、`inspiration_id`、`inspiration_terms`、`expansion_id`、`angle_id`、`query_kind`、`source_domain`、`source_interest`、`grounding_source`、`generation_reason` 和 `normalized_keyword` 等字段，供 query 丰富度诊断和后续反馈学习使用。
- `search_local_inspiration_evidence(query, limit=..., lookback_days=...)` 是 local-first inspiration grounding 的 Phase 1 DAO：它只读 `content_cache`，用 CJK 2-gram / token overlap 做相关性筛选，并在 B站 legacy 行缺少 `content_url` 时用 `bvid` 合成视频 URL；返回值只作为灵感 evidence，不写候选池。
- `record_keyword_interest_selection(labels, query_kind=..., selection_scope=...)` 在 planner 抽中二级兴趣后立即写入 selection ledger；production 运行使用 `selection_scope="production"`，`keyword-inspiration-dry-run` 使用独立的 `preview` scope，因此多次 dry-run 可以验证冷却轮转，但不会污染正式运行的抽样状态。写入时会清理 30 天前的 selection ledger 行，coverage snapshot 默认只统计最近 14 天。
- `get_keyword_interest_coverage_snapshot()` 返回以 `source_interest` / `pool_topic_label` / `topic_group` 为 key 的 coverage bucket，包括 `interest_selection_count`、`generated_keyword_count`、`selected_keyword_count`、`yield_count`、`candidate_count`、`candidate_share`、`admitted_count`、`admitted_share`、候选 dominant platform / content type 和入池 dominant content type 信息。join 前会统一走 `_normalize_match_text()` 折叠大小写和空白漂移，但输出仍保留可读 display label。`KeywordPlanner` 用它降低已抽中过、已生成过很多词、raw candidate 高频或最终入池占比高的二级兴趣下一轮被抽中的概率；只在 raw candidate 层高频、但尚未 admit 的兴趣也会提前被识别出来。
- `migrate_keyword_interest_labels(mapping)` 会按同一归一化规则匹配现有 keyword `source_interest` 和 selection ledger `source_interest`，把画像整理后的旧标签迁到新标签；`ProfileConsolidator` 在 `--apply` 时记录被迁移行，`--revert` 会按行恢复，避免简单反向 mapping 误伤原本就叫新标签的 keyword / selection 记录。
- `get_keyword_cohort_stats(window_days=14)` 按 `inspiration_id` 溯源把窗口内关键词分为 `inspiration` 与 `merged` 两组，输出 generated / claimed / claimed_rate、yield-attributed admissions、admissions_per_claimed_keyword、mean_delight、distinct_topics 和 topic_diversity_per_100_admissions；同时输出 `interest_selection.production/preview` 的 total、distinct、by_source_interest、by_query_kind 和 last_selected_at，用于诊断抽样轮转；机械 replace gate 在样本不足、准入率低于 `0.8x`、delight 低于 `0.95x` 或 topic 多样性没有严格更高时均不允许开启 replace。

### Discovery Inspiration Cache

```python
from datetime import UTC, datetime

from openbiliclaw.discovery.inspiration import AxisRow

db.upsert_inspiration_axes(
    [
        AxisRow(
            interest_label="独立游戏叙事",
            axis_label="环境叙事",
            axis_kind="method",
            example_terms=("碎片化线索", "空间讲故事"),
            evidence_refs=("https://example.test/a",),
            yield_score=0.42,
        )
    ],
    bump_usage=False,
)

axes = db.list_inspiration_axes(
    ["独立游戏叙事", "动画制作"],
    limit=4,
    now=datetime.now(UTC),
)

db.upsert_discovery_inspiration_seed(
    platform="bilibili",
    profile_kw_digest=digest,
    aspect_id="interest:game-design",
    query_kind="explore",
    probe_backend="exa",
    freshness_digest="2026-W27",
    seed_query="独立游戏 叙事设计",
    inspiration_id="environmental-narrative",
    source_terms=["环境叙事"],
    evidence_titles=["叙事游戏如何设计碎片化线索"],
    evidence_urls=["https://example.test/a"],
)

db.upsert_discovery_inspiration_expansion(
    platform="bilibili",
    profile_kw_digest=digest,
    aspect_id="interest:game-design",
    query_kind="explore",
    inspiration_id="environmental-narrative",
    expansion_id="fragmented-clues",
    relation="adjacent-mechanic",
    text="碎片化线索",
    curator_decision="keep",
    curator_score=0.86,
)

seeds = db.list_discovery_inspiration_seeds("bilibili", digest)
expansions = db.list_discovery_inspiration_expansions("bilibili", digest)
db.increment_discovery_inspiration_yield(
    "bilibili",
    digest,
    aspect_id="interest:game-design",
    query_kind="explore",
    probe_backend="exa",
    freshness_digest="2026-W27",
    seed_query="独立游戏 叙事设计",
    inspiration_id="environmental-narrative",
)
```

行为说明：

- `discovery_inspiration_axis` 记录可复用的 inspiration 轴库，字段包含 `axis_id`、`interest_label` / `interest_id`、`axis_label`、`axis_kind`、`example_terms`、`evidence_refs`、`source`、`time_sensitive`、`freshness_ttl_days`、`yield_score`、`admissions`、`use_count`、`status`、`created_at`、`last_used_at`、`last_refreshed_at`，以及 Phase 2 通过容错 `ALTER TABLE ... ADD COLUMN` 迁移补上的 `window_uses`（trailing window 内被实际消费的关键词行数，成绩公式与退休阈值的分母）和 `yield_backfilled_at`（上次 yield 回填时间戳，用于节流）；索引 `idx_discovery_inspiration_axis_interest(interest_label, status)` 支持按兴趣快速取 active 轴。注意 `window_uses` 与选取簿记 `use_count`（该轴被喂给 LLM 的次数，多样性 tie-break 用）分工不同：成绩与生命周期一律用 `window_uses`。
- `upsert_inspiration_axes(axes, bump_usage=True)` 会按 `axis_id` 插入或合并：`example_terms` / `evidence_refs` 做 JSON 数组合并，`yield_score` / `admissions` 取历史与新值的较大值；`bump_usage=True` 时递增 `use_count` 并刷新 `last_used_at`，preview 只想持久化轴库时可传 `False`。合并进 `status='retired'` 行时只更新证据、**不复活状态**（防坏轴借尸还魂）；合并进 `stale` 行时允许被新鲜 upsert 复活（不对称是有意的：话题可以回来）。该 DAO 保持**同步、零 I/O**，embedding 近邻合并的目标解析在 pipeline 层完成后才把规范化轴交给它。
- 每个 `interest_label` 最多保留 16 条 `status='active'` 轴；超过上限时按有效分（`window_uses>0` 的轴用真实 `yield_score`，从未被消费过的轴才用 `max(yield_score, 0.3)` 探索 prior 地板）、`last_refreshed_at`、`use_count`、`axis_kind` 和 `axis_label` 排序保留前 16 条，其余标为 `stale`。
- `list_inspiration_axes(interest_labels, limit, now)` 只返回 active 且未过 `freshness_ttl_days` 的轴，并按每个兴趣独立排序：`freshness × 有效分` 优先（有效分同上——消费过的轴按真实 `yield_score` 排序，低分立刻下沉；未消费轴用 prior 0.3 地板），之后依次用 `last_refreshed_at` 较新、`use_count` 较低、`axis_kind` 排名和 `axis_label` 做 tie-break；`limit` 是每个兴趣的返回上限，不是全局总量。
- `list_inspiration_axes_by_source(source, *, min_yield=0.0, limit, now)`（Phase 2.3）按 **`source` 过滤（不按 interest_label）** 返回一条全局排序列表，供跨域 explore 通道复用自己那一族 `source='explore'` 的高产轴。生命周期镜像 `list_inspiration_axes`：`status='active'`、复用**同一个** `_axis_is_time_expired(row, now)` 抑制过期时效轴、复用**同一个** `_axis_list_sort_key` 排序（不复制排序逻辑）；额外用 SQL `yield_score >= min_yield` 按**原始** `yield_score`（回填后的真实成绩，非 prior 地板值）做高产筛，`limit` 是全局上限。explore 轴的 `interest_label` 是跨域话题、不匹配任何 like 兴趣，所以只能靠 source 捞出；配合 Phase 2 按 `axis_id` 的 yield 回填即构成舒适区扩张闭环。
- `backfill_inspiration_axis_yield(*, window_days=30, now)`（Phase 2）是 **trailing-window 全量重算（SET 语义），幂等按构造**——同一数据跑两遍全表字节相同，无水位线。它聚合 window 内 inspiration cohort 的 `discovery_keywords` 行：归属只有当 `angle_id` 在轴表真实存在时才直接用，否则回退 `derive_inspiration_axis_id(source_interest, angle_label)` 现场重导（存在性校验防 legacy `angle_id==angle_label` 恰好带 `axis:` 前缀的误判）；`window_uses = COUNT(status ∈ {claimed, executing, used, failed})`（离开过 pending 即算消费，`pending`/`expired` 不算），`admissions = SUM(yield_count)`，然后 SET `yield_score = (admissions + 0.3) / (window_uses + 1.0)`（Laplace 平滑，常数 0.3 刻意等于探索 prior，未使用轴回填后 score 恰为 0.3）与 `yield_backfilled_at = now`；无 window 行的轴 SET 为 `0 / 0 / 0.3`。
- `apply_inspiration_axis_lifecycle(*, now)`（Phase 2，回填后同 tick 调用）执行三条确定性迁移并返回 `{"staled", "retired", "purged"}` telemetry：`time_sensitive=1` 且超 `freshness_ttl_days` 的 active 轴 → `status='stale'`（真正落库，不再只读取时过滤）；`window_uses >= 5` 且回填后 `yield_score < 0.08` 的 active 轴 → `status='retired'`（给过 5 次消费机会仍近乎零产出，如 0.3/6≈0.05）；`status IN ('stale','retired')` 且 `last_refreshed_at` 早于 90 天的行物理 DELETE。阈值全为模块级常量（`>=5` / `<0.08` / 90 天），`now` 注入可单测。
- `discovery_inspiration_probe_cache` 以 `(platform, profile_kw_digest, aspect_id, query_kind, probe_backend, freshness_digest, seed_query, inspiration_id)` 为主键；同一个搜索探针的证据可以刷新，但 `selected_count` / `yielded_count` 不会被 upsert 清零。
- `discovery_inspiration_expansion_cache` 以 `(platform, profile_kw_digest, aspect_id, query_kind, inspiration_id, expansion_id)` 为主键，记录 hop、relation、detail axes、curator decision / score / feedback、status 和 yield 计数。
- 这些表由可选 `KeywordPlanner` inspiration stage 写入：轴库复用和 fresh grounding evidence 共同进入单次 `discovery.keyword_inspiration` 轴 + keyword 调用；旧 probe / expansion cache 仍保留历史证据和 yield 诊断。`increment_keyword_yield()` 在记录新的 `(keyword_id, content_id)` yield 后，会 best-effort 回填对应 inspiration / expansion 的 `yielded_count`，重复 content 不会 double-count。

### Pool Readiness

```python
readiness = db.count_pool_readiness()
assert set(readiness) == {
    "available",
    "raw",
    "pending",
    "pending_eval",
    "evaluated_pending",
}

raw_by_source = db.count_pool_raw_material_by_source()
```

行为说明：

- `available` 与 `count_pool_candidates()` 保持推荐 serve 同口径。
- `raw` 包含正式池 fresh raw material 和 `discovery_candidates` 中尚未缓存的候选。
- `pending` 独立计算，不用 `raw - available` 近似，避免 recently viewed 内容被误算为待整理。
- `pending_eval` 统计 `pending_eval + evaluating`；`evaluated_pending` 统计已评估但尚未 admission 到 `content_cache` 的候选。

### Admission Cleanup

```python
db.set_admission_min_score(0.60)
db.suppress_low_score_pool_items()
db.suppress_low_confidence_recommendations()
```

行为说明：

- `set_admission_min_score()` 由 runtime 在配置加载 / 热重载时调用；storage 不直接读取 runtime state 或 `config.toml`。
- `suppress_low_score_pool_items()` 会把 `content_cache.relevance_score` 低于阈值且仍可能展示的 `fresh / shown / suppressed` 行标为 `suppressed`。
- `suppress_low_confidence_recommendations()` 会把低于阈值且尚无用户反馈的历史推荐标为 `feedback_type='suppressed_low_score'`。
- `Database.initialize()` 会用默认阈值执行一次上述清理，处理旧版本已经入池 / 入历史的低分数据。

## 配置项

存储层本身不新增独立配置。本次涉及的运行时上限仍来自：

| 配置项 | 说明 |
|--------|------|
| `scheduler.pool_target_count` | 正式可换推荐池目标；达到后 runtime 不再 discovery / drain。 |
| `[scheduler.pool_source_shares]` | 平台族配比；raw material by-source 统计用它计算 source headroom。 |
| `discovery.admission_min_score` | 统一推荐池入池最低分；runtime 会注入给 `Database`，用于池读取和旧数据清理。 |
| `storage.db_path` | SQLite 数据库路径。 |

## 设计决策

1. **待评估池和正式推荐池分离**：`discovery_candidates` 只表示“已经找到但还未成为推荐素材”，`content_cache` 才是 recommendation serve 的正式候选池。
2. **来源只影响身份和统计**：候选 dedupe key、source share 和 prompt 上下文会保留来源；喜好判断统一交给 discovery evaluator。
3. **池满时不继续消耗**：runtime 以 `count_pool_candidates()` 的真实可换数为上限判断，正式池满时不 claim / evaluate 待评估候选。
4. **评估和入池可分步恢复**：`evaluated` 表示“已经通过喜好评估但还没 admission”，不是失败终态；池子恢复容量后会优先入池。batch 级 provider transient failure 释放回 `pending_eval` 且不递增 `eval_attempts`，但会递增 `batch_eval_attempts` 作为高阈值熔断；只有调用方显式要求递增 attempts 的可归因失败才会使用常规 `eval_attempts` 预算。
5. **状态机必须防 stale caller**：`evaluating` 有过期回收，terminal rows 有 status guard，避免进程 crash 或并发 caller 让候选永久卡住或复活。
6. **pending 不是 raw 减 available**：最近已看、缺文案、缺分类、缺链接、待评估属于不同诊断含义，必须分开统计。
7. **低分清理和展示防线都在存储层落地**：admission 仍由 discovery evaluator 决定；storage 只用统一阈值阻止旧脏数据、suppressed 低分复活和未来绕过入口继续进入可展示读取路径。
8. **keyword kind 是用途隔离，不是平台隔离**：`regular` 和 `explore` 共享同一张 `discovery_keywords` 表与生命周期，便于复用 claim / lease / yield 基础设施；但默认 claim / history / recycle 只读 `regular`，避免探索 query 被普通 search 提前消费或被常规补货历史污染。
9. **`style_key` 迁移只改已知旧值**：历史安装用户的本地 SQLite 里可能已有 `deep_dive`、`story_doc`、`lifestyle` 等旧内容风格 key。初始化迁移会把这些已知值物理改写为 `deep_focus`、`story_immersion`、`daily_wander` 等新观看模式；未知自定义值会原样保留，避免误删无法识别的历史数据。
