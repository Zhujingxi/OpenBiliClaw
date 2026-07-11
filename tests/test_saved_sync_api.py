from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.saved_sync.models import (
    NativeSaveCapability,
    NativeSaveResult,
    NativeSaveRoute,
    SavedItemInput,
    SavedMembershipResult,
)
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.saved_sync.service import SavedSyncService
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _FakeBilibiliAdapter:
    capability = NativeSaveCapability(
        platform="bilibili",
        supports_favorite=True,
        supports_watch_later=True,
        supports_named_collection=True,
    )

    def __init__(self, *, status: str = "synced") -> None:
        self.status = status
        self.calls: list[str] = []

    def target_label(self, action: str) -> str:
        return "B站稍后再看" if action == "watch_later" else "B站 OpenBiliClaw 收藏夹"

    async def save(
        self,
        item: SavedItemInput,
        route: NativeSaveRoute,
    ) -> NativeSaveResult:
        self.calls.append(item.item_key)
        if self.status == "login_required":
            return NativeSaveResult(
                item_key=item.item_key,
                status="login_required",
                resolved_action=route.resolved_action,
                resolved_target=route.resolved_target,
                error_code="bilibili_-101",
                error_message="Bilibili login required",
            )
        return NativeSaveResult(
            item_key=item.item_key,
            status="synced",
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )


@pytest.fixture
def saved_sync_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Database, _FakeBilibiliAdapter]]:
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    config = Config()
    config.scheduler.enabled = False
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    save_config(config, project_root / "config.toml")

    database = Database(tmp_path / "saved-sync.db")
    database.initialize()
    adapter = _FakeBilibiliAdapter()
    app = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=database,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
    )
    context = app.state.runtime_context
    context.config = config
    context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    yield TestClient(app), database, adapter


def _saved_item(content_id: str, **overrides: str) -> dict[str, str]:
    payload = {
        "source_platform": "bilibili",
        "content_id": content_id,
        "content_url": f"https://www.bilibili.com/video/{content_id}",
        "content_type": "video",
        "title": content_id,
        "author_name": "测试 UP",
        "cover_url": "https://i0.hdslb.com/bfs/archive/test.jpg",
        "note": "local note",
    }
    payload.update(overrides)
    return payload


def test_save_defaults_to_local_pending(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client

    response = client.post("/api/saved/watch_later", json=_saved_item("BV1LOCAL"))

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "item_key": "bilibili:BV1LOCAL",
        "sync_status": "pending",
        "sync_task_id": "",
        "resolved_action": "",
        "resolved_target": "",
        "error_code": "",
        "error_message": "",
    }
    row = database.get_saved_membership("watch_later", "bilibili:BV1LOCAL")
    assert row is not None
    assert row["note"] == "local note"
    assert adapter.calls == []


def test_auto_sync_returns_pending_task_without_waiting_for_platform_io(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    context = client.app.state.runtime_context
    context.config.saved_sync.auto_sync_enabled = True
    captured: list[object] = []

    def capture_task(_name: str, coro: object) -> object:
        captured.append(coro)
        return SimpleNamespace()

    context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
        task_starter=capture_task,  # type: ignore[arg-type]
    )

    response = client.post("/api/saved/favorite", json=_saved_item("BV1AUTO"))

    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True
    assert body["sync_status"] == "pending"
    assert body["sync_task_id"]
    assert adapter.calls == []
    assert len(captured) == 1
    captured[0].close()  # type: ignore[union-attr]


def test_auto_sync_response_stays_pending_if_background_result_wins_race(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, _adapter = saved_sync_client
    context = client.app.state.runtime_context
    context.config.saved_sync.auto_sync_enabled = True

    class _RacingService:
        def save_local(
            self,
            list_kind: str,
            item: SavedItemInput,
            note: str,
            auto_sync: bool,
        ) -> SavedMembershipResult:
            assert auto_sync is True
            database.upsert_saved_membership(list_kind, item, note)
            database.upsert_native_save_state(
                list_kind,
                item.item_key,
                requested_action=list_kind,
                resolved_action=list_kind,
                resolved_target="B站 OpenBiliClaw 收藏夹",
                status="login_required",
                task_id="old-terminal-task",
                last_error_code="bilibili_-101",
                last_error_message="Bilibili login required",
            )
            return SavedMembershipResult(
                saved=True,
                item_key=item.item_key,
                sync_status="pending",
                sync_task_id="11111111-1111-4111-8111-111111111111",
            )

    context.saved_sync_service = _RacingService()

    response = client.post("/api/saved/favorite", json=_saved_item("BV1RACE"))

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "item_key": "bilibili:BV1RACE",
        "sync_status": "pending",
        "sync_task_id": "11111111-1111-4111-8111-111111111111",
        "resolved_action": "",
        "resolved_target": "",
        "error_code": "",
        "error_message": "",
    }


