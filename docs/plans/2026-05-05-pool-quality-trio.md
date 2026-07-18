# Pool Quality Trio Implementation Plan (v0.3.57 + extension v0.3.10)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
>
> Spec: `docs/plans/2026-05-05-pool-quality-trio-spec.md`

**Goal:** Three互不耦合 pool quality fixes,合发后 popup 推荐质量从"占位模板 + 自家笔记 + 7min 空窗"变成"LLM 个性化文案 + 干净源 + 启动即 history"。

**Architecture:**
- **W1** 一行 SQL `WHERE` 改动 + 兜底 warn 日志,把 pool 入场严格化为"已 precompute"
- **W2** 后端两个 ingest endpoint 统一 self_info 提取协议,启动钩子清存量
- **W3** 扩展三个 content-script 路径全部抽 self_info + scrape-time 过滤
- **W4** Daemon 订阅 cookie-synced 事件 → 一次性 history 重拉

**Tech Stack:** Python 3.11/FastAPI/SQLite, TypeScript Chrome MV3, pytest + node:test.

**版本切换:** 全部完成后 `__init__.py` + `pyproject.toml` 同步从 0.3.56 → 0.3.57;`extension/manifest.json` + `extension/package.json` 0.3.9 → 0.3.10。

---

## Wave 1 — P3: Pool gate on precomputed copy

**为什么先做这个**:改动最小、最隔离,直接让 popup 文案立即变好,且不依赖任何扩展协议变化。

### Task 1.1: Pool gate 测试先行

**Files:**
- Test: `tests/test_database.py`(已存在,加测试)
- Test: `tests/test_recommendation_engine.py`(已存在,加测试)

**Step 1: 写失败测试**

`tests/test_database.py` 加:

```python
def test_get_pool_candidates_skips_rows_without_precomputed_copy(tmp_db) -> None:
    db = tmp_db
    db.cache_content(
        bvid="bv-no-copy",
        title="未 precompute 的笔记",
        source="bilibili",
        source_platform="bilibili",
        relevance_score=0.9,
        pool_status="fresh",
        # pool_expression / pool_topic_label 留空
    )
    db.cache_content(
        bvid="bv-precomputed",
        title="已 precompute",
        source="bilibili",
        source_platform="bilibili",
        relevance_score=0.85,
        pool_status="fresh",
    )
    db.update_pool_copy("bv-precomputed", expression="LLM 文案", topic_label="某 topic")

    rows = db.get_pool_candidates(limit=10)
    assert [r["bvid"] for r in rows] == ["bv-precomputed"]


def test_count_pool_candidates_respects_precompute_gate(tmp_db) -> None:
    db = tmp_db
    db.cache_content(bvid="a", title="a", pool_status="fresh", source="bilibili", source_platform="bilibili")
    db.cache_content(bvid="b", title="b", pool_status="fresh", source="bilibili", source_platform="bilibili")
    db.update_pool_copy("b", expression="x", topic_label="y")
    assert db.count_pool_candidates() == 1
```

`tests/test_recommendation_engine.py` 加:

```python
async def test_serve_never_falls_back_to_template_when_pool_is_gated(
    tmp_engine, tmp_profile
) -> None:
    engine, db = tmp_engine
    # 30 个候选,15 个有 precomputed 文案,15 个没有
    for i in range(15):
        db.cache_content(bvid=f"bv-with-{i}", title=f"T{i}", pool_status="fresh",
                         source="bilibili", source_platform="bilibili", relevance_score=0.5)
        db.update_pool_copy(f"bv-with-{i}", expression=f"理由 {i}", topic_label=f"主题 {i}")
    for i in range(15):
        db.cache_content(bvid=f"bv-without-{i}", title=f"U{i}", pool_status="fresh",
                         source="bilibili", source_platform="bilibili", relevance_score=0.6)
    recs = await engine.serve(profile=tmp_profile, limit=10)
    for rec in recs:
        # 不可以是 _fallback_expression 任何一条模板
        assert "切口挺顺的" not in rec.expression
        assert rec.expression.strip()
        assert rec.topic_label.strip()
```

