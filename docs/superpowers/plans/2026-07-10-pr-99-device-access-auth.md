# PR 99 Device Access Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PR #99's forgeable Extension-Origin authentication with an opt-in, hashed device access key flow that issues short-lived sessions and works end to end in the real browser extension.

**Architecture:** The backend stores only device-key hashes and exposes a rate-limited exchange endpoint when `extension_access_enabled=true`. Extension HTTP requests use a shared Bearer-session wrapper; only WebSocket and image-proxy URLs receive the short-lived session query token. Popup and service worker share storage keys but keep context-specific adapters.

**Tech Stack:** Python 3.12, FastAPI/Starlette, Typer, pytest, TypeScript/ES modules, Chrome/Firefox MV3 APIs, Node test runner, Playwright CLI.

## Global Constraints

- `extension_access_enabled` defaults to `false`; local loopback extension and current Web auth behavior remain unchanged.
- Persist only `key_id:sha256(secret)` on the backend; never persist the device secret there.
- Never persist the backend password in extension storage.
- Device keys never enter URLs or logs; normal HTTP session tokens use `Authorization: Bearer`.
- Query session tokens are restricted to `/api/image-proxy` and `/api/runtime-stream`.
- Remove automatic Docker default-gateway trust; proxy trust remains explicit through `trusted_proxies`.
- Public endpoints require HTTPS; HTTP is allowed only for loopback, RFC1918 IPv4, `.local`, and `.lan` hosts.
- Every production behavior change follows RED -> GREEN -> focused regression before commit.
- Keep PR #99 author's commit in branch history and preserve all unrelated latest-main changes.

---

### Task 1: Integrate Latest Main And Establish The Known-Red Baseline

**Files:**
- Modify by merge: repository state from `origin/main`
- Resolve: `docs/changelog.md`

**Interfaces:**
- Consumes: PR #99 commit `75b2ac15` and current `origin/main`
- Produces: takeover branch containing both histories with only the changelog conflict resolved

- [ ] **Step 1: Merge current main**

Run:

```bash
git fetch origin
git merge origin/main
```

Expected: one content conflict in `docs/changelog.md`; no source-code conflicts.

- [ ] **Step 2: Resolve the changelog conflict**

Keep the current `main` version block at the top and append PR #99's device-auth entry under that same current version block. Remove all conflict markers without dropping either side's entries.

Run:

```bash
rg -n '^(<<<<<<<|=======|>>>>>>>)' docs/changelog.md
git diff --check
```

Expected: no matches and exit code 0.

- [ ] **Step 3: Install branch dependencies**

Run:

```bash
uv sync --extra dev
cd extension
npm ci
```

Expected: Python and extension dependencies install without changing tracked manifests or lock files.

- [ ] **Step 4: Reproduce the known PR failures before rewriting**

Run:

```bash
uv run --extra dev pytest -q tests/test_api_auth.py tests/test_auth_core.py tests/test_config.py
cd extension
node --test --experimental-strip-types tests/popup-api.test.ts
```

Expected: backend legacy tests pass; `popup-api.test.ts` completes its existing assertions but fails to terminate because PR #99 introduced an async token read before the timeout-controlled fetch.

- [ ] **Step 5: Commit the merge resolution**

```bash
git add docs/changelog.md
git commit
```

Expected merge commit message: `Merge origin/main into codex/pr-99-device-auth`.

---

### Task 2: Device-Key Primitives And Safe Configuration

**Files:**
- Modify: `src/openbiliclaw/auth_core.py`
- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`
- Test: `tests/test_auth_core.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `generate_extension_access_key() -> tuple[str, str, str]`
- Produces: `parse_extension_access_key(value: str) -> tuple[str, str] | None`
- Produces: `verify_extension_access_key(value: str, records: Iterable[str]) -> bool`
- Produces: `extension_access_key_ids(records: Iterable[str]) -> list[str]`
- Produces: `ApiAuthConfig.extension_access_enabled`, `.extension_access_keys`, `.extension_token_ttl_hours`

- [ ] **Step 1: Write failing primitive tests**

Add tests that require this contract:

