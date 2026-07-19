"""Shared auth fixtures for browser/E2E tests (the quarantined 17-file suite).

Two authentication strategies, per the user-approved design (2026-07-19):

1. **Loopback bypass (default)** — ``trust_loopback=True`` makes the auth
   middleware skip token checks for requests arriving from 127.0.0.1 *without*
   cross-origin browser headers (no ``Origin``, no
   ``Sec-Fetch-Site: cross-site|same-site``). See
   ``src/openbiliclaw/api/auth.py`` ``AuthGate.is_trusted_local`` /
   ``_origin_safe_for_local``. Tests get this path by serving the app on a
   loopback uvicorn (:func:`start_loopback_server`) and talking to it with a
   header-clean client (:func:`loopback_client`). This is the same path
   ``tests/test_bili_extension_browser_e2e.py`` has always used implicitly.

2. **Extension-token exchange (opt-in)** — for tests that later drive the real
   Chrome extension in a container (where the browser is not a loopback peer),
   enable ``extension_access_enabled`` + a fixed test device key, then call
   :func:`mint_extension_token` to exchange the key at
   ``POST /api/auth/extension-token`` for a signed Bearer token. This mirrors
   ``extension/src/shared/auth.ts`` ``exchangeDeviceKey``. It is NOT the
   default: host-side tests should prefer the loopback bypass.

The app factory (:func:`build_e2e_app`) generalizes the ``_build_app`` pattern
from ``tests/test_api_auth.py`` so E2E files share one construction site. It
writes a real config into an isolated ``OPENBILICLAW_PROJECT_ROOT`` because
``create_app`` loads config via the global ``load_config()``.

Fixed test-only secrets below follow the ``_SECRET`` pattern from
``tests/test_api_auth.py``; they are never valid outside a tmp_path sandbox.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import uvicorn

from openbiliclaw.api.app import create_app
from openbiliclaw.storage.database import Database

from .model_route_helpers import use_native_ollama

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openbiliclaw.config import Config

# Fixed test-only signing secret — mirrors tests/test_api_auth.py::_SECRET.
E2E_SESSION_SECRET = "fixed-e2e-session-secret-bbbbbbbbbbbb"


def make_extension_device_key() -> tuple[str, str]:
    """Generate a test device key + its config record.

    Returns ``(full_key, config_record)``: ``full_key`` is what the extension
    (or :func:`mint_extension_token`) presents; ``config_record`` is the
    digest-only entry for ``extension_access_keys`` (config never stores the
    raw secret). Delegates to ``auth_core.generate_extension_access_key`` —
    the same helper the CLI uses to provision real devices.
    """
    from openbiliclaw import auth_core

    _key_id, full_key, record = auth_core.generate_extension_access_key()
    return full_key, record

# TestClient-visible loopback origin; real servers get their own port.
_LOOPBACK_ORIGIN = "http://127.0.0.1:8420"


def find_free_port() -> int:
    """Return an available loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_e2e_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_enabled: bool = True,
    trust_loopback: bool = True,
    extension_access_enabled: bool = False,
    extension_access_keys: tuple[str, ...] = (),
    session_secret: str = E2E_SESSION_SECRET,
    session_ttl_hours: int = 0,
    extension_token_ttl_hours: int = 24,
) -> Config:
    """Write an isolated config with a deterministic auth surface.

    Sets ``OPENBILICLAW_PROJECT_ROOT`` to ``tmp_path / "runtime"`` and saves
    the config there so the global ``load_config()`` inside ``create_app``
    picks up exactly these values (env auth vars from the developer shell are
    neutralized first so they cannot leak into the test gate).
    """
    from openbiliclaw.config import API_AUTH_ENV_VARS, Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    for var in API_AUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    cfg = Config()
    cfg.scheduler.enabled = False
    use_native_ollama(cfg)
    cfg.api.auth.enabled = auth_enabled
    cfg.api.auth.session_secret = session_secret
    cfg.api.auth.session_ttl_hours = session_ttl_hours
    cfg.api.auth.trust_loopback = trust_loopback
    cfg.api.auth.extension_access_enabled = extension_access_enabled
    cfg.api.auth.extension_access_keys = list(extension_access_keys)
    cfg.api.auth.extension_token_ttl_hours = extension_token_ttl_hours
    save_config(cfg, project_root / "config.toml")
    return cfg


