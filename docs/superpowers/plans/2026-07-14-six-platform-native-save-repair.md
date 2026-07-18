# Six-Platform Native Save Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bilibili, YouTube, Xiaohongshu, Douyin, Zhihu, and Reddit native favorite synchronization produce a proven `synced` or `already_synced` terminal result in the user's real logged-in environment.

**Architecture:** Preserve the existing local-first `SavedSyncService` and durable extension broker. Repair Bilibili's backend Cookie extraction, add a closed diagnostic allow-list at the extension boundary, and update each browser executor with identity-scoped discovery plus a platform-specific postcondition. Use read-only live-page inspection to ground selectors, then TDD fixtures before production edits.

**Tech Stack:** Python 3.11, FastAPI, SQLite, TypeScript, Chrome MV3, DOM/open shadow DOM, same-origin platform HTTP, pytest, node:test, Ruff, MyPy.

## Global Constraints

- `[saved_sync].auto_sync_enabled` remains default `false`.
- Local saved membership is never rolled back because a platform write fails.
- A click or mutation HTTP 2xx is insufficient; every success requires an observable saved postcondition.
- Executors check `already_synced` before mutation and never automatically retry an uncertain write.
- Cookie values, CSRF tokens, signed URL query values, raw DOM, account identifiers, selectors, exception strings, and platform response bodies never cross the safe result boundary.
- The only new failed error codes are `native_content_not_ready`, `native_control_not_found`, `native_dialog_not_opened`, `native_target_not_found`, `native_request_rejected`, and `native_confirmation_not_observed`.
- Unrecognized executor codes normalize to `native_save_failed`.
- Real verification uses the installed extension browser and the durable production job path. Replacement public item IDs require user approval before mutation.
- No version bump, release, tag, push, marketplace publication, or PR is in scope.

---

### Task 1: Safe Stage Diagnostics

**Files:**
- Modify: `extension/src/shared/native-save.ts`
- Modify: `extension/tests/native-save-shared.test.ts`
- Modify: `extension/tests/native-save-task-runner.test.ts`

**Interfaces:**
- Produce `NativeSaveFailureCode`, a closed union of the existing generic/timeout codes and six stage codes.
- `sanitizeNativeSaveResult(value)` preserves a recognized `(failed, code)` pair with a fixed message and collapses unknown pairs to `failed/native_save_failed`.

- [ ] **Step 1: Write the failing allow-list tests**

```ts
for (const code of [
  "native_content_not_ready",
  "native_control_not_found",
  "native_dialog_not_opened",
  "native_target_not_found",
  "native_request_rejected",
  "native_confirmation_not_observed",
] as const) {
  const result = sanitizeNativeSaveResult({
    status: "failed",
    error_code: code,
    error_message: "cookie=must-not-cross",
  });
  assert.equal(result.status, "failed");
  assert.equal(result.error_code, code);
  assert.doesNotMatch(result.error_message, /cookie|must-not-cross/);
}
assert.equal(
  sanitizeNativeSaveResult({ status: "failed", error_code: "selector=.secret" }).error_code,
  "native_save_failed",
);
```

- [ ] **Step 2: Verify RED**

Run: `cd extension && npm test -- --test-name-pattern='native save result'`

Expected: FAIL because every new stage code is currently collapsed to `native_save_failed`.

- [ ] **Step 3: Implement the minimal closed mapping**

Add fixed `SAFE_RESULTS` rows such as:

```ts
"failed:native_control_not_found": {
  status: "failed",
  error_code: "native_control_not_found",
  error_message: "Platform native-save control was not found",
},
```

Select a failed key only when `SAFE_RESULTS[\`failed:${code}\`]` exists. Do not pass executor-provided messages through.

- [ ] **Step 4: Verify GREEN and commit**

Run:

```bash
cd extension
npm test -- --test-name-pattern='native save result|native-save runner'
npm run typecheck
```

Expected: PASS.

Commit: `git commit -am "fix: expose safe native save failure stages"`

---

### Task 2: Tolerant Bilibili Cookie Extraction

**Files:**
- Modify: `src/openbiliclaw/bilibili/api.py`
- Modify: `tests/test_bilibili_api.py`

**Interfaces:**
- Produce `_cookie_value(raw_cookie: str, name: str) -> str` as a module-private exact-name parser.
- `_csrf_token()` requires nonempty exact `SESSDATA` and `bili_jct` values before any POST.

- [ ] **Step 1: Write the real-shape regression test**

