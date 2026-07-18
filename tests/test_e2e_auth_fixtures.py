"""Unit tests for the shared E2E auth fixtures (tests/e2e_auth_fixtures.py).

These are ordinary fast tests (not part of the quarantined browser suite):
they prove the loopback bypass grants token-free access to host-side callers,
that browser-shaped cross-origin requests are still rejected, and that the
opt-in extension-token exchange mints a usable Bearer token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .e2e_auth_fixtures import (
    E2E_EXTENSION_DEVICE_KEY_RECORD,
    LoopbackServer,
    build_e2e_app,
    find_free_port,
    loopback_client,
    loopback_test_client,
    mint_extension_token,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_loopback_request_passes_with_no_token(tmp_path: Path, monkeypatch) -> None:
    """Auth enabled + trust_loopback: header-clean loopback caller skips tokens."""
    app, _db = build_e2e_app(tmp_path, monkeypatch)
    with LoopbackServer(app) as server, loopback_client(server.base_url) as client:
        response = client.get("/api/favorites/BV1E2E")
    assert response.status_code == 200


def test_loopback_test_client_also_bypasses(tmp_path: Path, monkeypatch) -> None:
    """In-process TestClient variant gets the same bypass (no HTTP server)."""
    app, _db = build_e2e_app(tmp_path, monkeypatch)
    client = loopback_test_client(app)
    response = client.get("/api/favorites/BV1E2E")
    assert response.status_code == 200


def test_cross_origin_browser_headers_forfeit_the_bypass(
    tmp_path: Path, monkeypatch
) -> None:
    """Origin: http://evil.example from the same loopback peer must be 401.

    This is the localhost-CSRF / DNS-rebinding defense in auth.py
    ``_origin_safe_for_local``; the fixture must not accidentally defeat it.
    """
    app, _db = build_e2e_app(tmp_path, monkeypatch)
    with LoopbackServer(app) as server, loopback_client(server.base_url) as client:
        response = client.get(
            "/api/favorites/BV1E2E",
            headers={"Origin": "http://evil.example"},
        )
    assert response.status_code == 401


def test_sec_fetch_cross_site_forfeits_the_bypass(tmp_path: Path, monkeypatch) -> None:
    """Fetch-Metadata cross-site browser requests must not inherit the bypass."""
    app, _db = build_e2e_app(tmp_path, monkeypatch)
    with LoopbackServer(app) as server, loopback_client(server.base_url) as client:
        response = client.get(
            "/api/favorites/BV1E2E",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
    assert response.status_code == 401


def test_extension_token_mint_and_bearer_works_when_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    """Opt-in extension path: device key exchanges for a usable Bearer token."""
    app, _db = build_e2e_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=(E2E_EXTENSION_DEVICE_KEY_RECORD,),
    )
    with LoopbackServer(app) as server:
        token = mint_extension_token(server.base_url)
        assert isinstance(token, str) and token
        # A caller bearing a chrome-extension Origin + the minted token gets in.
        # Per AuthGate.pick_token, extension origins may use an explicit Bearer
        # session (mirrors how the real extension talks to the backend).
        # NOTE: The extension Origin itself is enough to pass the loopback
        # bypass when the peer is 127.0.0.1 (auth.py is_extension_origin), so
        # to prove the token matters we assert on a NON-loopback peer. We
        # can't easily spoof the peer IP with httpx; instead we assert the
        # token works via TestClient with a non-loopback client tuple.
        from fastapi.testclient import TestClient

        remote = TestClient(app, client=("192.168.1.50", 5000))
        unauthorized = remote.get(
            "/api/favorites/BV1E2E",
            headers={"Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        )
        assert unauthorized.status_code == 401
        authorized = remote.get(
            "/api/favorites/BV1E2E",
            headers={
                "Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "Authorization": f"Bearer {token}",
            },
        )
        assert authorized.status_code == 200


def test_extension_token_exchange_rejects_unknown_device_key(
    tmp_path: Path, monkeypatch
) -> None:
    """The exchange endpoint stays strict: wrong key -> 401, helper raises."""
    app, _db = build_e2e_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=(E2E_EXTENSION_DEVICE_KEY_RECORD,),
    )
    with LoopbackServer(app) as server, pytest.raises(RuntimeError, match="401"):
        mint_extension_token(server.base_url, device_key="wrong-key")


def test_find_free_port_returns_bindable_loopback_port() -> None:
    import socket

    port = find_free_port()
    assert 1024 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