**Step 2: Verify red**

```bash
pytest tests/test_database.py::test_get_pool_candidates_skips_rows_without_precomputed_copy \
       tests/test_database.py::test_count_pool_candidates_respects_precompute_gate \
       tests/test_recommendation_engine.py::test_serve_never_falls_back_to_template_when_pool_is_gated -q
```

预期:三个 FAIL(目前 SQL 不带 expression 过滤,前两个会返回所有 row;serve 测试会出现 fallback 文案)。

### Task 1.2: 改 SQL — `get_pool_candidates` + `count_pool_candidates`

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`

`get_pool_candidates`(L766)两个 SQL 分支的 `WHERE` 都加:
```sql
AND COALESCE(pool_expression, '') != ''
AND COALESCE(pool_topic_label, '') != ''
```

放在已有的 `AND COALESCE(feedback_type, '') != 'dislike'` 之后,跟其他 ingest-time gate 一起。

`count_pool_candidates`(L871)同样位置加上。

注意 window-function 分支里 `WITH ranked AS (... WHERE ...)` 内部的 WHERE 也要加,**不是**外层 SELECT 的 WHERE。

**Verify:**
```bash
pytest tests/test_database.py -q
pytest tests/test_recommendation_engine.py -q
```
预期:Task 1.1 三个测试转 GREEN,其他测试不破坏。

### Task 1.3: 加 pool gate leak 警告

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`

L320 改成:
```python
if not rec.expression:
    logger.warning(
        "Pool gate leak: bvid=%s pool_expression empty at serve time "
        "(expected to be filtered out by get_pool_candidates SQL). "
        "Falling back to template.",
        item.bvid,
    )
    rec.expression = self._fallback_expression(item)
if not rec.topic_label:
    rec.topic_label = self._fallback_topic_label(profile)
```

**Verify:**
```bash
pytest tests/test_recommendation_engine.py -q
```
所有测试 PASS。生产中 grep `Pool gate leak` 应该 0 条(就算有,也是 race window 的硬证据)。

### Task 1.4: 修复因 gate 引发的现有测试 fallout

**Files:**
- 任何 fixture 里 `pool_status='fresh'` 但没设 `pool_expression`/`pool_topic_label` 的测试

**Step:** 跑 `pytest -q`,看 fail 列表。常见模式:
- 测试 setup 写了 `db.cache_content(bvid=..., pool_status='fresh', ...)` 然后期望 `count_pool_candidates() > 0`——补一行 `db.update_pool_copy(bvid, expression='x', topic_label='y')`

不要为了让测试过把 SQL 过滤删掉。

**Verify:** 全套测试绿。

### Task 1.5: 文档同步

**Files:**
- Modify: `docs/modules/recommendation.md`(若存在;不存在跳过)
- Modify: `docs/changelog.md` 当前 v0.3.57 块下加一条:
  > - **Pool gate**:`get_pool_candidates` / `count_pool_candidates` 现在严格过滤未 precompute 的 row(`pool_expression`/`pool_topic_label` 非空才算"在池里"),消除 popup 文案落到占位模板的旧 bug。原 `_fallback_expression` 路径变成 race-window 兜底,触发即 WARN。

---

## Wave 2 — P2 后端:统一 self_info 协议 + 存量清理

### Task 2.1: 抽出统一 self_info 提取函数测试先行

**Files:**
- Modify: `tests/test_api_xhs_ingest.py`

**Step 1: 写测试**

```python
def test_extract_self_info_prefers_top_level_payload() -> None:
    payload = {
        "self_info": {"user_id": "uid-123", "nickname": "屎屎"},
        "debug": {
            "xhs_bootstrap": {
                "steps": [{"self_info": {"user_id": "stale", "nickname": "stale"}}]
            }
        },
    }
    assert _extract_self_info_from_payload(payload) == {"user_id": "uid-123", "nickname": "屎屎"}


def test_extract_self_info_falls_back_to_bootstrap_debug() -> None:
    payload = {
        "debug": {
            "xhs_bootstrap": {
                "steps": [{"self_info": {"user_id": "uid-456", "nickname": "屎屎"}}]
            }
        },
    }
    assert _extract_self_info_from_payload(payload) == {"user_id": "uid-456", "nickname": "屎屎"}


def test_extract_self_info_returns_none_when_absent() -> None:
    assert _extract_self_info_from_payload({}) is None
```

