# Issue #96 Mobile QR Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser-extension mobile QR panel use the existing lightweight `/api/qr-info` endpoint so opening it never waits for `/api/health` embedding readiness work.

**Architecture:** Preserve the current popup data flow and replace only the loopback LAN-IP lookup endpoint. Lock the client contract with a focused extension regression test, keep existing fallback behavior, then verify the real backend endpoint and the popup-to-live-HTTP browser flow independently.

**Tech Stack:** Browser extension native JavaScript, Node test runner, FastAPI/Pytest, Playwright CLI.

## Global Constraints

- Do not add a new backend endpoint; `GET /api/qr-info` already exists.
- Do not add popup startup prefetching or persistent LAN-IP caching.
- Do not change QR UI, permissions, authentication, timeout, or fallback behavior.
- Preserve all pre-existing staged changes, including overlapping edits in `extension/popup/popup.js` and documentation.
- Update the extension/config module docs and the current changelog block as required by `AGENTS.md`.

---

### Task 1: Lock and migrate the popup QR endpoint

**Files:**
- Modify: `extension/tests/popup-mobile-qr.test.ts`
- Modify: `extension/popup/popup.js:2123-2143`

**Interfaces:**
- Consumes: existing `GET /api/qr-info -> {"lan_ip": string | null}` and `isLoopbackMobileHost(host)`.
- Produces: unchanged `renderMobileQrPanel(): Promise<void>` behavior, with its loopback lookup bound to `/api/qr-info` instead of `/api/health`.

- [ ] **Step 1: Write the failing endpoint-contract test**

Add filesystem imports and this test to `extension/tests/popup-mobile-qr.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("mobile QR LAN-IP lookup uses the lightweight QR endpoint", () => {
  const popupSource = readFileSync(resolve("popup", "popup.js"), "utf8");
  const renderSource =
    popupSource.match(
      /async function renderMobileQrPanel\(\) \{[\s\S]*?\n\}\n\nasync function openMobileQrPanel/,
    )?.[0] ?? "";

  assert.ok(renderSource, "popup.js must keep renderMobileQrPanel");
  assert.match(renderSource, /\/api\/qr-info/);
  assert.doesNotMatch(renderSource, /\/api\/health/);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
node --test --experimental-strip-types tests/popup-mobile-qr.test.ts
```

Workdir: `extension/`.

Expected: FAIL in `mobile QR LAN-IP lookup uses the lightweight QR endpoint` because the function still contains `/api/health` and not `/api/qr-info`.

- [ ] **Step 3: Apply the minimal endpoint migration**

Change only the comment, request URL, and fallback comment inside the loopback branch:

