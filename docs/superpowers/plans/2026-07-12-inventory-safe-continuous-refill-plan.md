# Inventory-Safe Continuous Refill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复两个真实用户共同遇到的“刚补出的可用库存被维护流程再次裁成 0”问题，并在同一 runtime 内实现总并发 4、后台并发 3、补货保底 2、文案 8 条立即/尾批最多等 3 秒的持续补货闭环。

**Architecture:** 先把 source family 与 pool maintenance 收敛到 SQLite 的单一原子入口，保护 canonical available 并优先复用历史 suppressed 成果；随后让 API、OpenClaw、Soul、推荐与 CLI composition 注入同一个 `LLMConcurrencyGate`，由 gate 在真实总上限内调度 interactive/refill/maintenance；最后把候选评估和文案生成拆成两个事件驱动协调器，使用 durable projected inventory、token-owned claim、串行 admission 与有界失败重试。

**Tech Stack:** Python 3.11+、`asyncio`、SQLite WAL/`BEGIN IMMEDIATE`、dataclasses、FastAPI/Pydantic、pytest/pytest-asyncio、Ruff、MyPy、Chrome extension TypeScript/JavaScript。

**Supersedes:** 本计划完整替代 `2026-07-12-global-llm-reservation-expression-microbatch-plan.md`。不要交叉执行两份计划；库存维护、补货额度和文案失败语义以本计划为准。

## Global Constraints

- 对任意后台 pool maintenance，始终满足 `available_after >= min(available_before, pool_target_count)`；用户消费、反馈和正常展示不受此不变量约束。
- canonical available 的判定、topic window、viewed/linkable/self-XHS/delight guards 和排序只保留一份实现；维护代码不得复制近似 SQL。
- SQLite 事务不得跨越任何 LLM gate 等待或 provider 请求；pool maintenance 使用短连接与 `BEGIN IMMEDIATE`，评估 provider 调用不持有数据库事务。
- raw ceiling 同时统计 `content_cache` 与 `discovery_candidates`；`evaluating` 行永不成为 victim，candidate victim 进入 `trimmed_capacity` terminal 状态而不是删除。
- 恢复历史库存只允许 `pool_status='suppressed'` 且当前仍可服务的行，不复活 recommended/viewed/disliked/purged/shown/self-XHS/unlinkable/delight-claimed 内容。
- 默认 `llm.concurrency=4`，显式旧值（例如 3）保持原值；后台容量为 `max(1, total-1)`，默认 3。
- interactive 只经过 total gate；所有 background 同时经过 total 与 background admission。`bypass_semaphore=True` 只能跳过 background admission，不能跳过 total provider 上限。
- 当 `available < target` 且有 refill waiter 时，新 maintenance 最多占一个后台槽；refill 可获得两个并借用第三个。`available == 0` 时不准入新的 maintenance provider 请求。
- refill 优先级固定为 expression > evaluation > supply；没有可运行的 refill waiter 时，maintenance 可以借用空槽。
- candidate evaluator 配置上限 3，每个 provider batch 不超过 30，总 claimed in-flight 不超过 90；任一 worker 完成后立即补位，60 秒只作 safety wake。
- projected inventory 只等于 `available + admitted_pending_copy + evaluated_pending_admission`；普通 `pending_eval` 与 `evaluating` 不计入。
- 文案 pending 达 8 条立即启动，1–7 条从首次通知起最多等待 3 秒；重复通知不延长 deadline。单 provider batch 不超过 30，一轮最多 fan-out 2 个 batch（最多处理 60 条）。
- 429、timeout、connection、5xx 不拆批；只有成功响应但缺项/损坏才重试 missing subset，拆分深度与额外请求数必须有界；零写入至少退避 15 秒。
- 不修改 Soul prompt、事件压缩、token 预算、调用价格或成本核算；这些由独立工作处理。
- 每个代码提交同步更新受影响的 `docs/modules/*.md` 与 `docs/changelog.md`；跨模块 wiring、配置默认值和图示按 `CLAUDE.md#documentation-requirements` 同步。
- 不记录或提交 API key、Cookie、完整 prompt、完整用户画像或生产数据库内容。

---

## File Map

- Create `src/openbiliclaw/sources/platforms.py`：可枚举的七个平台 family、别名、来源策略前缀与 URL host 规范化。
- Modify `src/openbiliclaw/storage/database.py`：canonical availability helper、原子维护结果、candidate terminal trim、suppressed recovery、精确 readiness 计数。
- Create `tests/test_pool_maintenance.py`：用户 A/B 形状、回滚、恢复排除条件和 raw 跨表测试。
- Create `src/openbiliclaw/llm/concurrency.py`：共享 total/background/refill-aware gate 与 runtime diagnostics。
- Modify `src/openbiliclaw/llm/service.py`、`soul/engine.py`、`soul/dialogue.py`：所有 provider 调用使用注入 gate。
- Modify `src/openbiliclaw/api/runtime_context.py`、`integrations/openclaw/bootstrap.py`、`cli.py`：每个 composition root 只创建一套 gate。
- Modify `src/openbiliclaw/runtime/candidate_eval.py` 与 `discovery/candidate_pipeline.py`：durable projected inventory、串行 admission headroom、持续 worker 补位。
- Create `src/openbiliclaw/runtime/expression_copy.py` 与 `tests/test_expression_copy_coordinator.py`：8/3/30/2 文案微批状态机。
- Modify `src/openbiliclaw/recommendation/engine.py`：missing-subset split、transient backoff 传播、copy-only public drain。
- Modify runtime/API status、配置与四份架构图；新增 opt-in 真实 provider/B 站集成验证。

### Task 0: Sync the Continuous-Evaluation Branch With Main

**Files:**
- Merge source: `main` at `8c49f07a` or newer
- Working branch: `codex/continuous-candidate-evaluation`
- Verify only; do not edit business files in this task

**Interfaces:**
- Preserves the existing token-owned staged candidate pipeline and coordinator commits.
- Incorporates the current network proxy changes before touching LLM composition roots.
- Establishes a green test baseline before new failures are introduced.

- [ ] **Step 1: Confirm the intended branch and clean tree**

Run:

```bash
git branch --show-current
git status --short
```

Expected: branch is `codex/continuous-candidate-evaluation`; status has no output.

- [ ] **Step 2: Merge the current local main**

Run:

```bash
git merge --no-edit main
```

Expected: a clean merge commit (the 2026-07-12 `git merge-tree --write-tree main HEAD` preflight completed without conflicts). If `main` has moved since this plan was written, rerun `git merge-tree --write-tree main HEAD` first and stop before editing if it reports unmerged entries.

- [ ] **Step 3: Run the branch baseline**

Run:

```bash
pytest tests/test_candidate_eval_coordinator.py tests/test_discovery_candidate_pipeline.py tests/test_discovery_candidate_store.py tests/test_refresh_runtime.py tests/test_llm_service.py tests/test_recommendation_engine.py -q
```

Expected: PASS. Existing documented skips are acceptable; any failure must be diagnosed before Task 1 so it is not attributed to the inventory fix.

- [ ] **Step 4: Record the baseline without another commit**

Run:

```bash
git log -1 --oneline
git status --short
```

Expected: merge commit (or fast-forward/current-main result) is visible and the worktree is clean.

---

### Task 1: Normalize Every Pool Source Family, Including Zhihu

**Files:**
- Create: `src/openbiliclaw/sources/platforms.py`
- Modify: `src/openbiliclaw/storage/database.py:498-765,8190-8295`
- Modify: `src/openbiliclaw/api/app.py:525-565`
- Modify: `src/openbiliclaw/runtime/keyword_fetch.py:45-56`
- Modify: `src/openbiliclaw/discovery/engine.py:1515-1555`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_discovery_engine.py`
- Create: `tests/test_source_platforms.py`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Produces `SourceFamilyRule`, `SOURCE_FAMILY_RULES`, `CANONICAL_SOURCE_FAMILIES`.
- Produces `normalize_source_platform(value: object, *, default: str = "") -> str`.
- Produces `source_family(source: object, source_platform: object = "") -> str`.
- Produces `infer_source_platform_from_url(url: object) -> str`.
- Preserves private database wrappers `_pool_source_family()` and `_normalize_source_platform_key()` for current callers while delegating to the new source of truth.
- Preserves API behavior where an empty platform defaults to Bilibili by calling `normalize_source_platform(..., default="bilibili")`.

- [ ] **Step 1: Write the failing family-registry tests**

```python
@pytest.mark.parametrize(
    ("platform", "source", "expected"),
    [
        ("bilibili", "search", "bilibili"),
        ("bili", "related_chain", "bilibili"),
        ("xhs", "xhs-search", "xiaohongshu"),
        ("rednote", "xiaohongshu_task", "xiaohongshu"),
        ("dy", "dy-hot", "douyin"),
        ("tiktok", "douyin_search", "douyin"),
        ("yt", "yt-search", "youtube"),
        ("x", "x-feed", "twitter"),
        ("rd", "reddit-hot", "reddit"),
        ("zh", "zhihu-creator", "zhihu"),
        ("", "zhihu_hot", "zhihu"),
        ("zhihu", "zhihu-related", "zhihu"),
    ],
)
def test_source_family_aliases(platform: str, source: str, expected: str) -> None:
    assert source_family(source, platform) == expected


def test_registry_contains_every_runtime_platform() -> None:
    assert CANONICAL_SOURCE_FAMILIES == (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.bilibili.com/video/BV1abc", "bilibili"),
        ("https://www.xiaohongshu.com/explore/a", "xiaohongshu"),
        ("https://www.douyin.com/video/1", "douyin"),
        ("https://youtu.be/abc", "youtube"),
        ("https://x.com/user/status/1", "twitter"),
        ("https://www.zhihu.com/question/1/answer/2", "zhihu"),
        ("https://www.reddit.com/r/python/comments/a/title", "reddit"),
    ],
)
def test_url_inference_uses_registry(url: str, expected: str) -> None:
    assert infer_source_platform_from_url(url) == expected
```

Add a parameterized test over every value in `ZHIHU_SOURCE_STRATEGIES` and the five values in `ZHIHU_DISCOVERY_SCOPE_STRATEGIES`; each must resolve to `zhihu` even when `source_platform` is blank.

- [ ] **Step 2: Prove current Zhihu accounting fails**

Run:

```bash
pytest tests/test_source_platforms.py tests/test_storage.py -q -k 'source_family or zhihu'
```

Expected: FAIL because `src/openbiliclaw/sources/platforms.py` does not exist and `_pool_source_family("zhihu-creator", "")` returns `zhihu-creator`.

- [ ] **Step 3: Implement the enumerable rule table**

```python
@dataclass(frozen=True)
class SourceFamilyRule:
    family: str
    platform_aliases: frozenset[str]
    source_keys: frozenset[str] = frozenset()
    source_prefixes: tuple[str, ...] = ()
    url_hosts: tuple[str, ...] = ()


