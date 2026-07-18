# Douyin Bootstrap Import Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Import Douyin (douyin.com) posted / favorite / liked / followed signals during initialization so the first profile is built from Bilibili + Xiaohongshu + Douyin combined.

**Architecture:** Mirror the existing `xhs_tasks` bridge with a new `dy_tasks` table and a `bootstrap_profile` task. The extension opens an active Douyin tab in the user's logged-in session, navigates through 4 profile sub-tabs (`/user/<sec_uid>` posted, `/user/self?showTab=favorite_collection` favorites, `/user/<sec_uid>` likes, `/user/<sec_uid>` follows), and **observes** Douyin's own `webmssdk.js`-signed network calls via a MAIN-world fetch-tap rather than DOM-scraping the virtualised list. The backend converts the captured items into normal event-layer payloads consumed by `SoulEngine.analyze_events()` and `build_initial_profile()`.

**Why fetch-tap, not DOM-scrape:** Douyin's profile pages use a virtualised React list — cards are evicted from the DOM after a few scroll rounds. Hooking `window.fetch` / `XMLHttpRequest.send` in MAIN world captures every `/aweme/v1/web/aweme/{post,favorite,collection,like,follow}/` page Douyin's React app fires; we never re-issue these calls so we never need to compute X-Bogus / msToken / `_signature` ourselves.

**Tech Stack:** Python/FastAPI/SQLite/Typer, TypeScript Chrome extension MV3, node:test, pytest.

**See also:** [`2026-05-06-douyin-bootstrap-import-design.md`](2026-05-06-douyin-bootstrap-import-design.md) for the architecture rationale and open-source prior-art notes.

---

### Task 1: Backend Event Conversion

**Files:**
- New: `src/openbiliclaw/sources/dy_tasks.py`
- New: `tests/test_dy_tasks.py`

**Step 1: Write failing tests**

Add tests for a pure helper that converts captured Douyin items to events:

```python
def test_dy_bootstrap_videos_to_events_maps_scopes() -> None:
    events = dy_bootstrap_videos_to_events([
        {"scope": "dy_post", "title": "我发的", "url": "https://www.douyin.com/video/a", "aweme_id": "a"},
        {"scope": "dy_collect", "title": "收藏的", "url": "https://www.douyin.com/video/b", "aweme_id": "b"},
        {"scope": "dy_like", "title": "点赞的", "url": "https://www.douyin.com/video/c", "aweme_id": "c"},
        {"scope": "dy_follow", "title": "关注的人", "url": "https://www.douyin.com/user/d", "creator_sec_uid": "d"},
    ])
    assert [e["event_type"] for e in events] == ["view", "favorite", "like", "follow"]
    assert all(e["metadata"]["source_platform"] == "douyin" for e in events)
    assert events[1]["metadata"]["signal_strength"] == 1.0   # collect
    assert events[2]["metadata"]["signal_strength"] == 0.85  # like
```

**Step 2: Verify red**

Run: `uv run pytest tests/test_dy_tasks.py::test_dy_bootstrap_videos_to_events_maps_scopes -q`

Expected: FAIL — module does not exist.

**Step 3: Implement helper**

In new `dy_tasks.py`:

- Map `dy_post -> view` (signal 0.4), `dy_collect -> favorite` (1.0), `dy_like -> like` (0.85), `dy_follow -> follow` (0.6).
- Include in metadata: `source_platform="douyin"`, `aweme_id` (or `creator_sec_uid` for follows), `author`, `cover_url`, `import_source=f"dy_bootstrap_{scope_short}"`, `signal_strength`.
- Skip items with no title and no URL/id.
- `event_type="follow"` may be new — check `event_format.py`; if absent, add it as a recognized type and update the test in `test_event_format.py`.

**Step 4: Verify green**

Run: `uv run pytest tests/test_dy_tasks.py -q`

Expected: PASS.

### Task 2: Backend Task Queue + Ingestion Endpoint

**Files:**
- Modify: `src/openbiliclaw/sources/dy_tasks.py`
- Modify: `src/openbiliclaw/storage/database.py` (or wherever XHS schema lives)
- Modify: `src/openbiliclaw/api/app.py`
- New: `tests/test_api_dy_ingest.py`