```python
def test_csrf_token_tolerates_non_rfc_chrome_cookie_segment() -> None:
    client = BilibiliAPIClient(
        cookie="bad segment; CURRENT_FNVAL=4048; SESSDATA=session; bili_jct=csrf-token"
    )
    assert client._csrf_token() == "csrf-token"


def test_csrf_token_requires_exact_cookie_names() -> None:
    client = BilibiliAPIClient(cookie="MY_SESSDATA=x; old_bili_jct=y")
    with pytest.raises(BilibiliAuthExpiredError):
        client._csrf_token()
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_bilibili_api.py -k 'csrf_token' -q`

Expected: the non-RFC segment case fails with `BilibiliAuthExpiredError`.

- [ ] **Step 3: Implement exact tolerant parsing**

```python
def _cookie_value(raw_cookie: str, name: str) -> str:
    for segment in raw_cookie.split(";"):
        key, separator, value = segment.strip().partition("=")
        if separator and key == name:
            return value.strip()
    return ""
```

Remove the `SimpleCookie`/`CookieError` dependency if no other call site uses it. `_csrf_token()` calls the helper for both required names and raises the existing sanitized auth error when either is empty.

- [ ] **Step 4: Verify GREEN and commit**

Run:

```bash
.venv/bin/pytest tests/test_bilibili_api.py tests/test_saved_sync_bilibili.py -q
.venv/bin/ruff check src/openbiliclaw/bilibili/api.py tests/test_bilibili_api.py
.venv/bin/mypy src/openbiliclaw/bilibili/api.py
```

Expected: PASS.

Commit: `git commit -am "fix: parse browser-style Bilibili cookies"`

---

### Task 3: Read-Only Live Surface Inventory

**Files:**
- Modify: `docs/testing/six-platform-native-save-e2e.md`

**Interfaces:**
- Produce a credential-free table for YouTube, Xiaohongshu, Douyin, Zhihu, and Reddit containing route match, target-root kind, control semantic attributes, dialog/root kind, and saved-state postcondition.

- [ ] **Step 1: Inspect without mutation**

Use the installed logged-in browser. Navigate only to the already authorized public items. Do not click favorite/save controls. Record booleans and semantic attribute names only; do not copy page HTML, text bodies, signed query values, account data, or tokens.

- [ ] **Step 2: Form one hypothesis per platform**

Write one sentence per platform in the E2E document in the form: “The executor fails at `<stage>` because the live surface uses `<semantic difference>` while the implementation requires `<old assumption>`.”

- [ ] **Step 3: Verify the inventory is safe**

Run:

```bash
rg -n 'SESSDATA|bili_jct|xsec_token=|cookie=|authorization:|<html|account_id' docs/testing/six-platform-native-save-e2e.md
```

Expected: no newly added secret/raw-DOM matches.

Do not commit this task separately; commit the evidence with the first selector repair it grounds.

---

### Task 4: YouTube Active-Surface and Playlist Dialog Repair

**Files:**
- Modify: `extension/src/content/native-save/youtube.ts`
- Modify: `extension/tests/youtube-native-save.test.ts`
- Modify: `docs/testing/six-platform-native-save-e2e.md`

**Interfaces:**
- `hasSaveControl()` and `openSaveDialog()` recognize the observed current control only inside the active watch/shorts surface.
- Playlist dialog correlation uses the one newly opened visible dialog containing a playlist renderer.
- Failure stages are control, dialog, target row, request, or confirmation.

- [ ] **Step 1: Add a failing sanitized DOM fixture**

Create a fake active video surface with the semantic structure observed in Task 3. Assert that `saveYouTube()` opens exactly one dialog, clicks the exact `OpenBiliClaw` row once, and returns `synced` only after `isChecked()` becomes true. Add focused cases asserting:

```ts
assert.deepEqual(await saveYouTube(task, fixture({ saveControl: null })), {
  status: "failed", error_code: "native_control_not_found",
});
assert.deepEqual(await saveYouTube(task, fixture({ dialogAvailable: false })), {
  status: "failed", error_code: "native_dialog_not_opened",
});
assert.deepEqual(await saveYouTube(task, fixture({ rows: [] })), {
  status: "failed", error_code: "native_target_not_found",
});
```

- [ ] **Step 2: Verify RED**

Run: `cd extension && npm test -- --test-name-pattern='YouTube native save.*live|YouTube native save.*stage'`

Expected: FAIL at the observed selector/dialog assumption and stage-code assertions.

- [ ] **Step 3: Implement the minimal selector/correlation change**

Restrict candidates to the active content surface and accept the observed `yt-button-shape`, menu-item, aria-label, title, or exact visible-text form. Keep candidate uniqueness. Scope rows to the correlated `ytd-add-to-playlist-renderer`; preserve exact case-sensitive target matching and checked-state confirmation.

- [ ] **Step 4: Verify and commit**

Run:

```bash
cd extension
npm test -- --test-name-pattern='YouTube native save'
npm run typecheck
```