SOURCE_FAMILY_RULES = (
    SourceFamilyRule(
        family="bilibili",
        platform_aliases=frozenset({"bilibili", "bili"}),
        source_keys=frozenset({"search", "related_chain", "trending", "explore"}),
        url_hosts=("bilibili.com", "b23.tv"),
    ),
    SourceFamilyRule(
        family="xiaohongshu",
        platform_aliases=frozenset({"xiaohongshu", "xhs", "rednote"}),
        source_prefixes=("xhs-", "xhs_", "xiaohongshu"),
        url_hosts=("xiaohongshu.com", "xhslink.com"),
    ),
    SourceFamilyRule(
        family="douyin",
        platform_aliases=frozenset({"douyin", "dy", "tiktok"}),
        source_prefixes=("dy-", "dy_", "douyin"),
        url_hosts=("douyin.com",),
    ),
    SourceFamilyRule(
        family="youtube",
        platform_aliases=frozenset({"youtube", "yt"}),
        source_prefixes=("yt-", "yt_", "youtube"),
        url_hosts=("youtube.com", "youtu.be"),
    ),
    SourceFamilyRule(
        family="twitter",
        platform_aliases=frozenset({"twitter", "x"}),
        source_prefixes=("x-", "x_", "twitter"),
        url_hosts=("x.com", "twitter.com"),
    ),
    SourceFamilyRule(
        family="zhihu",
        platform_aliases=frozenset({"zhihu", "zh", "知乎"}),
        source_prefixes=("zhihu-", "zhihu_"),
        url_hosts=("zhihu.com",),
    ),
    SourceFamilyRule(
        family="reddit",
        platform_aliases=frozenset({"reddit", "rd"}),
        source_prefixes=("reddit-", "reddit_"),
        url_hosts=("reddit.com", "redd.it"),
    ),
)
CANONICAL_SOURCE_FAMILIES = tuple(rule.family for rule in SOURCE_FAMILY_RULES)
```

`normalize_source_platform()` checks aliases only; `source_family()` checks platform aliases, exact strategy keys, then prefixes; `infer_source_platform_from_url()` parses the hostname and matches exact host or `.<registered-host>` suffix. It must not use substring matching on the entire URL.

- [ ] **Step 4: Delegate database/API/runtime normalization to the registry**

```python
def _pool_source_family(source: object, source_platform: object = "") -> str:
    return source_family(source, source_platform)


def _normalize_source_platform_key(source_platform: object) -> str:
    return normalize_source_platform(source_platform)
```

Replace both database and API URL-inference ladders with the imported registry function. Keep API's private wrappers so existing tests and call sites do not change signatures. Re-export the seven `PLATFORM_*` constants from `runtime/keyword_fetch.py` by importing them from `sources.platforms`; do not maintain a second literal list. Replace `ContentDiscoveryEngine._candidate_view_keys()`'s four-alias ladder with `normalize_source_platform(content.source_platform, default="bilibili" if content.bvid else "")`.

- [ ] **Step 5: Add the production-shape Zhihu pool regression**

Seed three ready rows whose `source` values are `zhihu-creator`, `zhihu-hot`, and `zhihu-feed`, with `source_platform=""`. Assert:

```python
assert db.count_pool_available_candidates_by_source() == {"zhihu": 3}
assert db.count_pool_raw_material_by_source() == {"zhihu": 3}
```

Also seed a viewed event carrying `source_platform="zh"` and a Zhihu answer URL; assert `_extract_content_keys_from_view_event()` emits a `zhihu:<content-id>` key.

- [ ] **Step 6: Update storage documentation and changelog**

Document the seven canonical families, alias/prefix rules, and the fact that all pool accounting, discovery viewed filtering, stored viewed identity and URL inference use `sources.platforms`. Add a current-version changelog bullet for the Zhihu quota fix.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
pytest tests/test_source_platforms.py tests/test_storage.py tests/test_api_app.py tests/test_discovery_engine.py -q -k 'platform or source_family or zhihu or viewed'
ruff check src/openbiliclaw/sources/platforms.py src/openbiliclaw/storage/database.py src/openbiliclaw/api/app.py src/openbiliclaw/runtime/keyword_fetch.py src/openbiliclaw/discovery/engine.py tests/test_source_platforms.py
```

Expected: PASS; all `zhihu-*` strategies report under `zhihu`.

```bash
git add src/openbiliclaw/sources/platforms.py src/openbiliclaw/storage/database.py src/openbiliclaw/api/app.py src/openbiliclaw/runtime/keyword_fetch.py src/openbiliclaw/discovery/engine.py tests/test_source_platforms.py tests/test_storage.py tests/test_api_app.py tests/test_discovery_engine.py docs/modules/storage.md docs/modules/discovery.md docs/changelog.md
git commit -m "fix: normalize pool source families including zhihu"
```

---

### Task 2: Preserve Available Inventory in One Atomic Maintenance Transaction

**Files:**
- Modify: `src/openbiliclaw/storage/database.py:910-975,2820-3260,3550-4015`
- Modify: `src/openbiliclaw/runtime/refresh.py:150-190,707-825`
- Create: `tests/test_pool_maintenance.py`
- Modify: `tests/test_refresh_runtime.py`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Produces immutable `PoolMaintenanceResult` with the exact observability fields below.
- Produces `Database.maintain_pool_inventory(*, target: int, raw_ceiling: int, source_share_quotas: Mapping[str, int], raw_source_share_quotas: Mapping[str, int] | None = None, max_per_topic_group: int = 3, max_per_explore_cluster: int = 3, stale_max_age_days: int = 14, xhs_self_nickname: str = "") -> PoolMaintenanceResult`.
- Produces candidate terminal status `trimmed_capacity`; `eval_error` stores `pool_raw_ceiling` or `source_raw_ceiling:<family>`.
- Changes `trim_discovery_candidates_for_source()` to count only active `pending_eval/evaluating/evaluated` rows and terminalize unclaimed excess as `trimmed_capacity`; queue-cap enforcement no longer deletes rows.
- Replaces runtime's separately committed topic/reactivate/source/raw calls with one production call.
- Keeps old trim methods callable for compatibility tests and manual tools, but `_enforce_pool_cap()` no longer composes them.

- [ ] **Step 1: Define failing User A and User B regressions**

Use a local helper that creates ready rows with unique `topic_group`, valid copy/style, score `0.9`, and a linkable URL. The User A test is:

```python
def test_user_a_shape_raw_trim_cannot_erase_sixteen_available(tmp_path: Path) -> None:
    db = _database(tmp_path)
    for index in range(16):
        _seed_ready(db, f"BV_READY_{index:03d}", topic_group=f"ready-{index}")
    for index in range(602):
        _seed_unready(db, f"BV_RAW_{index:03d}", topic_group=f"raw-{index % 5}")

    before = db.count_pool_candidates()
    result = db.maintain_pool_inventory(
        target=600,
        raw_ceiling=600,
        source_share_quotas={"bilibili": 5},
        raw_source_share_quotas={"bilibili": 600},
        max_per_topic_group=3,
    )

    assert before == 16
    assert result.available_before == 16
    assert result.available_after >= 16
    assert result.raw_before == 618
    assert result.raw_after <= 600
    assert result.rolled_back is False
```

The User B test seeds ten ready rows and twelve unready rows distributed across `zhihu-creator`, `zhihu-hot`, `zhihu-feed`, and `zhihu-related`, then runs target/raw ceiling 10 with source quota 3:

```python
assert result.available_before == 10
assert result.available_after == 10
assert result.trimmed_raw == 12
assert result.deferred_source_trim >= 7
assert db.count_pool_available_candidates_by_source() == {"zhihu": 10}
```

- [ ] **Step 2: Add cross-table raw and active-claim tests**

Seed:

- 4 protected ready `content_cache` rows;
- 3 unready `content_cache` rows;
- 4 `discovery_candidates(status='pending_eval')`;
- 2 `discovery_candidates(status='evaluated')`;
- 2 token-owned `discovery_candidates(status='evaluating')`.

Run with `target=4`, `raw_ceiling=8`. Assert pending rows are terminal-trimmed before evaluated rows, evaluating rows retain status/token, no row is deleted, and:

```python
assert result.available_after == 4
assert result.raw_after == 8
assert db.count_discovery_candidates_by_status()["evaluating"] == 2
assert db.count_discovery_candidates_by_status()["trimmed_capacity"] >= 1
```

Call `trim_discovery_candidates_for_source()` on a source with terminal history plus active rows. Assert terminal history does not consume the active cap, excess active rows become `trimmed_capacity`, total row count is unchanged, and every `evaluating` token survives.

- [ ] **Step 3: Add surplus and rollback tests**

For 16 ready rows with `target=10`, assert maintenance may suppress at most six and ends at exactly 10. Then monkeypatch `_validate_pool_maintenance_invariant()` to raise `PoolMaintenanceInvariantError("forced test failure")` after victim updates; assert `rolled_back=True`, all original `pool_status` values remain unchanged, and all candidate statuses/tokens remain unchanged.

- [ ] **Step 4: Run the new tests and verify current destructive behavior**

Run:

```bash
pytest tests/test_pool_maintenance.py -q
```

Expected: FAIL because `PoolMaintenanceResult`, `PoolMaintenanceInvariantError`, and `maintain_pool_inventory()` do not exist.

- [ ] **Step 5: Add the result and invariant types**

```python
@dataclass(frozen=True)
class PoolMaintenanceResult:
    available_before: int
    available_after: int
    target: int
    protected_available: int
    recovered_suppressed: int
    trimmed_stale: int
    trimmed_explore_cluster: int
    trimmed_ready_reserve: int
    trimmed_evaluated: int
    trimmed_raw: int
    trimmed_by_source: dict[str, int]
    deferred_topic_trim: int
    deferred_source_trim: int
    deferred_stale_trim: int
    deferred_explore_cluster_trim: int
    raw_before: int
    raw_after: int
    raw_ceiling: int
    untrimmed_raw_excess: int
    rolled_back: bool
    reason: str = ""

    @property
    def at_target(self) -> bool:
        return self.available_after >= self.target


class PoolMaintenanceInvariantError(RuntimeError):
    pass
```

`recovered_suppressed` is zero until Task 3 but is present now so runtime logging and the return type do not churn.

- [ ] **Step 6: Refactor canonical availability to accept an explicit connection**

Create `_load_available_pool_candidate_rows_on(self, conn: sqlite3.Connection, *, max_per_topic_group: int = 3, xhs_self_nickname: str = "") -> list[dict[str, Any]]`, `_recent_viewed_content_keys_on(self, conn: sqlite3.Connection, *, limit: int = 2000) -> set[str]`, and `_dynamic_delight_threshold_on(self, conn: sqlite3.Connection, *, default_threshold: float) -> float`. Move the complete current body of `_load_available_pool_candidate_rows()` into the first helper, changing `self.conn.execute` to `conn.execute`, `self.get_recent_viewed_content_keys()` to the second helper, and `self.dynamic_delight_threshold()` to the third helper; the public method becomes a one-line delegation using `self.conn`. Move the complete current event query/`_extract_content_keys_from_view_event` loop into the second helper and the current scored-delight percentile query into the third; both public methods delegate using `self.conn`.

This literal extraction preserves admission/self-XHS/delight/link/copy/style/topic/recommendation predicates, topic `ROW_NUMBER()` window and viewed filtering. Add the existing serve `ORDER BY` (`candidate_tier`, relevance, score time, view count, BVID) before returning rows so protection of `min(available_before, target)` is stable. `count_pool_candidates()`, maintenance protection, and Task 3 recovery all call this one helper; no second servability SQL predicate is allowed.

- [ ] **Step 7: Implement the short-connection transaction**

The production shape is:

