# Agent Deployment

OpenBiliClaw has one supported runtime entrypoint. The installers clone/update the checkout, install the package, create `config.toml` from `config.example.toml` when absent, and run a complete composition readiness check. They never start an unmanaged background process.

## Install

Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 | iex
```

Manual equivalent:

```bash
python -m pip install -e .
cp config.example.toml config.toml  # only when config.toml does not exist
openbiliclaw check --config config.toml --data-dir data
```

`check` validates settings, migrates/opens the target database, starts resources and supervised jobs, checks readiness, then shuts everything down. An unversioned application database or destructive migration stops and requires an explicit backed-up reset/import decision.

## Start and verify

```bash
openbiliclaw serve --config config.toml --data-dir data
curl -fsS http://127.0.0.1:8420/v1/runtime/health
```

The Web UI is `/`. There is no `/setup/` wizard or legacy bootstrap/status protocol. Edit the strict typed `config.toml`, then rerun `openbiliclaw check` before restart. Unknown keys fail validation; credentials are opaque vault references rather than inline secrets.

## Environment/install options

- `INSTALL_DIR`: checkout target.
- `BRANCH`: branch to clone/update.
- `HOST` / `PORT` (shell) or `ApiHost` / `Port` (PowerShell): URLs printed in the completion summary. Runtime binding remains owned by `[host]` in `config.toml` or `OPENBILICLAW_API_HOST` / `OPENBILICLAW_API_PORT`.
- `SKIP_START` / `SkipStart`: retained installer-input compatibility; installers always validate and never start a background service.
- `FORCE_UPDATE`: permit installer update behavior for an existing checkout.

## Troubleshooting

1. Run `openbiliclaw check --config config.toml --data-dir data` and fix the bounded validation/readiness error.
2. Run `curl -v http://127.0.0.1:8420/v1/runtime/health` only while `serve` is active.
3. Verify port 8420 is free or change `[host].api_port`.
4. Do not delete or overwrite `data/openbiliclaw.db`; follow the explicit backup/reset/import decision reported by the target migrator.
5. Provider packages without a configured production transport fail closed without affecting unrelated providers.