def test_manual_sync_ignores_auto_sync_toggle_and_poll_exposes_terminal_result(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, _database, adapter = saved_sync_client
    assert client.app.state.runtime_context.config.saved_sync.auto_sync_enabled is False
    client.post("/api/saved/watch_later", json=_saved_item("BV1SYNC"))

    response = client.post(
        "/api/saved/watch_later/sync",
        json={"item_keys": ["bilibili:BV1SYNC"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"]
    assert body["items"] == [
        {
            "item_key": "bilibili:BV1SYNC",
            "status": "pending",
            "resolved_action": "watch_later",
            "resolved_target": "",
            "error_code": "",
            "error_message": "",
        }
    ]
    asyncio.run(client.app.state.runtime_context.saved_sync_service.run_sync_task(body["task_id"]))

    polled = client.get(f"/api/saved-sync/tasks/{body['task_id']}")
    assert polled.status_code == 200
    assert polled.json()["items"][0] == {
        "item_key": "bilibili:BV1SYNC",
        "status": "synced",
        "resolved_action": "watch_later",
        "resolved_target": "B站稍后再看",
        "error_code": "",
        "error_message": "",
    }
    assert adapter.calls == ["bilibili:BV1SYNC"]


def test_task_poll_preserves_login_required_instead_of_generic_success(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, _adapter = saved_sync_client
    adapter = _FakeBilibiliAdapter(status="login_required")
    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    client.post("/api/saved/favorite", json=_saved_item("BV1LOGIN"))
    created = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": ["bilibili:BV1LOGIN"]},
    ).json()

    asyncio.run(
        client.app.state.runtime_context.saved_sync_service.run_sync_task(created["task_id"])
    )

    item = client.get(f"/api/saved-sync/tasks/{created['task_id']}").json()["items"][0]
    assert item["status"] == "login_required"
    assert item["error_code"] == "bilibili_-101"
    assert item["error_message"] == "Bilibili login required"


def test_manual_sync_reports_missing_memberships_per_item_without_claiming_them(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, _database, _adapter = saved_sync_client
    client.post("/api/saved/favorite", json=_saved_item("BV1PRESENT"))

    response = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": ["bilibili:BV1PRESENT", "bilibili:BV1MISSING"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"]
    assert {item["item_key"]: item["status"] for item in body["items"]} == {
        "bilibili:BV1PRESENT": "pending",
        "bilibili:BV1MISSING": "failed",
    }
    missing = next(item for item in body["items"] if item["item_key"].endswith("MISSING"))
    assert missing["error_code"] == "not_saved_locally"
    assert missing["error_message"] == "Item is not saved locally"


def test_saved_list_status_and_local_only_remove_round_trip(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, _database, adapter = saved_sync_client
    client.post("/api/saved/favorite", json=_saved_item("BV1CRUD"))

    status = client.get(
        "/api/saved/favorite/status",
        params={"item_key": "bilibili:BV1CRUD"},
    )
    listing = client.get("/api/saved/favorite?limit=20&offset=0")
    removed = client.post(
        "/api/saved/favorite/remove",
        json={"item_key": "bilibili:BV1CRUD"},
    )

    assert status.status_code == 200
    assert status.json()["saved"] is True
    assert status.json()["sync_status"] == "pending"
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0] == {
        "item_key": "bilibili:BV1CRUD",
        "source_platform": "bilibili",
        "content_id": "BV1CRUD",
        "content_url": "https://www.bilibili.com/video/BV1CRUD",
        "content_type": "video",
        "title": "BV1CRUD",
        "author_name": "测试 UP",
        "cover_url": "https://i0.hdslb.com/bfs/archive/test.jpg",
        "note": "local note",
        "added_at": listing.json()["items"][0]["added_at"],
        "sync_status": "pending",
        "sync_task_id": "",
        "requested_action": "favorite",
        "resolved_action": "",
        "resolved_target": "",
        "error_code": "",
        "error_message": "",
    }
    assert removed.status_code == 200
    assert removed.json()["saved"] is False
    assert (
        client.get(
            "/api/saved/favorite/status",
            params={"item_key": "bilibili:BV1CRUD"},
        ).json()["saved"]
        is False
    )
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/api/saved/queue", {"json": _saved_item("BV1BAD")}),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": " ", "content_id": "BV1BAD"}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": "bad platform", "content_id": "BV1BAD"}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": "bilibili", "content_id": ""}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {"json": {**_saved_item("BV1BAD"), "unexpected": "field"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "not-an-item-key"}},
        ),
        (
            "get",
            "/api/saved/favorite/status",
            {"params": {"item_key": " "}},
        ),
        (
            "post",
            "/api/saved/favorite/sync",
            {"json": {"item_keys": ["bilibili:BV1OK", " "]}},
        ),
        ("get", "/api/saved-sync/tasks/not-a-uuid", {}),
        ("get", "/api/favorites/%20", {}),
    ],
)
def test_saved_routes_reject_invalid_identifiers_and_selections(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
    method: str,
    path: str,
    kwargs: dict[str, object],
) -> None:
    client, database, adapter = saved_sync_client

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 422
    assert database.count_favorites() == 0
    assert database.count_watch_later() == 0
    assert adapter.calls == []


async def test_runtime_context_rebuilds_tracked_bilibili_saved_sync_service(
    tmp_path: Path,
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config

    config = Config(data_dir=str(tmp_path / "runtime-data"))
    config.scheduler.enabled = False
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    context = build_runtime_context(config)
    first_service = context.saved_sync_service
    first_client = context.bilibili_client

    local = first_service.save_local(
        "favorite",
        SavedItemInput("bilibili", "BV1RUNTIME"),
        auto_sync=False,
    )
    created = first_service.create_sync_task(
        "favorite",
        [local.item_key],
        "manual_single",
    )
    for _ in range(100):
        result = first_service.get_sync_task(created.task_id)
        if result.items and result.items[0].status != "pending":
            break
        await asyncio.sleep(0.01)

    assert result.items[0].status == "login_required"
    assert context.task_registry.stats() == {}

    await context.rebuild_from_config(config)

    assert context.saved_sync_service is not first_service
    assert context.bilibili_client is not first_client