```python
conn = self.open_connection()
try:
    conn.execute("BEGIN IMMEDIATE")
    before_rows = self._load_available_pool_candidate_rows_on(
        conn,
        xhs_self_nickname=xhs_self_nickname,
    )
    protected_ids = {
        str(row["bvid"])
        for row in before_rows[: min(len(before_rows), clean_target)]
    }
    raw_before = self._count_pool_raw_material_on(conn)
    stale_plan = self._plan_stale_trim_on(
        conn,
        protected_ids=protected_ids,
        max_age_days=clean_stale_days,
    )
    explore_plan = self._plan_explore_cluster_trim_on(
        conn,
        protected_ids=protected_ids,
        max_per_cluster=clean_explore_cap,
    )
    topic_plan = self._plan_topic_trim_on(
        conn,
        protected_ids=protected_ids,
        max_per_topic_group=clean_topic_cap,
    )
    source_plan = self._plan_source_trim_on(
        conn,
        protected_ids=protected_ids,
        source_share_quotas=clean_source_quotas,
    )
    self._apply_content_status_on(conn, stale_plan.victim_bvids, status="stale")
    self._apply_content_suppression_on(conn, explore_plan.victim_bvids)
    self._apply_content_suppression_on(conn, topic_plan.victim_bvids)
    self._apply_content_suppression_on(conn, source_plan.victim_bvids)
    raw_plan = self._plan_raw_trim_on(
        conn,
        protected_ids=protected_ids,
        raw_ceiling=clean_raw_ceiling,
        raw_source_share_quotas=clean_raw_source_quotas,
    )
    self._apply_raw_trim_on(conn, raw_plan)
    after_rows = self._load_available_pool_candidate_rows_on(
        conn,
        xhs_self_nickname=xhs_self_nickname,
    )
    self._validate_pool_maintenance_invariant(
        available_before=len(before_rows),
        available_after=len(after_rows),
        target=clean_target,
    )
    conn.commit()
except Exception as exc:
    conn.rollback()
    return self._rolled_back_pool_maintenance_result(exc, target=clean_target)
finally:
    conn.close()
```

Implement the helper bodies in the same task. Do not call `_execute_write()` inside this transaction because it commits the process-wide connection.

- [ ] **Step 8: Enforce victim ordering and terminal audit**

`_plan_raw_trim_on()` counts active rows across both tables and chooses only the excess, in this exact victim order:

1. unready, non-protected `content_cache` raw rows;
2. unclaimed `discovery_candidates.status='pending_eval'`;
3. unclaimed `discovery_candidates.status='evaluated'`;
4. ready-reserve `content_cache` rows not in `protected_ids`.

Within a tier, over-quota family first, then lower relevance, older score/seen time, explore source, stable ID. It must never select `evaluating`. Candidate application is:

```python
placeholders = ", ".join("?" for _ in candidate_ids)
conn.execute(
    f"""
    UPDATE discovery_candidates
    SET status = 'trimmed_capacity',
        eval_error = ?,
        claimed_at = NULL,
        claim_token = NULL
    WHERE id IN ({placeholders})
      AND status IN ('pending_eval', 'evaluated')
      AND claim_token IS NULL
    """,
    (trim_reason, *candidate_ids),
)
```

Use the same conditional update in `trim_discovery_candidates_for_source()` with `eval_error='source_raw_ceiling:<family>'`. Its count query is restricted to `status IN ('pending_eval', 'evaluating', 'evaluated')`; victim selection excludes `evaluating` and every non-null `claim_token`.

If protected plus evaluating rows alone exceed the ceiling, keep them, set `untrimmed_raw_excess`, and log `ERROR`; never violate available protection to force the configured ceiling.

- [ ] **Step 9: Wire runtime to the single entry point**

Add `maintain_pool_inventory()` to `SupportsEventDatabase`. Replace `_enforce_pool_cap()`'s individual topic/reactivation/source/raw calls with one call:

```python
result = self.database.maintain_pool_inventory(
    target=self.pool_target_count,
    raw_ceiling=self._raw_material_ceiling(),
    source_share_quotas=self._source_target_counts(),
    raw_source_share_quotas=self._raw_source_target_counts(),
    max_per_topic_group=max(3, self.pool_target_count // 10),
    max_per_explore_cluster=3,
    stale_max_age_days=14,
    xhs_self_nickname=self._xhs_self_nickname(),
)
```

Emit one structured summary containing every `PoolMaintenanceResult` field. If `rolled_back`, log at `ERROR` and return the pre-transaction availability decision; otherwise return `result.at_target`. In `_run_refresh_plan()`, replace the direct explore/topic/stale calls with one `_enforce_pool_cap()` call after durable admissions; there must be no second destructive maintenance composition. Update runtime fakes to return a deterministic result rather than retaining the old method-call expectations.

- [ ] **Step 10: Update module docs and changelog in the same commit**

Document the invariant, protected tiers, cross-table raw definition, `trimmed_capacity`, and `BEGIN IMMEDIATE` boundary in storage docs. Document that runtime calls one maintenance entry and treats source/topic quotas as deferrable below target.

- [ ] **Step 11: Run tests and commit**

Run:

```bash
pytest tests/test_pool_maintenance.py tests/test_storage.py tests/test_refresh_runtime.py -q
ruff check src/openbiliclaw/storage/database.py src/openbiliclaw/runtime/refresh.py tests/test_pool_maintenance.py tests/test_refresh_runtime.py
```

Expected: PASS, including User A 618→600, User B 22→10, cross-table trim, active claim protection, surplus-only trimming, and rollback.

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/runtime/refresh.py tests/test_pool_maintenance.py tests/test_refresh_runtime.py docs/modules/storage.md docs/modules/runtime.md docs/changelog.md
git commit -m "fix: preserve available inventory during pool maintenance"
```

---

### Task 3: Recover Eligible Historical Suppressed Inventory Before LLM Work

**Files:**
- Modify: `src/openbiliclaw/storage/database.py` (`maintain_pool_inventory` and suppressed selection)
- Modify: `src/openbiliclaw/runtime/refresh.py:1065-1145`
- Modify: `tests/test_pool_maintenance.py`
- Modify: `tests/test_refresh_runtime.py`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Extends `maintain_pool_inventory(..., recover_suppressed: bool = True)` without changing callers.
- Produces `_recover_suppressed_pool_inventory_on(conn, *, deficit: int, source_share_quotas: Mapping[str, int], xhs_self_nickname: str) -> list[str]`.
- Guarantees recovery occurs inside the same maintenance transaction before victim planning and before any runtime LLM loop starts.
- Guarantees idempotency and caps canonical availability at `target`.

- [ ] **Step 1: Write the exclusion-matrix test first**

Seed suppressed, fully ready rows for these cases:

| Row | Expected |
|---|---|
| highest-score eligible Bilibili | recovered |
| eligible Zhihu | recovered |
| eligible XHS with `xsec_token` | recovered |
| present in `recommendations` | excluded |
| matching a recent view event | excluded |
| `feedback_type='dislike'` | excluded |
| `pool_status='purged_by_dislike'` | excluded |
| `pool_status='shown'` | excluded |
| `recommended_at` non-null | excluded |
| self-authored XHS nickname | excluded |
| XHS URL without `xsec_token` | excluded |
| missing expression/topic/style/group | excluded |
| below admission threshold | excluded |
| current delight claim fields set above threshold | excluded |

Run with target 2 and assert only the two highest-ranked eligible rows become fresh. Run the same maintenance call again and assert `recovered_suppressed == 0` and no counts/statuses change.

- [ ] **Step 2: Add source-deficit ordering and global-fill tests**

Start with Bilibili already at its source target but total availability below target; seed one suppressed under-quota Zhihu row and two higher-score Bilibili rows. Assert Zhihu is recovered first. Then remove the Zhihu row and assert Bilibili is allowed to fill the global gap: a source quota affects ordering, not admission, while total availability is below target.

- [ ] **Step 3: Add startup-order tests**

In `tests/test_refresh_runtime.py`, record calls from `maintain_pool_inventory`, `prepare_delight_candidates`, candidate coordinator start, and expression coordinator start. Assert maintenance is first. Add a hot-reload construction test that a new controller's first `run_forever()` performs maintenance before launching its new background tasks.

- [ ] **Step 4: Run tests and verify recovery is absent**

Run:

```bash
pytest tests/test_pool_maintenance.py tests/test_refresh_runtime.py -q -k 'recover or startup_order or source_deficit'
```

Expected: FAIL because suppressed rows remain suppressed and runtime currently prepares delight before pool repair.

- [ ] **Step 5: Implement recovery using the canonical helper**

Inside the transaction, compute `deficit = max(0, target - len(before_rows))`. Select `pool_status='suppressed'` rows with the canonical readiness predicate adapted only by replacing the status equality; additionally require `recommended_at IS NULL`. Apply recent-view and generic linkability checks through the same helper functions used by availability.

Rank candidates by:

```python
(
    0 if current_family_count[family] < source_quota.get(family, 0) else 1,
    -float(row["relevance_score"] or 0.0),
    -self._sort_timestamp_score(str(row["last_scored_at"] or "")),
    str(row["bvid"]),
)
```

Walk ranked candidates inside the same transaction, update one BVID to `pool_status='fresh'`, and reload canonical availability after each update. Stop as soon as canonical availability reaches target or the eligible list is exhausted; this allows a candidate outside the current topic window to become ready reserve without preventing a later candidate from filling the visible deficit. Add every newly canonical-available BVID to `protected_ids` before topic/source/raw victim selection. Report `recovered_suppressed` as the number of selected rows that remain fresh after all maintenance plans apply, so a row restored and re-trimmed in the same transaction is not double-counted as net recovery.

- [ ] **Step 6: Move startup repair ahead of all LLM work**

At the top of `ContinuousRefreshController.run_forever()`:

```python
with suppress(Exception):
    self._enforce_pool_cap()
if self._llm_work_allowed():
    with suppress(Exception):
        await self.prepare_delight_candidates()
```

Because API hot reload and OpenClaw both start a new controller through this method, the same ordering covers startup and reload. Do not add a second recovery call in composition roots.

- [ ] **Step 7: Update docs and changelog**

Document the exact recovery filters, source-deficit ordering, target cap, idempotency and “reuse paid results before LLM” lifecycle. Add the upgrade behavior to the current changelog block.

- [ ] **Step 8: Run tests and commit**

Run:

```bash
pytest tests/test_pool_maintenance.py tests/test_refresh_runtime.py -q
```

Expected: PASS; eligible history recovers before the first background provider call and all exclusion cases remain untouched.

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/runtime/refresh.py tests/test_pool_maintenance.py tests/test_refresh_runtime.py docs/modules/storage.md docs/modules/runtime.md docs/changelog.md
git commit -m "fix: recover eligible suppressed pool inventory"
```

---

### Task 4: Share One True Total/Background LLM Gate Across Each Runtime

