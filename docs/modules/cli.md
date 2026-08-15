# CLI Host

The installed `openbiliclaw` command is owned by Runtime Composition. There is no legacy/v2 split.

```text
openbiliclaw check [--config PATH] [--data-dir PATH]
openbiliclaw serve [--config PATH] [--data-dir PATH]
openbiliclaw set-password [--config PATH]  # prompt twice; store only PBKDF2 hash
openbiliclaw ext-token [--data-dir PATH]   # mint extension token; print raw value once
openbiliclaw export PATH [--data-dir PATH] [--include-config] [--config PATH]
openbiliclaw import PATH [--data-dir PATH] [--force]  # restore OpenBiliClaw archive
openbiliclaw import youtube TAKEOUT_PATH [--data-dir PATH]  # JSON workflow result
openbiliclaw sources list [--account-id ID] [--limit N]
openbiliclaw sources status PROVIDER [--account-id ID]
openbiliclaw sources add PROVIDER METHOD --permission PERMISSION --idempotency-key KEY [--account-id ID] [--field KEY=VALUE]…
openbiliclaw sources remove PROVIDER --idempotency-key KEY [--account-id ID]
openbiliclaw sources sync PROVIDER
openbiliclaw feed [--limit N]
openbiliclaw feedback SHOWN_ID <like|dismiss|save|open> --idempotency-key KEY [--exposed]
openbiliclaw profile show [--profile-id ID]
openbiliclaw profile exploration <disable|enable> --idempotency-key KEY [--profile-id ID] [--account-id ID]
openbiliclaw assistant MESSAGE [--conversation-id ID] [--device-id ID] [--locale LOCALE]
openbiliclaw search PROVIDER QUERY [--limit N]
```

`sources sync` invokes one bounded Application workflow; disconnected/public-only sources return a successful skipped result and credentialed providers import at most two recent pages per declared `History`/`Saved` capability. `import youtube TAKEOUT_PATH` accepts Google's real extracted/ZIP Takeout watch-history and uses the same observation normalization/idempotency path; likes/subscriptions are ignored rather than mislabeled as saves. No Bilibili archive import is advertised because no stable official export format was verified.

`check` performs a complete lifecycle/readiness check without binding a socket. `serve` starts the composed FastAPI host using `[host]` settings. `set-password` never echoes or stores the raw password; `ext-token` stores only a SHA-256 token hash and prints the raw token once. `export` writes one versioned archive from a consistent SQLite backup snapshot; `--include-config` adds the selected `--config` (default `config.toml`) with vault references intact and password verifiers redacted. `import` validates the format and migrates the staged snapshot forward before installing it into the data directory; it refuses a non-empty destination unless `--force`. The removed monolithic commands are not compatibility-supported.

All product workflow commands start the in-process composition graph (never an HTTP loopback and never a server), call exactly one Application facade workflow, then stop it. Stdout is exactly one compact JSON document; expected typed Application errors are one JSON error document on stderr with exit status 1 and no traceback. Credentialed `sources add` connections are stored behind `CredentialVault` and rehydrated and verified when a later one-shot command or server process starts; `sources remove` deletes the durable vault material. `feedback` resolves `shown_id` through the `RecordFeedbackForShown` Application workflow, so the transport does not reconstruct content identity. `search` requires a provider because the current Application search workflow is provider-scoped.

CLI doctrine: the CLI covers **basically all product functionality** — sources, feed, feedback, profile, assistant, search, export/import, tokens — as **thin pass-through commands over Application workflows**, sharing the exact contracts of the API host. Commands carry no business logic; breadth is welcome, logic is not (that is what killed the legacy 15k-line cli.py). For agents the CLI is a **thin request/answer pipe**: take the agent's request, return the information needed — no agent-specific command zoo, no MCP, no exposed OpenAPI surface; `skills/` teaches an agent how to ask.
