from __future__ import annotations

import base64
import logging
import os
import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.infrastructure.credentials.keyring import (
    KeyringBackend,
    ProtectedFileBackend,
    keyring_or_file,
)
from openbiliclaw.infrastructure.credentials.vault import CredentialVault


def test_vault_store_resolve_replace_delete_and_restart(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "secrets.json"
    vault = CredentialVault(ProtectedFileBackend(path))
    caplog.set_level(logging.DEBUG)
    secret_id = vault.store(b"top-secret")
    assert secret_id.startswith("cred_")
    assert "top-secret" not in repr(vault)
    assert vault.resolve(secret_id, lambda value: bytes(value).decode()) == "top-secret"
    vault.replace(secret_id, b"new-secret")
    restarted = CredentialVault(ProtectedFileBackend(path))
    assert restarted.resolve(secret_id, bytes) == b"new-secret"
    assert "top-secret" not in caplog.text
    assert "new-secret" not in caplog.text
    restarted.delete(secret_id)
    with pytest.raises(KeyError):
        restarted.resolve(secret_id, bytes)


def test_file_backend_refuses_unsafe_permissions(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o644)
    with pytest.raises(PermissionError):
        ProtectedFileBackend(path)


def test_opaque_id_validation(tmp_path: Path) -> None:
    vault = CredentialVault(ProtectedFileBackend(tmp_path / "secrets.json"))
    with pytest.raises(ValueError):
        vault.replace("../../config", b"secret")


def test_protected_file_rejects_invalid_shape_and_missing_delete(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    backend = ProtectedFileBackend(path)
    with pytest.raises(KeyError):
        backend.delete("missing")
    path.write_text("[]", encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(ValueError, match="shape"):
        backend.get("missing")


def test_keyring_adapter_and_missing_keyring_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def runner(
        args: list[str],
        *,
        input: str | None = None,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "get":
            encoded = base64.b64encode(b"from-keyring").decode("ascii")
            return subprocess.CompletedProcess(args, 0, f"{encoded}\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/keyring")
    backend = KeyringBackend(runner=runner)
    assert backend.get("cred_x") == bytearray(b"from-keyring")
    backend.set("cred_x", b"replacement")
    backend.delete("cred_x")
    assert [call[1] for call in calls] == ["get", "set", "del"]

    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        KeyringBackend()
    assert isinstance(keyring_or_file(tmp_path / "fallback.json"), ProtectedFileBackend)


@pytest.mark.parametrize("secret", [bytes((0xFF, 0x00)), b"token\n"])
def test_keyring_round_trips_arbitrary_bytes_and_trailing_newline(
    secret: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = ""

    def runner(
        args: list[str],
        *,
        input: str | None = None,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal stored
        if args[1] == "set":
            assert input is not None
            stored = input.removesuffix("\n")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, f"{stored}\n", "")

    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/keyring")
    backend = KeyringBackend(runner=runner)
    backend.set("cred_x", secret)
    assert backend.get("cred_x") == bytearray(secret)


def test_keyring_failures_are_content_free(monkeypatch: pytest.MonkeyPatch) -> None:
    def runner(
        args: list[str],
        *,
        input: str | None = None,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "provider detail")

    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/keyring")
    backend = KeyringBackend(runner=runner)
    with pytest.raises(KeyError):
        backend.get("cred_x")
    with pytest.raises(RuntimeError, match="refused"):
        backend.set("cred_x", b"not-in-error")
    with pytest.raises(KeyError):
        backend.delete("cred_x")