**Files:**
- Create: `src/openbiliclaw/llm/concurrency.py`
- Modify: `src/openbiliclaw/llm/service.py:1-285,350-680`
- Modify: `src/openbiliclaw/soul/engine.py:125-180`
- Modify: `src/openbiliclaw/soul/dialogue.py:85-235`
- Modify: `src/openbiliclaw/api/runtime_context.py:360-445,810-930`
- Modify: `src/openbiliclaw/api/app.py:9050-9075`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py:55-135`
- Modify: `src/openbiliclaw/runtime/refresh.py:280-340,490-550`
- Modify: `src/openbiliclaw/cli.py:500-730` and every direct `LLMService` composition site
- Modify: `src/openbiliclaw/config.py:100-115`
- Modify: `src/openbiliclaw/api/models.py:170-215,1055-1070`
- Modify: `config.example.toml:48-58`
- Modify: `extension/popup/popup.html`, `extension/popup/popup.js`, `extension/tests/popup-settings.test.ts`
- Modify: `src/openbiliclaw/web/desktop/index.html`, `src/openbiliclaw/web/desktop/assets/js/app.js`
- Create: `tests/test_llm_concurrency.py`
- Modify: `tests/test_llm_service.py`, `tests/test_soul_dialogue.py`, `tests/test_api_app.py`, `tests/test_openclaw_adapter.py`, `tests/test_cli.py`, `tests/test_config.py`, `tests/test_desktop_web_multimodal_settings.py`
- Modify: `docs/modules/llm.md`, `docs/modules/config.md`, `docs/modules/soul.md`, `docs/modules/runtime.md`, `docs/modules/integrations.md`, `docs/modules/extension.md`, `docs/changelog.md`
- Modify: `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`

**Interfaces:**
- Produces `DEFAULT_TOTAL_LLM_CONCURRENCY = 4` and `background_llm_concurrency(total: object) -> int`.
- Produces `LLMTrafficClass` values `interactive`, `refill.expression`, `refill.evaluation`, `refill.supply`, `maintenance`.
- Produces `LLMConcurrencyGate(total_concurrency: int)` with `slot(*, caller: str, bypass_background: bool = False) -> AsyncContextManager[None]`, `classify(caller: str) -> LLMTrafficClass`, and `status_payload() -> dict[str, int | str | bool]`.
- Adds `LLMService.concurrency_gate: LLMConcurrencyGate | None`; after `__post_init__` the field always holds an object.
- Adds `SoulEngine(..., llm_concurrency_gate: LLMConcurrencyGate | None = None)`.
- Adds `ContinuousRefreshController.llm_concurrency_gate: Any | None = None`.
- Sets API, OpenClaw and CLI `DiscoveryConcurrencyController.llm_evaluation_concurrency` to `background_llm_concurrency(llm_concurrency)` instead of hard-coded 2/4/32 fan-out.
- Preserves `PrioritySemaphore` import compatibility from `openbiliclaw.llm.service` by re-exporting it from the new module.
- Changes only the absent/invalid default to 4; explicitly configured positive values are not rewritten.

- [ ] **Step 1: Write failing total/background and identity tests**

```python
def test_background_concurrency_reserves_one_total_slot() -> None:
    assert background_llm_concurrency(4) == 3
    assert background_llm_concurrency(3) == 2
    assert background_llm_concurrency(1) == 1
    assert background_llm_concurrency("invalid") == 3


async def test_three_background_calls_leave_default_interactive_slot() -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def background() -> None:
        async with gate.slot(caller="soul.preference"):
            await release.wait()

    background_tasks = [asyncio.create_task(background()) for _ in range(4)]
    await _wait_until(lambda: gate.status_payload()["llm_background_active"] == 3)

    async def interactive() -> None:
        async with gate.slot(caller="soul.dialogue"):
            entered.set()

    interactive_task = asyncio.create_task(interactive())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert gate.status_payload()["llm_total_active"] == 4
    release.set()
    await asyncio.gather(*background_tasks, interactive_task)


def test_two_services_share_exact_gate_object(memory: Any, registry: Any) -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    left = LLMService(registry=registry, memory=memory, concurrency_gate=gate)
    right = LLMService(registry=registry, memory=memory, concurrency_gate=gate)
    assert left.concurrency_gate is gate
    assert right.concurrency_gate is gate
```

Add cancellation tests for a queued total waiter and a queued background waiter, plus a total=1 degradation test. After cancellation, a fresh call must acquire within one second and all active counters must return to zero.

Add this parameterized classification audit; a final Task 9 `rg` scan keeps it synchronized:

```python
@pytest.mark.parametrize(
    "caller",
    [
        "discovery.douyin.keyword_gen",
        "discovery.evaluate_batch",
        "discovery.evaluate_single",
        "discovery.explore.queries",
        "discovery.keyword_inspiration",
        "discovery.keyword_planner",
        "discovery.search.queries",
        "discovery.x.keyword_gen",
        "eval.query_quality",
        "eval.relevance",
        "eval.scenario_gen",
        "eval.specificity",
        "pool_purge.llm_agent",
        "recommendation.evaluate_batch",
        "recommendation.expression",
        "recommendation.write_expression",
        "runtime.bilibili_extension_search.queries",
        "soul.avoidance_speculate",
        "soul.awareness",
        "soul.category_migration",
        "soul.consolidation",
        "soul.core_update",
        "soul.dialogue_insight",
        "soul.insight",
        "soul.preference",
        "soul.preference.chunk",
        "soul.profile_build",
        "soul.role_update",
        "soul.speculate",
        "soul.values_update",
        "sources.xhs.keyword_gen",
        "sources.zhihu.extract",
        "yt_search.generate_queries",
    ],
)
def test_current_background_callers_are_classified(caller: str) -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    assert gate.classify(caller) is not LLMTrafficClass.INTERACTIVE
```

Assert separately that the four confirmed interactive tags classify interactive. `sources.zhihu.extract` proves the dynamic `sources.<platform>.extract` prefix. Another test invokes an unmatched tag twice and asserts one warning total while both calls remain background-limited.

- [ ] **Step 2: Write failing composition tests**

Assert:

```python
assert soul_engine._llm_service.concurrency_gate is injected_gate
assert dialogue._build_service() is soul_engine._llm_service
assert api_main_service.concurrency_gate is api_soul_engine._llm_service.concurrency_gate
assert openclaw_main_service.concurrency_gate is openclaw_soul_engine._llm_service.concurrency_gate
```

In CLI tests, call `_build_soul_engine()`, `_build_recommendation_engine()`, and `_build_discovery_engine()` within one cleared `_RUNTIME_COMPONENTS` composition and assert all internal services use the same cached gate object.

- [ ] **Step 3: Run tests and confirm the current double-semaphore bug**

Run:

```bash
pytest tests/test_llm_concurrency.py tests/test_llm_service.py tests/test_soul_dialogue.py tests/test_openclaw_adapter.py tests/test_cli.py -q
```

Expected: FAIL because no shared gate module/constructor arguments exist and Soul currently constructs an independent `PrioritySemaphore`.

- [ ] **Step 4: Move the priority semaphore and implement the first gate layer**

Move `PrioritySemaphore` unchanged except for explicit read-only `capacity`, `active`, and `waiting` properties. Implement a total priority semaphore plus a background semaphore:

```python
class LLMTrafficClass(StrEnum):
    INTERACTIVE = "interactive"
    REFILL_EXPRESSION = "refill.expression"
    REFILL_EVALUATION = "refill.evaluation"
    REFILL_SUPPLY = "refill.supply"
    MAINTENANCE = "maintenance"


def background_llm_concurrency(total: object) -> int:
    normalized = coerce_total_concurrency(total)
    return max(1, normalized - 1)


class LLMConcurrencyGate:
    def __init__(self, total_concurrency: int = DEFAULT_TOTAL_LLM_CONCURRENCY) -> None:
        self.total_concurrency = coerce_total_concurrency(total_concurrency)
        self.background_concurrency = background_llm_concurrency(self.total_concurrency)
        self._total = PrioritySemaphore(self.total_concurrency)
        self._background = PrioritySemaphore(self.background_concurrency)
```

For this task, `classify()` uses exact interactive tags (`soul.dialogue`, `soul.dialogue.tools`, `soul.dialogue.tool_followup`, `api.sentiment`), exact expression/evaluation tags, known supply prefixes, and maintenance fallback. Unknown callers warn once and remain maintenance. Task 5 will make background admission refill-aware without changing the public API.

- [ ] **Step 5: Route every service provider path through the gate**

`LLMService.__post_init__()` creates a private compatibility gate only when no gate was injected. Add one internal context manager and use it in normal, multimodal, structured, dialogue and tool paths:

```python
@asynccontextmanager
async def _provider_slot(
    self,
    *,
    caller: str,
    bypass_background: bool = False,
) -> AsyncIterator[None]:
    gate = cast("LLMConcurrencyGate", self.concurrency_gate)
    async with gate.slot(caller=caller, bypass_background=bypass_background):
        yield
```

`bypass_semaphore=True` passes `bypass_background=True` but still obtains `_total`. Remove the old branch that directly called `_do_llm_call()`. Remove internal `bypass_semaphore=True` from `complete_socratic_dialogue()`; its caller tag is interactive.

- [ ] **Step 6: Inject one gate at API/OpenClaw/Soul/dialogue roots**

In each long-running root:

```python
llm_concurrency = _llm_concurrency_from_config(config)
llm_gate = LLMConcurrencyGate(total_concurrency=llm_concurrency)
llm_service = LLMService(
    registry=registry,
    memory=memory,
    usage_recorder=usage_recorder,
    module_overrides=module_overrides,
    concurrency=llm_concurrency,
    concurrency_gate=llm_gate,
)
soul_engine = SoulEngine(
    llm=registry,
    memory=memory,
    usage_recorder=usage_recorder,
    module_overrides=module_overrides,
    llm_concurrency=llm_concurrency,
    llm_concurrency_gate=llm_gate,
)
runtime_controller.llm_concurrency_gate = llm_gate
```

`SocraticDialogue._build_service()` first returns `soul_engine._llm_service`; only isolated legacy doubles without that attribute build a compatibility service using `soul_engine._llm_concurrency_gate`.

- [ ] **Step 7: Share one CLI composition gate**

Add `_build_llm_concurrency_gate()` backed by `_RUNTIME_COMPONENTS["llm_concurrency_gate"]`. Use it in `_build_soul_engine`, `_build_recommendation_engine`, `_build_discovery_engine`, `keyword_inspiration_dry_run`, `_run_xhs_discovery`, `profile_consolidate`, and every other direct `LLMService(...)` construction in `cli.py`. `_RUNTIME_COMPONENTS` is cleared by existing CLI test fixtures and each real Typer invocation is one process/command, so a gate is never shared across independent CLI processes.

- [ ] **Step 8: Change the default and all user-facing fallback surfaces to 4**

Change only total LLM concurrency defaults/fallbacks/placeholders:

- `src/openbiliclaw/config.py` and `src/openbiliclaw/llm/service.py`: 4;
- `LLMConfigOut.concurrency`: 4;
- `config.example.toml`: `concurrency = 4` with comment “总并发 4；后台派生为 3”；
- extension `cfgLlmConcurrency`: placeholder/fallback 4;
- desktop `llmConcurrency`: placeholder/fallback 4.

Keep `candidate_eval_concurrency=3` everywhere. Add a config test proving explicit `concurrency=3` loads as 3 and derives background 2.

At all three discovery composition roots, pass `llm_evaluation_concurrency=background_llm_concurrency(llm_concurrency)` to `DiscoveryConcurrencyController`. Under the default this is 3; under explicit total 3 it is 2. Preserve unrelated Bilibili request/search budgets. Add API/OpenClaw/CLI construction assertions for both derivations.

- [ ] **Step 9: Expose base gate status**

Merge `gate.status_payload()` into `ContinuousRefreshController.get_runtime_status()` and add these response fields:

```python
llm_total_concurrency: int = 0
llm_background_concurrency: int = 0
llm_total_active: int = 0
llm_total_waiting: int = 0
llm_background_active: int = 0
llm_background_waiting: int = 0
```

- [ ] **Step 10: Update mandatory documentation in this commit**

Document the shared object ownership, total/background semantics, caller classification, legacy bypass behavior, default 4 and explicit-value compatibility. Record OpenClaw ownership in integrations docs and the new settings fallback in extension docs. Update all four architecture diagrams with:

```text
interactive ─────────────────────────┐
                                    ├─ runtime total gate (default 4) ─ provider