```python
def test_extension_access_key_generation_stores_only_digest() -> None:
    key_id, full_key, record = auth_core.generate_extension_access_key()
    assert full_key.startswith(f"obc_ext_{key_id}.")
    assert full_key not in record
    assert record.startswith(f"{key_id}:")
    assert auth_core.verify_extension_access_key(full_key, [record]) is True


def test_extension_access_key_rejects_malformed_unknown_and_wrong_secret() -> None:
    key_id, full_key, record = auth_core.generate_extension_access_key()
    assert auth_core.verify_extension_access_key("not-a-key", [record]) is False
    assert auth_core.verify_extension_access_key(full_key.replace(key_id, "f" * 12), [record]) is False
    assert auth_core.verify_extension_access_key(full_key + "x", [record]) is False
```

Also assert `_TRUSTED_LOCAL_IPS == {"127.0.0.1", "::1"}` and remove tests that expect a detected gateway.

- [ ] **Step 2: Write failing config tests**

Require defaults and round-trip behavior:

```python
assert cfg.api.auth.extension_access_enabled is False
assert cfg.api.auth.extension_access_keys == []
assert cfg.api.auth.extension_token_ttl_hours == 24
```

Set two records, enable the switch, save/reload, and assert exact preservation. Add invalid TTL cases `0`, `169`, and non-numeric values; they must normalize to `24`. Add `config.local.toml` provenance assertions for all three fields.

- [ ] **Step 3: Verify RED**

Run:

```bash
uv run --extra dev pytest -q tests/test_auth_core.py tests/test_config.py -k 'extension_access or trusted_local_ips'
```

Expected: failures for missing functions/config fields and gateway still being trusted.

- [ ] **Step 4: Implement minimal primitives and config**

Use the following production shape:

```python
EXTENSION_ACCESS_KEY_PREFIX = "obc_ext_"


def generate_extension_access_key() -> tuple[str, str, str]:
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    full_key = f"{EXTENSION_ACCESS_KEY_PREFIX}{key_id}.{secret}"
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return key_id, full_key, f"{key_id}:{digest}"
```

Parsing must require exactly 12 lowercase hex characters for `key_id`, a non-empty secret, one record separator, and a 64-character lowercase hex digest. Compare digests with `hmac.compare_digest()`.

Remove `_detect_default_gateway`, `_GATEWAY_IP`, `socket`, and `struct`. Restore `_TRUSTED_LOCAL_IPS = _LOOPBACK`.

Replace PR #99 config fields with:

```python
extension_access_enabled: bool = False
extension_access_keys: list[str] = field(default_factory=list)
extension_token_ttl_hours: int = 24
```

Add load/save/provenance rendering. Do not add environment overrides for these three fields.

- [ ] **Step 5: Verify GREEN and regressions**

```bash
uv run --extra dev pytest -q tests/test_auth_core.py tests/test_config.py
uv run --extra dev ruff check src/openbiliclaw/auth_core.py src/openbiliclaw/config.py tests/test_auth_core.py tests/test_config.py
```

- [ ] **Step 6: Commit**

```bash
git add src/openbiliclaw/auth_core.py src/openbiliclaw/config.py config.example.toml tests/test_auth_core.py tests/test_config.py
git commit -m "feat(auth): add hashed extension device keys"
```

---

### Task 3: Token Exchange And Transport Boundaries

**Files:**
- Modify: `src/openbiliclaw/api/auth.py`
- Test: `tests/test_api_auth.py`

**Interfaces:**
- Consumes: `verify_extension_access_key()` and new `ApiAuthConfig` fields
- Produces: `POST /api/auth/extension-token`
- Produces: `AuthGate.pick_token()` that accepts extension Bearer headers but not general query tokens

- [ ] **Step 1: Write failing exchange tests**

Extend the auth test app helper with the three new config fields. Add tests for:

```python
def test_extension_token_exchange_is_disabled_by_default(...):
    response = remote.post("/api/auth/extension-token", json={"key": full_key})
    assert response.status_code == 403
    assert response.json()["error"] == "extension_access_disabled"


def test_extension_token_exchange_issues_finite_session(...):
    response = remote.post("/api/auth/extension-token", json={"key": full_key})
    assert response.status_code == 200
    token = response.json()["token"]
    assert auth_core.token_expires_at(token) is not None
```

