# Manual end-to-end verification

The maintained manual matrix has four runbooks. Run them against a disposable vNext environment. Do not use old data, live provider/source credentials, or state-changing platform actions unless the test explicitly requires and authorizes them.

| Runbook | Purpose |
|---|---|
| [Docker first run](e2e/docker-first-run.md) | installation, migrations, LiteLLM aliases, onboarding, retained product journey |
| [Web](e2e/web.md) | `/setup`, `/web`, `/m`, auth, settings, profile, feed, feedback, chat, library |
| [Browser extension](e2e/extension.md) | device bearer, popup, passive events, generic source tasks, Chrome/Firefox builds |
| [Source install](e2e/source-install.md) | external LiteLLM, migration, API/worker lifecycle, doctor, backup, recovery |

## Shared completion criteria

- first-run setup reaches a terminal success only after source sync, profile projection, and feed replenishment succeed;
- before onboarding completes, periodic source sync, profile projection, and feed replenishment create no job rows, while cleanup and explicit onboarding child jobs remain enabled; after completion, due periodic maintenance resumes;
- first-run installer exposes the Web password and extension key once, persists neither plaintext, and a rerun exposes neither again;
- setup login succeeds, the LiteLLM Admin link is visible, manifest credentials are configured before onboarding, and empty-schema sources show no backend credential form;
- configured first-run onboarding rejects anonymous requests, browser settings cannot disable the Web login path, and explicit rotation remains the recovery path;
- concurrent installers serialize credential stage/start/commit/disclosure, and Docker reruns preserve a previously persisted custom Admin URL unless an explicit replacement is supplied;
- failed or cancelled jobs/onboarding are shown as failed or cancelled, never as success;
- profile facets cite evidence and an explicit edit creates a new revision;
- feedback is preserved and changes later feed ordering or score projection;
- chat streams deltas and one terminal event, then appears in bounded history;
- favorites and watch later are local collections and never mutate a platform account;
- API and worker use the same application database and separate Huey queue;
- changing any of the four schedule intervals in Web or extension settings affects the next worker tick without restart and does not duplicate a time bucket;
- feed SEARCH uses a generated query, admitted items expose grounded generated explanations, and embedding alias failure is visible as a feed job failure rather than an unused setup prerequisite;
- errors use the safe typed envelope and do not contain secrets, traceback, SQL, or provider payloads;
- no residual test process, temporary browser tab, or disposable container remains.

Record exact commands, commit, browser/build version, pass/fail result, and environment-blocked checks. Never record `.env`, bearer tokens, device keys, cookies, provider keys, or source credentials.
