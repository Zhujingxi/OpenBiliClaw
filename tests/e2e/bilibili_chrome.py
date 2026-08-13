"""Read Bilibili session cookies from local Chromium storage without persisting values."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.sources import ConnectSourceCommand

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_REQUIRED = ("SESSDATA", "bili_jct")
_OPTIONAL = ("buvid3", "DedeUserID")
_NAMES = frozenset((*_REQUIRED, *_OPTIONAL))


@dataclass(frozen=True, slots=True)
class BrowserCookies:
    """Selected cookie values kept in process memory only."""

    values: dict[str, str]

    def __repr__(self) -> str:
        return "BrowserCookies(<redacted>)"

    def __str__(self) -> str:
        return "BrowserCookies(<redacted>)"

    def __post_init__(self) -> None:
        missing = [name for name in _REQUIRED if not self.values.get(name)]
        if missing:
            raise RuntimeError(
                f"Chrome is missing required Bilibili cookie names: {', '.join(missing)}"
            )

    @property
    def header(self) -> str:
        return "; ".join(
            f"{name}={self.values[name]}"
            for name in (*_REQUIRED, *_OPTIONAL)
            if name in self.values
        )

    def structural_summary(self) -> tuple[str, ...]:
        return tuple(
            f"{name} ({len(self.values[name])} bytes)"
            for name in (*_REQUIRED, *_OPTIONAL)
            if name in self.values
        )


def connect_command(cookies: BrowserCookies, idempotency_key: str) -> ConnectSourceCommand:
    """Build the product connection command without persisting the cookie value."""

    return ConnectSourceCommand(
        idempotency_key=idempotency_key,
        request=AccessRequest(
            provider_id="bilibili",
            permissions=frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
            supported_method_ids=("builtin.manual",),
        ),
        allowed_method_ids=frozenset({"builtin.manual"}),
        submission={"cookie": cookies.header},
    )


def chrome_cookie_database(home: Path | None = None) -> Path:
    root = home or Path.home()
    candidates = (
        root / ".config/google-chrome/Default/Cookies",
        root / ".config/google-chrome/Default/Network/Cookies",
        root / ".config/chromium/Default/Cookies",
        root / ".config/chromium/Default/Network/Cookies",
        root / ".config/google-chrome-beta/Default/Cookies",
        root / ".config/google-chrome-beta/Default/Network/Cookies",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("no supported Chrome/Chromium cookie database found")


def snapshot_database(source: Path, destination: Path) -> None:
    """Take a SQLite backup so live WAL data is included while Chrome runs."""

    with (
        sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original,
        sqlite3.connect(destination) as snapshot,
    ):
        original.backup(snapshot)


def read_selected_cookies(database: Path, decrypt: Callable[[bytes], str]) -> BrowserCookies:
    values: dict[str, str] = {}
    with sqlite3.connect(database) as connection:
        rows: Iterable[tuple[str, str, bytes]] = connection.execute(
            """SELECT name, value, encrypted_value FROM cookies
               WHERE host_key LIKE ? AND name IN (?, ?, ?, ?)
               ORDER BY expires_utc DESC""",
            ("%.bilibili.com", *_REQUIRED, *_OPTIONAL),
        )
        for name, plain, encrypted in rows:
            if name not in values:
                value = plain or decrypt(bytes(encrypted))
                if value:
                    values[name] = value
    return BrowserCookies(values)


def _safe_storage_password() -> bytes:
    try:
        import secretstorage

        bus = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(bus)
        collection.unlock()
        for item in collection.search_items({"application": "chrome"}):
            label = item.get_label().lower()
            if "safe storage" in label:
                return bytes(item.get_secret())
    except Exception as exc:
        raise RuntimeError("could not read Chrome Safe Storage from libsecret") from exc
    raise RuntimeError("Chrome Safe Storage password not found in libsecret")


def chrome_decryptor(password: bytes | None = None) -> Callable[[bytes], str]:
    """Build the Linux Chromium v10/v11 AES-CBC cookie decryptor."""

    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    key = hashlib.pbkdf2_hmac("sha1", password or _safe_storage_password(), b"saltysalt", 1, 16)

    def decrypt(encrypted: bytes) -> str:
        if not encrypted.startswith((b"v10", b"v11")):
            raise RuntimeError("unsupported Chrome cookie encryption format")
        plaintext = unpad(
            AES.new(key, AES.MODE_CBC, iv=b" " * 16).decrypt(encrypted[3:]), AES.block_size
        )
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError:
            # Chromium DB version >=24 prefixes SHA-256(host_key).
            return plaintext[32:].decode("utf-8")

    return decrypt


def extract_bilibili_cookies(database: Path | None = None) -> BrowserCookies:
    source = database or chrome_cookie_database()
    with tempfile.TemporaryDirectory(prefix="openbiliclaw-chrome-") as directory:
        snapshot = Path(directory) / "Cookies"
        snapshot_database(source, snapshot)
        return read_selected_cookies(snapshot, chrome_decryptor())
