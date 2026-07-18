# XHS Safe Discovery Implementation Plan

> **For Claude:** Work task-by-task. Each task ends with a `git commit` so history stays bisectable. Run the full backend test suite (`.venv/bin/pytest -q`) before every commit that touches Python code; run `cd extension && npm test` before commits that touch extension code.

**Goal:** Replace the browser-based `XiaohongshuAdapter` with a safe-discovery architecture: passive URL collection + no-scroll active search + creator subscription, with a GPL-isolated XHS-Downloader sidecar for detail enrichment.

**Architecture:** See `docs/plans/2026-04-18-xhs-safe-discovery-design.md`.

**Tech Stack:** Python 3.11 (backend), Python 3.12 (sidecar container only), FastAPI, SQLite, TypeScript (Chrome MV3 extension).

---

## Task 1: Scaffold the XHS-Downloader sidecar

**Files:**
- Create: `sidecar/xhs-downloader/Dockerfile`
- Create: `sidecar/xhs-downloader/wrapper.py`
- Create: `sidecar/xhs-downloader/requirements.txt`
- Create: `sidecar/xhs-downloader/LICENSE` (copy of GPL-3.0)
- Create: `sidecar/xhs-downloader/README.md` (attribution + GPL note)
- Create: `sidecar/xhs-downloader/.dockerignore`

**Step 1: Failing verification**

Before any code, the repo should not already contain a sidecar directory. Check:

```bash
test ! -d sidecar/xhs-downloader && echo OK
```

Expected: OK.

**Step 2: Build the sidecar image layout**

1. Pin XHS-Downloader to a known-good commit (top of master as of 2026-04-17) — clone at build time into `/opt/xhs-downloader` inside the image.
2. `wrapper.py`: thin FastAPI app that imports `source.XHS` from the cloned tree and exposes a single endpoint:

   ```python
   POST /xhs/detail
   Body: { "url": "https://www.xiaohongshu.com/explore/XXX?xsec_token=..." }
   Returns: { "ok": true, "data": { ... } } or { "ok": false, "error": "..." }
   ```

   Internally calls `xhs.extract(url, download=False)` and flattens the result to a single note dict (XHS-Downloader returns a list because a note URL can expand to multiple items — we pick the first or return an error if empty).

3. Also expose `GET /health` returning `{ "ok": true }` without calling xhs (for container health check).

4. Dockerfile:
   - Base: `python:3.12-slim`
   - Install XHS-Downloader requirements.txt from the cloned repo
   - Install wrapper dependencies (`fastapi`, `uvicorn`)
   - Non-root user
   - Expose 5556
   - CMD: `uvicorn wrapper:app --host 0.0.0.0 --port 5556`

5. `README.md` must say:
   - What this sidecar is for
   - It embeds GPL-3.0 code (XHS-Downloader)
   - Link to upstream
   - Only communicates with main backend over HTTP (no shared code)

**Step 3: Verify container builds and responds**

```bash
docker build -t openbiliclaw-xhs-sidecar:dev sidecar/xhs-downloader/
docker run --rm -d --name xhs-sidecar-test -p 5556:5556 openbiliclaw-xhs-sidecar:dev
sleep 3
curl -sf http://127.0.0.1:5556/health
docker rm -f xhs-sidecar-test
```

Expected: `{"ok":true}`.

**Step 4: Commit**

```bash
git add sidecar/xhs-downloader/
git commit -m "feat(sidecar): add XHS-Downloader sidecar for xhs detail enrichment"
```

---

## Task 2: Wire sidecar into docker-compose

**Files:**
- Modify: `docker-compose.yml`
- Modify: `config.example.toml` (add `[sources.xiaohongshu]` section)
- Modify: `src/openbiliclaw/config.py` (add sidecar URL field)
- Modify: `tests/test_config.py` (test new field)

**Step 1: Failing test**

Extend `tests/test_config.py` to assert the new config field loads and has a sensible default:

```python
def test_xhs_sidecar_url_defaults_to_none() -> None:
    cfg = AppConfig()
    assert cfg.sources.xiaohongshu.sidecar_url is None


def test_xhs_sidecar_url_reads_from_toml(tmp_path: Path) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        '[sources.xiaohongshu]\nsidecar_url = "http://xhs-sidecar:5556"\n',
        encoding="utf-8",
    )
    cfg = AppConfig.load(toml)
    assert cfg.sources.xiaohongshu.sidecar_url == "http://xhs-sidecar:5556"
```

Run `.venv/bin/pytest tests/test_config.py -q`. Expected: FAIL (field missing).

**Step 2: Implement config schema**

