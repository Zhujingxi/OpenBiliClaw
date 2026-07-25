# Six-Platform Native Save Repair Design

## Goal

Repair the real-account native favorite path for Bilibili, YouTube, Xiaohongshu,
Douyin, Zhihu, and Reddit after the 2026-07-13 authorized run exposed one backend
Cookie parsing defect, one stale-navigation case, and four browser-executor failures.
The local favorite/watch-later model, manual sync buttons, and the default-off
automatic-sync setting remain unchanged.

Success means every supported platform action reaches either `synced` or
`already_synced` with a platform-specific postcondition. A successful click or HTTP
response alone is not proof of synchronization.

## Approaches Considered

### 1. Platform-specific execution with explicit postconditions — selected

Keep Bilibili on its backend API adapter and the other five sources in the installed,
logged-in extension. Give each executor a narrow identity check, platform-specific
control or same-origin request path, and a platform-specific confirmation step. Return
safe stage error codes so a future site change identifies the failing boundary without
exposing account data.

This is the smallest change that preserves the current security model and gives reliable
evidence for each platform.

### 2. Convert every platform to private HTTP APIs

Private APIs can avoid fragile DOM selectors, but YouTube, Xiaohongshu, Douyin, and
Zhihu require platform-specific signatures, tokens, or internal request contracts that
change independently. Uploading browser credentials to the backend would also weaken the
existing same-origin security boundary. This option is rejected.

### 3. Use one generic DOM heuristic for all platforms

A shared heuristic could search visible buttons for labels such as Save or 收藏. It would
be concise, but pages containing feeds, comments, menus, and background dialogs make a
generic selector unable to prove that the target content was changed. This option is
rejected.

## Architecture

The durable backend flow remains:

1. Local save membership is persisted.
2. A manual sync or enabled click-time sync resolves the platform adapter.
3. Bilibili executes directly through `BilibiliNativeSaveAdapter`; the five browser
   sources enqueue an `extension_native_save_jobs` row.
4. The installed extension opens or reuses the exact content page, executes the
   platform-specific mutation, and posts a safe terminal result.
5. The backend persists the result in `native_save_states` and exposes it on the local
   saved pages.

No Cookie, CSRF token, signed URL query, raw DOM, account identifier, or platform
response body may enter a job result, log message, or user-visible error.

## Shared Executor Contract

The public result statuses remain unchanged. Browser executors may return these new,
allow-listed diagnostic error codes when `status=failed`:

- `native_content_not_ready`
- `native_control_not_found`
- `native_dialog_not_opened`
- `native_target_not_found`
- `native_request_rejected`
- `native_confirmation_not_observed`

The task runner must reject any unrecognized diagnostic and normalize it to the existing
`native_save_failed`. Diagnostics describe only the failed stage. They contain no
selector, URL, response payload, or exception string.

Readiness and confirmation use bounded condition polling. They do not replay a mutation
while waiting. A second execution first checks the already-saved postcondition and must
return `already_synced` rather than toggle the platform state.

## Platform Repairs

### Bilibili

Replace `SimpleCookie` parsing of the Chrome-style raw Cookie header with a tolerant,
minimal parser that splits semicolon-delimited pairs, partitions each pair at the first
`=`, trims the name/value, and extracts exact `SESSDATA` and `bili_jct` names. Invalid or
unrelated segments are ignored; missing required names still fail before a write.

Regression coverage uses a header where a non-RFC segment precedes valid `SESSDATA` and
`bili_jct`, reproducing the real `-101` failure without including real values.

### YouTube

Keep the exact video route and named target contract. Broaden the save-control discovery
only within the active watch/shorts content surface to cover current `yt-button-shape`,
menu-item, aria-label, and visible-text variants. Correlate the playlist chooser by the
newly opened visible dialog, then locate one exact `OpenBiliClaw` row and confirm its
checked state. A missing control, dialog, row, or checked postcondition returns its own
safe stage error.

### Xiaohongshu

Preserve a fresh validated `xsec_token` and `xsec_source` when present. Reuse an already
open tab whose route contains the exact note ID before navigating. After navigation, the
executor must wait for an exact note identity container; a redirect to `/explore` without
the note ID is `unsupported_content_type`, while a matching route whose content never
renders is `native_content_not_ready`.

The adapter cannot manufacture an expired platform signature. Real verification must use
a currently accessible note URL with a fresh token; stale-token behavior remains a safe,
diagnosable failure rather than a false success.

### Douyin

Bind the task to the exact `/video/<id>` route. Discover the favorite control from the
route's active player surface, supporting current `data-e2e`, aria-label, title, and
visible 收藏 variants while excluding controls nested under another content identity.
Confirm the selected state through `aria-pressed`, active class/data state, or the exact
取消收藏 label. Ambiguous controls fail closed.

### Zhihu

Bind the action to the exact question/answer/article identity. Correlate the collection
chooser by the one newly opened visible portal/dialog instead of requiring that the
portal remain under the answer DOM subtree. Find or create one exact `OpenBiliClaw`
collection and confirm its selected state before returning success. Multiple matching
dialogs, controls, or rows fail closed.

### Reddit

Prefer the same-origin `/api/save` request when a request token is available. After an
accepted request, query the same-origin item-info endpoint and require the exact fullname
to report `saved=true`; the visible `Unsave` control remains an additional confirmation
path for modern `shreddit-*` shadow DOM. A successful mutation response without either
postcondition returns `native_confirmation_not_observed`.

The DOM fallback searches open shadow roots and correlated menu/slot content belonging to
the exact `t1_` or `t3_` identity. It never accepts an unrelated page-level Save control.

## Testing

Each repair follows red-green-refactor:

1. Add a focused regression test reproducing the observed failure.
2. Run that test and confirm the expected failure.
3. Implement the smallest platform-specific fix.
4. Run the focused test and the platform test file.
5. Run backend tests, extension tests, typecheck, build, Ruff, and MyPy.

Browser tests use sanitized HTML fixtures and fake same-origin responses; they never use
real credentials. Diagnostic contract tests prove that only the allow-listed codes cross
the extension/backend boundary.

## Real Verification

After automated checks pass, rebuild and hot-reload the installed development extension,
restart the backend from this worktree, and verify through the production durable task
path in the user's existing logged-in browser.

Use one currently accessible public content item per platform. Before each mutation,
record only platform, action, public content ID, and expected target. Accept only
`synced` or `already_synced`, then confirm the persisted `native_save_states` row. For
Xiaohongshu, select a fresh note URL. Never retry an uncertain mutation blindly; inspect
the postcondition first.

Real writes remain state-changing actions. The existing exact authorization covers the
original named items only; any replacement public content ID must be shown to the user
for approval before execution.

## Documentation

Update `docs/modules/saved-sync.md`, `docs/modules/extension.md`,
`docs/modules/runtime.md`, `docs/testing/six-platform-native-save-e2e.md`, and
`docs/changelog.md`. Update architecture diagrams only if implementation changes the
existing cross-module flow; selector and parser repairs alone do not change architecture.
