"""Password login and durable extension-token authentication."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from pydantic import ValidationError
from starlette.websockets import WebSocket

from openbiliclaw.core.config import HostSettings
from openbiliclaw.hosts.api import HostDependencies, HostSecurityPolicy, create_app
from openbiliclaw.hosts.api.app import bearer_authorized
from openbiliclaw.hosts.api.auth import (
    AuthTokenService,
    SqliteAuthTokenRepository,
    hash_password,
    verify_password,
)
from openbiliclaw.hosts.api.routers.events import _websocket_authorized
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from tests.hosts.test_api import Facade

if TYPE_CHECKING:
    from pathlib import Path

_MUTATION = {"x-device-id": "browser", "x-csrf-token": "browser"}


async def _service(path: Path) -> tuple[SqliteDatabase, AuthTokenService]:
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    return database, AuthTokenService(SqliteAuthTokenRepository(database))


def test_password_hash_round_trip_and_tamper_rejection() -> None:
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("pbkdf2:")
    assert int(encoded.split(":", 3)[1]) >= 100_000
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)
    replacement = "0" if encoded[-1] != "0" else "1"
    assert not verify_password("correct horse battery staple", encoded[:-1] + replacement)
    assert not verify_password("correct horse battery staple", "garbage")
    with pytest.raises(ValidationError):
        HostSettings(password_hash="pbkdf2:99999:00:00")


@pytest.mark.asyncio
async def test_login_mints_session_and_handles_bad_or_unconfigured_password(tmp_path: Path) -> None:
    database, tokens = await _service(tmp_path / "auth.db")
    configured = HostDependencies(
        facade=Facade(),
        security=HostSecurityPolicy(password_hash=hash_password("secret")),
        auth_tokens=tokens,
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(configured)), base_url="http://test"
        ) as client:
            denied = await client.post(
                "/v1/auth/login", json={"password": "wrong"}, headers=_MUTATION
            )
            assert denied.status_code == 401
            response = await client.post(
                "/v1/auth/login", json={"password": "secret"}, headers=_MUTATION
            )
            assert response.status_code == 200
            body = response.json()
            assert body["label"] == "session"
            assert body["token"]
            assert await tokens.verify(body["token"]) == "session"

        unconfigured = HostDependencies(facade=Facade(), auth_tokens=tokens)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(unconfigured)), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/auth/login", json={"password": "anything"}, headers=_MUTATION
            )
            assert response.status_code == 503
            assert response.json()["error"]["message"] == "password login not configured"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_login_rate_limits_repeated_failures(tmp_path: Path) -> None:
    database, tokens = await _service(tmp_path / "rate.db")
    dependencies = HostDependencies(
        facade=Facade(),
        security=HostSecurityPolicy(password_hash=hash_password("secret")),
        auth_tokens=tokens,
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="http://test"
        ) as client:
            for _ in range(5):
                response = await client.post(
                    "/v1/auth/login", json={"password": "wrong"}, headers=_MUTATION
                )
                assert response.status_code == 401
            response = await client.post(
                "/v1/auth/login", json={"password": "wrong"}, headers=_MUTATION
            )
            assert response.status_code == 429
            # A valid password clears the local failure bucket.
            response = await client.post(
                "/v1/auth/login", json={"password": "secret"}, headers=_MUTATION
            )
            assert response.status_code == 200
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_middleware_accepts_minted_or_static_token_and_rejects_garbage(
    tmp_path: Path,
) -> None:
    database, tokens = await _service(tmp_path / "middleware.db")
    minted = await tokens.mint("extension")
    dependencies = HostDependencies(
        facade=Facade(),
        security=HostSecurityPolicy(bearer_token="static"),
        auth_tokens=tokens,
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(dependencies)), base_url="http://test"
        ) as client:
            assert (await client.get("/v1/sources")).status_code == 401
            assert (
                await client.get("/v1/sources", headers={"authorization": "Bearer garbage"})
            ).status_code == 401
            assert (
                await client.get("/v1/sources", headers={"authorization": f"Bearer {minted.token}"})
            ).status_code == 200
            assert (
                await client.get("/v1/sources", headers={"authorization": "Bearer static"})
            ).status_code == 200
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_websocket_auth_accepts_minted_token(tmp_path: Path) -> None:
    database, tokens = await _service(tmp_path / "ws.db")
    minted = await tokens.mint("extension")
    dependencies = HostDependencies(
        facade=Facade(),
        security=HostSecurityPolicy(bearer_token="static"),
        auth_tokens=tokens,
    )
    try:
        websocket = cast(
            "WebSocket",
            SimpleNamespace(headers={"authorization": f"Bearer {minted.token}"}),
        )
        assert await _websocket_authorized(websocket, dependencies)
        websocket = cast("WebSocket", SimpleNamespace(headers={"authorization": "Bearer static"}))
        assert await _websocket_authorized(websocket, dependencies)
        websocket = cast("WebSocket", SimpleNamespace(headers={"authorization": "Bearer garbage"}))
        assert not await _websocket_authorized(websocket, dependencies)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_non_ascii_authorization_header_is_401_not_500(tmp_path: Path) -> None:
    """Attacker-controlled non-latin1 headers must not crash compare_digest."""

    database, tokens = await _service(tmp_path / "nonascii.db")
    dependencies = HostDependencies(
        facade=Facade(),
        security=HostSecurityPolicy(bearer_token="static"),
        auth_tokens=tokens,
    )
    try:
        assert not await bearer_authorized(dependencies, "Bearer \xff\xfe")
        websocket = cast("WebSocket", SimpleNamespace(headers={"authorization": "Bearer \xff\xfe"}))
        assert not await _websocket_authorized(websocket, dependencies)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_token_revoke_and_database_stores_no_raw_secret(tmp_path: Path) -> None:
    database, tokens = await _service(tmp_path / "revoke.db")
    try:
        minted = await tokens.mint("extension")
        assert await tokens.verify(minted.token) == "extension"
        async with database.transaction() as session:
            row = await session.fetch_one(
                "SELECT token_hash FROM auth_tokens WHERE token_id=?", (minted.token_id,)
            )
        assert row is not None and row[0] != minted.token
        await tokens.revoke(minted.token_id)
        assert await tokens.verify(minted.token) is None
        with pytest.raises(KeyError):
            await tokens.revoke(minted.token_id)
    finally:
        await database.close()