**Step 2: Verify red**:符号未导出,会 ImportError。

### Task 2.2: 实现 `_extract_self_info_from_payload`

**Files:**
- Modify: `src/openbiliclaw/api/app.py`(L1904 区域)

**Step:**
1. 把现有 `_extract_self_info_from_debug` 重构成 `_extract_self_info_from_payload(payload)`:
   - 先看 `payload.get("self_info")`,若是 `dict` 且有 `user_id` 或 `nickname` → 直接返回
   - 否则 fallback 到原来的 `debug.xhs_bootstrap.steps[*].self_info` 逻辑
2. 把它 export 到模块顶层(单元测试 import 用)
3. 所有调用方迁移过去

**Verify:**
```bash
pytest tests/test_api_xhs_ingest.py::test_extract_self_info_prefers_top_level_payload \
       tests/test_api_xhs_ingest.py::test_extract_self_info_falls_back_to_bootstrap_debug \
       tests/test_api_xhs_ingest.py::test_extract_self_info_returns_none_when_absent -q
```
GREEN。

### Task 2.3: `/observed-urls` 端点 wire 进 self_info 测试

**Files:**
- Modify: `tests/test_api_xhs_ingest.py`

**Step 1: 写测试**

```python
async def test_observed_urls_persists_self_info_and_filters_self_authored(client) -> None:
    # 第一次:发顶层 self_info + 一条自己作者的 note + 一条别人的 note
    resp = await client.post(
        "/api/sources/xhs/observed-urls",
        json={
            "self_info": {"user_id": "u1", "nickname": "屎屎"},
            "notes": [
                {"url": "https://www.xiaohongshu.com/explore/aaa", "title": "自家 165",
                 "author": "屎屎", "cover_url": ""},
                {"url": "https://www.xiaohongshu.com/explore/bbb", "title": "别人发的",
                 "author": "Jupiter", "cover_url": ""},
            ],
            "page_type": "search",
        },
    )
    assert resp.status_code == 200

    # content_cache 里只剩 Jupiter 那条
    rows = list(database.conn.execute(
        "SELECT bvid, up_name FROM content_cache WHERE source_platform='xiaohongshu'"
    ))
    assert {r["bvid"] for r in rows} == {"bbb"}

    # 第二次:不带 self_info,但 persisted state 仍生效
    resp = await client.post(
        "/api/sources/xhs/observed-urls",
        json={
            "notes": [
                {"url": "https://www.xiaohongshu.com/explore/ccc", "title": "屎屎又发一条",
                 "author": "屎屎", "cover_url": ""},
            ],
            "page_type": "explore",
        },
    )
    assert resp.status_code == 200
    rows2 = list(database.conn.execute(
        "SELECT bvid FROM content_cache WHERE source_platform='xiaohongshu'"
    ))
    assert "ccc" not in {r["bvid"] for r in rows2}
```

**Step 2: Verify red**:目前 `/observed-urls` 没读 self_info,第一次 POST 会两条都进 cache。

### Task 2.4: 改 `/observed-urls` + `/task-result` 端点

**Files:**
- Modify: `src/openbiliclaw/api/app.py`(`ingest_xhs_observed_urls` + `xhs_task_result`)

**Step 1:** `/observed-urls` 入口处:
```python
self_info_now = _extract_self_info_from_payload(payload)
if self_info_now:
    _persist_xhs_self_info(self_info_now)
self_info_for_filter = self_info_now or _load_xhs_self_info()
# ...
if notes_raw:
    cached = _cache_xhs_notes(ctx.database, notes_raw, page_type, self_info=self_info_for_filter)
```

**Step 2:** `/task-result` 把已有的 `_extract_self_info_from_debug(debug)` 替换成 `_extract_self_info_from_payload(payload)`,代码语义不变(top-level 优先,debug fallback)。

