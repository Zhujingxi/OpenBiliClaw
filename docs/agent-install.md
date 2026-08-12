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

`openbiliclaw check` is the installer/readiness smoke. The only runtime command is `serve`; removed legacy initialization, daemon, and API aliases are not supported. Existing unversioned databases require an explicit backed-up reset/import decision before target startup.
