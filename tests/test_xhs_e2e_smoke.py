"""End-to-end smoke test for the xhs safe-discovery pipeline.

Guarded by ``XHS_E2E_SMOKE=1`` so CI and local dev default do not need
docker. Tests the backend API endpoints for xhs content ingestion.

Uses the shared E2E auth fixture (``tests/e2e_auth_fixtures.py``) with the
default loopback-bypass strategy: the TestClient talks to the app from a
loopback peer with no cross-origin browser headers, so no session token is
required. See ``docs/testing/e2e-guide.md`` for the full fixture documentation.

Usage::

    XHS_E2E_SMOKE=1 .venv/bin/pytest tests/test_xhs_e2e_smoke.py -q
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from .e2e_auth_fixtures import build_e2e_app, loopback_test_client

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from openbiliclaw.storage.database import Database

_SMOKE_ENABLED = os.environ.get("XHS_E2E_SMOKE", "") == "1"

pytestmark = pytest.mark.skipif(
    not _SMOKE_ENABLED,
    reason="XHS_E2E_SMOKE=1 not set; skipping live test",
)


@pytest.fixture
def smoke_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Database]:
    app, db = build_e2e_app(tmp_path, monkeypatch)
    return loopback_test_client(app), db


@pytest.mark.integration
def test_observed_urls_accepted(smoke_client: tuple[TestClient, Database]) -> None:
    """Backend accepts observed xhs URLs and stores them."""
    client, _db = smoke_client
    resp = client.post(
        "/api/sources/xhs/observed-urls",
        json={
            "urls": ["https://www.xiaohongshu.com/explore/abc123def456"],
            "page_type": "search",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.integration
def test_task_queue_round_trip(smoke_client: tuple[TestClient, Database]) -> None:
    """Task queue round trip against the current server contract.

    Empty queue → 204. Enqueue a real task (via ``XhsTaskQueue`` on the same
    DB the app uses), claim it through ``next-task`` → 200 with the task id,
    then post the result for that *existing* id → 200. Posting a result for
    an unknown id is a 409 conflict by contract
    (``src/openbiliclaw/api/app.py`` ``_require_legacy_task``), so the smoke
    test must use a task it created itself.
    """
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

    client, db = smoke_client

    # Empty queue → 204 No Content.
    resp = client.get("/api/sources/xhs/next-task")
    assert resp.status_code == 204

    # Enqueue a real task on the same DB the app's queue reads from.
    queue = XhsTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "search",
        {"keyword": "e2e-smoke", "source": "e2e"},
    )
    assert task_id, "enqueue_with_id must return a task id (default budget allows it)"

    # Claim it through the dispatcher endpoint → 200 with our task id.
    resp = client.get("/api/sources/xhs/next-task")
    assert resp.status_code == 200
    assert resp.json()["id"] == task_id

    # Post the result for the existing (claimed) task → 200.
    resp = client.post(
        "/api/sources/xhs/task-result",
        json={
            "task_id": task_id,
            "status": "ok",
            "urls": ["https://www.xiaohongshu.com/explore/e2etest"],
        },
    )
    assert resp.status_code == 200

    # Queue is drained again → 204.
    resp = client.get("/api/sources/xhs/next-task")
    assert resp.status_code == 204


@pytest.mark.integration
def test_creator_subscription_lifecycle(smoke_client: tuple[TestClient, Database]) -> None:
    """Add → list → delete creator subscription."""
    client, _db = smoke_client
    resp = client.post(
        "/api/sources/xhs/creators",
        json={
            "creator_id": "e2e_user",
            "creator_url": "https://www.xiaohongshu.com/user/profile/e2e_user",
            "display_name": "E2E Test User",
        },
    )
    assert resp.status_code == 201

    resp = client.get("/api/sources/xhs/creators")
    items = resp.json()["items"]
    assert len(items) == 1

    resp = client.delete(f"/api/sources/xhs/creators/{items[0]['id']}")
    assert resp.status_code == 200