**Verify:**
```bash
pytest tests/test_api_xhs_ingest.py -q
```
Task 2.3 测试 GREEN。已有 `/task-result` 测试不应破坏。

### Task 2.5: 启动钩子 — purge 存量自家笔记

**Files:**
- Modify: `src/openbiliclaw/api/app.py`(create_app 末尾或 startup hook)
- Test: `tests/test_api_xhs_ingest.py`

**Step 1: 写测试**

```python
def test_purge_self_authored_pool_items_suppresses_existing_rows(database) -> None:
    database.cache_content(bvid="aaa", title="自家 165", up_name="屎屎",
                            source="xhs-extension-task", source_platform="xiaohongshu",
                            pool_status="fresh")
    database.cache_content(bvid="bbb", title="别人的", up_name="Jupiter",
                            source="xhs-extension-task", source_platform="xiaohongshu",
                            pool_status="fresh")
    self_info = {"user_id": "u1", "nickname": "屎屎"}
    suppressed = _purge_self_authored_pool_items(database, self_info)
    assert suppressed == 1
    rows = {r["bvid"]: r["pool_status"] for r in
            database.conn.execute("SELECT bvid, pool_status FROM content_cache").fetchall()}
    assert rows == {"aaa": "suppressed", "bbb": "fresh"}
```

**Step 2: 实现** `_purge_self_authored_pool_items(database, self_info) -> int`:

```python
def _purge_self_authored_pool_items(database: Any, self_info: dict[str, str]) -> int:
    nickname = (self_info or {}).get("nickname", "").strip()
    if not nickname:
        return 0
    cursor = database.conn.execute(
        "UPDATE content_cache SET pool_status='suppressed' "
        "WHERE source_platform='xiaohongshu' "
        "  AND COALESCE(pool_status, 'fresh') = 'fresh' "
        "  AND LOWER(COALESCE(up_name, '')) = LOWER(?)",
        (nickname,),
    )
    database.conn.commit()
    return cursor.rowcount
```

**Step 3: 启动时调用一次** — 在 `create_app()` 末尾或 `runtime_context` 启动钩子里,加载 self_info,有就 purge 一次:

```python
existing_self_info = _load_xhs_self_info()
if existing_self_info:
    suppressed = _purge_self_authored_pool_items(ctx.database, existing_self_info)
    if suppressed:
        logger.info("startup purge: suppressed %d self-authored xhs pool items", suppressed)
```

**Verify:**
```bash
pytest tests/test_api_xhs_ingest.py -q
```
GREEN。重启 daemon 看日志:有 self_info 的话出现 `startup purge: suppressed N`。

---

## Wave 3 — P2 扩展端:passive + search 抽 self_info + scrape-time 过滤

### Task 3.1: 类型 + helper 测试先行

**Files:**
- Modify: `extension/tests/passive.test.ts`(新建若不存在)

**Step 1: 写测试**

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { runPassiveCollectionWithState } from "../src/content/xhs/passive.js";
// (需要把 runPassiveCollection 重构成可测试的纯函数版本,见 Task 3.2)

test("passive collection includes self_info when state exposes user info", () => {
  const state = {
    user: {
      loggedIn: true,
      userInfo: { userId: "uid-1", nickname: "屎屎" },
    },
  };
  const observation = runPassiveCollectionWithState({
    state,
    anchors: [/* mock 一条 author=屎屎 一条 author=Jupiter */],
    baseUrl: "https://www.xiaohongshu.com/search_result?q=test",
  });
  assert.deepEqual(observation.self_info, { user_id: "uid-1", nickname: "屎屎" });
});

