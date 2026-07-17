"""Browser and extension authentication contracts for the vNext API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from openbiliclaw import auth_core
from openbiliclaw.api.app import create_app
from openbiliclaw.api.dependencies import AccessPolicy, DependencyUnavailableError
from openbiliclaw.features.system.domain import UserSettings
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.models import AuthStateModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True)
class _Clock:
    value: int = 1_800_000_000

    def __call__(self) -> int:
        return self.value


class _Settings:
    def __init__(self, *, onboarding_complete: bool = True, trust_loopback: bool = False) -> None:
        defaults = UserSettings(onboarding_complete=onboarding_complete)
        self.value = defaults.model_copy(
            update={
                "access_control": defaults.access_control.model_copy(
                    update={
                        "web_password_enabled": True,
                        "trust_loopback": trust_loopback,
                        "session_ttl_hours": 2,
                        "extension_access_enabled": True,
                        "extension_session_ttl_hours": 1,
                    }
                )
            }
        )

    def get(self) -> UserSettings:
        return self.value

    def update(self, patch: dict[str, object]) -> UserSettings:
        merged = self.value.model_dump(mode="python")
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        self.value = UserSettings.model_validate(merged)
        return self.value


class _Container:
    def __init__(
        self,
        access: AccessPolicy,
        *,
        onboarding_complete: bool = True,
        trust_loopback: bool = False,
    ) -> None:
        self.access = access
        self.settings = _Settings(
            onboarding_complete=onboarding_complete,
            trust_loopback=trust_loopback,
        )
        self.onboarding = SimpleNamespace(status=self.settings.get)

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def _policy(
    *,
    clock: _Clock | None = None,
    extension_records: tuple[str, ...] = (),
) -> AccessPolicy:
    return AccessPolicy(
        token="installer-only-token",
        password_hash=auth_core.hash_password("correct horse battery staple"),
        session_secret="test-session-signing-secret",
        session_ttl_hours=2,
        extension_access_enabled=True,
        extension_access_records=extension_records,
        extension_session_ttl_hours=1,
        clock=clock or _Clock(),
    )


def _client(container: _Container) -> TestClient:
    return TestClient(
        create_app(container=container),  # type: ignore[arg-type]
        base_url="https://testserver",
    )


def _loopback_client(container: _Container) -> TestClient:
    return TestClient(
        create_app(container=container),  # type: ignore[arg-type]
        base_url="http://127.0.0.1:8420",
        client=("127.0.0.1", 51000),
    )


def _peer_client(container: _Container, peer: str) -> TestClient:
    return TestClient(
        create_app(container=container),  # type: ignore[arg-type]
        base_url="https://testserver",
        client=(peer, 51000),
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"password": "correct horse battery staple"},
    )
    assert response.status_code == 200


def test_web_cookie_login_csrf_and_logout_contract() -> None:
    client = _client(_Container(_policy()))

    anonymous = client.get("/api/v1/auth/status")
    assert anonymous.status_code == 200
    assert anonymous.json()["authenticated"] is False

    login = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"password": "correct horse battery staple"},
    )
    assert login.status_code == 200
    assert login.json() == {"authenticated": True}
    assert "token" not in login.text.lower()
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie

    assert client.get("/api/v1/settings").status_code == 200
    rejected = client.patch("/api/v1/settings", json={"feed": {"low_watermark": 19}})
    assert rejected.status_code == 403

    accepted = client.patch(
        "/api/v1/settings",
        headers={"Origin": "https://testserver", "X-OBC-Auth": "1"},
        json={"feed": {"low_watermark": 19}},
    )
    assert accepted.status_code == 200

    logout = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://testserver", "X-OBC-Auth": "1"},
    )
    assert logout.status_code == 200
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert client.get("/api/v1/auth/status").json()["authenticated"] is False


def test_installer_bearer_and_web_session_coexist() -> None:
    client = _client(_Container(_policy()))

    installer = client.get(
        "/api/v1/settings",
        headers={"Authorization": "Bearer installer-only-token"},
    )
    assert installer.status_code == 200

    _login(client)
    assert client.get("/api/v1/settings").status_code == 200


def test_onboarding_public_window_remains_explicit() -> None:
    public_container = _Container(_policy(), onboarding_complete=False)
    public = _client(public_container)
    assert public.get("/api/v1/onboarding").status_code == 200

    public_container.settings.value = public_container.settings.value.model_copy(
        update={"onboarding_complete": True}
    )
    assert public.get("/api/v1/onboarding").status_code == 401


def test_extension_device_exchange_expiry_and_revocation() -> None:
    clock = _Clock()
    _key_id, device_key, record = auth_core.generate_extension_access_key()
    client = _client(_Container(_policy(clock=clock, extension_records=(record,))))

    exchange = client.post(
        "/api/v1/auth/extension-token",
        headers={"Origin": "chrome-extension://test-extension"},
        json={"key": device_key},
    )
    assert exchange.status_code == 200
    extension_token = exchange.json()["token"]
    assert exchange.json()["expires_at"] == clock.value + 3600
    assert (
        client.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {extension_token}"},
        ).status_code
        == 200
    )

    revoke = client.post(
        "/api/v1/auth/revoke",
        headers={"Authorization": "Bearer installer-only-token"},
    )
    assert revoke.status_code == 204
    assert (
        client.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {extension_token}"},
        ).status_code
        == 401
    )

    fresh = client.post(
        "/api/v1/auth/extension-token",
        headers={"Origin": "chrome-extension://test-extension"},
        json={"key": device_key},
    ).json()["token"]
    clock.value += 3600
    assert (
        client.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {fresh}"},
        ).status_code
        == 401
    )


def test_auth_negative_responses_and_schema_do_not_contain_configured_secrets() -> None:
    _key_id, device_key, record = auth_core.generate_extension_access_key()
    client = _client(_Container(_policy(extension_records=(record,))))

    bad_login = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"password": "wrong-password"},
    )
    bad_device = client.post(
        "/api/v1/auth/extension-token",
        headers={"Origin": "chrome-extension://test-extension"},
        json={"key": f"{device_key}wrong"},
    )
    schema_response = client.get("/api/v1/openapi.json")
    schema = schema_response.text

    assert bad_login.status_code == 401
    assert bad_device.status_code == 401
    combined = bad_login.text + bad_device.text + schema
    for secret in (
        "wrong-password",
        device_key,
        record,
        "test-session-signing-secret",
        "installer-only-token",
    ):
        assert secret not in combined
    assert "/api/auth/" not in schema
    assert "/api/v1/auth/status" in schema
    paths = schema_response.json()["paths"]
    assert schema_response.json()["components"]["securitySchemes"]["SessionCookie"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "obc_session",
    }
    for public_path, method in (
        ("/api/v1/auth/status", "get"),
        ("/api/v1/auth/login", "post"),
        ("/api/v1/auth/extension-token", "post"),
    ):
        assert not paths[public_path][method].get("security")
    for protected_path in ("/api/v1/auth/logout", "/api/v1/auth/revoke"):
        assert paths[protected_path]["post"]["security"] == [
            {"BearerAuth": []},
            {"SessionCookie": []},
        ]


def test_auth_status_is_typed_and_reports_only_safe_capabilities() -> None:
    client = _client(_Container(_policy()))

    payload: dict[str, Any] = client.get("/api/v1/auth/status").json()

    assert payload == {
        "enabled": True,
        "authenticated": False,
        "password_configured": True,
        "installer_bearer_configured": True,
        "extension_access_enabled": True,
        "trust_loopback": False,
    }
    forbidden_names = {"password_hash", "session_secret", "extension_access_records", "token"}
    assert forbidden_names.isdisjoint(payload)


def test_trusted_loopback_bypass_is_same_origin_only() -> None:
    client = _loopback_client(
        _Container(AccessPolicy(token="configured-installer-token"), trust_loopback=True)
    )

    assert client.get("/api/v1/settings").status_code == 401
    assert (
        client.get("/api/v1/settings", headers={"Origin": "http://127.0.0.1:8420"}).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/settings", headers={"Origin": "chrome-extension://untrusted-extension"}
        ).status_code
        == 401
    )

    assert (
        client.get("/api/v1/settings", headers={"Origin": "https://evil.example"}).status_code
        == 401
    )


def test_extension_privileged_fetch_requires_exchanged_bearer_without_cors_trust() -> None:
    _key_id, device_key, record = auth_core.generate_extension_access_key()
    container = _Container(_policy(extension_records=(record,)), trust_loopback=True)
    client = _loopback_client(container)
    origin = "chrome-extension://any-installed-extension"

    exchange = client.post(
        "/api/v1/auth/extension-token",
        headers={"Origin": origin},
        json={"key": device_key},
    )
    assert exchange.status_code == 200
    response = client.get(
        "/api/v1/settings",
        headers={
            "Origin": origin,
            "Authorization": f"Bearer {exchange.json()['token']}",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers

    preflight = client.options(
        "/api/v1/settings",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers
    assert (
        client.get("/api/v1/settings", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 401
    )
    assert (
        client.get(
            "/api/v1/settings",
            headers={"Host": "rebound.example", "Origin": "http://rebound.example"},
        ).status_code
        == 401
    )


def test_loopback_bypass_is_disabled_by_authoritative_setting() -> None:
    client = _loopback_client(
        _Container(AccessPolicy(token="configured-installer-token"), trust_loopback=False)
    )

    assert client.get("/api/v1/settings").status_code == 401


def test_public_auth_rate_limits_are_bounded_separate_and_clock_driven() -> None:
    clock = _Clock()
    _key_id, device_key, record = auth_core.generate_extension_access_key()
    policy = AccessPolicy(
        token="installer-only-token",
        password_hash=auth_core.hash_password("correct horse battery staple"),
        session_secret="rate-limit-session-secret",
        extension_access_enabled=True,
        extension_access_records=(record,),
        clock=clock,
        rate_limit_max_failures=2,
        rate_limit_window_seconds=60,
        rate_limit_lockout_seconds=30,
        rate_limit_max_clients=2,
    )
    container = _Container(policy)
    client = _peer_client(container, "198.51.100.10")
    wrong_password = "never-log-this-password"

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={"password": wrong_password},
        )
        assert response.status_code == 401
    limited = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"password": wrong_password},
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "30"
    assert limited.json() == {
        "error": {"code": "rate_limited", "message": "request rate limit exceeded"}
    }
    assert wrong_password not in limited.text

    extension = client.post(
        "/api/v1/auth/extension-token",
        headers={"Origin": "chrome-extension://rate-limit-test"},
        json={"key": device_key},
    )
    assert extension.status_code == 200

    wrong_device_key = f"{device_key}never-log-this-device-key"
    for _ in range(2):
        response = client.post(
            "/api/v1/auth/extension-token",
            headers={"Origin": "chrome-extension://rate-limit-test"},
            json={"key": wrong_device_key},
        )
        assert response.status_code == 401
    extension_limited = client.post(
        "/api/v1/auth/extension-token",
        headers={"Origin": "chrome-extension://rate-limit-test"},
        json={"key": wrong_device_key},
    )
    assert extension_limited.status_code == 429
    assert extension_limited.headers["Retry-After"] == "30"
    assert wrong_device_key not in extension_limited.text

    clock.value += 30
    assert (
        client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={"password": wrong_password},
        ).status_code
        == 401
    )

    bounded_policy = AccessPolicy(
        token="installer-only-token",
        password_hash=auth_core.hash_password("correct horse battery staple"),
        session_secret="bounded-rate-limit-secret",
        clock=clock,
        rate_limit_max_failures=1,
        rate_limit_window_seconds=60,
        rate_limit_lockout_seconds=30,
        rate_limit_max_clients=2,
    )
    bounded = _Container(bounded_policy)
    for peer in ("198.51.100.1", "198.51.100.2", "198.51.100.3"):
        assert (
            _peer_client(bounded, peer)
            .post(
                "/api/v1/auth/login",
                headers={"Origin": "https://testserver"},
                json={"password": "wrong"},
            )
            .status_code
            == 401
        )
    first_peer = _peer_client(bounded, "198.51.100.1")
    assert (
        first_peer.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={"password": "wrong"},
        ).status_code
        == 401
    )
    assert (
        first_peer.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={"password": "wrong"},
        ).status_code
        == 429
    )


def test_auth_epoch_and_password_fingerprint_reconcile_across_restarts(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'auth.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=url))

    def current_epoch() -> int:
        with UnitOfWork(session_factory) as uow:
            return uow.auth_state.current_epoch()

    def bump_epoch() -> int:
        with UnitOfWork(session_factory) as uow:
            epoch = uow.auth_state.bump_epoch()
            uow.commit()
            return epoch

    def reconcile_fingerprint(fingerprint: str) -> bool:
        with UnitOfWork(session_factory) as uow:
            changed = uow.auth_state.reconcile_password_fingerprint(fingerprint)
            uow.commit()
            return changed

    initial_hash = auth_core.hash_password("initial-password")
    first_process = AccessPolicy(
        password_hash=initial_hash,
        session_secret="restart-stable-signing-secret",
        epoch_getter=current_epoch,
        epoch_bumper=bump_epoch,
        fingerprint_reconciler=reconcile_fingerprint,
        clock=_Clock(),
    )
    assert first_process.reconcile_password_fingerprint() is False
    assert current_epoch() == 0
    old_session = first_process.mint_session(ttl_hours=1)
    assert first_process.verify_session(old_session) is True

    unchanged_restart = AccessPolicy(
        password_hash=initial_hash,
        session_secret="restart-stable-signing-secret",
        epoch_getter=current_epoch,
        epoch_bumper=bump_epoch,
        fingerprint_reconciler=reconcile_fingerprint,
        clock=_Clock(),
    )
    assert unchanged_restart.reconcile_password_fingerprint() is False
    assert current_epoch() == 0
    assert unchanged_restart.verify_session(old_session) is True

    rotated = AccessPolicy(
        password_hash=auth_core.hash_password("rotated-password"),
        session_secret="restart-stable-signing-secret",
        epoch_getter=current_epoch,
        epoch_bumper=bump_epoch,
        fingerprint_reconciler=reconcile_fingerprint,
        clock=_Clock(),
    )
    assert rotated.reconcile_password_fingerprint() is True
    assert current_epoch() == 1
    assert rotated.verify_session(old_session) is False
    assert rotated.reconcile_password_fingerprint() is False
    assert current_epoch() == 1

    with session_factory() as session:
        rows = session.scalars(select(AuthStateModel).order_by(AuthStateModel.key)).all()
        assert [row.key for row in rows] == ["password_fingerprint", "session_epoch"]
        assert next(row for row in rows if row.key == "session_epoch").integer_value == 1
    assert "auth_state" in inspect(engine).get_table_names()
    database_bytes = (tmp_path / "auth.db").read_bytes()
    for secret in (
        "restart-stable-signing-secret",
        initial_hash,
        rotated.password_hash,
    ):
        assert secret.encode() not in database_bytes
    engine.dispose()


def test_password_fingerprint_reconcile_failure_closes_session_auth() -> None:
    policy = AccessPolicy(
        password_hash=auth_core.hash_password("configured-password"),
        session_secret="configured-session-secret",
        fingerprint_reconciler=lambda _fingerprint: (_ for _ in ()).throw(OSError()),
    )

    with pytest.raises(DependencyUnavailableError):
        policy.reconcile_password_fingerprint()
    with pytest.raises(DependencyUnavailableError):
        policy.mint_session(ttl_hours=1)


def test_vnext_auth_environment_is_authoritative_and_never_accepts_plain_device_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.config as legacy_config

    _key_id, device_key, record = auth_core.generate_extension_access_key()
    legacy = SimpleNamespace(
        api=SimpleNamespace(
            auth=SimpleNamespace(
                enabled=True,
                password_hash="legacy-password-hash",
                session_secret="legacy-session-secret",
                session_ttl_hours=99,
                extension_access_enabled=True,
                extension_access_keys=[record],
                extension_token_ttl_hours=99,
            )
        )
    )
    monkeypatch.setattr(legacy_config, "load_config", lambda: legacy)
    for name in (
        "OPENBILICLAW_ACCESS_TOKEN",
        "OPENBILICLAW_WEB_PASSWORD_HASH",
        "OPENBILICLAW_SESSION_SECRET",
        "OPENBILICLAW_EXTENSION_ACCESS_KEYS",
    ):
        monkeypatch.delenv(name, raising=False)

    unconfigured = AccessPolicy.from_environment()
    assert unconfigured.password_hash == ""
    assert unconfigured.session_secret == ""
    assert unconfigured.extension_access_records == ()

    monkeypatch.setenv("OPENBILICLAW_WEB_PASSWORD_HASH", "vnext-password-hash")
    monkeypatch.setenv("OPENBILICLAW_SESSION_SECRET", "vnext-session-secret")
    monkeypatch.setenv("OPENBILICLAW_EXTENSION_ACCESS_KEYS", json.dumps([record]))
    configured = AccessPolicy.from_environment()
    assert configured.password_hash == "vnext-password-hash"
    assert configured.session_secret == "vnext-session-secret"
    assert configured.extension_access_records == (record,)
    assert device_key not in repr(configured)
    status_payload = _client(_Container(configured)).get("/api/v1/auth/status").json()
    assert status_payload["password_configured"] is True
    assert status_payload["extension_access_enabled"] is True
