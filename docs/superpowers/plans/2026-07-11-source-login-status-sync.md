# Source Login Status Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make desktop Web and extension settings show truthful, consistent source access states without any settings-page request reaching a content platform or invoking `rdt status`.

**Architecture:** `GET /api/sources/status` becomes a pure local snapshot over config, SQLite auth state, credential files, and persisted health rows. The extension refreshes XHS/Zhihu boolean login signals when its localhost runtime stream reconnects; both settings surfaces map the same complete state vocabulary and explain that XHS `xsec_token` is not login proof.

**Tech Stack:** Python 3.11+/FastAPI/Pydantic/SQLite/Pytest; TypeScript/Chrome MV3/node:test; vanilla desktop Web JavaScript.

## Global Constraints

- `GET /api/sources/status`, settings polling, and login-state heartbeats must not access Bilibili, Xiaohongshu, Douyin, YouTube, X, Zhihu, or Reddit remote endpoints.
- Do not run discover, search, feed, hot, related, init, profile rebuild, or account-changing E2E commands.
- XHS login cookie is `web_session`; Zhihu login cookie is `z_c0`; Reddit structural credential requires `reddit_session`.
- Fresh explicit `logged_in=false` is authoritative and cannot be overwritten by task history.
- Browser-cookie absence must not delete backend credentials that may belong to env, CLI, or manual configuration.
- Production changes follow red-green-refactor; each new regression test must be observed failing before implementation.
- Update `docs/modules/extension.md`, `docs/modules/config.md`, and `docs/changelog.md` with the final behavior.

## File Map

- `src/openbiliclaw/api/app.py`: local source-status aggregation and runtime-stream sync requests.
- `src/openbiliclaw/sources/reddit_tasks.py`: reusable local-only Reddit credential-state adapter.
- `src/openbiliclaw/api/models.py`: authoritative status vocabulary documentation.
- `extension/src/background/cookie-sync.ts`: handles XHS/Zhihu runtime sync requests.
- `src/openbiliclaw/web/desktop/assets/js/app.js`: complete desktop state labels.
- `extension/popup/popup.js`: complete popup state labels.
- `tests/test_api_app.py`, `tests/test_api_reddit_tasks.py`, `tests/test_desktop_web_zhihu_settings.py`, `extension/tests/cookie-sync.test.ts`, `extension/tests/popup-settings.test.ts`: regressions.
- `docs/modules/extension.md`, `docs/modules/config.md`, `docs/changelog.md`: product contract and release notes.

---

### Task 1: Make backend source status local-only and authoritative

**Files:**
- Modify: `src/openbiliclaw/sources/reddit_tasks.py:676-705,864-945`
- Modify: `src/openbiliclaw/api/app.py:7626-8026`
- Modify: `src/openbiliclaw/api/models.py:462-510`
- Test: `tests/test_api_app.py:1431-1937`
- Test: `tests/test_api_reddit_tasks.py:172-242`

**Interfaces:**
- Consumes: `_rdt_saved_credential_state() -> tuple[str, str]`, `Database.get_xhs_login_state()`, `Database.get_zhihu_login_state()`.
- Produces: `local_reddit_credential_status() -> RedditCommandStatus`; local-only `SourcesStatusResponse` states.

- [ ] **Step 1: Write failing backend regressions**

Add tests that express the desired semantics:

```python
def test_reddit_source_status_uses_local_credential_without_command_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = Config()
    cfg.sources.reddit.enabled = True
    cfg.sources.reddit.backend = "rdt"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks._rdt_saved_credential_state",
        lambda: ("present", "rdt credential 就绪。"),
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.probe_reddit_command_backend",
        lambda *_a, **_kw: pytest.fail("settings status must remain local-only"),
    )
    db = Database(tmp_path / "status.db")
    db.initialize()
    client = TestClient(create_app(memory_manager=object(), database=db, soul_engine=object()))
    item = client.get("/api/sources/status").json()["reddit"]
    assert item["state"] == "ready"
    assert item["logged_in"] is True
    assert "未实时访问 Reddit" in item["detail"]
```

Change the existing Zhihu parameter case from `(False, 0.1, "completed", "ready", True)` to `(False, 0.1, "completed", "missing", False)`, add no-signal and stale-true cases for XHS/Zhihu, and assert Douyin with a stored Cookie returns `unverified` plus `logged_in=False`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_api_app.py tests/test_api_reddit_tasks.py \
  -k 'sources_status or local_credential' --tb=short