Also cover auth disabled, malformed/unknown/wrong key returning the same `401 invalid_device_key`, rate limiting, and spoofed `Origin: chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` without a device key.

- [ ] **Step 2: Write failing transport-boundary tests**

Require:

- `Authorization: Bearer` from `chrome-extension://...` succeeds.
- `?token=` on `/api/favorites/...` returns 401.
- `?token=` on `/api/image-proxy` reaches the handler.
- WebSocket query token with extension Origin is accepted.
- existing Web cookie CSRF and `allowed_bearer_origins` tests remain unchanged.

- [ ] **Step 3: Verify RED**

```bash
uv run --extra dev pytest -q tests/test_api_auth.py -k 'extension_token or extension_bearer or query_token'
```

Expected: endpoint missing and PR #99 still accepts extension query tokens on general APIs.

- [ ] **Step 4: Implement the exchange endpoint and token rules**

Add `/api/auth/extension-token` to `_is_public()`. Reuse the existing per-IP limiter and return stable error codes. Sign with:

```python
token = auth_core.sign_token(
    gate.auth.session_secret,
    epoch=gate.current_epoch(),
    ttl_hours=gate.auth.extension_token_ttl_hours,
)
```

Restore `/api/auth/login` to Web-only behavior. Remove `_extension_allowed()` and ID allow-list logic. Split bearer parsing from query parsing so HTTP middleware only reads query token for `/api/image-proxy`. WebSocket reads its query token explicitly after validating that the Origin is an extension or an existing allowed bearer origin.

- [ ] **Step 5: Verify GREEN and full auth regressions**

```bash
uv run --extra dev pytest -q tests/test_api_auth.py tests/test_auth_core.py tests/test_config.py
uv run --extra dev ruff check src/openbiliclaw/api/auth.py tests/test_api_auth.py
```

- [ ] **Step 6: Commit**

```bash
git add src/openbiliclaw/api/auth.py tests/test_api_auth.py
git commit -m "feat(auth): exchange device keys for short sessions"
```

---

### Task 4: CLI Device-Key Lifecycle

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `generate_extension_access_key()` and `extension_access_key_ids()`
- Produces: `ext-key generate|enable|disable|list|revoke`

- [ ] **Step 1: Write failing CLI tests**

Use `CliRunner` and a temporary project root to assert:

- `generate` persists one hash record, prints the full key once, and leaves the switch off.
- `list` prints key IDs but neither digest nor secret.
- `enable` fails with no keys and succeeds with one key.
- `disable` preserves records.
- `revoke` removes only the requested ID and bumps `auth_epoch`.
- failed epoch bump restores the previous config file and exits non-zero.
- env-managed and `config.local.toml`-shadowed auth config refuse writes.

- [ ] **Step 2: Verify RED**

```bash
uv run --extra dev pytest -q tests/test_cli.py -k 'ext_key'
```

Expected: old RSA/manifest-ID commands do not satisfy the new output and persistence assertions.

- [ ] **Step 3: Replace the PR #99 CLI implementation**

Delete OpenSSL/RSA generation and `add/remove/status`. Implement the five approved commands. `revoke` must snapshot the config file, persist removal, call `_bump_auth_epoch(cfg)`, and restore the snapshot if bumping fails.

Never print `extension_access_keys`; list only parsed IDs.

- [ ] **Step 4: Verify GREEN**

```bash
uv run --extra dev pytest -q tests/test_cli.py -k 'ext_key or set_password'
uv run --extra dev ruff check src/openbiliclaw/cli.py tests/test_cli.py
```

- [ ] **Step 5: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat(cli): manage extension device access keys"
```

---

### Task 5: Remote Endpoint Scheme And Optional Permissions

**Files:**
- Modify: `extension/manifest.json`
- Modify: `extension/manifest.firefox.json`
- Modify: `extension/src/shared/backend-endpoint.ts`
- Modify: `extension/popup/popup-backend-config.js`
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/backend-endpoint.test.ts`
- Test: `extension/tests/popup-settings.test.ts`
- Test: `extension/tests/manifest-assets.test.ts`

