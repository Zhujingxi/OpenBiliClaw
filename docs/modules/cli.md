# CLI Host

The installed `openbiliclaw` command is owned by Runtime Composition. There is no legacy/v2 split.

```text
openbiliclaw check [--config PATH] [--data-dir PATH]
openbiliclaw serve [--config PATH] [--data-dir PATH]
openbiliclaw export / import        # versioned user-data ownership/backup (SQLite + config)
openbiliclaw ext-token              # generate/revoke the extension & agent token
openbiliclaw <workflow commands>    # sources/feed/feedback/profile/assistant… thin pass-throughs
```

`check` performs a complete lifecycle/readiness check without binding a socket. `serve` starts the composed FastAPI host using `[host]` settings. The removed monolithic commands are not compatibility-supported.

CLI doctrine: the CLI covers **basically all product functionality** — sources, feed, feedback, profile, assistant, export/import, tokens — as **thin pass-through commands over Application workflows**, sharing the exact contracts of the API host. Commands carry no business logic; breadth is welcome, logic is not (that is what killed the legacy 15k-line cli.py). For agents the CLI is a **thin request/answer pipe**: take the agent's request, return the information needed — no agent-specific command zoo, no MCP, no exposed OpenAPI surface; `skills/` teaches an agent how to ask.