```

Expected: failures show Reddit still calls `probe_reddit_command_backend`, fresh Zhihu false is overwritten by task history, no-signal XHS returns `missing`, stale true collapses to `missing`, and Douyin Cookie returns `ready`.

- [ ] **Step 3: Add the local Reddit adapter**

Add to `reddit_tasks.py`:

```python
def local_reddit_credential_status() -> RedditCommandStatus:
    """Return local rdt credential readiness without running rdt or Reddit requests."""
    state, message = _rdt_saved_credential_state()
    if state == "present":
        return RedditCommandStatus(
            "rdt",
            "ready",
            "Reddit 本地凭据已就绪（未实时访问 Reddit 验证）。",
        )
    if state == "expired":
        return RedditCommandStatus("rdt", "stale", message)
    if state == "missing":
        return RedditCommandStatus("rdt", "login_required", message)
    return RedditCommandStatus("rdt", "error", message)
```

Keep `probe_reddit_command_backend()` unchanged for explicit CLI/discover calls.

- [ ] **Step 4: Implement backend status precedence**

In `sources_status()`:

- distinguish no login-state row by an empty timestamp;
- return `unverified` when there is no XHS signal;
- return `missing` for fresh explicit false;
- return `stale` for expired true;
- apply the same rules to Zhihu and consult task history only when the timestamp is empty;
- return `unverified` for a stored Douyin Cookie;
- call `local_reddit_credential_status()` for `rdt`, never `probe_reddit_command_backend()`.

Update `SourceStatusItem` documentation so `ready`, `unverified`, `login_required`, `error`, `stale`, and `expired_cookie` match the implemented vocabulary.

- [ ] **Step 5: Run focused backend tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_api_app.py tests/test_api_reddit_tasks.py \
  -k 'sources_status or local_credential' --tb=short
```

Expected: all selected tests pass and no command runner is called by `/api/sources/status`.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py \
  src/openbiliclaw/sources/reddit_tasks.py tests/test_api_app.py tests/test_api_reddit_tasks.py
git commit -m "fix: make source status local-only and truthful"
```

---

### Task 2: Refresh local browser login signals on backend reconnect

**Files:**
- Modify: `src/openbiliclaw/api/app.py:3280-3380`
- Modify: `extension/src/background/cookie-sync.ts:555-575`
- Test: `tests/test_api_app.py:4068-4120`
- Test: `extension/tests/cookie-sync.test.ts:94-430`

**Interfaces:**
- Consumes: `syncXhsLoginStateToBackend(source?: string)` and `syncZhihuLoginStateToBackend(source?: string)`.
- Produces: runtime events `xhs_login_state_sync_requested` and `zhihu_login_state_sync_requested`.

- [ ] **Step 1: Write failing runtime-stream tests**

Add a backend WebSocket test that connects with `client=background`, consumes initial messages, and asserts both event types are present. Add extension tests:

```typescript
test("runtime events refresh xhs and zhihu local login states", async () => {
  const { handleCookieSyncRuntimeEvent } = await importCookieSync();
  installChromeMock([
    { name: "web_session", value: "xhs", domain: ".xiaohongshu.com" },
    { name: "z_c0", value: "zh", domain: ".zhihu.com" },
  ]);
  const urls: string[] = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  assert.equal(handleCookieSyncRuntimeEvent({ type: "xhs_login_state_sync_requested" }), true);
  assert.equal(handleCookieSyncRuntimeEvent({ type: "zhihu_login_state_sync_requested" }), true);
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(urls.sort(), [
    "http://127.0.0.1:8420/api/sources/xhs/login-state",
    "http://127.0.0.1:8420/api/sources/zhihu/login-state",
  ]);
});
```

- [ ] **Step 2: Run both tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_api_app.py -k 'runtime_stream_requests_login_state' --tb=short
cd extension && node --test --experimental-strip-types \
  --test-name-pattern='runtime events refresh xhs' tests/cookie-sync.test.ts
```

Expected: backend messages are absent and extension handler returns false.

- [ ] **Step 3: Emit and handle the two localhost-only events**

On every background runtime-stream connect, send:

```python
await websocket.send_json(
    {
        "type": "xhs_login_state_sync_requested",
        "reason": "runtime_stream_connected",
        "source": "runtime-stream",
    }
)
await websocket.send_json(
    {
        "type": "zhihu_login_state_sync_requested",
        "reason": "runtime_stream_connected",
        "source": "runtime-stream",
    }
)
```