Add a `SourcesConfig` / `XiaohongshuSourceConfig` to `config.py` with:
- `sidecar_url: str | None = None`
- `daily_search_budget: int = 20`
- `daily_creator_budget: int = 10`
- `task_interval_seconds: int = 45`

Re-run the test. Expected: PASS.

**Step 3: docker-compose entry**

Add a second service `xhs-sidecar` to `docker-compose.yml`:

- `build: ./sidecar/xhs-downloader`
- `container_name: openbiliclaw-xhs-sidecar`
- `restart: unless-stopped`
- `expose: ["5556"]` (internal only, not host-published)
- Main backend service gets `OPENBILICLAW_XHS_SIDECAR_URL=http://xhs-sidecar:5556` and `depends_on: [xhs-sidecar]`.

`config.example.toml` gets a new `[sources.xiaohongshu]` block documenting the three fields.

**Step 4: Verify compose config**

```bash
docker compose config > /dev/null
```

Expected: no error.

**Step 5: Commit**

```bash
git add docker-compose.yml config.example.toml src/openbiliclaw/config.py tests/test_config.py
git commit -m "feat(config): wire xhs sidecar service into docker-compose and config schema"
```

---

## Task 3: Replace XiaohongshuAdapter with HTTP-sidecar version

**Files:**
- Create: `src/openbiliclaw/sources/xiaohongshu_adapter.py`
- Modify: `src/openbiliclaw/sources/web_adapter.py` (delete `XiaohongshuAdapter` class)
- Modify: `src/openbiliclaw/sources/registry.py` (register new adapter)
- Create: `tests/test_xiaohongshu_adapter.py`

**Step 1: Failing test**

Write `tests/test_xiaohongshu_adapter.py` that:

1. Builds an adapter with a fake `AsyncClient` that returns a canned sidecar response for `POST /xhs/detail`.
2. Calls `adapter.fetch(recipe)` with a recipe of `strategy="enrich"` and `config={"urls": ["https://www.xiaohongshu.com/explore/abc"]}`.
3. Asserts the returned `DiscoveredContent` list has `source_platform="xiaohongshu"`, populated title / author / URL, and `content_id` derived from the note ID.
4. Asserts a malformed sidecar response (`{"ok": false, ...}`) is logged and skipped rather than crashing.

Run: `.venv/bin/pytest tests/test_xiaohongshu_adapter.py -q`. Expected: FAIL (adapter module missing).

**Step 2: Implement adapter**

`XiaohongshuAdapter`:

- Constructor takes `sidecar_url: str` and an optional `httpx.AsyncClient` (default: create one).
- `source_type` property returns `"xiaohongshu"`.
- `fetch(recipe, profile, limit)` dispatches by `recipe.strategy`:
  - `"enrich"`: expects `recipe.config["urls"]: list[str]`; POSTs each to `/xhs/detail`, maps to `DiscoveredContent`.
  - other strategies: return `[]` for now (future work).
- URL-level error handling: one bad URL must not fail the whole batch.
- Rate limiting: concurrency cap of 2 parallel detail calls (`asyncio.Semaphore`).
- Map XHS-Downloader's Chinese-keyed response dict to `DiscoveredContent` fields (e.g., `作品标题` → `title`, `作者昵称` → `up_name`, `作品链接` → `content_url`).

**Step 3: Remove browser-based XiaohongshuAdapter**

Delete the `XiaohongshuAdapter` class in `web_adapter.py`. Update `registry.py` so `"xiaohongshu"` resolves to the new HTTP-sidecar adapter, reading `sidecar_url` from config.

**Step 4: Run full test suite**

```bash
.venv/bin/pytest -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/sources/xiaohongshu_adapter.py \
        src/openbiliclaw/sources/web_adapter.py \
        src/openbiliclaw/sources/registry.py \
        tests/test_xiaohongshu_adapter.py
git commit -m "refactor(sources): replace browser-based xhs adapter with sidecar HTTP client"
```

---

## Task 4: Extension — passive xhs URL collection

**Files:**
- Modify: `extension/src/content/xhs/collector.ts` (or closest existing path)
- Create: `extension/test/xhs-passive.test.ts`
- Modify: `extension/src/background/service-worker.ts` (route new event type)

**Step 1: Failing test**

Add a test using jsdom-style fixtures that loads a minimal xhs search result DOM and asserts the collector extracts the expected note URLs with their `xsec_token` query string.

Run `cd extension && npm test`. Expected: FAIL.

**Step 2: Implement collector**

In the existing xhs content script, add a `collectVisibleNoteUrls()` that:

- Queries note links matching `a[href*="/explore/"]` or `a[href*="/discovery/item/"]` or other observed patterns
- Filters to those currently in the viewport (`getBoundingClientRect` overlap with viewport)
- Extracts full URL including `xsec_token` query param
- Deduplicates per page-session
- Emits a `xhs_urls_observed` event to the background worker with `{ urls: string[], page_type: "search" | "profile" | "explore" | "other", observed_at }`.

Trigger hook: run once on page load, once on scroll-stop (debounced 500ms). No automated scrolling — only react to user's own scrolling.

**Step 3: Background worker**

Add a handler for `xhs_urls_observed` that POSTs to a new backend endpoint `POST /api/sources/xhs/observed-urls` with the URL batch.

**Step 4: Run tests**

```bash
cd extension && npm test
```

Expected: PASS.

**Step 5: Commit**

```bash
git add extension/src/content/xhs/ extension/src/background/ extension/test/xhs-passive.test.ts
git commit -m "feat(extension): passive xhs note URL collection on user browsing"
```

---

## Task 5: Backend — observed-urls ingestion endpoint

**Files:**
- Modify: `src/openbiliclaw/api/app.py` (or the nearest sources router)
- Create or modify: `src/openbiliclaw/api/routers/sources_xhs.py`
- Create: `tests/test_api_xhs_ingest.py`

**Step 1: Failing test**

Test that `POST /api/sources/xhs/observed-urls` with a JSON body `{ "urls": [...], "page_type": "search" }` returns 200 and schedules enrichment. Mock the adapter to assert `fetch()` was called with a recipe containing those URLs.

Run `.venv/bin/pytest tests/test_api_xhs_ingest.py -q`. Expected: FAIL.

**Step 2: Implement endpoint**

- Accept up to N URLs per call (N=50, reject if larger)
- Validate URL shape (must start with `https://www.xiaohongshu.com/`)
- Queue an enrichment job via the existing discovery engine using the new adapter with `strategy="enrich"`
- Store observed URL + observation context in the DB for analytics (new table `xhs_observed_urls`)

Run tests.

**Step 3: Commit**

```bash
git add src/openbiliclaw/api/ tests/test_api_xhs_ingest.py
git commit -m "feat(api): ingest observed xhs note URLs for enrichment"
```

---

## Task 6: Extension — backend task dispatcher (background)

**Files:**
- Create: `extension/src/background/xhs-task-dispatcher.ts`
- Modify: `extension/src/background/service-worker.ts` (register dispatcher)
- Create: `extension/test/xhs-task-dispatcher.test.ts`

**Step 1: Failing test**

Mock `chrome.tabs`, `chrome.runtime`, and `fetch` against a fake backend that hands out one `search` task `{ id, type: "search", keyword: "机械键盘" }`. Assert the dispatcher:

- opens a tab at `https://www.xiaohongshu.com/search_result?keyword=%E6%9C%BA%E6%A2%B0%E9%94%AE%E7%9B%98` with `active: false`
- waits for content-script to emit `xhs_task_result`
- POSTs result to `/api/sources/xhs/task-result`
- closes the tab
- respects `task_interval_seconds` before requesting the next task

Run `cd extension && npm test`. Expected: FAIL.

**Step 2: Implement dispatcher**

- Poll `GET /api/sources/xhs/next-task` every N seconds (N = task_interval from config, default 45)
- When task arrives, call `chrome.tabs.create({ url, active: false })`
- Hand off to the content script (Task 7) via a per-task `task_id`
- Hard timeout 30s per task; on timeout, close tab and report failure
- Close tab on success or failure
- Only one task in flight at a time (mutex)

**Step 3: Commit**

```bash
git add extension/src/background/ extension/test/xhs-task-dispatcher.test.ts
git commit -m "feat(extension): background dispatcher for xhs search/creator tasks"
```

---

## Task 7: Extension — no-scroll task executor in content script

**Files:**
- Modify: `extension/src/content/xhs/collector.ts`
- Modify: `extension/test/xhs-passive.test.ts` (extend)

**Step 1: Failing test**

Load an xhs search result fixture. Simulate a `xhs_task_execute` message from background `{ task_id, type: "search" }`. Assert the executor:

- Waits until at least one note card is rendered (observe with `MutationObserver`, hard cap 5s)
- **Does not scroll** — reads only what's in the initial viewport + immediately adjacent DOM
- Extracts up to 20 URLs
- Emits `xhs_task_result` with `{ task_id, urls, status: "ok" }`

Expected: FAIL.

**Step 2: Implement executor**

- On `xhs_task_execute` message: wait for render → call `collectVisibleNoteUrls` (from Task 4) → emit result
- If no cards appear within 5s, emit `{ status: "empty" }`
- Never call any scroll method

**Step 3: Commit**

