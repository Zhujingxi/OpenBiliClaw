"""Opaque-reference credential vault."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from .keyring import CredentialBackend

T = TypeVar("T")
_SECRET_ID = re.compile(r"^cred_[0-9a-f]{32}$")


class CredentialVault:
    """Keep secret bytes behind a trusted callback scope."""

    def __init__(self, backend: CredentialBackend) -> None:
        self._backend = backend

    def __repr__(self) -> str:
        return "CredentialVault(<redacted>)"

    def store(self, secret: bytes) -> str:
        """Store bytes and return a new opaque reference."""

        secret_id = f"cred_{uuid.uuid4().hex}"
        self._backend.set(secret_id, secret)
        return secret_id

    def resolve(self, secret_id: str, callback: Callable[[memoryview], T]) -> T:
        """Expose a temporary read-only view only inside ``callback``."""

        self._validate_id(secret_id)
        secret = self._backend.get(secret_id)
        try:
            return callback(memoryview(secret).toreadonly())
        finally:
            secret[:] = b"\0" * len(secret)

    def replace(self, secret_id: str, secret: bytes) -> None:
        """Replace an existing secret."""

        self._validate_id(secret_id)
        # Do not silently turn a caller typo into a new credential.
        existing = self._backend.get(secret_id)
        existing[:] = b"\0" * len(existing)
        self._backend.set(secret_id, secret)

    def delete(self, secret_id: str) -> None:
        """Delete a secret reference."""

        self._validate_id(secret_id)
        self._backend.delete(secret_id)

    @staticmethod
    def _validate_id(secret_id: str) -> None:
        if _SECRET_ID.fullmatch(secret_id) is None:
            raise ValueError("invalid opaque credential reference")