**Interfaces:**
- Produces: `BackendEndpoint { scheme: "http" | "https"; host: string; port: number }`
- Produces: `requestBackendPermission(endpoint, permissionsApi) -> Promise<boolean>`
- Produces: `updateBackendEndpoint(scheme, host, port, options?)`

- [ ] **Step 1: Write failing endpoint and permission tests**

Require old storage rows without `scheme` to migrate to `http`, HTTPS to derive WSS, public HTTP to throw `https_required`, private HTTP to pass, and permission requests to use `https://host/*`. WebExtension match patterns cannot portably scope permissions by port (Firefox ignores port-qualified patterns), while endpoint requests remain pinned to the configured port.

Add manifest assertions:

```typescript
assert.deepEqual(manifest.optional_host_permissions, ["http://*/*", "https://*/*"]);
assert.equal(manifest.host_permissions.includes("http://*/*"), false);
```

Add a popup settings contract for a protocol `<select id="cfgBackendScheme">`.

- [ ] **Step 2: Verify RED**

```bash
cd extension
node --test --experimental-strip-types tests/backend-endpoint.test.ts tests/popup-settings.test.ts tests/manifest-assets.test.ts
```

- [ ] **Step 3: Implement scheme validation and exact permission requests**

Define private HTTP hosts as `localhost`, `127.0.0.0/8`, RFC1918 IPv4, `.local`, and `.lan`. Public IPs and all other hostnames require HTTPS.

Both endpoint modules must generate the same origin. Popup `updateBackendEndpoint()` requests permission before storing; denied permission throws `backend_permission_denied` and leaves cached/storage endpoint unchanged.

- [ ] **Step 4: Wire the scheme selector**

Populate/save `cfgBackendScheme` beside host and port. Error messages distinguish invalid endpoint, HTTPS required, and permission denied.

- [ ] **Step 5: Verify GREEN and builds**

```bash
cd extension
node --test --experimental-strip-types tests/backend-endpoint.test.ts tests/popup-settings.test.ts tests/manifest-assets.test.ts
npm run typecheck
npm run build
npm run build:firefox
```

- [ ] **Step 6: Commit**

```bash
git add extension/manifest.json extension/manifest.firefox.json extension/src/shared/backend-endpoint.ts extension/popup/popup-backend-config.js extension/popup/popup.html extension/popup/popup.js extension/tests/backend-endpoint.test.ts extension/tests/popup-settings.test.ts extension/tests/manifest-assets.test.ts
git commit -m "feat(extension): request exact remote backend permissions"
```

---

### Task 6: Extension Session Store And Authenticated Fetch Core

**Files:**
- Modify: `extension/src/shared/token-store.ts`
- Modify: `extension/src/shared/auth.ts`
- Modify: `extension/src/shared/backend-endpoint.ts`
- Create: `extension/tests/device-auth.test.ts`

**Interfaces:**
- Produces: `DeviceSession { token: string; expires_at: number }`
- Produces: `ensureSession(options?) -> Promise<string | null>`
- Produces: `getSessionToken() -> Promise<string | null>`
- Produces: `authenticatedFetch(url, init?, fetchImpl?) -> Promise<Response>`
- Produces: `clearSession()` and `clearLegacyCredentials()`

- [ ] **Step 1: Write failing storage and exchange tests**

Cover structured session load/save, pre-refresh within 60 seconds, missing device key, invalid exchange, and deletion of `obc_auth_password` / `obc_auth_token`.

- [ ] **Step 2: Write failing fetch tests**

Require this behavior:

```typescript
const response = await authenticatedFetch("https://backend/api/runtime-status", {}, fetchImpl);
assert.equal(calls[0].url.includes("token="), false);
assert.equal(calls[0].init.headers.authorization, "Bearer session-1");
```

Add 401 single retry, two concurrent 401s causing one exchange, and second 401 returning without recursion.

- [ ] **Step 3: Verify RED**

```bash
cd extension
node --test --experimental-strip-types tests/device-auth.test.ts
```

- [ ] **Step 4: Implement the storage and single-flight exchange**

Use storage keys `obc_extension_device_key` and `obc_auth_session`. Keep one module-level `refreshInFlight: Promise<string | null> | null`. `ensureSession({force:true})` calls `/api/auth/extension-token` with the key in JSON body and never logs it.

