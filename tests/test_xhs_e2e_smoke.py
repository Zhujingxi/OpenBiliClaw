"""End-to-end smoke test for the xhs safe-discovery pipeline.

Guarded by ``XHS_E2E_SMOKE=1`` so CI and local dev default do not need
docker. When enabled, expects the xhs-sidecar container to be running
and reachable.

Usage::

    docker compose up -d xhs-sidecar
    XHS_E2E_SMOKE=1 .venv/bin/pytest tests/test_xhs_e2e_smoke.py -q
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_SMOKE_ENABLED = os.environ.get("XHS_E2E_SMOKE", "") == "1"

pytestmark = pytest.mark.skipif(
    not _SMOKE_ENABLED,
    reason="XHS_E2E_SMOKE=1 not set; skipping live sidecar test",
)


@pytest.fixture
def smoke_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from types import SimpleNamespace

    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "smoke.db")
    db.initialize()

    sidecar_url = os.environ.get(
        "OPENBILICLAW_XHS_SIDECAR_URL", "http://127.0.0.1:5556"
    )

    fake_config = SimpleNamespace(
        data_path=tmp_path,
        bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
        sources=SimpleNamespace(
            browser_cdp_url="",
            browser_headed=False,
            xiaohongshu=SimpleNamespace(
                sidecar_url=sidecar_url,
                daily_search_budget=20,
                daily_creator_budget=10,
                task_interval_seconds=45,
            ),
        ),
        scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
    monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
    monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")

    from openbiliclaw.api.app import create_app

    app = create_app(database=db)
    return TestClient(app)


@pytest.mark.integration
def test_sidecar_health(smoke_client: TestClient) -> None:
    """Sidecar /health must respond with 200."""
    import httpx

    sidecar_url = os.environ.get(
        "OPENBILICLAW_XHS_SIDECAR_URL", "http://127.0.0.1:5556"
    )
    resp = httpx.get(f"{sidecar_url}/health", timeout=5)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.integration
def test_observed_urls_accepted(smoke_client: TestClient) -> None:
    """Backend accepts observed xhs URLs and stores them."""
    resp = smoke_client.post(
        "/api/sources/xhs/observed-urls",
        json={
            "urls": ["https://www.xiaohongshu.com/explore/abc123def456"],
            "page_type": "search",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.integration
def test_task_queue_round_trip(smoke_client: TestClient) -> None:
    """Task queue: no tasks → 204, then result posted → 200."""
    resp = smoke_client.get("/api/sources/xhs/next-task")
    assert resp.status_code == 204

    resp = smoke_client.post(
        "/api/sources/xhs/task-result",
        json={
            "task_id": "e2e-fake",
            "status": "ok",
            "urls": ["https://www.xiaohongshu.com/explore/e2etest"],
        },
    )
    assert resp.status_code == 200


@pytest.mark.integration
def test_creator_subscription_lifecycle(smoke_client: TestClient) -> None:
    """Add → list → delete creator subscription."""
    resp = smoke_client.post(
        "/api/sources/xhs/creators",
        json={
            "creator_id": "e2e_user",
            "creator_url": "https://www.xiaohongshu.com/user/profile/e2e_user",
            "display_name": "E2E Test User",
        },
    )
    assert resp.status_code == 201

    resp = smoke_client.get("/api/sources/xhs/creators")
    items = resp.json()["items"]
    assert len(items) == 1

    resp = smoke_client.delete(f"/api/sources/xhs/creators/{items[0]['id']}")
    assert resp.status_code == 200