**Step 1: Write failing test**

Add a test posting `/api/sources/dy/task-result` with `status=ok`, type `bootstrap_profile`, and grouped `videos`. Assert the task is marked completed, items are cached, and converted events are propagated to memory.

**Step 2: Verify red**

Run: `uv run pytest tests/test_api_dy_ingest.py::test_dy_bootstrap_task_result_records_events -q`

Expected: FAIL — endpoint does not exist.

**Step 3: Implement**

- Add `dy_tasks` SQLite table mirroring `xhs_tasks` (id TEXT, type TEXT, payload JSON, status TEXT, created_at, completed_at, result JSON, daily_budget). Expose `DyTaskQueue` with the same interface as `XhsTaskQueue` (`enqueue_with_id`, `get_pending`, `complete`).
- Add `/api/sources/dy/task-result` endpoint mirroring the XHS one. On `bootstrap_profile` results, call `dy_bootstrap_videos_to_events` then `ctx.memory_manager.propagate_event` for each event. Do **not** call `soul_engine.analyze_events()` here — init handles the combined batch.
- Add `/api/sources/dy/pending` and `/api/sources/dy/heartbeat` mirrors so the extension dispatcher can poll.

**Step 4: Verify green**

Run: `uv run pytest tests/test_api_dy_ingest.py tests/test_dy_tasks.py -q`

Expected: PASS.

### Task 3: Extension MAIN-World Fetch Tap

**Files:**
- New: `extension/src/main/dy-fetch-tap.ts`
- New: `extension/tests/dy-fetch-tap.test.ts`

**Step 1: Write failing tests**

Add pure tests for parsing `/aweme/v1/web/aweme/{post,favorite,collection,like}/` JSON responses into normalized items:

```ts
test("parseAwemeListResponse extracts aweme_id, desc, author for post scope", () => {
  const items = parseAwemeListResponse({
    aweme_list: [
      { aweme_id: "111", desc: "demo", author: { nickname: "u", sec_uid: "s" }, video: { cover: { url_list: ["https://c"] } }, duration: 18000 },
    ],
  }, "dy_post");
  assert.equal(items.length, 1);
  assert.equal(items[0].scope, "dy_post");
  assert.equal(items[0].aweme_id, "111");
  assert.equal(items[0].url, "https://www.douyin.com/video/111");
});

test("classifyDouyinResponseUrl maps endpoints to scopes", () => {
  assert.equal(classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/aweme/post/?count=18"), "dy_post");
  assert.equal(classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/aweme/favorite/?count=18"), "dy_collect");
  assert.equal(classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/aweme/like/?count=18"), "dy_like");
  assert.equal(classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/user/follow/list/?count=20"), "dy_follow");
  assert.equal(classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/recommend/?count=10"), null);
});
```

**Step 2: Verify red**

Run: `cd extension && node --test --experimental-strip-types tests/dy-fetch-tap.test.ts`

Expected: FAIL — module does not exist.

**Step 3: Implement pure parsers + injector skeleton**

Export from `dy-fetch-tap.ts`:

- `classifyDouyinResponseUrl(url: string): DouyinScope | null`
- `parseAwemeListResponse(json, scope): DouyinBootstrapItem[]`
- `parseUserFollowListResponse(json): DouyinBootstrapItem[]` (separate JSON shape)
- `waitForDouyinSdk(target: Window, timeoutMs: number): Promise<boolean>` — resolves true when `target.byted_acrawler` exists, false on timeout
- `installFetchTap(target: Window, postBack: (items, scope) => void): () => void` — wraps `target.fetch` and `target.XMLHttpRequest.prototype.send`, returns disposer

Wrap responses by tee-ing `Response.clone()` then calling `.json()` off the clone so the page's own consumer is unaffected. **Never modify the request.** On parse, `postMessage` to `window` with a sentinel type like `OPENBILICLAW_DOUYIN_AWEME_PAGE`.

