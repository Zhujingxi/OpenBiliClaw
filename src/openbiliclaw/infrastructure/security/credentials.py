"""Fernet encryption derived from the installer-managed application secret."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet

SECRET_KEY_ENV = "OPENBILICLAW_SECRET_KEY"
_DERIVATION_CONTEXT = b"openbiliclaw-vnext-credentials-v1\0"
_CIPHER_AUTHORITY = object()


class MissingCredentialKeyError(RuntimeError):
    """Raised when the installer-managed application secret is unavailable."""


class EncryptedCredential(str):
    """Opaque authenticated ciphertext minted only by :class:`CredentialCipher`."""

    def __new__(cls, value: str, *, _authority: object | None = None) -> EncryptedCredential:
        if _authority is not _CIPHER_AUTHORITY:
            raise TypeError("encrypted credentials must be produced by CredentialCipher")
        return str.__new__(cls, value)

    @classmethod
    def _from_fernet(cls, value: str) -> EncryptedCredential:
        return cls(value, _authority=_CIPHER_AUTHORITY)


class CredentialCipher:
    """Encrypt opaque credentials without retaining the source secret or plaintext."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise MissingCredentialKeyError(f"{SECRET_KEY_ENV} must be set")
        digest = hashlib.sha256(_DERIVATION_CONTEXT + secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    @classmethod
    def from_environment(cls) -> CredentialCipher:
        """Construct the cipher from the installer-generated environment secret."""

        secret = os.environ.get(SECRET_KEY_ENV)
        if not secret:
            raise MissingCredentialKeyError(f"{SECRET_KEY_ENV} must be set")
        return cls(secret)

    def encrypt(self, plaintext: str) -> EncryptedCredential:
        """Return an authenticated, URL-safe ciphertext token."""

        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return EncryptedCredential._from_fernet(token)

    def decrypt(self, ciphertext: str) -> str:
        """Authenticate and decrypt a previously generated token."""

        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