```js
  // When the configured host is loopback, ask the lightweight QR endpoint
  // for the server's detected LAN IP. Unlike /api/health, this endpoint does
  // not wait for embedding readiness before the QR code can be rendered.
  let effectiveEndpoint = endpoint;
  if (isLoopbackMobileHost(endpoint.host)) {
    try {
      const base = `http://${endpoint.host}:${endpoint.port}`;
      const resp = await fetch(`${base}/api/qr-info`, { signal: AbortSignal.timeout(2000) });
      if (resp.ok) {
        const data = await resp.json();
        if (data.lan_ip && !isLoopbackMobileHost(data.lan_ip)) {
          effectiveEndpoint = { ...endpoint, host: data.lan_ip };
        }
      }
    } catch {
      // QR-info fetch failed — fall through with original endpoint.
    }
  }
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
node --test --experimental-strip-types tests/popup-mobile-qr.test.ts
```

Workdir: `extension/`.

Expected: 4 tests PASS.

---

### Task 2: Synchronize user-facing documentation

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: Task 1's `/api/qr-info` popup behavior.
- Produces: documentation that consistently describes the extension and desktop QR path.

- [ ] **Step 1: Correct the extension module references**

In the Side Panel implementation row of `docs/modules/extension.md`, replace:

```text
会先读 `/api/health.lan_ip` 并用局域网 IP 生成二维码
```

with:

```text
会先读轻量端点 `/api/qr-info.lan_ip`（不触发 embedding readiness probe）并用局域网 IP 生成二维码
```

In the detailed mobile QR paragraph, replace the loopback-only warning sentence with:

```text
当前 host 仍是 `127.0.0.1` / `localhost` 时，插件会先通过轻量 `/api/qr-info` 读取后端探测到的局域网 IP 并替换二维码 host；端点失败或没有有效 LAN IP 才保留 loopback URL 与警告。
```

- [ ] **Step 2: Correct the config module reference**

In `docs/modules/config.md`, change:

```text
读取 `/api/health.lan_ip`
```

to:

```text
读取轻量端点 `/api/qr-info.lan_ip`（不触发 embedding readiness probe）
```

- [ ] **Step 3: Attribute the fix in the current changelog block**

In the existing v0.3.161 frontend/QR bullet in `docs/changelog.md`, change:

```text
手机版二维码改走轻量 `GET /api/qr-info`
```

to:

```text
插件手机版二维码（Issue #96）改走轻量 `GET /api/qr-info`
```

Keep every other already-staged PR #97 sentence in that bullet unchanged.

- [ ] **Step 4: Verify stale documentation is gone**

Run:

```bash
rg -n "二维码.*api/health|api/health\.lan_ip|qr-info" docs/modules/extension.md docs/modules/config.md docs/changelog.md
```

Expected: the phone QR behavior points to `/api/qr-info`; no QR-specific `/api/health.lan_ip` reference remains.

---

### Task 3: Run automated and browser end-to-end verification

**Files:**
- Verify: `extension/popup/popup.js`
- Verify: `extension/tests/popup-mobile-qr.test.ts`
- Verify: `tests/test_api_app.py`
- Verify: `tests/test_api_auth.py`

**Interfaces:**
- Consumes: completed Tasks 1-2.
- Produces: evidence that the extension contract, backend contract, build, and actual browser click flow all pass.

- [ ] **Step 1: Run extension regression, type, and build checks**

Run:

```bash
npm test
npm run typecheck
npm run build
```

Workdir: `extension/`.

Expected: all extension tests PASS; TypeScript reports no errors; the Chrome extension build completes.

- [ ] **Step 2: Run backend QR endpoint checks**

Run:

```bash
pytest tests/test_api_app.py -k "qr_info" -v
pytest tests/test_api_auth.py -k "qr_info" -v
```

Expected: the QR-info endpoint returns `lan_ip`, skips embedding readiness, remains available in degraded mode, and remains public when API auth is enabled.

- [ ] **Step 3: Start a live QR stub and static popup server**

In separate terminal processes, run:

```bash
python -c 'from fastapi import FastAPI; from fastapi.middleware.cors import CORSMiddleware; import uvicorn; app=FastAPI(); app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]); app.get("/api/qr-info")(lambda: {"lan_ip":"192.168.99.7"}); uvicorn.run(app, host="127.0.0.1", port=8420)'
python -m http.server 4173 --directory extension
```

Expected: `curl http://127.0.0.1:8420/api/qr-info` returns `{"lan_ip":"192.168.99.7"}` and `http://127.0.0.1:4173/popup/popup.html` loads.

- [ ] **Step 4: Exercise the real popup DOM with Playwright CLI**

Set the wrapper and open the served popup:

```bash
export PWCLI="$HOME/.codex/skills/playwright/scripts/playwright_cli.sh"
bash "$PWCLI" --session issue96 open http://127.0.0.1:4173/popup/popup.html
bash "$PWCLI" --session issue96 snapshot
```

Click the fresh snapshot reference for the “手机版” button, take another snapshot, and inspect network activity. `eX` below is intentionally runtime-bound because Playwright CLI allocates refs only in the immediately preceding snapshot; replace it with the actual “手机版” ref printed by that snapshot:

```bash
bash "$PWCLI" --session issue96 click eX
bash "$PWCLI" --session issue96 snapshot
bash "$PWCLI" --session issue96 network
```

Expected: the overlay is visible, its URL text is `http://192.168.99.7:8420/m/`, an SVG QR code is rendered, and the click triggers `GET http://127.0.0.1:8420/api/qr-info` with status 200. The focused source contract separately proves that `renderMobileQrPanel()` contains no `/api/health` request.

- [ ] **Step 5: Review the scoped diff and commit only Issue #96 files**

Run:

```bash
git diff -- extension/popup/popup.js extension/tests/popup-mobile-qr.test.ts docs/modules/extension.md docs/modules/config.md docs/changelog.md
git diff --check
```

Expected: only the endpoint migration, regression test, and required documentation changes appear for Issue #96; no whitespace errors.

Commit only these paths without including unrelated staged work:

```bash
git commit --only extension/popup/popup.js extension/tests/popup-mobile-qr.test.ts docs/modules/extension.md docs/modules/config.md docs/changelog.md -m "fix(extension): speed up mobile QR for issue 96"
```
