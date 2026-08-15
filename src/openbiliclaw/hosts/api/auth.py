"""Single-user password verification and durable bearer-token authentication."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase

TokenLabel = Literal["session", "extension"]
_DEFAULT_ITERATIONS = 310_000


class MintedToken(StrictBaseModel):
    """A newly minted secret; the raw token is returned exactly once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str = Field(pattern=r"^at_[0-9a-f]{32}$")
    token: str = Field(min_length=32, repr=False)
    label: TokenLabel


class AuthTokenRepository(Protocol):
    async def add(
        self, token_id: str, label: TokenLabel, token_hash: str, created_at: str
    ) -> None: ...
    async def label_for_hash(self, token_hash: str) -> TokenLabel | None: ...
    async def revoke(self, token_id: str) -> None: ...


def hash_password(password: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """Hash a password using stdlib PBKDF2-SHA256 and a random 128-bit salt."""

    if not password:
        raise ValueError("password must not be empty")
    if iterations < 100_000:
        raise ValueError("PBKDF2 iterations must be at least 100000")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2:{iterations}:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a PBKDF2 password hash without leaking malformed-hash errors."""

    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split(":")
        iterations = int(raw_iterations)
        salt = bytes.fromhex(raw_salt)
        expected = bytes.fromhex(raw_digest)
        if algorithm != "pbkdf2" or iterations < 100_000 or not salt or not expected:
            return False
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SqliteAuthTokenRepository:
    """SQLite persistence for token hashes; raw bearer tokens never enter storage."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    async def add(self, token_id: str, label: TokenLabel, token_hash: str, created_at: str) -> None:
        async with self._database.transaction() as session:
            await session.execute(
                "INSERT INTO auth_tokens(token_id,label,token_hash,created_at) VALUES(?,?,?,?)",
                (token_id, label, token_hash, created_at),
            )

    async def label_for_hash(self, token_hash: str) -> TokenLabel | None:
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT label FROM auth_tokens WHERE token_hash=?", (token_hash,)
            )
        return None if row is None else cast("TokenLabel", row[0])

    async def revoke(self, token_id: str) -> None:
        async with self._database.transaction() as session:
            changed = await session.execute("DELETE FROM auth_tokens WHERE token_id=?", (token_id,))
        if changed == 0:
            raise KeyError(token_id)


class AuthTokenService:
    """Mint, verify, and revoke opaque bearer tokens.

    ponytail: tokens do not expire in this single-user local app; add expirations when
    remote/multi-device threat models require lifecycle management.
    """

    def __init__(
        self,
        repository: AuthTokenRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def mint(self, label: TokenLabel) -> MintedToken:
        raw = secrets.token_urlsafe(32)
        digest = _token_hash(raw)
        token_id = f"at_{digest[:32]}"
        await self._repository.add(token_id, label, digest, self._clock().isoformat())
        return MintedToken(token_id=token_id, token=raw, label=label)

    async def verify(self, raw: str) -> TokenLabel | None:
        if not raw:
            return None
        return await self._repository.label_for_hash(_token_hash(raw))

    async def revoke(self, token_id: str) -> None:
        await self._repository.revoke(token_id)
