# CLI Host

The installed `openbiliclaw` command is owned by Runtime Composition. There is no legacy/v2 split.

```text
openbiliclaw check [--config PATH] [--data-dir PATH]
openbiliclaw serve [--config PATH] [--data-dir PATH]
```

`check` performs a complete lifecycle/readiness check without binding a socket. `serve` starts the composed FastAPI host using `[host]` settings. Product reads and mutations use the `/v1` API and the shared Vue clients. `hosts/cli/app.py` is a tested host adapter available to embedders but is not wired to the production console script; production intentionally exposes only `check` and `serve`. The removed monolithic commands are not compatibility-supported.
