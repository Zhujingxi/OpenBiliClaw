"""Exact response contract for the pilot endpoints.

``/api/ping`` and ``/api/qr-info`` were extracted in pilot 1 (api/routes/system.py).
``/api/health`` and ``/api/init-status`` were extracted in pilot 2 (api/routes/health.py).

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
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    client = TestClient(app)
    response = client.get("/api/ping")
    assert response.status_code == 200
    assert response.headers["content-type"] == _JSON_CONTENT_TYPE
    assert response.json() == {"status": "ok", "service": "openbiliclaw-api"}
    assert response.content == b'{"status":"ok","service":"openbiliclaw-api"}'


def test_qr_info_exact_response_with_ip(monkeypatch: pytest.MonkeyPatch) -> None:
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
    from openbiliclaw.api import app as app_module

    monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: None)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    client = TestClient(app)
    response = client.get("/api/qr-info")
    assert response.status_code == 200
    assert response.headers["content-type"] == _JSON_CONTENT_TYPE
    assert response.json() == {"lan_ip": None}
    assert response.content == b'{"lan_ip":null}'


def test_health_exact_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.api import app as app_module

    monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: "192.168.1.100")

    class _MockDB:
        def get_latest_init_run(self) -> None:
            return None

        def update_init_run(self, *args: object, **kwargs: object) -> None:
            pass

        def create_init_run(self, *args: object, **kwargs: object) -> str:
            return "test-run-id"

        def init_active(self) -> bool:
            return False

        def count_events_by_source_platform(self) -> dict[str, int]:
            return {}

        def initialize(self) -> None:
            pass

    app = create_app(
        memory_manager=object(),
        database=_MockDB(),
        soul_engine=object(),
        runtime_controller=object(),
        runtime_event_hub=object(),
    )
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["content-type"] == _JSON_CONTENT_TYPE
    assert response.json() == {
        "status": "ok",
        "service": "openbiliclaw-api",
        "lan_ip": "192.168.1.100",
        "embedding_ready": False,
    }
    assert response.content == (
        b'{"status":"ok","service":"openbiliclaw-api",'
        b'"lan_ip":"192.168.1.100","embedding_ready":false}'
    )


# pylint: disable=too-many-statements
def test_init_status_exact_response(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.api import app as app_module

    monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: "192.168.1.100")

    class _MockDB:
        def get_latest_init_run(self) -> None:
            return None

        def update_init_run(self, *args: object, **kwargs: object) -> None:
            pass

        def create_init_run(self, *args: object, **kwargs: object) -> str:
            return "test-run-id"

        def init_active(self) -> bool:
            return False

        def count_events_by_source_platform(self) -> dict[str, int]:
            return {}

        def initialize(self) -> None:
            pass

    app = create_app(
        memory_manager=object(),
        database=_MockDB(),
        soul_engine=object(),
        runtime_controller=object(),
        runtime_event_hub=object(),
    )
    client = TestClient(app)
    response = client.get("/api/init-status")

    assert response.status_code == 200
    assert response.headers["content-type"] == _JSON_CONTENT_TYPE

    data = response.json()
    assert data["initialized"] is False
    assert data["running"] is False
    assert data["run_id"] is None
    assert data["sequence"] == 0
    assert data["current_stage"] == 0
    assert data["total_stages"] == 4
    assert len(data["stages"]) == 4
    assert data["partial_success"] is False
    assert data["can_start"] is False
    assert data["can_manage"] is False
    assert data["start_mode"] == "local_only"
    assert data["reason"] == "local_only"
    assert data["detail"] == "只能在本机发起初始化"
    assert data["last_failure_reason"] == ""
    assert data["last_failure_detail"] == ""
    assert data["last_activity"] == ""
    prereqs = data["prerequisites"]
    assert prereqs["bilibili_logged_in"] is False
    assert prereqs["bilibili_check"] == "failed"
    assert prereqs["llm_ready"] is False
    assert prereqs["embedding_ready"] is False
    assert prereqs["embedding_check"] == "disabled"
    assert prereqs["embedding_repair_running"] is False
    assert prereqs["embedding_repair_completed"] == 0
    assert prereqs["embedding_repair_total"] == 0
    assert prereqs["ollama_phase"] == "ready"
    assert prereqs["embedding_pull_status"] == ""
    assert prereqs["embedding_required"] is False
    assert prereqs["enabled_platforms"] == []

    # Lock serialized bytes — each b-string < 100 chars.
    assert response.content == (
        b'{"initialized":false,"running":false,"run_id":null,'
        b'"sequence":0,"current_stage":0,"total_stages":4,'
        b'"stages":['
        b'{"n":1,"label":"\xe6\x8b\x89\xe5\x8f\x96\xe6\x95\xb0\xe6\x8d\xae",'
        b'"status":"pending","reason":null,"progress":null,'
        b'"eta_seconds":90},'
        b'{"n":2,"label":"\xe5\x88\x86\xe6\x9e\x90\xe5\x81\x8f\xe5\xa5\xbd",'
        b'"status":"pending","reason":null,"progress":null,'
        b'"eta_seconds":180},'
        b'{"n":3,"label":"\xe7\x94\x9f\xe6\x88\x90\xe7\x94\xbb\xe5\x83\x8f",'
        b'"status":"pending","reason":null,"progress":null,'
        b'"eta_seconds":70},'
        b'{"n":4,'
        b'"label":"\xe5\x8f\x91\xe7\x8e\xb0\xe5\x86\x85\xe5\xae\xb9\xe6\xb1\xa0",'
        b'"status":"pending","reason":null,"progress":null,'
        b'"eta_seconds":120}],'
        b'"partial_success":false,'
        b'"can_start":false,"can_manage":false,'
        b'"start_mode":"local_only",'
        b'"prerequisites":{'
        b'"bilibili_logged_in":false,'
        b'"bilibili_check":"failed",'
        b'"bilibili_detail":"'
        b'\xe5\x90\x8e\xe7\xab\xaf\xe8\xbf\x98\xe6\xb2\xa1\xe6\x9c\x89'
        b'\xe6\x94\xb6\xe5\x88\xb0 B\xe7\xab\x99 Cookie\xe3\x80\x82",'
        b'"llm_ready":false,'
        b'"embedding_ready":false,'
        b'"embedding_check":"disabled",'
        b'"embedding_detail":"",'
        b'"embedding_repair_running":false,'
        b'"embedding_repair_completed":0,'
        b'"embedding_repair_total":0,'
        b'"ollama_phase":"ready",'
        b'"embedding_pull_status":"",'
        b'"embedding_required":false,'
        b'"enabled_platforms":[]'
        b"},"
        b'"reason":"local_only",'
        b'"detail":"'
        b'\xe5\x8f\xaa\xe8\x83\xbd\xe5\x9c\xa8\xe6\x9c\xac\xe6\x9c\xba'
        b'\xe5\x8f\x91\xe8\xb5\xb7\xe5\x88\x9d\xe5\xa7\x8b\xe5\x8c\x96",'
        b'"last_failure_reason":"",'
        b'"last_failure_detail":"",'
        b'"last_activity":""'
        b"}"
    )