def build_e2e_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_enabled: bool = True,
    trust_loopback: bool = True,
    extension_access_enabled: bool = False,
    extension_access_keys: tuple[str, ...] = (),
    session_secret: str = E2E_SESSION_SECRET,
    session_ttl_hours: int = 0,
    extension_token_ttl_hours: int = 24,
    seed_content: bool = True,
    runtime_event_hub: object | None = None,
) -> tuple[FastAPI, Database]:
    """Create the API app + real SQLite DB wired for E2E auth testing.

    Defaults enable auth with the loopback bypass on: a real server bound to
    127.0.0.1 (or a loopback TestClient) passes without any token, while
    cross-origin browser-shaped requests still get 401.
    """
    build_e2e_config(
        tmp_path,
        monkeypatch,
        auth_enabled=auth_enabled,
        trust_loopback=trust_loopback,
        extension_access_enabled=extension_access_enabled,
        extension_access_keys=extension_access_keys,
        session_secret=session_secret,
        session_ttl_hours=session_ttl_hours,
        extension_token_ttl_hours=extension_token_ttl_hours,
    )

    db = Database(tmp_path / "e2e.db")
    db.initialize()
    if seed_content:
        db.cache_content(
            "BV1E2E",
            title="t",
            up_name="u",
            source="test",
            source_platform="bilibili",
            content_url="https://www.bilibili.com/video/BV1E2E",
        )
    app = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
        runtime_event_hub=runtime_event_hub,
    )
    return app, db


# Test-only extension device key pair (fixed, deterministic, never valid outside tests).
# The full key is what the extension presents; the record is the digest-only config entry.
E2E_EXTENSION_DEVICE_KEY, E2E_EXTENSION_DEVICE_KEY_RECORD = make_extension_device_key()


def loopback_test_client(app: FastAPI) -> TestClient:
    """TestClient wired for the loopback bypass (no Origin/Sec-Fetch headers).

    Prefer :func:`start_loopback_server` + :func:`loopback_client` for true
    E2E; this is the in-process equivalent for tests that must stay on
    TestClient (e.g. SQLite connections that reject cross-thread access).
    """
    from fastapi.testclient import TestClient

    return TestClient(app, client=("127.0.0.1", 5000), base_url=_LOOPBACK_ORIGIN)


class LoopbackServer:
    """Background uvicorn serving ``app`` on a free 127.0.0.1 port.

    Pattern lifted from ``tests/test_bili_extension_browser_e2e.py``::
        ``_start_backend``. Use as a context manager for clean shutdown.
    """

    def __init__(self, app: FastAPI, *, timeout_seconds: float = 15.0) -> None:
        port = find_free_port()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
        )
        self._app = app
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self.base_url = f"http://127.0.0.1:{port}"
        self._timeout = timeout_seconds

    @property
    def app(self) -> FastAPI:
        return self._app

    def start(self) -> LoopbackServer:
        self._thread.start()
        self._wait_ready()
        return self

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                response = httpx.get(f"{self.base_url}/api/ping", timeout=2.0, trust_env=False)
                if response.status_code == 200:
                    return
            time.sleep(0.2)
        raise RuntimeError(f"E2E backend did not become ready: {self.base_url}")

    def close(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    def __enter__(self) -> LoopbackServer:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()


def start_loopback_server(app: FastAPI) -> LoopbackServer:
    """Start ``app`` on a free loopback port; returns a running server handle.

    The handle is NOT started twice: either call ``server.close()`` when done,
    or use ``with LoopbackServer(app) as server: ...`` which starts/stops for
    you. Do not wrap an already-started handle in another ``with``.
    """
    return LoopbackServer(app).start()


def loopback_client(base_url: str) -> httpx.Client:
    """HTTP client for the loopback bypass — no Origin / Sec-Fetch headers.

    Do NOT add ``Origin`` or ``Sec-Fetch-Site`` headers: browser-shaped
    cross-origin headers forfeit the trusted-local bypass by design
    (``auth.py`` ``_origin_safe_for_local``, review r7/r9) and the request
    would fall back to token auth and fail with 401.
    """
    return httpx.Client(base_url=base_url, timeout=10.0, trust_env=False)


def mint_extension_token(base_url: str, device_key: str = E2E_EXTENSION_DEVICE_KEY) -> str:
    """Exchange a device key for a signed Bearer token (extension auth path).

    Calls ``POST /api/auth/extension-token`` exactly like the extension's
    ``exchangeDeviceKey`` (extension/src/shared/auth.ts). Requires the app to
    be built with ``extension_access_enabled=True`` and the device key listed
    in ``extension_access_keys``; raises on any non-ok response.
    """
    with loopback_client(base_url) as client:
        response = client.post("/api/auth/extension-token", json={"key": device_key})
    if response.status_code != 200:
        raise RuntimeError(
            f"extension-token exchange failed: {response.status_code} {response.text}"
        )
    payload = response.json()
    token = payload.get("token")
    if not payload.get("ok") or not isinstance(token, str) or not token:
        raise RuntimeError(f"extension-token exchange returned no token: {payload}")
    return token
