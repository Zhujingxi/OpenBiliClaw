# Web E2E

Run against a healthy disposable backend with the required LiteLLM aliases. Use a browser capable of inspecting network and EventStream responses.

## 1. Authentication

Provision the Web password hash and session signing secret through the deployment secret store, then restart API. Do not put the plaintext password in a command, fixture, screenshot, or issue.

1. Open `http://127.0.0.1:8420/setup/` in a fresh browser profile.
2. Verify `GET /api/v1/auth/status` exposes only readiness booleans.
3. Sign in. Confirm the session cookie is HttpOnly and no token appears in JSON or browser storage.
4. Send an unsafe request without `X-OBC-Auth`; expect a typed 403.
5. Before signing in, confirm configured onboarding returns 401; after signing in, confirm setup can read/start onboarding.
6. Sign out and confirm the cookie is cleared.

## 2. Setup

1. Confirm readiness and all three AI aliases before Continue is enabled.
2. Inspect the seven source manifests and their exact capability sets.
3. Configure a test source. Confirm credentials are write-only.
4. Start onboarding and follow `/api/v1/onboarding/{run_id}/events`.
5. Exercise successful, failed, cancelled, disconnected, and reconnect/resume terminal paths.

Pass when the page never reports a failed or cancelled terminal event as success.

## 3. Desktop Web

Open `http://127.0.0.1:8420/web/` and verify:

- navigation and responsive drawer remain usable;
- feed cards render source, title, author, URL, explanation, and available metadata without fabricating a Bilibili URL;
- each interaction sends one typed `/api/v1/interactions` request;
- profile edit includes the expected revision; stale revision returns 409 and does not overwrite newer data;
- chat uses `POST /api/v1/chat/stream`, renders deltas once, handles failed terminal events, and persists history from `GET /api/v1/chat/{conversation_id}`;
- favorites and watch later use `/api/v1/library/{collection}` and remain local;
- all safe mutable nested settings round-trip; `web_password_enabled` and deployment facts remain read-only, and an attempted browser PATCH disabling password login returns typed 422 without invalidating the current login;
- AI settings show alias health and the safe LiteLLM Admin link, with no provider editor.

For library partial failure, make collection add succeed and interaction fail. The UI must show the item saved and retry only the interaction signal.

## 4. Mobile Web

Open `http://127.0.0.1:8420/m/` at narrow and wide viewport sizes. Verify feed, interaction, profile edit, chat/history, library, replenishment progress, and the documented high-frequency settings subset. Mobile must not claim source bootstrap or full settings parity.

## 5. Evidence

Record browser/version, viewport, route, expected/actual status, and screenshots with synthetic data only. Check the network log for duplicate writes, obsolete endpoints, raw credential fields, and WebSocket connections; all should be absent.
