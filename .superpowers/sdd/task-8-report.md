# Task 8 Report: Four-Surface Save, Sync, And Configuration UI

## Status

Implemented Task 8 in the commit containing this report
(`feat: add saved item sync controls`).

The extension side panel, mobile Web, desktop Web, and recommendation save controls now use the
Task 7 platform-neutral saved-item contract. Local save remains independent from platform sync;
manual sync is available while automatic sync is off; local removal never deletes from a platform.
Task 9 real integration E2E, account-mutating smoke tests, packaging, and release work were not run.

## RED Evidence

The front-end contract tests were added before production changes.

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_saved_sync_frontend_contract.py -q --tb=short
```

Initial result: `4 failed`. The failures identified the missing mobile, desktop, extension, and CSS
saved-sync contracts.

The new extension test initially failed while importing the absent `fetchSavedItems` helper. After
the helper boundary was added, the view-model regression first failed for missing presentation,
sanitization, and grouped-summary exports, then specifically failed because U+2028 was not removed
from backend status text. Production code was added or tightened only after each intended failure.

## GREEN And Verification Evidence

Fresh final extension regression:

```bash
cd extension && npm test
```

Result: `681 passed, 0 failed`.

Fresh final Python front-end regression:

```bash
PYTHONPATH=src .venv/bin/pytest \
  tests/test_saved_sync_frontend_contract.py \
  tests/test_mobile_web_view_models.py \
  tests/test_desktop_web_card_links.py -q --tb=short
```

Result: `42 passed in 3.43s`.

Static and production-build checks:

```bash
cd extension && npm run typecheck
cd extension && npm run build
.venv/bin/ruff format --check \
  tests/test_saved_sync_frontend_contract.py \
  tests/test_mobile_web_view_models.py \
  tests/test_desktop_web_card_links.py
.venv/bin/ruff check \
  tests/test_saved_sync_frontend_contract.py \
  tests/test_mobile_web_view_models.py \
  tests/test_desktop_web_card_links.py