`authenticatedFetch()` clones headers, adds Bearer, performs one request, force-refreshes only after 401, and replays once.

Remove token imports from `backend-endpoint.ts`; `apiUrl()` becomes a pure URL builder and `wsUrl(path, token?)` appends only an explicitly supplied short session.

- [ ] **Step 5: Verify GREEN**

```bash
cd extension
node --test --experimental-strip-types tests/device-auth.test.ts tests/backend-endpoint.test.ts
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add extension/src/shared/token-store.ts extension/src/shared/auth.ts extension/src/shared/backend-endpoint.ts extension/tests/device-auth.test.ts extension/tests/backend-endpoint.test.ts
git commit -m "feat(extension): add refreshable device sessions"
```

---

### Task 7: Adopt Bearer Sessions Across Background Requests

**Files:**
- Modify: `extension/src/background/service-worker.ts`
- Modify: `extension/src/background/bili-task-dispatcher.ts`
- Modify: `extension/src/background/cookie-sync.ts`
- Modify: `extension/src/background/debug-log.ts`
- Modify: `extension/src/background/dy-task-dispatcher.ts`
- Modify: `extension/src/background/e2e-runner.ts`
- Modify: `extension/src/background/reddit-task-dispatcher.ts`
- Modify: `extension/src/background/xhs-task-dispatcher.ts`
- Modify: `extension/src/background/yt-task-dispatcher.ts`
- Modify: `extension/src/background/zhihu-task-dispatcher.ts`
- Modify: `extension/src/content/douyin.ts`
- Test: `extension/tests/device-auth.test.ts`
- Test: `extension/tests/service-worker-stream.test.ts`
- Create: `extension/tests/authenticated-fetch-adoption.test.ts`

**Interfaces:**
- Consumes: `authenticatedFetch()`, `ensureSession()`, `getSessionToken()`, `clearSession()`
- Produces: all protected extension HTTP calls using Bearer sessions

- [ ] **Step 1: Write a failing adoption contract**

Scan the listed files and fail when protected code still contains `fetch(await apiUrl(`. Allow only `/ping` and `/health` raw probes. Add a runtime-stream test requiring the explicit short session argument in `wsUrl()`.

- [ ] **Step 2: Verify RED**

```bash
cd extension
node --test --experimental-strip-types tests/authenticated-fetch-adoption.test.ts tests/service-worker-stream.test.ts
```

- [ ] **Step 3: Replace protected fetch calls**

Apply this exact pattern in each listed file:

```typescript
const response = await authenticatedFetch(await apiUrl("/protected-path"), {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});
```

Leave health probes raw. Startup calls `ensureSession()` before runtime-stream connection. Runtime stream calls `wsUrl(path, await ensureSession())`. Endpoint changes clear the short session before reconnecting.

- [ ] **Step 4: Verify GREEN and background regressions**

```bash
cd extension
node --test --experimental-strip-types tests/authenticated-fetch-adoption.test.ts tests/service-worker-stream.test.ts tests/service-worker-buffer.test.ts tests/*task-dispatcher.test.ts tests/cookie-sync.test.ts
npm run typecheck
```

- [ ] **Step 5: Commit**

```bash
git add extension/src extension/tests/authenticated-fetch-adoption.test.ts extension/tests/device-auth.test.ts extension/tests/service-worker-stream.test.ts
git commit -m "refactor(extension): authenticate background API calls"
```

---

### Task 8: Popup Device Pairing, Bearer Requests, WebSocket And Images

