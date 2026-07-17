from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

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


def test_backup_includes_committed_wal_data_before_checkpoint(tmp_path: Path) -> None:
    source = tmp_path / "app.db"
    target = tmp_path / "backup.db"
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("pragma journal_mode=wal").fetchone() == ("wal",)
        writer.execute("pragma wal_autocheckpoint=0")
        writer.execute("create table values_table (value text not null)")
        writer.commit()
        writer.execute("insert into values_table values ('only-in-wal')")
        writer.commit()
        assert source.with_name(f"{source.name}-wal").stat().st_size > 0

        SQLiteOperationalStore().backup(source=source, destination=target)
    finally:
        writer.close()

    with sqlite3.connect(target) as connection:
        assert connection.execute("select value from values_table").fetchall() == [("only-in-wal",)]
        assert connection.execute("pragma integrity_check").fetchone() == ("ok",)


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
        if Path(dst) == destination:
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

    def fail_copy(
        self: SQLiteOperationalStore,
        *,
        source: Path,
        source_descriptor: int,
        source_identity: tuple[int, int],
        snapshot_parent: Path,
        descriptor: int,
    ) -> None:
        del self, source, source_descriptor, source_identity, snapshot_parent, descriptor
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


def test_backup_rejects_source_replaced_by_symlink_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "app.db"
    original = tmp_path / "original.db"
    attacker = tmp_path / "attacker.db"
    destination = tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("create table identity (value text not null)")
        connection.execute("insert into identity values ('verified')")
    with sqlite3.connect(attacker) as connection:
        connection.execute("create table identity (value text not null)")
        connection.execute("insert into identity values ('attacker')")

    original_create = SQLiteOperationalStore._create_temp

    def replace_source_after_validation(
        self: SQLiteOperationalStore, directory: Path
    ) -> tuple[Path, int, tuple[int, int]]:
        temp = original_create(self, directory)
        source.rename(original)
        source.symlink_to(attacker)
        return temp

    monkeypatch.setattr(SQLiteOperationalStore, "_create_temp", replace_source_after_validation)

    with pytest.raises(DatabaseBackupError, match="source changed"):
        SQLiteOperationalStore().backup(source=source, destination=destination)

    assert not destination.exists()
    with sqlite3.connect(original) as connection:
        assert connection.execute("select value from identity").fetchone() == ("verified",)


def test_backup_private_path_fallback_reads_pinned_source_after_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.infrastructure.database import operations

    source = tmp_path / "app.db"
    original = tmp_path / "original.db"
    attacker = tmp_path / "attacker.db"
    destination = tmp_path / "backup.db"
    for database, value in ((source, "verified"), (attacker, "attacker")):
        with sqlite3.connect(database) as connection:
            connection.execute("create table identity (value text not null)")
            connection.execute("insert into identity values (?)", (value,))

    def force_private_path(*, directory: Path, directory_descriptor: int) -> tuple[str, bool]:
        del directory_descriptor
        database = directory / "source.db"
        return f"file:{quote(str(database), safe='/')}?mode=ro", False

    original_connect = operations.sqlite3.connect
    swapped = False

    def swap_original_path_while_opening(
        database: object, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        nonlocal swapped
        if not swapped and ".obc-backup-source-" in str(database):
            swapped = True
            source.rename(original)
            source.symlink_to(attacker)
            try:
                return original_connect(database, *args, **kwargs)
            finally:
                source.unlink()
                original.rename(source)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(operations, "_stable_source_uri", force_private_path)
    monkeypatch.setattr(operations.sqlite3, "connect", swap_original_path_while_opening)

    SQLiteOperationalStore().backup(source=source, destination=destination)

    with sqlite3.connect(destination) as connection:
        assert connection.execute("select value from identity").fetchone() == ("verified",)


def test_backup_private_path_fallback_fails_if_private_directory_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.infrastructure.database import operations

    source = tmp_path / "app.db"
    attacker = tmp_path / "attacker.db"
    destination = tmp_path / "backup.db"
    sqlite3.connect(source).close()
    sqlite3.connect(attacker).close()

    def force_private_path(*, directory: Path, directory_descriptor: int) -> tuple[str, bool]:
        del directory_descriptor
        database = directory / "source.db"
        return f"file:{quote(str(database), safe='/')}?mode=ro", False

    original_connect = operations.sqlite3.connect
    swapped = False

    def replace_private_directory(
        database: object, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        nonlocal swapped
        if not swapped and ".obc-backup-source-" in str(database):
            swapped = True
            stable_database = Path(str(database).split("?", maxsplit=1)[0].removeprefix("file:"))
            stable_directory = stable_database.parent
            stable_directory.rename(stable_directory.with_name(f"{stable_directory.name}-held"))
            stable_directory.mkdir(mode=0o700)
            os.link(attacker, stable_database, follow_symlinks=False)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(operations, "_stable_source_uri", force_private_path)
    monkeypatch.setattr(operations.sqlite3, "connect", replace_private_directory)

    with pytest.raises(DatabaseBackupError, match="stable database source changed"):
        SQLiteOperationalStore().backup(source=source, destination=destination)

    assert not destination.exists()


def test_backup_rejects_sidecar_created_after_snapshot_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.infrastructure.database import operations

    source = tmp_path / "app.db"
    destination = tmp_path / "backup.db"
    sqlite3.connect(source).close()
    original_uri = operations._stable_source_uri

    def create_late_wal(*, directory: Path, directory_descriptor: int) -> tuple[str, bool]:
        source.with_name(f"{source.name}-wal").write_bytes(b"late-wal")
        return original_uri(
            directory=directory,
            directory_descriptor=directory_descriptor,
        )

    monkeypatch.setattr(operations, "_stable_source_uri", create_late_wal)

    with pytest.raises(DatabaseBackupError, match="sidecar changed"):
        SQLiteOperationalStore().backup(source=source, destination=destination)

    assert not destination.exists()


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="platform lacks O_NOFOLLOW")
def test_backup_opens_source_and_temporary_file_with_nofollow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.infrastructure.database import operations

    source = tmp_path / "app.db"
    destination = tmp_path / "backup.db"
    sqlite3.connect(source).close()
    original_open = operations.os.open
    source_flags: list[int] = []

    def record_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(path) == source:
            source_flags.append(flags)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(operations.os, "open", record_open)

    SQLiteOperationalStore().backup(source=source, destination=destination)

    assert source_flags
    assert all(flags & os.O_NOFOLLOW for flags in source_flags)