test("passive collection drops self-authored notes at scrape time", () => {
  const state = {
    user: { loggedIn: true, userInfo: { userId: "uid-1", nickname: "屎屎" } },
  };
  const observation = runPassiveCollectionWithState({ state, anchors: [...], baseUrl: "..." });
  assert.equal(observation.notes.find((n) => n.author === "屎屎"), undefined);
  assert.ok(observation.notes.find((n) => n.author === "Jupiter"));
});
```

### Task 3.2: 重构 `runPassiveCollection` 为纯函数

**Files:**
- Modify: `extension/src/content/xhs/passive.ts`
- Modify: `extension/src/content/xiaohongshu.ts`

**Step:**
1. 在 `passive.ts` 加纯函数 `buildPassiveObservation({ state, anchors, baseUrl, viewport })`(不依赖 `window`/`chrome`),返回 `XhsUrlObservation` 包含可选 `self_info`
2. 内部使用 `extractSelfInfoFromState`(已存在于 `bootstrap.ts`,export 出来 import 到 `passive.ts`)
3. 用 self_info 做 scrape-time 过滤:`notes = notes.filter((n) => !selfInfo || n.author?.toLowerCase() !== selfInfo.nickname.toLowerCase())`
4. `xiaohongshu.ts:runPassiveCollection` 改成 thin wrapper:读 `window.__INITIAL_STATE__`,读 anchors,调 `buildPassiveObservation`,把结果丢给 `chrome.runtime.sendMessage`

类型加:
```typescript
export interface XhsUrlObservation {
  urls: string[];
  notes: XhsNoteMetadata[];
  page_type: XhsPageType;
  observed_at: number;
  self_info?: { user_id: string; nickname: string };
}
```

**Verify:**
```bash
cd extension && npm run test
```
Task 3.1 测试 GREEN。

### Task 3.3: search/creator 任务分支抽 self_info

**Files:**
- Modify: `extension/src/content/xhs/task-executor.ts`(L615–665 `executeTaskInPage` 非 bootstrap 分支)
- Test: `extension/tests/xhs-task-executor.test.ts`

**Step 1: 测试**

```typescript
test("non-bootstrap task captures self_info into result payload", async () => {
  const fakeDoc = makeFakeDoc({
    state: { user: { loggedIn: true, userInfo: { userId: "uid", nickname: "屎屎" } } },
    notes: [{ author: "屎屎", title: "自家", url: "..." }, { author: "X", title: "别人", url: "..." }],
  });
  const result = await executeTaskInPage(
    { task_id: "t1", type: "search" },
    fakeWindow,
    fakeDoc,
  );
  assert.deepEqual(result.self_info, { user_id: "uid", nickname: "屎屎" });
  // 自家笔记被 scrape-time drop
  assert.equal(result.notes.find((n) => n.author === "屎屎"), undefined);
});
```

**Step 2: 实现** — 非 bootstrap 分支返回 payload 前:
```typescript
const state = extractBootstrapStateFromDocument(doc);
const selfInfo = state ? extractSelfInfoFromState(state) : null;
// 过滤
const filteredNotes = selfInfo
  ? notes.filter((n) => n.author?.toLowerCase() !== selfInfo.nickname.toLowerCase())
  : notes;
return {
  task_id: msg.task_id,
  urls: urls.slice(0, MAX_URLS),
  notes: filteredNotes,
  status: "ok",
  self_info: selfInfo ?? undefined,  // ← 新字段
};
```

`TaskResultPayload` 类型加 `self_info?: { user_id: string; nickname: string }`。

**Step 3:** `service-worker.ts:handleTaskResult` 把 `self_info` 透传到 POST body 顶层(已经透传整个 payload,不一定改;如有筛字段处补上)。

**Verify:**
```bash
cd extension && npm run test && npm run typecheck
```

### Task 3.4: 扩展端版本切换

**Files:**
- Modify: `extension/manifest.json` version 0.3.9 → 0.3.10
- Modify: `extension/package.json` version 0.3.9 → 0.3.10

**Verify:** `cd extension && npm run build` 成功。

---

## Wave 4 — P1: Cookie-ready hook → 一次性 history 重拉

### Task 4.1: 测试先行

**Files:**
- Test: `tests/test_runtime_refresh.py`(已存在)

**Step:**

```python
async def test_history_retried_after_cookie_synced_event(monkeypatch) -> None:
    fetch_calls = []

    async def fake_get_history(max_items):
        fetch_calls.append(max_items)
        if len(fetch_calls) == 1:
            # 第一次模拟无 cookie 状态(返回 [])
            return []
        return [{"title": "t1", "bvid": "bv1"}]

    controller = make_controller_with_history_fetcher(fake_get_history)
    # tick 1: 还没 cookie ready,fetch 拿不到东西
    await controller._on_cookie_ready_if_first_history()
    assert controller._history_first_fetch_done is False
    # 模拟 event_hub 收到 cookie_synced
    controller._notify_cookie_ready()
    # tick 2: 立即重试,这次 fetch 返回数据
    await controller._on_cookie_ready_if_first_history()
    assert controller._history_first_fetch_done is True
    assert len(fetch_calls) == 2
