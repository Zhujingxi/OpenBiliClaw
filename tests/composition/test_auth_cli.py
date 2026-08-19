"""Authentication CLI commands use configuration and the durable token store."""

from __future__ import annotations

import asyncio
import io
import json
import sys
from typing import TYPE_CHECKING

from openbiliclaw.composition.entrypoints import main
from openbiliclaw.core.config import load_settings
from openbiliclaw.hosts.api.auth import AuthTokenService, SqliteAuthTokenRepository, verify_password
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_set_password_writes_hash_without_printing_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    secret = "not-in-output"
    monkeypatch.setattr("getpass.getpass", lambda _prompt: secret)
    monkeypatch.setattr(sys, "argv", ["openbiliclaw", "set-password", "--config", str(config)])

    main()

    output = capsys.readouterr().out
    assert secret not in output
    assert json.loads(output) == {"configured": True, "config": str(config)}
    settings = load_settings(config)
    assert settings.host.password_hash is not None
    assert verify_password(secret, settings.host.password_hash)


def test_set_password_accepts_noninteractive_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    secret = "stdin-secret"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(f"{secret}\n".encode())))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openbiliclaw",
            "set-password",
            "--password-stdin",
            "--config",
            str(config),
        ],
    )

    main()

    assert secret not in capsys.readouterr().out
    settings = load_settings(config)
    assert settings.host.password_hash is not None
    assert verify_password(secret, settings.host.password_hash)


def test_ext_token_cli_prints_once_and_authenticates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["openbiliclaw", "ext-token", "--data-dir", str(tmp_path)])

    main()

    output = json.loads(capsys.readouterr().out)
    assert output["shown_once"] is True
    raw = output["token"]

    async def verify() -> str | None:
        database = SqliteDatabase(tmp_path / "openbiliclaw.db")
        await database.open()
        try:
            return await AuthTokenService(SqliteAuthTokenRepository(database)).verify(raw)
        finally:
            await database.close()

    assert asyncio.run(verify()) == "extension"