**Critical timing note** (verified 2026-05-07 via chrome-devtools MCP — see design doc §3 step 5): Douyin's page bundle wraps `window.fetch` with its own axios-style wrapper *after* document load. A `document_start` injection is shadowed and captures zero responses. The bootstrap content script must `await waitForDouyinSdk(window, 8000)` (polling for `window.byted_acrawler`) **before** calling `installFetchTap`. Wrapping the SDK's wrapper preserves the signing (their wrapper signs internally) and adds our observation as the outermost layer. Tests for `waitForDouyinSdk` use a mocked clock; tests for `installFetchTap` pass a fake `Window` with a stub `fetch` so the SDK-wrapping concern doesn't bleed into pure-parser tests.

**Step 4: Verify green**

Run: `cd extension && node --test --experimental-strip-types tests/dy-fetch-tap.test.ts`

Expected: PASS.

### Task 4: Extension Content-Script Executor

**Files:**
- New: `extension/src/content/dy/bootstrap.ts`
- New: `extension/src/content/dy/task-executor.ts`
- New: `extension/tests/dy-task-executor.test.ts`

**Step 1: Write failing tests**

Pure tests that don't need a live page:

```ts
test("groupBootstrapItemsByScope respects max_items_per_scope cap", () => {
  const items = [
    { aweme_id: "a", scope: "dy_post" },
    { aweme_id: "b", scope: "dy_post" },
    { aweme_id: "c", scope: "dy_collect" },
  ];
  const grouped = groupBootstrapItemsByScope(items, { max_items_per_scope: 1 });
  assert.equal(grouped.dy_post.length, 1);
  assert.equal(grouped.dy_collect.length, 1);
});

test("ingestMainWorldFetchMessage filters by sentinel and dedups by aweme_id", () => {
  const sink = new BootstrapItemSink({ maxItemsPerScope: 100 });
  sink.ingest({ data: { type: "OPENBILICLAW_DOUYIN_AWEME_PAGE", scope: "dy_post", items: [{ aweme_id: "a" }] } });
  sink.ingest({ data: { type: "OPENBILICLAW_DOUYIN_AWEME_PAGE", scope: "dy_post", items: [{ aweme_id: "a" }, { aweme_id: "b" }] } });
  sink.ingest({ data: { type: "UNRELATED" } });
  assert.equal(sink.snapshot().dy_post.length, 2);
});

test("buildBootstrapPartialPayload reports cumulative scope counts", () => {
  const payload = buildBootstrapPartialPayload({
    taskId: "t1",
    scope: "dy_collect",
    newItems: [{ aweme_id: "x" }],
    scopeCounts: { dy_collect: 5, dy_post: 0, dy_like: 0, dy_follow: 0 },
    round: 2,
  });
  assert.equal(payload.scope_counts.dy_collect, 5);
});
```

**Step 2: Verify red**

Run: `cd extension && node --test --experimental-strip-types tests/dy-task-executor.test.ts`

Expected: FAIL — helpers do not exist.

**Step 3: Implement orchestration**

- `bootstrap.ts`: read `<script id="RENDER_DATA">`, decode + parse, surface `{sec_uid, current_url}`. **Do not import from `extension/src/content/xhs/`** — Douyin gets its own tree.
- `task-executor.ts` ships its own scroll-loop helper (e.g. `dyShouldContinueScroll`) that measures stagnation at the **fetch-tap level**: a round counts stagnant when the current scope's `BootstrapItemSink` saw zero new aweme pages since the last scroll. This is a strictly better signal than XHS's DOM-card-count delta and a different metric, so we explicitly do not reuse `bootstrapScrollShouldContinue` from xhs/. Per-scope loop:
  1. Navigate to scope URL.
  2. Inject `dy-fetch-tap.ts` into MAIN world via `chrome.scripting.executeScript({world: "MAIN"})`.
  3. Listen for `OPENBILICLAW_DOUYIN_AWEME_PAGE` postMessages via `BootstrapItemSink`.
  4. Drive scrolls; emit partial payloads via `sendTaskResult` on each new batch.
  5. Stop when `dyShouldContinueScroll` returns false OR `max_items_per_scope` cap reached.
- After all scopes done, post final result with `status=ok` + `scope_counts`.