background ─ background gate (3) ───┘
```

Keep the Chinese and English README diagrams structurally identical. Do not add an internal-only README release highlight.

- [ ] **Step 11: Run backend/frontend tests and commit**

Run:

```bash
pytest tests/test_llm_concurrency.py tests/test_llm_service.py tests/test_soul_dialogue.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py tests/test_config.py tests/test_desktop_web_multimodal_settings.py -q
cd extension && npm test && npm run typecheck && npm run build
```

Expected: PASS; API/OpenClaw/CLI compositions share by object identity, total active never exceeds configured total, and default settings display 4 while candidate concurrency remains 3.

```bash
git add src/openbiliclaw/llm/concurrency.py src/openbiliclaw/llm/service.py src/openbiliclaw/soul/engine.py src/openbiliclaw/soul/dialogue.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/api/app.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/cli.py src/openbiliclaw/config.py src/openbiliclaw/api/models.py config.example.toml extension/popup/popup.html extension/popup/popup.js extension/tests/popup-settings.test.ts src/openbiliclaw/web/desktop/index.html src/openbiliclaw/web/desktop/assets/js/app.js tests/test_llm_concurrency.py tests/test_llm_service.py tests/test_soul_dialogue.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py tests/test_config.py tests/test_desktop_web_multimodal_settings.py docs/modules/llm.md docs/modules/config.md docs/modules/soul.md docs/modules/runtime.md docs/modules/integrations.md docs/modules/extension.md docs/changelog.md docs/architecture.md docs/spec.md README.md README_EN.md
git commit -m "feat: share runtime-wide llm concurrency gate"
```

---

### Task 5: Reserve Two Background Slots for Refill Traffic

**Files:**
- Modify: `src/openbiliclaw/llm/concurrency.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/runtime/candidate_eval.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `tests/test_llm_concurrency.py`
- Modify: `tests/test_candidate_eval_coordinator.py`
- Modify: `tests/test_api_app.py`, `tests/test_openclaw_adapter.py`
- Modify: `docs/modules/llm.md`, `docs/modules/runtime.md`, `docs/modules/integrations.md`, `docs/changelog.md`
- Modify: `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`

**Interfaces:**
- Produces `InventoryPriorityState` values `healthy`, `refill`, `empty`.
- Produces `LLMConcurrencyGate.update_inventory(*, available: int, target: int) -> None`.
- Replaces the basic background semaphore with cancellation-safe `RefillAdmissionSemaphore(capacity: int)`.
- Extends status with `llm_refill_active`, `llm_refill_waiting`, `llm_maintenance_active`, `llm_maintenance_waiting`, `llm_refill_priority_active`, and `inventory_priority_state`.
- Keeps Task 4's public `slot()` signature and true total bound unchanged.

- [ ] **Step 1: Write the refill guarantee tests**

```python
async def test_two_refill_waiters_take_next_two_background_releases() -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    gate.update_inventory(available=20, target=20)
    release = [asyncio.Event() for _ in range(3)]
    maintenance = [
        asyncio.create_task(_hold(gate, "soul.preference", release[index]))
        for index in range(3)
    ]
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_active"] == 3)

    gate.update_inventory(available=5, target=20)
    refill_entered = [asyncio.Event(), asyncio.Event()]
    refill = [
        asyncio.create_task(
            _enter_and_hold(gate, "recommendation.write_expression", refill_entered[index])
        )
        for index in range(2)
    ]
    await _wait_until(lambda: gate.status_payload()["llm_refill_waiting"] == 2)

    release[0].set()
    release[1].set()
    await asyncio.gather(*(event.wait() for event in refill_entered))
    assert gate.status_payload()["llm_refill_active"] == 2
    assert gate.status_payload()["llm_maintenance_active"] == 1
```

Add tests for:

- three refill waiters using all three background slots when maintenance releases;
- one runnable refill plus maintenance borrowing the other slots when no refill remains queued;
- a new refill receiving the next release ahead of queued maintenance;
- `available=0` parking new maintenance while three refill calls enter;
- `healthy` state un-parking maintenance;
- existing maintenance not being cancelled/preempted during state transition;
- cancellation at every queue position returning all counters/permits to zero.

- [ ] **Step 2: Test dynamic supply classification**

When healthy, `discovery.keyword_planner`, `discovery.search.queries`, `sources.xhs.keyword_gen`, and `runtime.bilibili_extension_search.queries` classify as maintenance. After `update_inventory(available=1, target=20)`, the same caller tags classify as `REFILL_SUPPLY`. Expression/evaluation tags always retain their refill classes; Soul maintenance never becomes refill merely because inventory is low.

- [ ] **Step 3: Test expression > evaluation > supply ordering**

Hold all background slots, enqueue one waiter for each refill class in reverse order, release one slot at a time, and assert entry order is:

```python
assert entry_order == [
    "recommendation.write_expression",
    "discovery.evaluate_batch",
    "discovery.keyword_planner",
]
```

- [ ] **Step 4: Run the new tests and verify the basic gate is insufficient**

Run:

```bash
pytest tests/test_llm_concurrency.py -q
```

Expected: FAIL because Task 4's background semaphore has no inventory state or class-aware admission.

- [ ] **Step 5: Implement the refill-aware admission semaphore**

Store waiters as `(traffic_priority, fifo_sequence, traffic_class, future)`. The admission predicate is exactly:

```python
def _can_admit(self, traffic: LLMTrafficClass) -> bool:
    if self._active_total >= self.capacity:
        return False
    if traffic is not LLMTrafficClass.MAINTENANCE:
        return True
    if self._inventory_state is InventoryPriorityState.EMPTY:
        return False
    if self._waiting_refill > 0 and self._active_maintenance >= 1:
        return False
    return True
```

On every release, cancellation, enqueue and inventory update, repeatedly grant the highest-priority admissible waiter until capacity is full or no waiter is admissible. Do not reserve idle permits when no refill waiter exists; this is what makes the gate work-conserving.

Use total-gate priorities `interactive=0`, `refill.expression=1`, `refill.evaluation=2`, `refill.supply=3`, `maintenance=4`; FIFO order is preserved within each class.

- [ ] **Step 6: Keep total and background acquisition ordering safe**

Background calls first acquire `RefillAdmissionSemaphore`, then acquire the total priority semaphore. Interactive/bypass-background calls acquire only total. Release in reverse order. This guarantees background holders can never consume the fourth default slot while waiting on total, and interactive work cannot be trapped behind a background admission permit.

- [ ] **Step 7: Synchronize inventory state from durable snapshots**

Add a small controller helper:

```python
def _update_llm_inventory_state(self, available: int) -> None:
    gate = self.llm_concurrency_gate
    update = getattr(gate, "update_inventory", None)
    if callable(update):
        update(available=max(0, int(available)), target=self.pool_target_count)
```

Call it after startup maintenance, in `_pool_readiness_counts()`, after recommendation pool status changes, and from every API/OpenClaw candidate snapshot closure. Initialize the gate once during composition using the current canonical database count before any task can call a provider.

- [ ] **Step 8: Expose full diagnostics and update diagrams**

Add the six class/state fields to `RuntimeStatusResponse` and exact response fixtures. Extend all four diagrams:

```text
background (3)
├─ refill waiters: expression > evaluation > supply (guarantee 2, borrow 3)
└─ maintenance: ≤1 while refill waits; parked while inventory empty
```

Document that guarantee applies to new admissions only and never cancels an in-provider maintenance request. Update integrations docs with the API/OpenClaw inventory-state wiring.

- [ ] **Step 9: Run a deterministic 50-round permit soak**

Run:

```bash
for i in {1..50}; do
  pytest tests/test_llm_concurrency.py -q || exit 1
done
```

Expected: 50/50 PASS with no leaked permit, deadlock, wrong entry order or over-release.

- [ ] **Step 10: Run runtime tests and commit**

Run:

```bash
pytest tests/test_llm_concurrency.py tests/test_candidate_eval_coordinator.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_refresh_runtime.py -q
```

Expected: PASS; low inventory gives queued refill the next two releases, empty inventory parks new maintenance, and runtime state reflects durable counts.

```bash
git add src/openbiliclaw/llm/concurrency.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/runtime/candidate_eval.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/api/models.py tests/test_llm_concurrency.py tests/test_candidate_eval_coordinator.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_refresh_runtime.py docs/modules/llm.md docs/modules/runtime.md docs/modules/integrations.md docs/changelog.md docs/architecture.md docs/spec.md README.md README_EN.md
git commit -m "feat: reserve background capacity for refill traffic"
```

---

### Task 6: Make Candidate Evaluation Use Durable Projected Inventory

**Files:**
- Modify: `src/openbiliclaw/storage/database.py:3145-3255`
- Modify: `src/openbiliclaw/discovery/candidate_pipeline.py:360-790`
- Modify: `src/openbiliclaw/runtime/candidate_eval.py`
- Modify: `src/openbiliclaw/api/runtime_context.py:850-915`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py:245-315`
- Modify: `tests/test_discovery_candidate_pipeline.py`
- Modify: `tests/test_candidate_eval_coordinator.py`
- Modify: `tests/test_refresh_runtime.py`
- Modify: `docs/modules/storage.md`, `docs/modules/discovery.md`, `docs/modules/runtime.md`, `docs/modules/integrations.md`, `docs/changelog.md`

**Interfaces:**
- Extends `count_pool_readiness()` with `admitted_pending_copy` and keeps `evaluated_pending` as the durable candidate-table count.
- Replaces `CandidateEvalSnapshot.committed_pending` with `admitted_pending_copy` and renames `evaluated` to `evaluated_pending_admission`.
- Produces `DiscoveryCandidatePipeline.complete_claim(outcome, *, admission_limit: int | None = None) -> dict[str, int]`.
- Extends `_admit_until_full(..., limit: int | None)` so evaluated output beyond headroom remains durable `evaluated` rather than being rejected or over-admitted.
- Produces `CandidateEvalCoordinator.on_admitted: Callable[[int], None] | None` for Task 7 notification; it never awaits the callback.

- [ ] **Step 1: Write the exact readiness-count tests**

Seed one row in each state:

- canonical available;
- admitted, classified, linkable, missing copy;
- admitted but missing style (not copy-pending);
- candidate `evaluated`;
- candidate `pending_eval`;
- candidate `evaluating`.

Assert:

```python
readiness = db.count_pool_readiness()
assert readiness["available"] == 1
assert readiness["admitted_pending_copy"] == 1
assert readiness["evaluated_pending"] == 1
assert readiness["pending_eval"] == 2
```

The `admitted_pending_copy` query must apply admission, viewed, linkability, recommendation, self-XHS and delight guards; it requires style/topic and requires either expression or topic label to be missing.

- [ ] **Step 2: Write projected-inventory and headroom tests**

```python
def test_projected_inventory_excludes_unscored_raw() -> None:
    snapshot = CandidateEvalSnapshot(
        available=2,
        target=10,
        pending_eval=500,
        evaluating=60,
        evaluated_pending_admission=3,
        admitted_pending_copy=4,
    )
    assert CandidateEvalCoordinator._projected_inventory(snapshot) == 9
