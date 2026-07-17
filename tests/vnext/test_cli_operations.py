from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openbiliclaw.infrastructure.ai.health import AIHealthResult, AliasHealth
from openbiliclaw.infrastructure.database.operations import (
    DatabaseBackupError,
    SQLiteOperationalStore,
)


def _healthy_aliases() -> AIHealthResult:
    return AIHealthResult(
        proxy_reachable=True,
        aliases=tuple(
            AliasHealth(alias=alias, available=True, state="healthy")
            for alias in ("obc-interactive", "obc-analysis", "obc-embedding")
        ),
    )


def test_doctor_checks_database_migration_queue_and_all_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw import cli

    database = tmp_path / "app.db"
    queue = tmp_path / "huey.db"
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("OPENBILICLAW_HUEY_PATH", str(queue))
    monkeypatch.setenv("OPENBILICLAW_ACCESS_TOKEN", "access-secret")
    monkeypatch.setenv("OPENBILICLAW_LITELLM_API_KEY", "proxy-secret")
    monkeypatch.setenv("OPENBILICLAW_LITELLM_BASE_URL", "http://proxy.invalid")

    runner = CliRunner()
    assert runner.invoke(cli.app, ["db", "migrate"]).exit_code == 0
    sqlite3.connect(queue).close()
    monkeypatch.setattr(cli, "run_ai_health_check", lambda **_: _healthy_aliases())

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "database: ready" in result.output
    assert "migration: head" in result.output
    assert "queue: ready" in result.output
    assert "queue-separation: ready" in result.output
    for alias in ("obc-interactive", "obc-analysis", "obc-embedding"):
        assert f"{alias}: healthy" in result.output
    assert "access-secret" not in result.output
    assert "proxy-secret" not in result.output


def test_doctor_fails_for_stale_migration_and_unreachable_litellm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw import cli

    database = tmp_path / "app.db"
    queue = tmp_path / "huey.db"
    sqlite3.connect(database).close()
    sqlite3.connect(queue).close()
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("OPENBILICLAW_HUEY_PATH", str(queue))
    monkeypatch.setenv("OPENBILICLAW_ACCESS_TOKEN", "access-secret")
    monkeypatch.setenv("OPENBILICLAW_LITELLM_API_KEY", "proxy-secret")
    monkeypatch.setattr(
        cli,
        "run_ai_health_check",
        lambda **_: AIHealthResult(
            proxy_reachable=False,
            aliases=tuple(
                AliasHealth(
                    alias=alias,
                    available=False,
                    state="unavailable",
                    reason="proxy_transport_error",
                )
                for alias in ("obc-interactive", "obc-analysis", "obc-embedding")
            ),
        ),
    )

    result = CliRunner().invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "migration: stale" in result.output
    assert "litellm: unreachable" in result.output
    assert "proxy-secret" not in result.output


def test_doctor_rejects_shared_queue_file_and_incomplete_alias_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw import cli

    database = tmp_path / "app.db"
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("OPENBILICLAW_HUEY_PATH", str(database))
    monkeypatch.setenv("OPENBILICLAW_ACCESS_TOKEN", "configured")
    monkeypatch.setenv("OPENBILICLAW_LITELLM_API_KEY", "configured")
    runner = CliRunner()
    assert runner.invoke(cli.app, ["db", "migrate"]).exit_code == 0
    health = _healthy_aliases()
    monkeypatch.setattr(
        cli,
        "run_ai_health_check",
        lambda **_: health.model_copy(update={"aliases": health.aliases[:2]}),
    )

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "queue-separation: invalid" in result.output
    assert "obc-embedding: unavailable" in result.output


