# CLI Host

The installed `openbiliclaw` command is owned by Runtime Composition. There is no legacy/v2 split.

```text
openbiliclaw check [--config PATH] [--data-dir PATH]
openbiliclaw serve [--config PATH] [--data-dir PATH]
openbiliclaw set-password [--config PATH]  # prompt twice; store only PBKDF2 hash
openbiliclaw ext-token [--data-dir PATH]   # mint extension token; print raw value once
openbiliclaw export PATH [--data-dir PATH] [--include-config] [--config PATH]
openbiliclaw import PATH [--data-dir PATH] [--force]
openbiliclaw <workflow commands>    # sources/feed/feedback/profile/assistant… thin pass-throughs
```

`check` performs a complete lifecycle/readiness check without binding a socket. `serve` starts the composed FastAPI host using `[host]` settings. `set-password` never echoes or stores the raw password; `ext-token` stores only a SHA-256 token hash and prints the raw token once. `export` writes one versioned archive from a consistent SQLite backup snapshot; `--include-config` adds the selected `--config` (default `config.toml`) with vault references intact and password verifiers redacted. `import` validates the format and migrates the staged snapshot forward before installing it into the data directory; it refuses a non-empty destination unless `--force`. The removed monolithic commands are not compatibility-supported.

CLI doctrine: the CLI covers **basically all product functionality** — sources, feed, feedback, profile, assistant, export/import, tokens — as **thin pass-through commands over Application workflows**, sharing the exact contracts of the API host. Commands carry no business logic; breadth is welcome, logic is not (that is what killed the legacy 15k-line cli.py). For agents the CLI is a **thin request/answer pipe**: take the agent's request, return the information needed — no agent-specific command zoo, no MCP, no exposed OpenAPI surface; `skills/` teaches an agent how to ask.