```

Start three 30-item workers with target 10. Finish the first worker with 30 passing results. Assert its commit admits exactly 10, leaves 20 rows `evaluated`, and neither of the other completions admits beyond target. A stale token completion still updates zero rows.

- [ ] **Step 3: Prove admission runs before the projected stop**

Start with `available=0`, `admitted_pending_copy=0`, and `evaluated_pending_admission=10`. The coordinator must call `admit_evaluated(limit=10)` even though projected already equals target, then notify copy and claim no new raw rows.

- [ ] **Step 4: Prove a fast worker refills in under one second**

Keep two worker futures blocked, finish the third, and record the time from `complete_claim()` returning to the next `claim_batch()`. Assert `< 1.0` second with `safety_wake_seconds=60.0`; this proves completion notification, not the safety tick, drives refill.

- [ ] **Step 5: Run the focused tests and verify current over-admission**

Run:

```bash
pytest tests/test_discovery_candidate_pipeline.py tests/test_candidate_eval_coordinator.py tests/test_storage.py -q -k 'projected or admission or readiness or fast_worker'
```

Expected: FAIL because `committed_pending` is imprecise, `complete_claim()` admits until visible availability fills, and no per-commit headroom exists.

- [ ] **Step 6: Implement precise pending-copy readiness**

Add a connection-aware `_load_admitted_pending_copy_rows_on()` that reuses canonical guard builders but selects rows with style/topic present and copy incomplete. Use it for `admitted_pending_copy`, expression coordinator pending count, and expression candidate loading. Do not derive the value as broad `pending - discovery_backlog`.

- [ ] **Step 7: Limit serial commit admission**

Before committing each completed worker:

```python
snapshot = self._snapshot()
admission_headroom = max(
    0,
    snapshot.target - snapshot.available - snapshot.admitted_pending_copy,
)
result = await self.pipeline.complete_claim(
    outcome,
    admission_limit=admission_headroom,
)
```

`complete_claim()` persists every token-owned evaluation first. `_admit_until_full()` then caches at most `admission_limit`; passing rows beyond it stay `status='evaluated'`. The coordinator's loop always invokes `_admit_evaluated()` before deciding whether projected inventory has reached target.

- [ ] **Step 8: Notify without blocking the evaluator lane**

When any commit/admit caches rows, call `on_admitted(cached_count)` synchronously. The callback contract returns `None`; runtime passes `lambda count: expression_copy_coordinator.notify(f"candidate_admitted:{count}")`. Tests use a callback that only records the count and assert evaluator completion does not wait on copy work.

- [ ] **Step 9: Wire exact snapshots in both composition roots**

Build snapshots directly from `count_pool_readiness()` and candidate status counts:

```python
return CandidateEvalSnapshot(
    available=int(readiness["available"]),
    target=pool_target,
    pending_eval=int(status_counts.get("pending_eval", 0)),
    evaluating=int(status_counts.get("evaluating", 0)),
    evaluated_pending_admission=int(status_counts.get("evaluated", 0)),
    admitted_pending_copy=int(readiness.get("admitted_pending_copy", 0)),
)
```

Update gate inventory from the same `available` value. Remove the broad `readiness["pending"] - discovery_backlog` approximation.

- [ ] **Step 10: Update docs, run tests and commit**

Document the new readiness key in storage docs, the three-term projected formula, serial admission headroom, 30×3 claim cap and 60-second safety-only role. Record the exact API/OpenClaw snapshot mapping in integrations docs.

Run:

```bash
pytest tests/test_discovery_candidate_pipeline.py tests/test_candidate_eval_coordinator.py tests/test_refresh_runtime.py tests/test_api_app.py tests/test_openclaw_adapter.py -q
```

Expected: PASS; raw pending never inflates projected stock and a completed worker refills immediately without over-admission.

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/discovery/candidate_pipeline.py src/openbiliclaw/runtime/candidate_eval.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py tests/test_discovery_candidate_pipeline.py tests/test_candidate_eval_coordinator.py tests/test_refresh_runtime.py tests/test_api_app.py tests/test_openclaw_adapter.py docs/modules/storage.md docs/modules/discovery.md docs/modules/runtime.md docs/modules/integrations.md docs/changelog.md
git commit -m "perf: schedule evaluation from projected inventory"
```

---

### Task 7: Add a Single-Flight 8/3/30/2 Expression Copy Coordinator

**Files:**
- Create: `src/openbiliclaw/runtime/expression_copy.py`
- Create: `tests/test_expression_copy_coordinator.py`
- Modify: `src/openbiliclaw/recommendation/engine.py:175-215,816-1035,2660-2705`
- Modify: `src/openbiliclaw/runtime/candidate_eval.py`
- Modify: `src/openbiliclaw/runtime/refresh.py:280-305,990-1065,1080-1265,1840-1910,2140-2220`
- Modify: `src/openbiliclaw/api/runtime_context.py:810-930,1050-1175`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py:235-320`
- Modify: `src/openbiliclaw/api/models.py:180-220`
- Modify: `tests/test_candidate_eval_coordinator.py`
- Modify: `tests/test_recommendation_engine.py`
- Modify: `tests/test_refresh_runtime.py`, `tests/test_api_app.py`, `tests/test_openclaw_adapter.py`
- Modify: `docs/modules/discovery.md`, `docs/modules/recommendation.md`, `docs/modules/runtime.md`, `docs/modules/integrations.md`, `docs/changelog.md`

**Interfaces:**
- Produces `ExpressionCopyCoordinator(*, pending_count_provider, drain_callback, min_items=8, max_wait_seconds=3.0, drain_limit=60, zero_progress_backoff_seconds=15.0, safety_wake_seconds=60.0, time_fn=time.monotonic, wait_fn=asyncio.sleep)`.
- Produces `notify(reason: str) -> None`, `run_forever() -> None`, `stop() -> None`, and `status_payload() -> dict[str, object]`.
- Produces `RecommendationEngine.drain_pending_expression_copy(*, profile: SoulProfile, limit: int = 60) -> int`, the runtime's copy-only entry point.
- Produces `RecommendationEngine.set_copy_pending_callback(callback: Callable[[str], None] | None) -> None`.
- Adds `ContinuousRefreshController.expression_copy_coordinator: Any | None = None`.
- Adds API status `expression_pending_count`, `expression_batch_state`, `expression_batch_deadline`, `expression_last_completed`, `expression_last_error`.
- Guarantees one collection deadline, one running task and one durable rerun check per runtime generation.

- [ ] **Step 1: Write deterministic threshold/deadline tests**

```python
async def test_eight_pending_starts_immediately() -> None:
    pending = _Pending(8)
    started = asyncio.Event()
    coordinator = _coordinator(pending, lambda limit: started.set() or min(limit, pending.value))
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("candidate_admitted")
    await asyncio.wait_for(started.wait(), timeout=0.2)
    await coordinator.stop()
    await task


async def test_tail_batch_uses_one_three_second_deadline() -> None:
    clock = _FakeClock()
    pending = _Pending(1)
    calls: list[tuple[float, int]] = []
    coordinator = _coordinator(
        pending,
        lambda limit: calls.append((clock.now, limit)) or pending.value,
        time_fn=clock,
        wait_fn=clock.wait,
    )
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("one")
    await clock.until_waiting()
    first_deadline = coordinator.status_payload()["expression_batch_deadline"]
    pending.value = 7
    coordinator.notify("seven")
    assert coordinator.status_payload()["expression_batch_deadline"] == first_deadline
    await clock.advance(3.0)
    await _wait_until(lambda: len(calls) == 1)
    assert calls[0][0] == 3.0
    await coordinator.stop()
    await task
```

Use an injected async `wait_fn(delay)` in tests; production defaults to `asyncio.sleep`. This avoids real three-second sleeps and makes deadline assertions exact.

- [ ] **Step 2: Add coalescing, rerun, backoff and stop tests**

Cover all of these:

- pending rises from 1 to 8 and starts before the original deadline;
- 20 notifications during one running drain produce exactly one subsequent durable recheck;
- pending 75 drains as limits 60 then 15, never as parallel coordinator tasks;
- a zero-result drain with pending still positive sets deadline `now + 15` and does not busy-loop;
- successful progress with remaining 1–7 starts one new three-second tail window;
- `stop()` cancels a collector, a queued gate waiter and a running callback without leaking a task;
- a stale notification from the old hot-reload generation cannot wake the new coordinator.

- [ ] **Step 3: Run the new tests and verify no coordinator exists**

Run:

```bash
pytest tests/test_expression_copy_coordinator.py -q
```

Expected: FAIL during collection because `runtime.expression_copy` does not exist.

- [ ] **Step 4: Implement the state machine**

Use states `idle | collecting | running | backoff | paused | stopping`. `notify()` records `_first_pending_at` only when it is zero, increments a generation counter, sets a wake event, and never executes the callback inline.

The scheduling decision is:

```python
pending = max(0, int(self.pending_count_provider()))
if pending <= 0:
    self._first_pending_at = 0.0
    self._deadline = 0.0
    self.state = "idle"
elif self._retry_not_before > now:
    self._deadline = self._retry_not_before
    self.state = "backoff"
elif pending >= self.min_items:
    self._deadline = now
    self.state = "running"
else:
    if self._first_pending_at <= 0.0:
        self._first_pending_at = now
    self._deadline = self._first_pending_at + self.max_wait_seconds
    self.state = "collecting"
```

When due, create one `_copy_task` calling `drain_callback(min(drain_limit, pending))`. The main loop waits on wake, copy completion, deadline or safety wake. On completion it always reloads durable pending count; notifications only accelerate that recheck and never determine remaining work.

- [ ] **Step 5: Add the copy-only recommendation entry**

```python
async def drain_pending_expression_copy(
    self,
    *,
    profile: SoulProfile,
    limit: int = 60,
) -> int:
    return await self._drain_expression_copy(
        profile=profile,
        limit=max(0, min(60, int(limit))),
        batch_size=_DEFAULT_EXPRESSION_BATCH_SIZE,
    )
```

Keep `_DEFAULT_EXPRESSION_BATCH_SIZE=30` and `_DEFAULT_EXPRESSION_BATCH_CONCURRENCY=2`. Add a 75-row test that records provider request item counts and active calls; assert request sizes `[30, 30]` in the first drain, maximum active 2, and 15 in the next drain.

- [ ] **Step 6: Route every production copy trigger to `notify()`**

In runtime production paths:

- candidate `on_admitted` calls `expression_copy_coordinator.notify("candidate_admitted:<count>")`;
- `_loop_pool_precompute` becomes a safety notifier and performs no provider call when a coordinator is present;
- `_drain_discovery_candidates_and_precompute` notifies after caching instead of awaiting copy;
- `_run_refresh_plan` removes `precompute_tasks` and emits one notify after durable admission;
- hot reload notifies the newly created coordinator after startup maintenance;
- post-classification calls the registered engine callback instead of directly draining copy.

`_loop_pool_precompute` first calls a controller wrapper around public `RecommendationEngine.classify_pool_backlog(profile=profile, limit=60)`, then notifies copy; the engine's registered callback also notifies immediately when classification writes rows. `prepare_delight_candidates()` calls public `precompute_delight_scores(profile=profile, limit=30)` and does not invoke `precompute_pool_copy`. Hot reload schedules those classification and delight calls independently, then notifies the new copy coordinator. Keep `precompute_pool_copy()` for CLI/isolated compatibility, but no long-running runtime path may call it for copy.

- [ ] **Step 7: Wire one coordinator into API and OpenClaw**

Use the same construction in both roots:

```python
async def _drain_expression_copy(limit: int) -> int:
    profile = await soul_engine.get_profile()
    if profile is None:
        return 0
    before = int(candidate_snapshot().available)
    completed = await recommendation_engine.drain_pending_expression_copy(
        profile=profile,
        limit=limit,
    )
    await runtime_controller._publish_precompute_replenishment_if_needed(  # noqa: SLF001
        before_pool_count=before,
    )
    return int(completed)