**Step 4: Verify green**

Run: `cd extension && node --test --experimental-strip-types tests/dy-task-executor.test.ts`

Expected: PASS.

### Task 5: Extension Dispatcher Task Type

**Files:**
- New: `extension/src/background/dy-task-dispatcher.ts`
- New: `extension/tests/dy-task-dispatcher.test.ts`
- Modify: `extension/src/background/service-worker.ts` (register the new dispatcher loop)

**Step 1: Write failing tests**

```ts
test("isValidDyTask accepts bootstrap_profile with douyin scopes", () => {
  assert.ok(isValidDyTask({ id: "t", type: "bootstrap_profile", scopes: ["dy_post", "dy_collect", "dy_like", "dy_follow"] }));
  assert.ok(!isValidDyTask({ id: "t", type: "unknown_type" }));
});

test("buildDyTaskUrl routes scopes to the right tab URLs", () => {
  assert.equal(
    buildDyTaskUrl({ id: "t", type: "bootstrap_profile", initial_url: "https://www.douyin.com/" }),
    "https://www.douyin.com/",
  );
});
```

**Step 2: Verify red**

Run: `cd extension && node --test --experimental-strip-types tests/dy-task-dispatcher.test.ts`

Expected: FAIL — module does not exist.

**Step 3: Implement dispatcher**

Independent module — `xhs-task-dispatcher.ts` is **read-only reference** for shape, not a place to import from:

- Poll `/api/sources/dy/pending` from the service worker on a slow loop (60s default).
- For `bootstrap_profile`, open an **active** tab on `https://www.douyin.com/`, wait for the content script to read `RENDER_DATA` and supply the navigation roadmap (4 scope URLs), then drive each in sequence.
- Honor `max_items_per_scope`, `max_scroll_rounds`, `max_stagnant_scroll_rounds` from the task payload. Defaults: `300 / 15 / 5` — the same numeric values as XHS post-v0.3.64, but **independently chosen** for Douyin (each platform owns its own tuning surface; if Douyin needs to diverge later it changes its own defaults without touching XHS).
- On task complete, POST `/api/sources/dy/task-result`.

**Step 4: Verify green**

Run: `cd extension && node --test --experimental-strip-types tests/dy-task-dispatcher.test.ts`

Expected: PASS.

### Task 6: Init Orchestration + CLI

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write failing tests**

```python
def test_init_includes_dy_bootstrap_events(monkeypatch) -> None:
    """Init must enqueue a Douyin bootstrap task when --yes-douyin is passed
    and propagate captured events through the same combined batch the XHS
    bootstrap uses."""
    ...

def test_enqueue_dy_bootstrap_task_uses_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS", "5")
    monkeypatch.setenv("OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS", "100")
    task_id = _enqueue_dy_bootstrap_task()
    payload = captured["payload"]
    assert payload["max_scroll_rounds"] == 5
    assert payload["max_items_per_scope"] == 100
    assert sorted(payload["scopes"]) == ["dy_collect", "dy_follow", "dy_like", "dy_post"]
```

**Step 2: Verify red**

Run: `uv run pytest tests/test_cli.py::test_enqueue_dy_bootstrap_task_uses_env_overrides -q`

Expected: FAIL — helper does not exist.

**Step 3: Implement**

- Add `_enqueue_dy_bootstrap_task()` mirroring the XHS variant. Defaults: `max_items_per_scope=300`, `max_scroll_rounds=15` (matching v0.3.64 XHS conventions). Env overrides as above.
- Add `--yes-douyin` / `--no-douyin` flags to `init` (and the agent_bootstrap.py non-interactive path), mirroring `--yes-xhs` exactly. **Default to `--no-douyin` in non-interactive mode** so `agent_bootstrap.py` callers must opt in explicitly — same safety stance as XHS.
- Add a `_collect_dy_bootstrap_events(task_id)` mirror of `_collect_xhs_bootstrap_events` that waits for the bootstrap task to settle.
- Surface scope_counts in the `1/4 拉取数据` summary line:
  ```
  抖音 收藏 47 / 点赞 86 / 关注 23 / 发布 12
  ```