git diff --check
```

Results: TypeScript typecheck passed; the Chrome/Edge production bundle built successfully;
all three Python files were formatted and lint-clean; the diff whitespace check was clean.
Syntax checks also passed for every modified JavaScript module.

## Implemented Behavior

- Added canonical `saveItem`, `removeSavedItem`, `fetchSavedItems`, `savedItemStatus`,
  `syncSavedItems`, and durable task-polling helpers to extension and mobile API clients.
- Normalized `item_key`, `source_platform`, `content_id`, `content_url`, and `content_type` on every
  graphical surface. Front-end code sends only `list_kind`; backend adapters retain all platform
  routing and watch-later fallback decisions.
- Replaced Bilibili-only recommendation toggles with canonical local saves on extension, mobile,
  and desktop. Active controls disable during requests, local optimistic state rolls back on local
  failure, and visible live-region messages report loading, success, and failure.
- Added platform-neutral saved lists with target/status chips, `同步未同步内容（N）`, per-item sync
  or retry, page-level confirmation containing item count and distinct platforms, durable task
  polling, grouped `平台 成功/总数` summaries, and server refresh after completion.
- Preserved local saved state when platform sync fails. `extension_required` tells users to connect
  an installed, logged-in extension and does not offer temporary browser automation.
- Made local removal explicitly local-only and routed it through `/api/saved/*/remove`; it never
  invokes a platform delete.
- Changed extension `全部稍后看` to snapshot the queue and use `Promise.allSettled`. Only successful
  local saves leave the queue, failed entries remain visible, and the result uses the exact
  `本地保存 N · 同步中 M · 失败 K` format.
- Added `saved_sync.auto_sync_enabled` to extension, mobile, and desktop settings. It defaults from
  API data to off; false-to-true shows the exact account-mutation warning; cancel leaves it false;
  manual sync remains available in both states.
- Applied the UI/UX design skill's accessibility guidance: 44px mobile/side-panel targets,
  `:focus-visible`, reduced-motion preservation, semantic button types, disabled busy states, and
  polite/assertive status announcements.
- Sanitized task IDs and backend-provided task text, bounded task/item fields, and mapped unknown
  statuses to a safe failure presentation before rendering.

## Documentation

Updated:

- `docs/changelog.md`
- `docs/modules/config.md`
- `docs/modules/extension.md`
- `docs/modules/recommendation.md`
- `docs/modules/saved-sync.md`
- `docs/architecture.md`
- `docs/spec.md`
- `docs/specs/favorites.md`
- `docs/specs/watch-later.md`

The documents now describe canonical identity flow, the three settings surfaces, default-off
consent, manual sync, durable status polling, grouped results, local-only removal, and the removal
of the former Task 8 UI-pending boundary.

## Self-Review

- Re-read the complete Task 8 brief and native-save design against the final diff.
- Confirmed front-end source has no `source_platform` routing switch and never calls a platform API.
- Confirmed every page-level sync action asks for confirmation even when only one eligible item
  remains; per-item sync does not add the batch confirmation.
- Confirmed only the active request control is disabled and list state is reloaded after completion.
- Confirmed auto-sync cancellation does not persist `true`, while manual sync remains enabled.
- Confirmed unknown/control-character task fields cannot flow unbounded into rendered status text.
- Confirmed `.venv`, `extension/node_modules`, and generated `extension/dist` are not staged.
- Confirmed no real platform request, logged-in browser automation, or account mutation was run.

## Commit

`feat: add saved item sync controls` — the commit containing this report.

## Review Repair

The follow-up review added 13 focused regression cases before changing production code. Their RED
states covered canonical cross-platform identity, rejection of namespaced legacy IDs, strict bounded
desktop requests, durable task tracking, retained refresh state, per-item mutation versioning,
semantic focus restoration, modal keyboard behavior, and all-queue failure retention.

The repaired implementation now preserves the canonical five saved-item fields on recommendation
and delight rows for Bilibili, YouTube, X, Zhihu, and URL-only fallbacks; uses backend-issued
`item_key` values for state and caches; and never promotes a namespaced row ID or unknown text item
into a video identity. Desktop calls use the strict request boundary with bounded timeouts. All
surfaces retain their last successful list on refresh failures, expose retry, resume nonterminal
tasks after visibility changes without inventing a terminal summary, isolate writes by
`list_kind:item_key`, and discard stale hydration. Mobile cards and settings dialogs now preserve
keyboard focus, expose modal semantics and Escape handling, and keep coarse-pointer targets at least
44 by 44 pixels. Desktop delight save controls also announce loading, success, and failure.

Fresh review verification:

```bash
cd extension && npm test
PYTHONPATH=src .venv/bin/pytest -q \
  tests/test_saved_sync_api.py \
  tests/test_saved_sync_storage.py \
  tests/test_saved_sync_service.py \
  tests/test_saved_sync_frontend_contract.py
cd extension && npm run typecheck && npm run build
```

Results: extension `694 passed, 0 failed`; Python `126 passed`; TypeScript typecheck passed; and the
Chrome/Edge production bundle built successfully. The one timing-sensitive Bilibili dispatcher case
that transiently failed during the first full run passed both in isolation (`8/8`) and in the fresh
full rerun. No Task 9 real integration E2E or account mutation was performed.

## Second Review Repair

The second review started with six failing JavaScript regression groups out of 19 and one failing
front-end contract out of five. The JavaScript RED results demonstrated absent saved/config request
bounds, reload task recovery, persisted item ownership, focus fallback, coarse-pointer sizing, and
stable button width. The Python RED result proved the desktop contract inspected the removed route
literal in `app.js` instead of the authoritative route builder in `saved-sync-core.js`. Follow-up RED
cycles separately caught same-task ownership cleanup, focus loss when a sync control disappears but
its card remains, missing page-teardown cleanup, callbacks escaping from an in-flight poll after
dispose, and desktop per-item sync label shift.

The repaired clients now apply explicit Abort timeouts to every extension/mobile saved save, remove,
list, status, sync, and task-poll call, plus config GET/PUT. Never-settling fetch tests exercise real
AbortSignals; mutation registries release busy state in `finally`, task poll timeouts remain
recoverable, and the mobile config dialog exposes its existing retry state. Each successful saved-list
load groups nonterminal rows by persisted `sync_task_id`, fetches each task once, and installs a
deduplicated tracker. Per-list task ownership marks every owned `item_key` as syncing and excludes it
from duplicate single/batch submissions, including popup all-queue auto-sync responses. Tracker
visibility listeners are bound once per view; page teardown disposes timers and suppresses late
in-flight callbacks.

Focus tokens now retain the original card index. If the exact action no longer exists, all three
surfaces try the next card action, then the previous card action, then the list sync/retry control,
then a focusable page heading. Rendered markup exposes deterministic hooks for those fallbacks.
Extension and desktop recommendation/delight save toggles meet 44×44 coarse-pointer targets, while
batch and per-item sync controls reserve inline width so `同步` / `同步中…` / `重试同步` does not shift
layout. Desktop delight tooltips and accessible labels now follow the pressed state.

Fresh final verification commands:

```bash
cd extension && npm test
PYTHONPATH=src .venv/bin/pytest -q \
  tests/test_saved_sync_api.py \
  tests/test_saved_sync_storage.py \
  tests/test_saved_sync_service.py \
  tests/test_saved_sync_frontend_contract.py
cd extension && npm run typecheck && npm run build
```

Final results: extension `702 passed, 0 failed` on repeated clean full runs; Python `127 passed`;
TypeScript typecheck and the Chrome/Edge production build passed. Every modified JavaScript file
passed `node --check`; the Python contract file passed Ruff format/check; and `git diff --check` was
clean. No Task 9 real integration E2E, signed-in browser automation, or account mutation was run.

## Final Review Repair

The final review began with four failing focused groups out of 25. Two 5ms regressions proved that
the popup request timeout started after authentication: a never-settling fresh device-session
exchange escaped the deadline, while a 401-triggered forced refresh returned the stale response
instead of aborting. The other failures proved that list-level sync-all / retry controls produced no
focus token and that retry handlers reloaded before preserving their opener.

The popup request boundary now starts one shared Abort deadline before backend-address resolution and
uses it for the initial device-session exchange, protected request, 401 forced exchange, replay, and
response parsing. Authentication fetches receive that same signal, and the request wrapper rejects on
deadline even while an awaited authentication promise is pending. Extension, mobile Web, and desktop
Web focus helpers now distinguish list-action tokens from item-action tokens. Batch-sync and retry
handlers capture the actual control before work begins; after rerender, restoration tries the same
list action before card actions and the heading. The focused tests exercise direct batch / retry token
round trips and both handler paths on all three runtimes.

Fresh final verification commands:

```bash
cd extension && npm test
PYTHONPATH=src .venv/bin/pytest -q \
  tests/test_saved_sync_api.py \
  tests/test_saved_sync_storage.py \
  tests/test_saved_sync_service.py \
  tests/test_saved_sync_frontend_contract.py
cd extension && npm run typecheck && npm run build
```

Final results: extension `706 passed, 0 failed`; Python `127 passed`; TypeScript typecheck and the
Chrome/Edge production build passed. Focused popup API / device-auth / saved-sync review coverage was
`62 passed, 0 failed`, including both real AbortSignal authentication cases. Every modified
JavaScript file passed `node --check`; the Python contract file remained Ruff-clean; and
`git diff --check` was clean. No Task 9 real integration E2E, signed-in browser automation, or
account mutation was run.

# Six-Platform Task 8 Report: Zhihu Exact OpenBiliClaw Collection

## Scope And Safety Boundary

Implemented the sixth extension native-save executor for Zhihu. The implementation and all evidence
in this report are fixture-only. No signed-in browser was controlled, no real Zhihu request or click
was made, and no account content was mutated.

## RED To GREEN Evidence

- Initial RED: `ERR_MODULE_NOT_FOUND` for `src/content/native-save/zhihu.ts`; dispatcher rejected the
  valid native union with `false !== true`.
- Expanded state-machine RED: reordered stale rate events were incorrectly classified as a new rate
  event; answer pages with the same answer ID under a mismatched question route were accepted.
- Expanded DOM RED: the minimal environment lacked full ancestor visibility and exact closest-
  identity control/dialog/row binding.
- Dispatcher behavior RED: the module lacked an executable native dependency seam and authenticated
  exact-result transport export.
- GREEN: 22 focused tests now cover typed identities, mismatch fences, both intents, exact Unicode
  title, duplicate ambiguity, create/close/reopen/re-query, created-unchecked selection, no fallback,
  checked idempotency, directional rate evidence, hidden/related/nested/reused DOM, asynchronous
  creation proof, and exact authenticated dispatcher closure.

## Independent Review Repair

The first independent read-only review found three Important issues. Regression tests first proved
that an untagged dialog nested under another item's identity could escape the target fence, that
trimming a row title accepted whitespace variants, and that creation returned success before an
asynchronous form or deterministic confirmation proof existed.

The repaired browser environment applies the closest-identity fence to dialogs, rows, create/close
controls, inputs, and confirmation controls. Collection names compare raw `textContent` with exact
case-sensitive Unicode equality. Creation clicks the create and confirm controls at most once,
waits for the asynchronous form, then requires a unique exact row or a deterministic form
transition; uncertainty fails closed. The outer executor still closes, reopens, and polls for the
unique exact row before any selection mutation. The same reviewer completed a second read-only pass
with no Critical, Important, or Minor findings.

## Verification

```text
focused Zhihu native-save + dispatcher: 22 passed
extension npm test: 832 passed, 0 failed
npm run typecheck: passed
npm run build: passed (Chrome/Edge chrome120 artifact)
TARGET=edge npm run build: passed (explicit Edge invocation; same shared artifact target)
git diff --check: passed
```

## Documentation

Updated changelog, extension/saved-sync/runtime module docs, architecture, spec diagram,
platform-source integration guide, and README CN/EN. All now state 6/6 executor wiring,
fixture-only verification, and no real-account verification.