**Files:**
- Create: `extension/popup/popup-device-auth.js`
- Modify: `extension/popup/popup-ext-login.js`
- Modify: `extension/popup/popup-api.js`
- Modify: `extension/popup/popup-stream.js`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup.html`
- Test: `extension/tests/popup-api.test.ts`
- Test: `extension/tests/popup-stream.test.ts`
- Create: `extension/tests/popup-device-auth.test.ts`
- Modify: `extension/tests/popup-settings.test.ts`

**Interfaces:**
- Produces: `pairDeviceKey()`, `ensurePopupSession()`, `popupAuthenticatedFetch()`, and `readPopupSessionToken()` in `popup-device-auth.js`
- Produces: popup device-key pairing UI in `popup-ext-login.js`
- Consumes: the same storage keys and backend endpoint contract as the service worker

- [ ] **Step 1: Write failing popup auth tests**

Require device-key copy, no password label/storage, stable error messages, structured session persistence, Authorization Header, one 401 refresh, and no token in ordinary URLs.

Add a regression that runs the existing final `updateConfig uses the shared 60s config PUT timeout` test and must terminate.

- [ ] **Step 2: Write failing WebSocket and image tests**

Require `createRuntimeStreamUrl(base, sessionToken)` and image-proxy URL generation to append only the short session token. Ordinary recommendation/config URLs must contain no token.

- [ ] **Step 3: Verify RED**

```bash
cd extension
node --test --experimental-strip-types tests/popup-device-auth.test.ts tests/popup-api.test.ts tests/popup-stream.test.ts tests/popup-settings.test.ts
```

Expected: old password storage/query-token code fails and `popup-api.test.ts` still hangs before the fix.

- [ ] **Step 4: Implement popup pairing and request wrapper**

Create `popup-device-auth.js` as the popup-context storage/exchange/fetch adapter. Replace password copy with “设备访问密钥”. Pair against `/auth/extension-token`, save `obc_extension_device_key` plus structured session, clear legacy credentials, and reload runtime stream.

Move token/session read before timeout creation in `requestJson()` so storage latency cannot consume the fetch timeout or leave tests waiting on an already-aborted signal. Add Bearer through headers and retry once after 401.

Update image URL builders and popup runtime stream to call a session-token reader; never use the long device key.

- [ ] **Step 5: Verify GREEN and full popup regression**

```bash
cd extension
node --test --experimental-strip-types tests/popup-device-auth.test.ts tests/popup-api.test.ts tests/popup-stream.test.ts tests/popup-settings.test.ts tests/popup-saved-surfaces-e2e.test.ts
```

- [ ] **Step 6: Commit**

```bash
git add extension/popup extension/tests/popup-device-auth.test.ts extension/tests/popup-api.test.ts extension/tests/popup-stream.test.ts extension/tests/popup-settings.test.ts
git commit -m "feat(extension): pair remote devices without passwords"
```

---

### Task 9: Required Documentation And Architecture Synchronization

**Files:**
- Modify: `docs/modules/api-auth.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`
- Modify: `config.example.toml`

**Interfaces:**
- Documents: default-off switch, CLI, exchange endpoint, storage, transport, proxy trust, endpoint permissions, and operational recovery

- [ ] **Step 1: Replace the inaccurate PR #99 security claims**

Remove “Extension ID key”, “Origin double factor”, RSA manifest key, universal query-token, and Docker gateway trust language. Document long-lived device keys versus short-lived sessions.

- [ ] **Step 2: Synchronize every required architecture surface**

Update `docs/architecture.md`, `docs/spec.md` section 3, and both README diagrams with this flow:

```text
CLI generate -> hashed device key in config
extension device key -> /api/auth/extension-token -> short session
HTTP -> Authorization Bearer
WS/image -> short session query
```

- [ ] **Step 3: Update module, CLI, config, and changelog references**

Record exact command names, defaults, errors, TTL range, explicit `trusted_proxies`, and the lack of real Docker E2E on this machine.

- [ ] **Step 4: Verify docs and commit**

```bash
rg -n "allowed_extension_ids|verify_extension_id|manifest key|Docker 网关 IP|统一通过.*token" README.md README_EN.md docs config.example.toml
git diff --check
```

Expected: no stale feature claims; only historical changelog text may mention the superseded PR implementation if explicitly labeled as replaced.

```bash
git add README.md README_EN.md config.example.toml docs
git commit -m "docs: document extension device access auth"
```

---

### Task 10: Full Verification And Real-Browser LAN E2E

**Files:**
- Create artifacts only: `output/playwright/pr99-device-auth/`
- No tracked production changes unless a reproduced failure requires a new RED/GREEN cycle

**Interfaces:**
- Verifies: complete backend, CLI, Chrome/Firefox extension build, real LAN auth path, refresh, revoke, WS, and image proxy

- [ ] **Step 1: Run complete static and automated checks**

```bash
uv run --extra dev ruff check src/ tests/
uv run --extra dev mypy src/
uv run --extra dev pytest -q
cd extension
npm run typecheck
npm test
npm run build
npm run build:firefox
```

Expected: all PR-relevant checks pass. If the known latest-main Reddit assertion remains, reproduce it on an untouched `origin/main` worktree, record it, then rerun branch pytest with only that exact test deselected; do not hide any new failure.

- [ ] **Step 2: Prepare a real backend on the LAN path**

Use `PORT=8431` after confirming it is free with `lsof -nP -iTCP:8431 -sTCP:LISTEN`. Create a temporary project root under `/tmp/openbiliclaw-pr99-e2e`, generate a key with the real CLI, enable auth and extension access, set `trust_loopback=false`, bind `0.0.0.0:$PORT`, and confirm unauthenticated `http://192.168.31.98:$PORT/api/config` returns 401.