```

**Verify red:** 方法不存在,`AttributeError`。

### Task 4.2: 实现 hook

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`

**Step:**
1. 新增字段:
   ```python
   _history_first_fetch_done: bool = field(default=False, init=False)
   _cookie_ready_signal: bool = field(default=False, init=False)
   ```
2. 新方法:
   ```python
   def _notify_cookie_ready(self) -> None:
       """订阅 event_hub 'bilibili_cookie_synced' 时调用 — 标记下个 tick 重试 history。"""
       self._cookie_ready_signal = True

   async def _on_cookie_ready_if_first_history(self) -> None:
       """启动后 + 每次 tick 开头调用 — 一次性把 cookie-ready 后的 history 拉下来。"""
       if self._history_first_fetch_done:
           return
       client = getattr(self, "bilibili_client", None)
       if client is None:
           return
       if not getattr(client, "is_authenticated", False):
           return
       try:
           items = await client.get_user_history(max_items=300)
       except Exception:
           logger.exception("First-tick history fetch failed; will retry next tick")
           return
       if not items:
           return
       # 转事件并 propagate
       events = self._history_items_to_events(items)
       for event in events:
           with suppress(Exception):
               await self.memory_manager.propagate_event(event)
       self._history_first_fetch_done = True
       self._cookie_ready_signal = False
       logger.info(
           "First-tick history fetch succeeded after cookie-ready: %d items",
           len(items),
       )
   ```
3. 在 `run_refresh_loop` 开头(同 `_on_profile_ready_if_first_time` 旁边)调用:
   ```python
   while True:
       with suppress(Exception):
           await self._on_cookie_ready_if_first_history()
       with suppress(Exception):
           await self._on_profile_ready_if_first_time()
       ...
   ```

### Task 4.3: 把 controller 接到 event_hub

**Files:**
- Modify: `src/openbiliclaw/api/runtime_context.py` 或 controller 启动处

**Step:** 在 `event_hub.subscribe("bilibili_cookie_synced", ...)` 处把 callback 改为调用 `controller._notify_cookie_ready()`。

(若现有 event_hub 不支持类型订阅,fallback 到 publisher 那侧——cookie sync handler 直接 `controller._notify_cookie_ready()`。)

**Verify:**
```bash
pytest tests/test_runtime_refresh.py -q
```
Task 4.1 测试 GREEN。

---

## Wave 5 — 文档同步

### Task 5.1: changelog

**Files:**
- Modify: `docs/changelog.md`

加新 block:

```markdown
## v0.3.57: pool quality trio (2026-05-05)

### Fixed
- **Cookie race**: daemon now retries `fetch_history` once cookie sync arrives,
  closing the 7-min silent gap between extension cookie push and first history
  pull observed in 2026-05-05 logs.
- **XHS self-author leak**: passive collector + search/creator task paths now
  extract `self_info` from `__INITIAL_STATE__` and piggyback it on every
  ingest request. Both extension scrape-time filter + backend persist-time
  filter cover the path. Startup purge suppresses existing pool rows
  authored by the logged-in user.
- **Pool gate on precomputed copy**: `get_pool_candidates` /
  `count_pool_candidates` now strictly require `pool_expression` and
  `pool_topic_label` to be non-empty. Items entering `content_cache` from
  discovery wait until `precompute_pool_copy` fills LLM-generated copy
  before becoming visible to `serve()`. Eliminates the placeholder template
  ("这条切口挺顺的，先丢给你看看…") leaking to popup.

### Companion extension release
- v0.3.10 — `passive.ts` + `task-executor.ts` non-bootstrap path抽 self_info,
  scrape-time drop `note.author === self.nickname`. Required for full P2 fix.
```