Expected: PASS.

Commit the source, test, and safe evidence: `git commit -m "fix: repair YouTube native playlist save"`.

---

### Task 5: Xiaohongshu Exact Note Navigation Repair

**Files:**
- Modify: `extension/src/background/native-save-task-runner.ts`
- Modify: `extension/src/content/native-save/xiaohongshu.ts`
- Modify: `extension/tests/native-save-task-runner.test.ts`
- Modify: `extension/tests/xhs-native-save.test.ts`
- Modify: `docs/testing/six-platform-native-save-e2e.md`

**Interfaces:**
- The runner may reuse one existing matching platform tab only when its canonical route contains the exact note ID; otherwise it creates a task-owned tab with the validated URL.
- A route redirect that loses the note ID is `unsupported_content_type`; a matching route with no exact content root after readiness is `native_content_not_ready`.

- [ ] **Step 1: Write failing navigation and readiness tests**

```ts
test("XHS reuses only an exact already-open note route", async () => {
  // one tab matches /explore/<content_id>, another is /explore
  // expect the exact tab to be selected and no new task tab to be created
});

assert.deepEqual(await saveXiaohongshu(task, fixture({ contentReady: false })), {
  status: "failed", error_code: "native_content_not_ready",
});
```

Add a redirect-to-landing case that remains `unsupported/unsupported_content_type` and performs no mutation.

- [ ] **Step 2: Verify RED**

Run: `cd extension && npm test -- --test-name-pattern='XHS.*exact already-open|Xiaohongshu.*content ready'`

Expected: FAIL because the runner always creates a tab and the executor returns a generic failure.

- [ ] **Step 3: Implement exact reuse and stage mapping**

Add a platform-specific, identity-exact tab lookup before task-owned creation. Do not reuse a landing/feed tab and do not compare signed query values. In the executor, distinguish route mismatch from matching-route readiness exhaustion.

- [ ] **Step 4: Verify and commit**

Run:

```bash
cd extension
npm test -- --test-name-pattern='native-save runner|Xiaohongshu native save'
npm run typecheck
```

Expected: PASS.

Commit: `git commit -m "fix: preserve exact Xiaohongshu save navigation"`.

---

### Task 6: Douyin Active Player Favorite Repair

**Files:**
- Modify: `extension/src/content/native-save/douyin.ts`
- Modify: `extension/tests/dy-native-save.test.ts`
- Modify: `docs/testing/six-platform-native-save-e2e.md`

**Interfaces:**
- Find one exact favorite control in the observed active player root on `/video/<id>`.
- Recognize selected state from observed aria/data/class/title/text semantics.
- Return `native_content_not_ready`, `native_control_not_found`, `native_request_rejected`, or `native_confirmation_not_observed` at the corresponding stage.

- [ ] **Step 1: Add the observed failing fixture**

Add a fake exact video route where the active player has the live semantic control but no old content identity node. Assert one mutation and selected-state confirmation. Add ambiguity and non-target nested-control cases that perform zero mutations.

- [ ] **Step 2: Verify RED**

Run: `cd extension && npm test -- --test-name-pattern='Douyin native save.*active player'`

Expected: FAIL because the current route fallback does not recognize the observed control/state.

- [ ] **Step 3: Implement the narrow active-player support**

Add only the semantic selectors observed in Task 3. Candidate roots must be visible and belong to the exact current video route; another content identity excludes the candidate. Extend `selected()` only with observed positive state markers, never favorite-count changes.

- [ ] **Step 4: Verify and commit**

Run:

```bash
cd extension
npm test -- --test-name-pattern='Douyin native save'
npm run typecheck
```

Expected: PASS.

Commit: `git commit -m "fix: bind Douyin favorite to the active player"`.

---

### Task 7: Zhihu Portal Dialog Correlation Repair

**Files:**
- Modify: `extension/src/content/native-save/zhihu.ts`
- Modify: `extension/tests/zhihu-native-save.test.ts`
- Modify: `docs/testing/six-platform-native-save-e2e.md`

**Interfaces:**
- Correlate one newly opened visible collection portal even when it is mounted under `document.body` rather than the answer subtree.
- Exact `OpenBiliClaw` row creation/selection remains case-sensitive and uniquely matched.
- Confirmation requires the row's selected state after mutation.

- [ ] **Step 1: Add a failing detached-portal fixture**

Build a fake answer identity and a portal dialog attached outside it. Assert the exact trigger is clicked once, the new portal is accepted, the target row is clicked once, and selection is confirmed. Add two-new-dialog and unrelated-preexisting-dialog cases that fail closed.

- [ ] **Step 2: Verify RED**