- [ ] **Step 3: Launch Chromium with the built unpacked extension**

Write an untracked Playwright CLI config in `output/playwright/pr99-device-auth/cli.config.json`:

```json
{
  "browser": {
    "browserName": "chromium",
    "isolated": false,
    "userDataDir": "output/playwright/pr99-device-auth/profile",
    "launchOptions": {
      "channel": "chrome",
      "headless": false,
      "args": [
        "--disable-extensions-except=/Users/white/workspace/OpenBiliClaw/.worktrees/pr-99-device-auth/extension",
        "--load-extension=/Users/white/workspace/OpenBiliClaw/.worktrees/pr-99-device-auth/extension"
      ]
    }
  }
}
```

Launch with the bundled wrapper through `bash` because the local wrapper lacks executable permission:

```bash
bash "$HOME/.codex/skills/playwright/scripts/playwright_cli.sh" -s=pr99 open about:blank --config output/playwright/pr99-device-auth/cli.config.json --headed --persistent
```

Extract `EXTENSION_ID` from `page.context().serviceWorkers()`, navigate to `chrome-extension://$EXTENSION_ID/popup/popup.html`, and use snapshots before each referenced click/fill.

- [ ] **Step 4: Exercise the full pairing and data path**

Set scheme `http`, host `192.168.31.98`, and the chosen port. Click save to trigger the exact optional-origin permission request, pair with the generated device key, and verify the popup reaches authenticated backend data.

Use Playwright CLI `requests`, `request-headers`, and `request` to assert:

- `/api/auth/extension-token` is called once for initial pairing.
- protected HTTP requests contain `Authorization: Bearer`.
- protected HTTP URLs contain no `token=` query.
- `/api/image-proxy` and `/api/runtime-stream` use only the short session query.
- runtime WebSocket is connected and an actual backend response renders.

If Chromium's browser-chrome optional-permission bubble cannot be controlled through Playwright page automation, record that limitation, grant only the exact LAN Origin in an untracked E2E manifest copy, rerun the same real LAN data path, and keep the exact permission-request behavior covered by Task 5's automated tests.

- [ ] **Step 5: Verify refresh and revoke in the real environment**

Advance `auth_epoch` or replace the stored session with an invalid value. Trigger a protected popup request and verify one token exchange plus one replay restores data.

Capture `KEY_ID` from `openbiliclaw ext-key list`, run `openbiliclaw ext-key revoke "$KEY_ID"`, verify the existing session receives 401 and refresh receives `invalid_device_key`, then generate/enable a replacement key and re-pair successfully.

- [ ] **Step 6: Capture evidence and secret scan**

Save desktop and compact popup screenshots, request summaries, backend log excerpts, and a short result file under `output/playwright/pr99-device-auth/`. Redact the device key and session before persisting artifacts.

Run:

```bash
rg -n "obc_ext_[a-f0-9]{12}\\.|Authorization: Bearer [A-Za-z0-9_-]+" output/playwright/pr99-device-auth /tmp/openbiliclaw-pr99-* || true
git status --short
git diff --check HEAD
```

Expected: no secrets in artifacts, only intended tracked changes/commits, and no whitespace errors.

- [ ] **Step 7: Final commit only if verification required tracked fixes**

Any newly found defect must first receive a failing regression test, then a minimal fix, focused green run, and a conventional commit. Do not create an empty “verification” commit.
