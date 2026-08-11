"""Credential storage backends: OS keyring command and protected-file fallback."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol


class CredentialBackend(Protocol):
    """Trusted byte-oriented secret storage boundary."""

    def get(self, secret_id: str) -> bytearray: ...

    def set(self, secret_id: str, secret: bytes) -> None: ...

    def delete(self, secret_id: str) -> None: ...


class ProtectedFileBackend:
    """Permission-checked local fallback for systems without an OS keyring."""

    def __init__(self, path: Path) -> None:
        self._path = path
        if path.exists() and path.stat().st_mode & 0o077:
            raise PermissionError("credential file must not be accessible by group or others")
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(path.parent, 0o700)

    def get(self, secret_id: str) -> bytearray:
        values = self._load()
        encoded = values.get(secret_id)
        if encoded is None:
            raise KeyError(secret_id)
        return bytearray(base64.b64decode(encoded, validate=True))

    def set(self, secret_id: str, secret: bytes) -> None:
        values = self._load()
        values[secret_id] = base64.b64encode(secret).decode("ascii")
        self._save(values)

    def delete(self, secret_id: str) -> None:
        values = self._load()
        if secret_id not in values:
            raise KeyError(secret_id)
        del values[secret_id]
        self._save(values)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        if self._path.stat().st_mode & 0o077:
            raise PermissionError("credential file permissions changed")
        loaded = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in loaded.items()
        ):
            raise ValueError("credential file has an invalid shape")
        return {str(key): str(value) for key, value in loaded.items()}

    def _save(self, values: dict[str, str]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", dir=self._path.parent
        )
        temporary = Path(temporary_name)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(values, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


class CommandRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        input: str | None = None,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _run_command(
    args: list[str],
    *,
    input: str | None = None,
    capture_output: bool,
    text: bool,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    from openbiliclaw.infrastructure.process import creationflags

    return subprocess.run(
        args,
        input=input,
        capture_output=capture_output,
        text=text,
        check=check,
        creationflags=creationflags(),
    )


class KeyringBackend:
    """Adapter over the standard ``keyring`` OS-keyring command."""

    def __init__(
        self,
        *,
        service: str = "openbiliclaw",
        executable: str = "keyring",
        runner: CommandRunner = _run_command,
    ) -> None:
        resolved = shutil.which(executable)
        if resolved is None:
            raise RuntimeError("OS keyring is unavailable")
        self._service = service
        self._executable = resolved
        self._runner = runner

    def get(self, secret_id: str) -> bytearray:
        result = self._runner(
            [self._executable, "get", self._service, secret_id],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise KeyError(secret_id)
        encoded = result.stdout.removesuffix("\n")
        return bytearray(base64.b64decode(encoded, validate=True))

    def set(self, secret_id: str, secret: bytes) -> None:
        value = base64.b64encode(secret).decode("ascii")
        result = self._runner(
            [self._executable, "set", self._service, secret_id],
            input=f"{value}\n",
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("OS keyring refused credential storage")

    def delete(self, secret_id: str) -> None:
        result = self._runner(
            [self._executable, "del", self._service, secret_id],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise KeyError(secret_id)


def keyring_or_file(path: Path) -> CredentialBackend:
    """Use the OS keyring when available, otherwise a protected local file."""

    try:
        return KeyringBackend()
    except RuntimeError:
        return ProtectedFileBackend(path)