```bash
git add extension/src/content/ extension/test/
git commit -m "feat(extension): no-scroll xhs task executor (search / creator)"
```

---

## Task 8: Backend — task queue and subscription storage

**Files:**
- Modify: `src/openbiliclaw/storage/database.py` (new tables)
- Create: `src/openbiliclaw/sources/xhs_tasks.py`
- Create: `src/openbiliclaw/api/routers/sources_xhs_tasks.py`
- Create: `tests/test_xhs_tasks.py`

**Step 1: Failing test**

Test that:
- A keyword task can be enqueued from a Soul-driven scheduler stub
- Extension polling `GET /api/sources/xhs/next-task` returns the oldest pending task
- Posting to `/api/sources/xhs/task-result` stores the URLs, triggers enrichment, and marks task done
- Daily budget (default 20) rejects further enqueues on the same day
- Creator subscriptions stored in a separate table; a nightly scheduler stub can enumerate them and enqueue creator tasks

Expected: FAIL.

**Step 2: Implement**

Tables:

- `xhs_tasks(id, type, payload_json, status, created_at, completed_at)`
- `xhs_creator_subscriptions(id, creator_id, creator_url, display_name, added_at, last_fetched_at)`

API:

- `GET /api/sources/xhs/next-task` (200 with task or 204 no-content)
- `POST /api/sources/xhs/task-result`
- `POST /api/sources/xhs/creators` (add subscription)
- `GET /api/sources/xhs/creators` (list subscriptions)
- `DELETE /api/sources/xhs/creators/{id}`

Soul-driven search scheduler: pull top-K interest keywords from current `SoulProfile`, enqueue up to `daily_search_budget` search tasks per day.

Creator nightly scheduler: for each subscription whose `last_fetched_at` < 24h ago, enqueue a creator task.

**Step 3: Commit**

```bash
git add src/openbiliclaw/storage/ src/openbiliclaw/sources/xhs_tasks.py src/openbiliclaw/api/ tests/test_xhs_tasks.py
git commit -m "feat(backend): xhs task queue, creator subscriptions, and schedulers"
```

---

## Task 9: Docs + changelog

**Files:**
- Modify: `docs/modules/sources.md` (or closest existing module doc)
- Modify: `docs/changelog.md`
- Modify: `docs/modules/config.md` (document new `[sources.xiaohongshu]` fields)
- Create: `docs/modules/xhs-safe-discovery.md` (narrative how-to)

**Step 1: Content**

- `sources.md`: replace browser-adapter description with sidecar + extension collector architecture.
- `changelog.md`: under current milestone, add a block describing the xhs-safe-discovery phase delivery.
- `config.md`: document `[sources.xiaohongshu]` `sidecar_url`, budgets, interval.
- `xhs-safe-discovery.md`: explain to users how it works, what requires the extension, what the sidecar does, and why.

**Step 2: Commit**

```bash
git add docs/
git commit -m "docs: document xhs safe discovery architecture"
```

---

## Task 10: End-to-end smoke test

**Files:**
- Create: `tests/test_xhs_e2e_smoke.py`

**Step 1: Write smoke test**

Marked `@pytest.mark.integration`. Spins up the sidecar container via `docker compose up xhs-sidecar`, waits for health, calls the backend's `POST /api/sources/xhs/observed-urls` with a known-public xhs note URL, polls until the discovered_content table has a row for that URL with `source_platform="xiaohongshu"` and a non-empty title.

Guard with `XHS_E2E_SMOKE=1` env so CI / local dev default does not need docker.

**Step 2: Commit**

```bash
git add tests/test_xhs_e2e_smoke.py
git commit -m "test: add xhs safe-discovery end-to-end smoke test"
```

---

## Verification Checklist

Before declaring the phase done:

- [ ] `.venv/bin/pytest -q` passes (all backend tests green)
- [ ] `cd extension && npm test` passes (all extension tests green)
- [ ] `docker compose build` succeeds
- [ ] `docker compose up -d` starts both services; `curl http://127.0.0.1:8420/api/health` and `docker exec openbiliclaw-backend curl -sf http://xhs-sidecar:5556/health` both OK
- [ ] Extension loaded in Chrome collects URLs while manually browsing xhs
- [ ] A manually enqueued keyword task opens a background tab, collects URLs, closes the tab, and enriched content appears in the DB
- [ ] Old `XiaohongshuAdapter(WebSourceAdapter)` class is fully removed; `grep -rn "class XiaohongshuAdapter" src/` returns only the new file
- [ ] `docs/changelog.md` reflects the new phase
- [ ] No `import source.XHS` or GPL-licensed code reachable from main backend imports (only via HTTP to sidecar)