### Task 5.2: README 高亮

**Files:**
- Modify: `README.md`(顶部 📌 v0.3.57 highlight callout)
- Modify: `README_EN.md` 同步

加一句:
> **v0.3.57**: popup 推荐文案不再有占位模板,XHS 自己发布的笔记不再混入推荐池,daemon cookie 就绪后立即拉历史。配套 extension v0.3.10 必须一起更新。

### Task 5.3: 模块文档

**Files:**
- Modify: `docs/modules/recommendation.md`(若存在)— "implemented features" 加一行 pool gate
- Modify: `docs/modules/extension.md` — 写明 v0.3.10 passive/search 都抽 self_info
- 不动 architecture diagrams(这次改动不改跨模块 wiring)

---

## Wave 6 — Release

### Task 6.1: 后端版本切换

**Files:**
- Modify: `src/openbiliclaw/__init__.py` `__version__ = "0.3.57"`
- Modify: `pyproject.toml` `version = "0.3.57"`

### Task 6.2: 跑全套测试

```bash
pytest -q
ruff check src/ tests/
mypy src/
cd extension && npm run typecheck && npm run test && npm run build
```
全部 PASS / 0 issue。

### Task 6.3: Smoke test

1. 删除 `data/bilibili_cookie.json` 模拟首次启动
2. `openbiliclaw start` 启动 daemon
3. 装好 extension v0.3.10,等扩展 sync cookie
4. **观测点**:
   - 日志里 `Cookie set and saved` 之后 ≤30s 出现 `First-tick history fetch succeeded after cookie-ready: N items` ✅ P1
   - 在小红书任意页滚一下,日志里出现 `xhs self_info persisted: user_id=... nickname=...` ✅ P2
   - 重启 daemon,日志出现 `startup purge: suppressed N self-authored xhs pool items`(如果之前有存量污染) ✅ P2
   - 等 5 min,popup 任意"换一批",所有卡片 expression 都不是 "这条切口挺顺的，先丢给你看看…" ✅ P3
   - 日志里 grep `Pool gate leak` 应该 0 条

### Task 6.4: Commit + push

每个 Wave 单独 commit:
```bash
git add -A && git commit -m "feat(pool): gate pool entry on precomputed expression (P3)"
git add -A && git commit -m "fix(xhs): unified self_info extraction across all ingest paths (P2 backend)"
git add -A && git commit -m "fix(xhs): passive + search tasks now capture self_info (P2 extension)"
git add -A && git commit -m "fix(runtime): retry fetch_history when cookie syncs after daemon start (P1)"
git add -A && git commit -m "docs: v0.3.57 changelog + README + module docs"
git add -A && git commit -m "release: v0.3.57 backend + extension v0.3.10"
git push
```

### Task 6.5: Tag

```bash
git tag v0.3.57
git tag extension-v0.3.10
git push --tags
```

(后端不发 Releases artifacts;扩展 CI 触发 `extension-v*` tag → 构建并发 Releases。)

---

## 失败 / 回滚策略

- **Wave 1 失败**(SQL 改动后 popup 永远空):问题大概率在测试 fixture 没补 expression。grep `pool_status='fresh'` 在 tests/ 目录下,挨个补 `update_pool_copy`
- **Wave 2 失败**(后端 500):多半是 `_extract_self_info_from_payload` 没处理 None payload。先 bail out 类型守卫
- **Wave 3 失败**(扩展打包失败):typecheck 是先行验证手段;build 失败回看具体错误
- **Wave 4 失败**(history 不重拉):event_hub.subscribe 没接通常见。日志 grep `notify_cookie_ready` 看是否被调用
- **整体回滚**:每个 Wave 一个 commit,可以单独 revert。Pool gate(W1)一旦 deploy,要 revert 必须先把 SQL 过滤拿掉,否则 popup 立刻空池

---

**End of plan.**
