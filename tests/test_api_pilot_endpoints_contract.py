"""Exact response contract for the pilot endpoints ``/api/ping`` and ``/api/qr-info``.

The route contract manifest (``api-route-contract.json``) locks the
*routing* surface. This file locks the *response body* shape: exact JSON
payload, exact content-type header, and exact serialized bytes. Together
they prove the narrow router-factory extraction did not change externally
visible behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app

_JSON_CONTENT_TYPE = "application/json"


def test_ping_exact_response() -> None:
    """``GET /api/ping`` returns exactly ``{"status":"ok","service":"openbiliclaw-api"}``
    with an exact application/json content-type and exact serialized bytes."""
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    client = TestClient(app)

    response = client.get("/api/ping")

    assert response.status_code == 200
    assert response.headers["content-type"] == _JSON_CONTENT_TYPE
    assert response.json() == {"status": "ok", "service": "openbiliclaw-api"}
    # Lock the serialized body bytes so a serializer-level drift (key order,
    # whitespace, unicode escaping) also trips the contract.
    assert response.content == b'{"status":"ok","service":"openbiliclaw-api"}'


def test_qr_info_exact_response_with_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /api/qr-info`` returns exactly ``{"lan_ip": "<ipv4>"}``."""
    from openbiliclaw.api import app as app_module

    monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: "192.168.1.7")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    client = TestClient(app)

    response = client.get("/api/qr-info")

    assert response.status_code == 200
    assert response.headers["content-type"] == _JSON_CONTENT_TYPE
    assert response.json() == {"lan_ip": "192.168.1.7"}
    assert response.content == b'{"lan_ip":"192.168.1.7"}'


def test_qr_info_exact_response_without_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /api/qr-info`` returns ``{"lan_ip": null}`` when no LAN IP is detected."""
    from openbiliclaw.api import app as app_module

    monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: None)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    client = TestClient(app)

    response = client.get("/api/qr-info")

    assert response.status_code == 200
    assert response.headers["content-type"] == _JSON_CONTENT_TYPE
    assert response.json() == {"lan_ip": None}
    assert response.content == b'{"lan_ip":null}'