Handle each event in `handleCookieSyncRuntimeEvent()` by invoking the matching existing boolean sync with source `runtime-stream-request`. Do not open a tab or call a platform URL.

- [ ] **Step 4: Run both tests and verify GREEN**

Run the same two commands from Step 2. Expected: both pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/openbiliclaw/api/app.py tests/test_api_app.py \
  extension/src/background/cookie-sync.ts extension/tests/cookie-sync.test.ts
git commit -m "fix: refresh source login state on backend reconnect"
```

---

### Task 3: Align desktop and popup status copy

**Files:**
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:4523-4560`
- Modify: `extension/popup/popup.js:6465-6490`
- Modify: `src/openbiliclaw/api/app.py:8030-8130` (XHS credential detail)
- Test: `tests/test_desktop_web_zhihu_settings.py`
- Test: `extension/tests/popup-settings.test.ts`
- Test: `tests/test_api_app.py`

**Interfaces:**
- Consumes: `SourceStatusItem.state` from Task 1.
- Produces: identical labels for every canonical state and explicit XHS token copy.

- [ ] **Step 1: Write failing UI/copy tests**

Assert both JavaScript files contain mappings for `login_required`, `error`, `stale`, and `unverified`, with these labels:

```text
login_required -> 需要登录
error -> 检查失败
stale -> 需要刷新
unverified -> 状态待验证
ready -> 凭据已就绪
```

Assert `/api/sources/credentials` returns XHS detail containing `不代表账号登录`, and popup/desktop static copy contains the same warning.

- [ ] **Step 2: Run UI/copy tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_desktop_web_zhihu_settings.py \
  tests/test_api_app.py -k 'desktop_source_status or sources_credentials' --tb=short
cd extension && node --test --experimental-strip-types \
  --test-name-pattern='source status|xsec_token' tests/popup-settings.test.ts
```

Expected: desktop lacks `login_required`/`error`, popup lacks complete labels, and XHS detail omits the warning.

- [ ] **Step 3: Implement complete mappings and truthful token copy**

Use the same mapping values in both clients. The popup may continue rendering a colored dot rather than a second badge, but its text must prepend the canonical label before detail. Change the XHS credential detail to:

```python
detail="小红书不保存整站 Cookie；xsec_token 只是内容访问令牌，不代表账号登录。"
```

- [ ] **Step 4: Run UI/copy tests and verify GREEN**

Run the same commands from Step 2. Expected: all pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/openbiliclaw/web/desktop/assets/js/app.js extension/popup/popup.js \
  src/openbiliclaw/api/app.py tests/test_desktop_web_zhihu_settings.py \
  extension/tests/popup-settings.test.ts tests/test_api_app.py
git commit -m "fix: align source status copy across settings pages"
```

---

### Task 4: Document and verify the complete local-only contract

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: final behavior from Tasks 1-3.
- Produces: user-facing and maintainer-facing contract matching the code.

- [ ] **Step 1: Update required documentation**

Document:

- XHS/Zhihu runtime-stream local heartbeat refresh;
- fresh false precedence and no-signal/stale distinctions;
- Reddit settings status reads only local credential metadata and never runs `rdt status`;
- Douyin Cookie presence is unverified;
- desktop/popup state labels and XHS token warning;
- settings polling performs no remote platform request.

- [ ] **Step 2: Run focused formatting, lint, typing, tests, and extension build**

Run:

```bash
.venv/bin/ruff format --check \
  src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py \
  src/openbiliclaw/sources/reddit_tasks.py tests/test_api_app.py \
  tests/test_api_reddit_tasks.py tests/test_desktop_web_zhihu_settings.py
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q --tb=short
cd extension && npm test && npm run typecheck && npm run build
```

Expected: every command exits 0. Extension tests may print intentional warning logs from failure-path mocks but must report zero failures.

- [ ] **Step 3: Run localhost-only E2E assertions**

With the existing backend, request `/api/sources/status` repeatedly and confirm each response returns promptly without spawning `rdt status`. Open `/web`, inspect the settings rows, and verify canonical labels. Inspect the installed extension side panel only if the local build is already loaded; do not navigate to platform sites and do not run platform tasks.

- [ ] **Step 4: Check docs and worktree integrity**

```bash
git diff --check
git status --short --ignored
```

Expected: no whitespace errors; only intentional source/test/docs changes and ignored build outputs are present.

- [ ] **Step 5: Commit Task 4**

```bash
git add docs/modules/extension.md docs/modules/config.md docs/changelog.md
git commit -m "docs: document local-only source status semantics"
```
