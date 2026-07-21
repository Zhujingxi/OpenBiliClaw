# Browser extension E2E

Use a disposable browser profile and synthetic/test source data. Do not perform like, follow, favorite, save, subscribe, or any other platform-account mutation.

## 1. Build

```bash
cd extension
npm ci
npm run api:check
npm run typecheck
npm test
npm run build
npm run build:firefox
npx web-ext lint --source-dir dist-firefox
```

Load `extension/dist/` as an unpacked Chromium extension and `extension/dist-firefox/` in a Firefox test profile.

## 2. Device authentication

Provision a new device key through the deployment secret workflow. Deliver the complete key once to the test extension; store only its `key-id:sha256-digest` record in backend configuration.

1. Configure backend URL and device key in the popup.
2. Confirm `POST /api/v1/auth/extension-token` returns a finite bearer and expiry.
3. Confirm the complete device key is not retained in backend settings, logs, errors, generated clients, or screenshots.
4. Test invalid key, expiry, backend revoke, and re-exchange.
5. Confirm an extension origin cannot use loopback trust or cookie auth.

## 3. Popup retained journeys

Verify login/connection status, sources, setup progress, evidence profile/edit, feed/feedback, chat/history, favorites/watch later, nested settings, and alias health/Admin navigation. Failed/cancelled onboarding and jobs must render their true terminal state.

Confirm dropped provider editor, native-save/saved-sync, notification, self-update, personality/probe, and desktop controls are absent from markup and navigation.

## 4. Passive activity

For each supported platform adapter, open a synthetic fixture or explicitly authorized page and perform a read-only interaction. Confirm exactly one normalized `POST /api/v1/events` payload with canonical source ID, stable content identity, URL, event type, time semantics, and no cookie/token/raw page response.

## 5. Generic source tasks

For every local executor capability:

1. inspect `GET /api/v1/sources` and confirm the manifest permits the operation;
2. let the service worker claim via `GET /api/v1/source-tasks/claim`;
3. verify typed request translation and the correct platform tab/fixture;
4. complete through `POST /api/v1/source-tasks/{task_id}/complete`;
5. verify normalized activity/content persistence.

Repeat with operation/source mismatch, malformed result, executor failure, request deadline before execution, deadline during execution, lease loss, and Xiaohongshu continuation/partial results. Late results must not success-complete. Timers, listeners, and temporary tabs must be removed.

The manifest is authoritative. In particular, do not dispatch browser operations that a source does not declare.

## 6. Teardown

Remove the unpacked extension, delete the disposable browser profile, revoke its sessions/device record, and verify no task tab or service-worker retry remains. Record Chrome/Firefox versions, build SHA, operation matrix, and blocked live-source checks without secrets.