Run: `cd extension && npm test -- --test-name-pattern='Zhihu native save.*portal'`

Expected: FAIL because `dialogMatchesIdentity()` currently rejects the detached portal.

- [ ] **Step 3: Implement newly-opened portal correlation**

Capture the set of visible dialogs before the exact target control click. Accept exactly one newly visible collection dialog whose contents match the collection chooser semantics. Do not require portal ancestry under the content identity. Keep the active content identity for trigger and rate-limit correlation.

- [ ] **Step 4: Verify and commit**

Run:

```bash
cd extension
npm test -- --test-name-pattern='Zhihu native save'
npm run typecheck
```

Expected: PASS.

Commit: `git commit -m "fix: correlate Zhihu collection portal"`.

---

### Task 8: Reddit Saved-State API Confirmation

**Files:**
- Modify: `extension/src/content/native-save/reddit.ts`
- Modify: `extension/tests/reddit-native-save.test.ts`
- Modify: `docs/testing/six-platform-native-save-e2e.md`

**Interfaces:**
- Add `fetchSavedState(fullname: string): Promise<"saved" | "unsaved" | "unknown">` to `RedditNativeSaveEnvironment`.
- Browser implementation GETs same-origin `/api/info.json?id=<fullname>&raw_json=1`, validates the exact child fullname, and reads boolean `saved`.
- Confirmation succeeds on exact API `saved=true` or one exact visible `Unsave` control.

- [ ] **Step 1: Add failing API postcondition tests**

```ts
const env = fixture({
  token: "page-modhash",
  responseStatus: 200,
  savedStates: ["unsaved", "saved"],
});
assert.deepEqual(await saveReddit(task, env), { status: "synced" });

const uncertain = fixture({
  token: "page-modhash",
  responseStatus: 200,
  savedStates: ["unknown"],
});
assert.deepEqual(await saveReddit(task, uncertain), {
  status: "failed", error_code: "native_confirmation_not_observed",
});
```

Add parser tests for exact fullname, wrong fullname, missing child, malformed JSON shape, and `saved=false`.

- [ ] **Step 2: Verify RED**

Run: `cd extension && npm test -- --test-name-pattern='Reddit native save.*API confirmation'`

Expected: FAIL because the environment has no saved-state API confirmation.

- [ ] **Step 3: Implement exact same-origin confirmation**

Poll `fetchSavedState()` and `findControl(fullname, "Unsave")` in the existing bounded confirmation loop. Treat 401/403 as unknown and 429 as rate-limited only before an uncertain accepted mutation; never issue a second mutation request. Keep the DOM fallback exact fullname correlation.

- [ ] **Step 4: Verify and commit**

Run:

```bash
cd extension
npm test -- --test-name-pattern='Reddit native save'
npm run typecheck
```

Expected: PASS.

Commit: `git commit -m "fix: confirm Reddit Saved through item state"`.

---

### Task 9: Documentation, Full Verification, Hot Reload, and Real E2E

**Files:**
- Modify: `docs/modules/saved-sync.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/testing/six-platform-native-save-e2e.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Documentation reports automated and real results separately and never claims success for an unverified platform.

- [ ] **Step 1: Update required documentation**

Record the parser repair, stage diagnostics, selector/API postconditions, commands, and credential-free terminal result matrix. Do not change architecture diagrams unless implementation changed the existing module/data flow.

- [ ] **Step 2: Run full automated verification**

Run:

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
.venv/bin/pytest
cd extension
npm test
npm run typecheck
npm run build
```

Expected: every command exits 0 with no test failures or type/lint/build errors.

- [ ] **Step 3: Hot reload the exact artifacts**

Sync `extension/dist` to the installed development extension using the repository's existing cachebuster/reload path. Restart the backend from `.worktrees/native-save-foundation`, then verify `/api/health` and extension connectivity before any mutation.

- [ ] **Step 4: Obtain replacement-item approval where required**

Show the exact public content IDs and targets for any replacement test item, especially a fresh Xiaohongshu note. Do not execute replacement writes before approval.

- [ ] **Step 5: Execute real verification serially**

For each approved platform action:

1. Inspect the already-saved postcondition.
2. Trigger the production durable sync path once.
3. Wait for the terminal job result.
4. Confirm platform postcondition and `native_save_states` terminal row.
5. Stop that platform on uncertain outcome; do not retry blindly.

Accept only `synced` or `already_synced` as success.

- [ ] **Step 6: Commit documentation and final evidence**

Run `git diff --check`, review `git status --short`, and commit only intended documentation/source/tests:

```bash
git commit -m "docs: record native save repair verification"
```

Do not include `.venv`, `extension/node_modules`, `.playwright-cli`, config files, databases, logs, or account artifacts.
