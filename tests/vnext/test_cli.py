from __future__ import annotations

import importlib
import sqlite3
import subprocess
import sys
from pathlib import Path  # noqa: TC003 - pytest fixtures resolve annotations

from typer.testing import CliRunner


def _load_app():
    sys.modules.pop("openbiliclaw.cli", None)
    module = importlib.import_module("openbiliclaw.cli")
    assert "openbiliclaw.cli_models" not in sys.modules
    assert "typer._click" not in module.__dict__.values()
    return module.app


def test_help_exposes_only_operational_surface() -> None:
    result = CliRunner().invoke(_load_app(), ["--help"])
    assert result.exit_code == 0, result.output
    for command in ("serve", "worker", "doctor", "eval", "db"):
        assert command in result.output
    for removed in ("start", "profile", "recommend", "config-show", "init", "serve-api"):
        assert removed not in result.output

    db_help = CliRunner().invoke(_load_app(), ["db", "--help"])
    assert db_help.exit_code == 0
    assert "migrate" in db_help.output
    assert "backup" in db_help.output


def test_python_module_help_exposes_the_operational_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "openbiliclaw.cli", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "db" in result.stdout


def test_db_migrate_and_backup_use_fresh_vnext_sqlite(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "vnext.db"
    backup = tmp_path / "backup.db"
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", f"sqlite:///{database}")
    runner = CliRunner()
    migrated = runner.invoke(_load_app(), ["db", "migrate"])
    assert migrated.exit_code == 0, migrated.output
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "select count(*) from sqlite_master where type='table' and name='settings'"
        ).fetchone() == (1,)

    backed_up = runner.invoke(_load_app(), ["db", "backup", str(backup)])
    assert backed_up.exit_code == 0, backed_up.output
    assert backup.exists()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("pragma integrity_check").fetchone() == ("ok",)


def test_process_commands_use_injected_boundaries(monkeypatch) -> None:
    cli = importlib.import_module("openbiliclaw.cli")
    calls: list[str] = []
    monkeypatch.setattr(cli, "run_server", lambda **kwargs: calls.append("serve"))
    monkeypatch.setattr(cli, "run_worker_process", lambda workers: calls.append("worker"))
    monkeypatch.setattr(cli, "run_offline_evals", lambda dataset: calls.append("eval") or 0)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["serve"]).exit_code == 0
    assert runner.invoke(cli.app, ["worker"]).exit_code == 0
    assert runner.invoke(cli.app, ["eval"]).exit_code == 0
    assert calls == ["serve", "worker", "eval"]


def test_worker_module_main_uses_the_configured_worker_entrypoint(monkeypatch) -> None:
    worker_module = importlib.import_module("openbiliclaw.worker")
    calls: list[str] = []
    monkeypatch.setattr(worker_module, "run_configured_worker", lambda: calls.append("worker"))
    worker_module.main()
    assert calls == ["worker"]


def test_offline_eval_loads_all_versioned_datasets_without_a_provider() -> None:
    cli = importlib.import_module("openbiliclaw.cli")
    assert cli.run_offline_evals(None) == 0


def test_doctor_is_secret_safe(monkeypatch) -> None:
    monkeypatch.setenv("OPENBILICLAW_ACCESS_TOKEN", "doctor-secret")
    result = CliRunner().invoke(_load_app(), ["doctor"])
    assert result.exit_code in {0, 1}
    assert "doctor-secret" not in result.output


def test_docker_image_default_uses_the_supported_serve_command() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["openbiliclaw", "serve", "--host", "0.0.0.0", "--port", "8420"]' in dockerfile
    assert "serve-api" not in dockerfile