**Step 4: Verify green**

Run: `uv run pytest tests/test_cli.py -k dy -q`

Expected: PASS.

### Task 7: Documentation + Changelog

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/soul.md` (signal strength table)
- Modify: `docs/changelog.md` (new `## v0.3.65: 抖音 bootstrap 接入 (YYYY-MM-DD)` entry)
- Modify: `README.md` + `README_EN.md` (release-history table row + the existing 「为什么需要 OpenBiliClaw」 跨平台 callout updated to "B 站 / 小红书 / 抖音")
- Modify: `docs/architecture.md` + `docs/spec.md` (add Douyin to the source-adapter diagram)
- Modify: `docs/agent-install.md` (mirror the XHS section: "接入会前台抢焦点" / `OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS=0` escape hatch)

**Step 1: Update docs**

Document:

- `bootstrap_profile` for Douyin
- 4 scopes + signal strengths
- `--yes-douyin` / `--no-douyin` CLI flag
- Why the fetch-tap approach (1-2 sentences pointing back to the design doc)
- Note that watch-history is deliberately deferred (link to design-doc Phase C)

**Step 2: Verify lint + types**

```bash
uv run ruff check src/ tests/
cd extension && npm run typecheck
cd extension && npm run build && npm run package
```

Expected: PASS for touched areas; pre-existing baseline failures remain documented. Build + package step verifies the new MAIN-world script ships in the extension zip.

### Task 8 (deferred): `dy_history` Spike

Out of scope for this plan. Tracked in design doc §Phasing Phase C. Decision recorded back into design doc when run: either ship as fifth scope or mark permanently deferred with reason.

---

## Cross-Cutting Notes

**Module isolation from XHS — strictly enforced:**

- Douyin lives in its own tree: `src/openbiliclaw/sources/dy_*.py`, `extension/src/content/dy/`, `extension/src/main/dy-fetch-tap.ts`, `extension/src/background/dy-task-dispatcher.ts`. **Zero `import` statements crossing into `xhs_*` / `xhs/`.** XHS code is read-only reference for shape, not a library.
- No moves of "shared-looking" helpers from `xhs/` into a `shared/` directory in this plan. If a true shared abstraction emerges naturally after Douyin ships, that's a separate refactor with its own design review — not something to bake in pre-emptively.
- Separate `dy_tasks` SQLite table. Separate `/api/sources/dy/*` endpoints. XHS budget exhaustion never blocks Douyin and vice versa.
- Defaults `300 / 15 / 5` are independently chosen for Douyin; they happen to match XHS post-v0.3.64 today but are not coupled to it.

**The one shared layer is intentional**: event taxonomy in `src/openbiliclaw/sources/event_format.py`. Douyin events emit `event_type` from the same vocabulary (`favorite`, `like`, `view`, `follow`) and `source_platform="douyin"` so the soul-engine can analyze cross-source events uniformly. This is the cross-source contract; per-platform-typed events would defeat the multi-source architecture. See design doc §"Module Isolation from XHS" for the full rationale.

**Naming consistency:** All Douyin-specific files use `dy` prefix (`dy_tasks.py`, `dy-fetch-tap.ts`, `dy-task-dispatcher.ts`, `dy/bootstrap.ts`). `source_platform` value in events is the full word `"douyin"` (matches the existing `"xiaohongshu"` convention, not the `dy` short prefix).

**No backend HTTP scraping ever.** If a future task is tempted to call Douyin endpoints from Python (e.g. for discovery loop), it must go through the same extension-based pattern, never a direct `httpx` call. This invariant is documented in `docs/architecture.md` and enforced by code review.

**Reused open-source references (no runtime code consumed):**

- f2 (Apache-2.0) — endpoint catalog only, used to determine which `/aweme/v1/web/aweme/<scope>/` paths the fetch-tap should classify
- jiji262/douyin-downloader (MIT) — README cited for `collect/collectmix` self-cookie semantics
- Evil0ctal/Douyin_TikTok_Download_API (Apache-2.0) — `RENDER_DATA` script-tag format reference

These are read-only references; we copy zero lines of their source code.