def test_eval_executes_cases_and_returns_nonzero_when_an_evaluator_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw import cli

    dataset_root = tmp_path / "datasets"
    dataset_root.mkdir()
    (dataset_root / "keyword_generation.yaml").write_text(
        """
name: failing_offline_dataset
cases:
  - name: invalid_expected_output
    inputs:
      profile:
        revision: 1
        narrative: Likes procedural modeling.
        facets: []
        confidence: 0.8
        created_at: '2026-07-17T00:00:00Z'
      limit: 2
    expected_output:
      keywords: [bread recipes, urban gardening]
    metadata:
      rubric: Must remain profile relevant.
      min_keywords: 2
      max_keywords: 2
      required_concepts: [procedural, modeling]
      minimum_relevant_keywords: 1
      forbidden_source_terms: []
evaluators:
  - KeywordGenerationInvariants
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "EVAL_DATASET_ROOT", dataset_root)

    result = CliRunner().invoke(cli.app, ["eval", "--dataset", "keyword_generation"])

    assert result.exit_code == 1, result.output
    assert "failed: keyword_generation" in result.output
    assert "cases=1" in result.output


def test_backup_is_consistent_private_and_atomically_published(tmp_path: Path) -> None:
    source = tmp_path / "app.db"
    target = tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("create table values_table (value text not null)")
        connection.execute("insert into values_table values ('committed')")

    SQLiteOperationalStore().backup(source=source, destination=target)

    assert target.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(target) as connection:
        assert connection.execute("select value from values_table").fetchall() == [("committed",)]
        assert connection.execute("pragma integrity_check").fetchone() == ("ok",)
    assert not list(tmp_path.glob(".backup-*.tmp"))


def test_backup_rejects_destination_symlink_without_touching_target(tmp_path: Path) -> None:
    source = tmp_path / "app.db"
    victim = tmp_path / "victim"
    destination = tmp_path / "backup.db"
    sqlite3.connect(source).close()
    victim.write_text("do-not-touch", encoding="utf-8")
    destination.symlink_to(victim)

    with pytest.raises(DatabaseBackupError, match="already exists"):
        SQLiteOperationalStore().backup(source=source, destination=destination)

    assert destination.is_symlink()
    assert victim.read_text(encoding="utf-8") == "do-not-touch"


def test_backup_loses_publish_race_without_overwriting_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.infrastructure.database import operations

    source = tmp_path / "app.db"
    destination = tmp_path / "backup.db"
    sqlite3.connect(source).close()
    original_link = operations.os.link

    def race_link(src: Path, dst: Path, *args: object, **kwargs: object) -> None:
        Path(dst).write_text("racer-won", encoding="utf-8")
        original_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(operations.os, "link", race_link)

    with pytest.raises(DatabaseBackupError, match="already exists"):
        SQLiteOperationalStore().backup(source=source, destination=destination)

    assert destination.read_text(encoding="utf-8") == "racer-won"
    assert not list(tmp_path.glob(".backup-*.tmp"))


def test_backup_failure_only_cleans_up_the_temp_inode_it_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "app.db"
    destination = tmp_path / "backup.db"
    sqlite3.connect(source).close()

    def fail_copy(self: SQLiteOperationalStore, *, source: Path, descriptor: int) -> None:
        raise sqlite3.OperationalError("simulated backup failure")

    monkeypatch.setattr(SQLiteOperationalStore, "_backup_into_descriptor", fail_copy)

    with pytest.raises(DatabaseBackupError, match="backup failed"):
        SQLiteOperationalStore().backup(source=source, destination=destination)

    assert not list(tmp_path.glob(".backup-*.tmp"))
    assert not destination.exists()


def test_backup_never_writes_or_unlinks_a_replacement_temp_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "app.db"
    destination = tmp_path / "backup.db"
    sqlite3.connect(source).close()
    original_create = SQLiteOperationalStore._create_temp

    def replace_reserved_path(
        self: SQLiteOperationalStore, directory: Path
    ) -> tuple[Path, int, tuple[int, int]]:
        temp, descriptor, identity = original_create(self, directory)
        temp.unlink()
        temp.write_text("foreign-inode", encoding="utf-8")
        return temp, descriptor, identity

    monkeypatch.setattr(SQLiteOperationalStore, "_create_temp", replace_reserved_path)

    with pytest.raises(DatabaseBackupError, match="temporary file changed"):
        SQLiteOperationalStore().backup(source=source, destination=destination)

    attacker_files = list(tmp_path.glob(".backup-*.tmp"))
    assert len(attacker_files) == 1
    assert attacker_files[0].read_text(encoding="utf-8") == "foreign-inode"
    assert not destination.exists()


def test_backup_rejects_source_symlink(tmp_path: Path) -> None:
    actual = tmp_path / "actual.db"
    source = tmp_path / "app.db"
    sqlite3.connect(actual).close()
    source.symlink_to(actual)

    with pytest.raises(DatabaseBackupError, match="source must not be a symlink"):
        SQLiteOperationalStore().backup(source=source, destination=tmp_path / "backup.db")


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="platform lacks O_NOFOLLOW")
def test_backup_contract_uses_nofollow_when_creating_temporary_file() -> None:
    assert os.O_NOFOLLOW
