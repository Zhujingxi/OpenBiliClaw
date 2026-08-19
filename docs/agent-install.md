# Installation

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
openbiliclaw check --config config.toml
openbiliclaw serve --config config.toml
```

`openbiliclaw check` is the installer/readiness smoke and emits JSON. The only long-running runtime command is `serve`; every user operation is also available as an in-process JSON CLI command documented in `docs/modules/cli.md`. Agent harnesses should load `skills/openbiliclaw/SKILL.md`, use stdin for secret-bearing JSON, and never edit the database or credential vault directly. Removed legacy initialization, daemon, and API aliases are not supported. Existing unversioned databases require an explicit backed-up reset/import decision before target startup.
