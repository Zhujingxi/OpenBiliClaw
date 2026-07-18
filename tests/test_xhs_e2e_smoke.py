"""End-to-end smoke test for the xhs safe-discovery pipeline.

Guarded by ``XHS_E2E_SMOKE=1`` so CI and local dev default do not need
docker. Tests the backend API endpoints for xhs content ingestion.

Usage::

    XHS_E2E_SMOKE=1 .venv/bin/pytest tests/test_xhs_e2e_smoke.py -q
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

_SMOKE_ENABLED = os.environ.get("XHS_E2E_SMOKE", "") == "1"

pytestmark = pytest.mark.skipif(
    not _SMOKE_ENABLED,
    reason="XHS_E2E_SMOKE=1 not set; skipping live test",
)


@pytest.fixture
def smoke_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from openbiliclaw.config import Config
    from openbiliclaw.storage.database import Database

    from .model_route_helpers import use_native_ollama

    db = Database(tmp_path / "smoke.db")
    db.initialize()

    fake_config = Config(data_dir=str(tmp_path))
    use_native_ollama(fake_config)
    fake_config.sources.xiaohongshu.daily_search_budget = 20
    fake_config.sources.xiaohongshu.daily_creator_budget = 10
    fake_config.sources.xiaohongshu.task_interval_seconds = 45
    fake_config.scheduler.pool_target_count = 300
    fake_config.scheduler.account_sync_interval_hours = 24
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
    monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")

    from openbiliclaw.api.app import create_app

    app = create_app(database=db)
    return TestClient(app)


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