expression_coordinator = ExpressionCopyCoordinator(
    pending_count_provider=lambda: int(
        database.count_pool_readiness(
            xhs_self_nickname=runtime_controller._xhs_self_nickname()  # noqa: SLF001
        ).get("admitted_pending_copy", 0)
    ),
    drain_callback=_drain_expression_copy,
    safety_wake_seconds=float(config.scheduler.refresh_check_interval_seconds),
)
```

Assign it to the controller, register `recommendation_engine.set_copy_pending_callback(expression_coordinator.notify)`, and pass `lambda count: expression_coordinator.notify(f"candidate_admitted:{count}")` to candidate coordinator `on_admitted`.

- [ ] **Step 8: Start/stop it with the runtime generation**

`ContinuousRefreshController.run_forever()` creates the expression coordinator task alongside candidate evaluation. In `finally`, call both coordinators' `stop()` before cancelling remaining loops. Runtime rebuild already stops old tasks before replacing services; add an identity test proving old and new coordinator/gate objects differ and old state is `stopping` before new provider work enters.

- [ ] **Step 9: Expose status and update documentation**

Merge `expression_copy_coordinator.status_payload()` into runtime status and update Pydantic fixtures. Document threshold 8, fixed tail deadline 3 seconds, drain limit 60, request batch 30, fan-out 2, safety wake and single-flight ownership with their 2026-07-12 production-log calibration provenance. Update discovery docs for non-blocking admission notification and integrations docs for one coordinator per API/OpenClaw generation.

- [ ] **Step 10: Run focused tests and commit**

Run:

```bash
pytest tests/test_expression_copy_coordinator.py tests/test_candidate_eval_coordinator.py tests/test_recommendation_engine.py tests/test_refresh_runtime.py tests/test_api_app.py tests/test_openclaw_adapter.py -q
```

Expected: PASS; copy never waits for the 60-second loop during normal work, and evaluator refill continues while expression is collecting/running.

```bash
git add src/openbiliclaw/runtime/expression_copy.py src/openbiliclaw/recommendation/engine.py src/openbiliclaw/runtime/candidate_eval.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/api/models.py tests/test_expression_copy_coordinator.py tests/test_candidate_eval_coordinator.py tests/test_recommendation_engine.py tests/test_refresh_runtime.py tests/test_api_app.py tests/test_openclaw_adapter.py docs/modules/discovery.md docs/modules/recommendation.md docs/modules/runtime.md docs/modules/integrations.md docs/changelog.md
git commit -m "perf: microbatch expression copy continuously"
```

---

### Task 8: Stop Transient Failures From Exploding Into Recursive Requests

**Files:**
- Modify: `src/openbiliclaw/llm/base.py:35-145`
- Modify: `src/openbiliclaw/discovery/engine.py:1395-1515,1680-1895`
- Modify: `src/openbiliclaw/discovery/candidate_pipeline.py:390-500,680-770`
- Modify: `src/openbiliclaw/recommendation/engine.py:1370-1625`
- Modify: `src/openbiliclaw/runtime/expression_copy.py`
- Modify: `src/openbiliclaw/runtime/candidate_eval.py`
- Modify: `tests/test_llm_service.py`
- Modify: `tests/test_discovery_engine.py`
- Modify: `tests/test_discovery_candidate_pipeline.py`
- Modify: `tests/test_recommendation_engine.py`
- Modify: `tests/test_expression_copy_coordinator.py`
- Modify: `tests/test_candidate_eval_coordinator.py`
- Modify: `docs/modules/llm.md`, `docs/modules/discovery.md`, `docs/modules/recommendation.md`, `docs/modules/runtime.md`, `docs/changelog.md`

**Interfaces:**
- Extends `classify_llm_failure_kind()` with `connection` and `server_error`.
- Produces `ExpressionBatchMalformed(missing_items: tuple[DiscoveredContent, ...], completed: int)`.
- Produces `ExpressionCopyTransientError(kind: str, completed: int, retry_after: float)`.
- Changes `_precompute_batch_with_split_retry(..., max_split_depth: int = 3, max_extra_requests: int = 6) -> int` to split only malformed/missing successful responses.
- Produces `ContentDiscoveryEngine._evaluate_batch_once(...) -> list[float | None]` and a bounded `_evaluate_batch(...) -> list[float]` wrapper; unresolved entries carry `relevance_reason='evaluation_response_missing'`.
- Removes recursive single-expression fallback from batch copy; one still-malformed item remains copy-pending for a later bounded round.
- Preserves successfully written members of a partial batch and retries only missing members.

- [ ] **Step 1: Add failure classification tests**

```python
@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (ConnectionError("connection reset by peer"), "connection"),
        (OSError("network is unreachable"), "connection"),
        (TimeoutError("request timed out"), "timeout"),
        (LLMProviderExecutionError("upstream returned HTTP 502"), "server_error"),
        (LLMProviderExecutionError("upstream returned HTTP 503"), "server_error"),
        (LLMRateLimitError("429 too many requests"), "rate_limited"),
    ],
)
def test_transient_failure_classification(error: BaseException, kind: str) -> None:
    assert classify_llm_failure_kind(error) == kind
