"""Export/import CLI is a thin adapter over the archive service."""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.composition.entrypoints import main
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator

if TYPE_CHECKING:
    from pathlib import Path


def test_export_import_cli_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    database = source / "openbiliclaw.db"
    import asyncio

    asyncio.run(SchemaMigrator(database).migrate())
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO understanding_profiles(profile_id, revision, profile_json, updated_at)"
            " VALUES ('p', 1, '{}', '2026-08-15T00:00:00+00:00')"
        )
    archive = tmp_path / "backup.obc"
    monkeypatch.setattr(
        sys,
        "argv",
        ["openbiliclaw", "export", str(archive), "--data-dir", str(source)],
    )
    main()
    assert json.loads(capsys.readouterr().out)["exported"] is True

    destination = tmp_path / "destination"
    monkeypatch.setattr(
        sys,
        "argv",
        ["openbiliclaw", "import", str(archive), "--data-dir", str(destination)],
    )
    main()
    assert json.loads(capsys.readouterr().out)["imported"] is True
    with sqlite3.connect(destination / "openbiliclaw.db") as connection:
        assert connection.execute("SELECT profile_id FROM understanding_profiles").fetchone() == (
            "p",
        )


def test_export_cli_include_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio
    import zipfile

    data_dir = tmp_path / "data"
    asyncio.run(SchemaMigrator(data_dir / "openbiliclaw.db").migrate())
    config = tmp_path / "config.toml"
    config.write_text("[host]\napi_port = 8420\n", encoding="utf-8")
    archive = tmp_path / "with-config.obc"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openbiliclaw",
            "export",
            str(archive),
            "--data-dir",
            str(data_dir),
            "--config",
            str(config),
            "--include-config",
        ],
    )

    main()
    assert json.loads(capsys.readouterr().out)["exported"] is True

    with zipfile.ZipFile(archive) as bundle:
        assert "config.toml" in bundle.namelist()

    destination = tmp_path / "restored"
    monkeypatch.setattr(
        sys,
        "argv",
        ["openbiliclaw", "import", str(archive), "--data-dir", str(destination)],
    )
    main()
    output = json.loads(capsys.readouterr().out)
    assert output["restored_config"] == str(destination / "config.toml")


def test_export_cli_reports_missing_config_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    data_dir = tmp_path / "data"
    asyncio.run(SchemaMigrator(data_dir / "openbiliclaw.db").migrate())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openbiliclaw",
            "export",
            str(tmp_path / "backup.obc"),
            "--data-dir",
            str(data_dir),
            "--config",
            str(tmp_path / "missing.toml"),
            "--include-config",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        main()
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "archive"
    assert "config does not exist" in error["message"]


def test_import_cli_reports_invalid_archive_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / "corrupt.obc"
    archive.write_bytes(b"not a zip")
    monkeypatch.setattr(
        sys,
        "argv",
        ["openbiliclaw", "import", str(archive), "--data-dir", str(tmp_path / "data")],
    )

    with pytest.raises(SystemExit, match="2"):
        main()
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "archive"
    assert "invalid archive" in error["message"]


def test_export_cli_refuses_database_as_archive_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    data_dir = tmp_path / "data"
    database = data_dir / "openbiliclaw.db"
    asyncio.run(SchemaMigrator(database).migrate())
    monkeypatch.setattr(
        sys,
        "argv",
        ["openbiliclaw", "export", str(database), "--data-dir", str(data_dir)],
    )

    with pytest.raises(SystemExit, match="2"):
        main()
    error = json.loads(capsys.readouterr().err)["error"]
    assert error == {
        "code": "validation",
        "message": "archive path must not be the live database",
    }
