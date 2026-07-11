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