```

Keep auth/no-provider/invalid-response precedence tests so a wrapper message cannot accidentally reclassify a credential failure as connection transient.

- [ ] **Step 2: Prove transient errors make exactly one provider call**

Parameterize 429, timeout, connection and 503 over a 30-item expression batch. For each, call `_precompute_batch_with_split_retry()` and assert:

```python
assert llm.calls == 1
assert db.count_pool_readiness()["admitted_pending_copy"] == 30
```

The call must raise `ExpressionCopyTransientError`; it must not log `split retry` and must not call `recommendation.expression` per item.

Repeat the same four failures through `ContentDiscoveryEngine.evaluate_content_batch()` and assert one `discovery.evaluate_batch` provider call, zero `discovery.evaluate_single` calls, and the outer candidate claim returns to `pending_eval` with no attempt increment.

- [ ] **Step 3: Prove malformed success retries only missing members**

Return valid keyed results for A/B and omit C/D in the first successful response. Return C/D in the second response. Assert request item IDs are `[(A, B, C, D), (C, D)]`, four rows receive copy, and A/B are not regenerated.

Add a full malformed 8-item response that becomes valid after splitting; assert total calls never exceed 7 (initial + six extra). Add a permanently malformed singleton; assert it remains pending, no `recommendation.expression` call occurs, and the coordinator waits at least 15 seconds before another round.

For candidate evaluation, return valid keyed scores for A/B and omit C/D. Assert only C/D appear in the second batch evaluation request. If D remains malformed after the bounded budget, A/B/C are token-conditionally persisted/admitted while D is reset from `evaluating` to `pending_eval` with `eval_error='evaluation_response_missing'`; D is not persisted as score zero or rejected low-score.

- [ ] **Step 4: Run tests and demonstrate the current recursion**

Run:

```bash
pytest tests/test_recommendation_engine.py tests/test_expression_copy_coordinator.py tests/test_discovery_engine.py tests/test_discovery_candidate_pipeline.py tests/test_llm_service.py -q -k 'transient or malformed or split or connection or server_error'
```

Expected: FAIL because arbitrary exceptions currently recurse 30→15→…→1 and partial successful payloads do not target only missing members.

- [ ] **Step 5: Extend the shared classifier**

Recognize exception types (`ConnectionError`, connection-related `OSError`) before message markers. Add conservative connection markers (`connection reset`, `connection refused`, `network is unreachable`, `name resolution`, `temporary failure in name resolution`) and server markers matching HTTP/status 500, 502, 503, 504. Do not classify arbitrary `ValueError` or JSON errors as provider transient.

- [ ] **Step 6: Separate provider failure from response-shape failure**

In `_precompute_batch()`, keep the provider call in its own `try` block. If `classify_llm_failure_kind(exc)` is one of `rate_limited`, `timeout`, `connection`, `server_error`, raise `ExpressionCopyTransientError`; if auth/no-provider, propagate for coordinator pause. Parsing/matching happens only after a successful response.

Write all valid keyed unique rows first, collect invalid/missing/duplicated items, then raise:

```python
raise ExpressionBatchMalformed(
    missing_items=tuple(missing_items),
    completed=completed,
)
```

No-ID multi-item output treats the whole batch as missing; a single keyed/positional item remains safe.

Apply the same separation in discovery evaluation. Rename the current provider/parsing body to `_evaluate_batch_once()` and make it return `float | None` per input item: valid keyed/positional members mutate/cache normally, missing or invalid members return `None`. The `_evaluate_batch()` wrapper retries only `None` members with depth 3 / six-extra-request budget. Provider transient/auth/no-provider exceptions propagate without entering the split wrapper.

- [ ] **Step 7: Bound missing-subset recursion**

Use one mutable budget object per top-level provider batch. Catch only `ExpressionBatchMalformed`. Add its `completed` count, split `missing_items` in half, and consume one budget unit per extra provider request. Stop when depth reaches 3, budget reaches zero, or one item remains malformed; return progress already written and leave the rest pending.

Transient/auth/no-provider/cancellation exceptions bypass this branch unchanged. This is the assertion that prevents the user A request storm.

After bounded discovery retries, set unresolved items' `relevance_reason` to `evaluation_response_missing` and return score `0.0` only as a transport placeholder. `DiscoveryCandidatePipeline._persist_evaluations()` must exclude those rows from low-score persistence, persist successful siblings, and call `reset_claimed_discovery_candidates_to_pending(..., increment_attempts=False, reason='evaluation_response_missing')` for the unresolved IDs while the claim token still matches.

- [ ] **Step 8: Apply coordinator retry semantics**

Expression coordinator uses provider `retry_after` when present, otherwise 15/30/60/120/300 seconds for transient streaks. Auth/no-provider enters `paused` until a `config_*`, `manual_*`, or `startup` notification. A successful call with zero writes uses 15 seconds. Candidate evaluator uses the same expanded transient classification and releases claims without incrementing quality-failure attempts.

- [ ] **Step 9: Update docs and run the error suite**

Document the provider-vs-payload distinction, bounded missing-subset retry for both evaluation and copy, token-safe reset of unresolved evaluations, retained pending state, coordinator pause/backoff states, and backoff ladder.

Run:

```bash
pytest tests/test_llm_service.py tests/test_recommendation_engine.py tests/test_expression_copy_coordinator.py tests/test_candidate_eval_coordinator.py tests/test_discovery_engine.py tests/test_discovery_candidate_pipeline.py -q
```

Expected: PASS; all four transient classes issue one request per round, malformed retry is bounded, and no failed copy returns to candidate scoring.

- [ ] **Step 10: Verify Soul cost behavior is untouched and commit**

Run:

```bash
git diff HEAD -- src/openbiliclaw/soul src/openbiliclaw/llm/prompts.py src/openbiliclaw/llm/usage_recorder.py
```

Expected: no changes in Task 8. The Task 4 constructor-only Soul gate injection is already committed and contains no prompt/token/cost change.

```bash
git add src/openbiliclaw/llm/base.py src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/candidate_pipeline.py src/openbiliclaw/recommendation/engine.py src/openbiliclaw/runtime/expression_copy.py src/openbiliclaw/runtime/candidate_eval.py tests/test_llm_service.py tests/test_discovery_engine.py tests/test_discovery_candidate_pipeline.py tests/test_recommendation_engine.py tests/test_expression_copy_coordinator.py tests/test_candidate_eval_coordinator.py docs/modules/llm.md docs/modules/discovery.md docs/modules/recommendation.md docs/modules/runtime.md docs/changelog.md
git commit -m "fix: bound expression retries on provider failures"
```

---

### Task 9: Verify Both User Shapes End to End, Including Real Requests

**Files:**
- Create: `tests/test_refill_end_to_end.py`
- Create: `tests/test_refill_real_provider_integration.py`
- Modify: `pyproject.toml` (integration marker description only if needed)
- Modify: `docs/modules/storage.md`, `docs/modules/llm.md`, `docs/modules/discovery.md`, `docs/modules/recommendation.md`, `docs/modules/runtime.md`, `docs/modules/config.md`, `docs/modules/integrations.md`, `docs/modules/extension.md`
- Modify: `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`
- Modify: `docs/changelog.md`
- Reference: `docs/superpowers/specs/2026-07-12-inventory-safe-continuous-refill-design.md`

**Interfaces:**
- Produces a deterministic temporary-SQLite end-to-end regression with real production coordinators and a controlled provider.
- Produces opt-in `OPENBILICLAW_REFILL_E2E=1` live test using the configured real provider and read-only public Bilibili ranking fetch.
- Records only sanitized counters/timings, never keys, Cookies, prompts, profiles or content bodies.
- Provides final evidence for inventory invariant, real total/background peak, refill latency, copy batch sizes and retry count.

- [ ] **Step 1: Build the deterministic production-component E2E**

Use a temporary `Database`, temporary `MemoryManager`, real `DiscoveryCandidatePipeline`, real `CandidateEvalCoordinator`, real `ExpressionCopyCoordinator`, real `RecommendationEngine`, shared `LLMConcurrencyGate(4)`, and a controlled registry that returns keyed structured JSON.

Run this sequence:

1. seed User A shape (16 ready + 602 raw, ceiling 600);
2. run maintenance and assert available remains at least 16;
3. consume 8 ready rows by inserting/marking recommendations;
4. enqueue 120 candidates while keeping supply available;
5. start three evaluator workers and two-copy fan-out;
6. wait until canonical available returns to the pre-consumption target;
7. run maintenance again;
8. assert invariant, no claim token, total peak ≤4, background peak ≤3, copy request size ≤30, copy fan-out ≤2, and fast-slot refill <1 second.

Repeat with User B's ten ready Zhihu rows plus twelve overflow rows. Assert every `zhihu-*` source is accounted under `zhihu` and maintenance cannot reduce ten to zero.

- [ ] **Step 2: Add sustained-consumption behavior**

For 50 deterministic rounds, consume one available item whenever possible while enqueuing enough raw candidates to keep evaluation runnable. Assert no fixed 60-second wait appears, inventory recovers whenever provider results pass, no duplicate cache admission occurs, and no permit/task/claim leaks after stop.

- [ ] **Step 3: Add the opt-in live test guard**

At module level:

```python
_LIVE = os.getenv("OPENBILICLAW_REFILL_E2E", "") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _LIVE, reason="set OPENBILICLAW_REFILL_E2E=1 for live refill E2E"),
]
```

The test loads provider/model/key through normal `load_config()` and `build_llm_registry()` but creates database and memory only under `tmp_path`. It uses a synthetic `SoulProfile` containing generic software/technology interests, not the user's stored profile.

- [ ] **Step 4: Fetch at most eight public Bilibili rows read-only**

Create `BilibiliAPIClient(cookie="")`, call `get_ranking()`, take at most eight rows, and always close the client. Convert only title/BVID/author/description/public metrics into `DiscoveredContent`; write them solely to temporary SQLite. Do not call view-history, like, favorite, follow, watch-later or authenticated mutation endpoints.

- [ ] **Step 5: Execute real evaluation → commit → copy → maintenance**

Use the configured real provider with the shared gate. The live assertions are:

```python
assert fetched_count > 0
assert evaluated_count > 0
assert copied_count > 0
assert available_before_maintenance > 0
assert available_after_maintenance >= min(available_before_maintenance, target)
assert peak_total <= 4
assert peak_background <= 3
assert max(expression_batch_sizes) <= 30
assert transient_retry_count <= provider_round_count
```

If the model legitimately rejects every public candidate, the test must fail with sanitized score/admission counts; do not weaken thresholds or substitute fake copy while claiming a live pass.

- [ ] **Step 6: Exercise live interactive reservation**

Wrap the real registry for the three background services with a test-only barrier that waits after gate admission and before delegating to the real registry. Start three background calls, wait until gate status reports three active, then issue a small `soul.dialogue` request through a second `LLMService` sharing the gate but using the unwrapped real registry. Assert dialogue enters the fourth total slot, release the barrier, and await all four real responses. This removes provider-speed flakiness while every completion still reaches the configured real provider. Assert `peak_total <= 4` and record timestamps/caller tags only.

- [ ] **Step 7: Run deterministic E2E first**

Run:

```bash
pytest tests/test_refill_end_to_end.py -q
```

Expected: PASS for both user shapes, 50 consumption rounds, concurrency limits, maintenance after refill, and clean shutdown.

- [ ] **Step 8: Run the real environment test**

Run:

```bash
OPENBILICLAW_REFILL_E2E=1 pytest tests/test_refill_real_provider_integration.py -q -s
```

Expected: PASS with one sanitized summary showing fetched/evaluated/copied/available counts, first-available latency, worker refill delay, provider batch sizes, peak total/background/refill concurrency, maintenance before/after and transient retries. A provider/network/auth failure is an explicit failed verification, not a pass or silent skip when the flag is set.

- [ ] **Step 9: Audit every required document**

Update final public APIs and implemented-feature tables in all eight touched module docs. Ensure the four architecture diagrams show:

```text
source registry → raw queue → candidate evaluator (3×30) → serial admission
       → expression coordinator (8 immediate / 3s tail / 30×2)
       → canonical available pool → atomic maintenance/recovery

interactive ───────────────────────────────┐
refill expression/eval/supply ─ background ├─ total gate 4 ─ provider
maintenance ─ refill-aware background 3 ──┘
```

Update config docs/example for default 4 and derived values. Add one current-version changelog section/bullet set covering inventory protection, Zhihu normalization, recovery, shared gate, refill reservation, continuous evaluation and bounded copy retries. README CN/EN diagrams stay synchronized; no internal-only highlight bullet.

- [ ] **Step 10: Scan for stale contracts and placeholders**

Run:

```bash
rg -n "默认.*并发.*3|llm\.concurrency.?=.?3|concurrency = 3" docs README.md README_EN.md config.example.toml
rg -n "trim_topic_group_overflow\(|trim_pool_source_overflow\(|trim_pool_to_target_count\(" src/openbiliclaw/runtime
rg -n "precompute_pool_copy\(" src/openbiliclaw/runtime src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw
rg -n "T[O]DO|T[B]D|PLACEHOLD[E]R|similar t[o]|and so o[n]" docs/superpowers/plans/2026-07-12-inventory-safe-continuous-refill-plan.md
```

Expected:

- total-default matches refer only to explicit legacy examples or candidate concurrency;
- runtime has no separately committed destructive trim composition;
- long-running runtime has no direct copy-producing `precompute_pool_copy()` call;
- plan placeholder scan has no output.

- [ ] **Step 11: Run all static and test gates**

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest -q
cd extension && npm test && npm run typecheck && npm run build
```

Expected: every command exits 0; integration tests remain skipped unless their explicit environment flags are set.

- [ ] **Step 12: Run the final 50-round concurrency/cancellation soak**

Run:

```bash
for i in {1..50}; do
  pytest tests/test_llm_concurrency.py tests/test_candidate_eval_coordinator.py tests/test_expression_copy_coordinator.py -q || exit 1
done
```

Expected: 50/50 PASS without leaked tasks, claims or permits.

- [ ] **Step 13: Commit verification and documentation**

```bash
git add tests/test_refill_end_to_end.py tests/test_refill_real_provider_integration.py pyproject.toml docs/modules/storage.md docs/modules/llm.md docs/modules/discovery.md docs/modules/recommendation.md docs/modules/runtime.md docs/modules/config.md docs/modules/integrations.md docs/modules/extension.md docs/architecture.md docs/spec.md README.md README_EN.md docs/changelog.md
git commit -m "test: verify inventory-safe refill end to end"
```

- [ ] **Step 14: Inspect the final branch**

Run:

```bash
git status --short
git log --oneline --decorate -15
git diff main...HEAD --check
git diff --stat main...HEAD
```

Expected: clean worktree, the ordered correctness/scheduling commits are present, no whitespace errors, and the diff contains no Soul prompt/token/cost work.

---

## Spec Coverage Self-Review

Before execution, verify this mapping remains true after any plan edit:

| Confirmed requirement | Covered by |
|---|---|
| User A/B maintenance cannot erase available inventory | Tasks 2, 9 |
| Zhihu aliases and strategies share one family | Tasks 1, 9 |
| Atomic rollback and cross-table raw ceiling | Task 2 |
| Recover paid suppressed results before LLM | Task 3 |
| True runtime-wide total 4/background 3 | Task 4 |
| Refill guarantee 2, borrow 3, empty parks maintenance | Task 5 |
| Continuous 3×30 evaluation and immediate refill | Task 6 |
| Durable projected formula and serial admission | Task 6 |
| Copy 8 immediate, 1–7/3s, 30×2 | Task 7 |
| Transient errors do not recursively split | Task 8 |
| Malformed success retries only missing subset, bounded | Task 8 |
| Runtime status and mandatory docs | Tasks 2, 4, 5, 7, 9 |
| Temporary-state real provider and read-only Bilibili E2E | Task 9 |
| Soul token/cost work excluded | Global constraints, Tasks 8–9 |

## Type/Ownership Self-Review

- `PoolMaintenanceResult` is immutable; mutable `trimmed_by_source` is constructed fresh and never reused.
- Database transaction helpers accept an explicit `sqlite3.Connection`; no helper invoked inside maintenance calls `_execute_write()`.
- `LLMConcurrencyGate` belongs to one runtime/CLI composition; individual services do not create another when injected.
- `CandidateEvalCoordinator` owns candidate claims; `ExpressionCopyCoordinator` owns copy timing; neither owns provider capacity.
- `LLMConcurrencyGate` owns provider capacity; it never owns SQLite state and only receives integer inventory snapshots.
- Candidate worker tasks perform provider I/O; the single coordinator lane performs token-checked persistence/admission.
- Expression coordinator has one timer/task; recommendation engine retains one expression lock and provider fan-out 2.
- API status fields use JSON-safe primitives; dataclass/enums are converted before entering Pydantic payloads.
- All optional callbacks accept sync or async implementations only where explicitly awaited; candidate `on_admitted` is intentionally sync fire-and-notify.

## Execution Handoff

After this plan is committed, choose one execution mode:

1. **Subagent-Driven (recommended):** use `superpowers:subagent-driven-development`, one fresh worker per task with review checkpoints after Tasks 3, 5, 8 and the live test.
2. **Inline Execution:** continue in this session task-by-task, using `superpowers:executing-plans` and stopping at the same checkpoints.

Do not start both modes on the same worktree.
