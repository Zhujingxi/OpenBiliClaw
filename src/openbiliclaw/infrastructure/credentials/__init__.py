"""Opaque credential storage."""

from .keyring import KeyringBackend, ProtectedFileBackend, keyring_or_file
from .vault import CredentialVault

__all__ = ["CredentialVault", "KeyringBackend", "ProtectedFileBackend", "keyring_or_file"]
