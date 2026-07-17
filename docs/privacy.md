# OpenBiliClaw privacy policy

Effective date: 2026-07-17

OpenBiliClaw is a self-hosted, local-first content discovery product. Its browser extension sends authorized activity and source-task results only to the backend selected by the user. OpenBiliClaw developers do not operate a service that receives this product data.

## Data processed

Depending on enabled sources and actions, the extension and backend may process:

- source account identifiers and write-only credentials needed for authorized read/import operations;
- page URL, title, stable content ID, author, timestamps, and visible content metadata;
- user activity such as views, clicks, searches, dwell, explicit feedback, and chat messages;
- normalized profile evidence, profile revisions, feed entries, interactions, local collections, jobs, and AI-run usage metadata;
- backend address, extension device session, settings, and UI state.

Source connectors are read/import/discovery only. OpenBiliClaw favorites and watch later are local collections; the product does not like, follow, favorite, save, subscribe, or otherwise mutate an external platform account.

## Data flow

The extension sends data to the configured OpenBiliClaw backend, normally on loopback. A remote or LAN backend requires explicit configuration, host permission, TLS when appropriate, and extension device authentication.

The backend sends task inputs to the user's LiteLLM Proxy for profile, assessment, chat, explanation, or embedding work. LiteLLM may forward the minimum required data to provider deployments configured by the user. Provider credentials, routing, fallback, budgets, and cache live in LiteLLM, not in OpenBiliClaw. Users should review the policies of their chosen deployment and provider.

The extension includes no analytics, advertising, telemetry endpoint, or remotely executed code.

## Storage and retention

Product data is stored in the user's SQLite database or self-hosted volume. Huey uses a separate local queue. Source credentials are encrypted. The backend stores only digest records for extension device keys; the complete key is delivered once to the extension. Web sessions use HttpOnly cookies.

Old v0.3 data files are left untouched and are not imported. Users control retention by changing product settings, removing source accounts, deleting local collection items, removing the extension, or deleting their self-hosted vNext data.

AI run records contain task/model alias, timing, provider-neutral usage, status, and error class; they do not contain task input/output payload fields.

## Browser permissions

The extension requests only permissions needed for its single purpose:

| permission | purpose |
|---|---|
| `alarms` | bounded backend checks and generic task polling |
| `cookies` | read authorized source login state/credential material for configured read operations |
| `scripting` | run packaged content adapters on supported source pages |
| `sidePanel` | display the OpenBiliClaw interface |
| `storage` | save backend connection, finite session, and UI state |
| source host permissions | passive evidence capture and declared browser tasks on the seven supported platforms |
| backend host permission | communicate with the user-selected OpenBiliClaw backend |

Published builds do not request `<all_urls>`. The exact manifest is the authority for the current permission set.

## Security and user control

Web uses same-origin HttpOnly session cookies plus CSRF protection. The extension exchanges a provisioned device key for a finite bearer; extension origins cannot use loopback trust. Installer, session, encryption, LiteLLM, provider, source, and device credentials are separate and must not be logged or committed.

Users can revoke browser sessions, disconnect a source account, remove the extension, clear extension storage, or delete their self-hosted vNext volumes. Disconnecting a source deletes encrypted source credential material. Removing a local favorite or watch-later item does not affect any external account.

## Contact

Report privacy or security concerns through [GitHub Issues](https://github.com/whiteguo233/OpenBiliClaw/issues).
