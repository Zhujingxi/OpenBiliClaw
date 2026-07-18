# Native Save Foundation And Bilibili Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the platform-neutral local save/sync foundation and deliver working Bilibili account sync for favorites and watch-later, including automatic and manual triggers on every user surface.

**Architecture:** Canonical `source_platform:content_id` identities feed normalized saved-item tables, a capability router, and one sync service shared by automatic and manual flows. Bilibili is the first production adapter and executes authenticated writes through the existing runtime `BilibiliAPIClient`; later platform plans plug into the same adapter protocol without changing saved pages or API semantics.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite, Typer, vanilla JavaScript, Chrome MV3 extension, pytest, node:test, Ruff, MyPy.

## Global Constraints

- Every favorite/watch-later action writes locally before any external account mutation.
- `[saved_sync].auto_sync_enabled = false` is the exact default.
- Manual sync ignores the automatic-sync toggle and is always an explicit state-changing action.
- `favorite` routes to native favorite; `watch_later` routes to native watch-later when supported and otherwise native favorite.
- Platform failures never roll back successful local saves and never retry indefinitely in the background.
- Removing a local item never removes it from the platform account.
- Named containers use the exact title `OpenBiliClaw`; a declared safe default container may be used only with a truthful result target.
- Extension task endpoints must keep `/api/sources/<slug>/{next-task,task-result,kick}` shape and use the authenticated shared extension client.
- Real account writes require explicit authorization or a test account; default tests and smoke commands are non-mutating.
- No Cookie, CSRF value, OAuth token, tokenized URL, or full platform response may enter logs or task errors.
- This plan deliberately covers the common foundation plus Bilibili only. YouTube/XHS/Zhihu and X/Reddit/Douyin each receive follow-on adapter plans after this contract passes review and real Bilibili E2E.

---

## File Structure

New focused backend files:

- `src/openbiliclaw/saved_sync/__init__.py` — public exports only.
- `src/openbiliclaw/saved_sync/identity.py` — canonical platform and item-key rules.
- `src/openbiliclaw/saved_sync/models.py` — typed saved-item, route, result, and status contracts.
- `src/openbiliclaw/saved_sync/router.py` — capability registry and intent routing.
- `src/openbiliclaw/saved_sync/service.py` — local-first orchestration and batch aggregation.
- `src/openbiliclaw/saved_sync/adapters/__init__.py` — adapter exports.
- `src/openbiliclaw/saved_sync/adapters/bilibili.py` — Bilibili favorite/watch-later adapter.

Existing files retain their current responsibility:

- `src/openbiliclaw/storage/database.py` owns schema/migration and saved-state DAO methods.
- `src/openbiliclaw/api/models.py` owns HTTP schemas; `src/openbiliclaw/api/app.py` registers routes.
- `src/openbiliclaw/api/runtime_context.py` constructs the hot-reloadable sync service.
- `src/openbiliclaw/config.py` owns `[saved_sync]` persistence/defaults.
- Existing desktop/mobile/popup saved views render status and trigger the shared API; no surface implements platform routing.

---

### Task 1: Canonical Saved Identity And Typed Contracts

**Files:**
- Create: `src/openbiliclaw/saved_sync/__init__.py`
- Create: `src/openbiliclaw/saved_sync/identity.py`
- Create: `src/openbiliclaw/saved_sync/models.py`
- Test: `tests/test_saved_sync_identity.py`

**Interfaces:**
- Produces: `canonical_source_platform(value: str) -> str`
- Produces: `make_item_key(source_platform: str, content_id: str, content_url: str = "") -> str`
- Produces: `content_storage_key(source_platform: str, content_id: str, content_url: str = "") -> str`
- Produces: `SavedItemInput`, `SavedMembership`, `SavedMembershipResult`, `SavedSyncBatchResult`, `NativeSaveCapability`, `NativeSaveRoute`, `NativeSaveResult`
- Produces literals: `SavedListKind`, `NativeSaveAction`, `NativeSaveStatus`

- [ ] **Step 1: Write failing identity and model tests**

```python
from openbiliclaw.saved_sync.identity import (
    canonical_source_platform,
    content_storage_key,
    make_item_key,
)
from openbiliclaw.saved_sync.models import SavedItemInput


def test_canonical_source_aliases_and_cross_platform_keys() -> None:
    assert canonical_source_platform("x") == "twitter"
    assert canonical_source_platform("yt") == "youtube"
    assert make_item_key("twitter", "123") == "twitter:123"
    assert make_item_key("douyin", "123") == "douyin:123"
    assert content_storage_key("bilibili", "BV1abc") == "BV1abc"
    assert content_storage_key("twitter", "123") == "twitter:123"


def test_saved_item_requires_stable_identity() -> None:
    item = SavedItemInput(
        source_platform="bilibili",
        content_id="BV1abc",
        content_url="https://www.bilibili.com/video/BV1abc",
        content_type="video",
        title="demo",
    )
    assert item.item_key == "bilibili:BV1abc"
```

- [ ] **Step 2: Run the tests and verify missing-module failure**

Run: `.venv/bin/pytest tests/test_saved_sync_identity.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: openbiliclaw.saved_sync`.

- [ ] **Step 3: Implement canonical identity and immutable contracts**

```python
# src/openbiliclaw/saved_sync/identity.py
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

_ALIASES = {
    "bili": "bilibili",
    "xhs": "xiaohongshu",
    "dy": "douyin",
    "yt": "youtube",
    "x": "twitter",
    "zh": "zhihu",
    "rd": "reddit",
}


def canonical_source_platform(value: str) -> str:
    normalized = value.strip().lower()
    return _ALIASES.get(normalized, normalized)


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def make_item_key(source_platform: str, content_id: str, content_url: str = "") -> str:
    platform = canonical_source_platform(source_platform)
    stable_id = content_id.strip()
    if not platform:
        raise ValueError("source_platform is required")
    if stable_id:
        return f"{platform}:{stable_id}"
    canonical_url = _canonical_url(content_url)
    if not canonical_url:
        raise ValueError("content_id or canonical content_url is required")
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    return f"{platform}:url:{digest}"


def content_storage_key(source_platform: str, content_id: str, content_url: str = "") -> str:
    """Keep legacy Bilibili cache keys; namespace every other platform."""
    platform = canonical_source_platform(source_platform)
    if platform == "bilibili" and content_id.strip():
        return content_id.strip()
    return make_item_key(platform, content_id, content_url)
```

```python
# src/openbiliclaw/saved_sync/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .identity import canonical_source_platform, make_item_key

SavedListKind = Literal["favorite", "watch_later"]
NativeSaveAction = Literal["favorite", "watch_later"]
NativeSaveStatus = Literal[
    "pending", "syncing", "synced", "already_synced", "login_required",
    "unsupported", "rate_limited", "extension_required", "failed",
]


@dataclass(frozen=True, slots=True)
class SavedItemInput:
    source_platform: str
    content_id: str
    content_url: str = ""
    content_type: str = "video"
    title: str = ""
    author_name: str = ""
    cover_url: str = ""

    @property
    def item_key(self) -> str:
        return make_item_key(self.source_platform, self.content_id, self.content_url)

    @property
    def platform(self) -> str:
        return canonical_source_platform(self.source_platform)


@dataclass(frozen=True, slots=True)
class SavedMembership:
    list_kind: SavedListKind
    item: SavedItemInput
    note: str = ""


@dataclass(frozen=True, slots=True)
class NativeSaveCapability:
    platform: str
    supports_favorite: bool
    supports_watch_later: bool
    supports_named_collection: bool
    requires_extension: bool = False


@dataclass(frozen=True, slots=True)
class NativeSaveRoute:
    requested_action: NativeSaveAction
    resolved_action: NativeSaveAction
    resolved_target: str


@dataclass(frozen=True, slots=True)
class NativeSaveResult:
    item_key: str
    status: NativeSaveStatus
    resolved_action: NativeSaveAction
    resolved_target: str
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class SavedSyncBatchResult:
    task_id: str
    items: tuple[NativeSaveResult, ...]


@dataclass(frozen=True, slots=True)
class SavedMembershipResult:
    saved: bool
    item_key: str
    sync_status: NativeSaveStatus
    sync_task_id: str = ""
```

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_saved_sync_identity.py -q`

Expected: PASS.

- [ ] **Step 5: Run lint and commit**

Run: `.venv/bin/ruff check src/openbiliclaw/saved_sync tests/test_saved_sync_identity.py`

Expected: PASS.

```bash
git add src/openbiliclaw/saved_sync tests/test_saved_sync_identity.py
git commit -m "feat: add canonical saved item identity"
```

---

### Task 2: Normalized Saved Storage And Legacy Migration

**Files:**
- Modify: `src/openbiliclaw/storage/database.py:4924-4934,7056-7130,7491-7545`
- Test: `tests/test_saved_sync_storage.py`
- Modify: `tests/test_watch_later_api.py`
- Modify: `tests/test_favorites_api.py`

**Interfaces:**
- Consumes: `SavedItemInput`, `SavedListKind`, `make_item_key()` from Task 1
- Produces: `Database.upsert_saved_membership(list_kind, item, note="") -> dict[str, Any]`
- Produces: `Database.remove_saved_membership(list_kind, item_key) -> bool`
- Produces: `Database.get_saved_membership(list_kind, item_key) -> dict[str, Any] | None`
- Produces: `Database.list_saved_memberships(list_kind, limit=50, offset=0) -> list[dict[str, Any]]`
- Produces: `Database.upsert_native_save_state(...) -> None`
- Produces: `Database.list_native_sync_eligible(list_kind, item_keys=None) -> list[dict[str, Any]]`
- Produces: `Database.list_native_save_states_by_task(task_id: str) -> list[dict[str, Any]]`

- [ ] **Step 1: Add failing new-schema and migration tests**

```python
def test_saved_memberships_allow_same_raw_id_on_two_platforms(db: Database) -> None:
    x = SavedItemInput(source_platform="twitter", content_id="123", title="x")
    dy = SavedItemInput(source_platform="douyin", content_id="123", title="dy")
    db.upsert_saved_membership("favorite", x)
    db.upsert_saved_membership("favorite", dy)
    rows = db.list_saved_memberships("favorite")
    assert {row["item_key"] for row in rows} == {"twitter:123", "douyin:123"}


def test_legacy_watch_later_and_favorite_rows_migrate_idempotently(tmp_path: Path) -> None:
    db = Database(tmp_path / "legacy.db")
    db.initialize()
    db.add_to_watch_later("BV1OLD")
    db.add_to_favorites("BV1OLD")
    db._ensure_saved_sync_tables()
    db._ensure_saved_sync_tables()
    assert db.get_saved_membership("watch_later", "bilibili:BV1OLD") is not None
    assert db.get_saved_membership("favorite", "bilibili:BV1OLD") is not None
    db.remove_saved_membership("watch_later", "bilibili:BV1OLD")
    db._ensure_saved_sync_tables()
    assert db.get_saved_membership("watch_later", "bilibili:BV1OLD") is None
```

- [ ] **Step 2: Verify tests fail because the DAO does not exist**

Run: `.venv/bin/pytest tests/test_saved_sync_storage.py -q`

Expected: FAIL with `AttributeError: 'Database' object has no attribute 'upsert_saved_membership'`.

- [ ] **Step 3: Add normalized tables and idempotent migration**

Add `_ensure_saved_sync_tables()` to `Database.initialize()` after both legacy saved tables exist. Use this exact schema:

```sql
CREATE TABLE IF NOT EXISTS saved_items (
    item_key        TEXT PRIMARY KEY,
    source_platform TEXT NOT NULL,
    content_id      TEXT NOT NULL,
    content_url     TEXT NOT NULL DEFAULT '',
    content_type    TEXT NOT NULL DEFAULT 'video',
    title           TEXT NOT NULL DEFAULT '',
    author_name     TEXT NOT NULL DEFAULT '',
    cover_url       TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS saved_memberships (
    list_kind TEXT NOT NULL CHECK (list_kind IN ('favorite', 'watch_later')),
    item_key  TEXT NOT NULL REFERENCES saved_items(item_key) ON DELETE CASCADE,
    note      TEXT NOT NULL DEFAULT '',
    added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (list_kind, item_key)
);
CREATE TABLE IF NOT EXISTS native_save_states (
    list_kind          TEXT NOT NULL,
    item_key           TEXT NOT NULL,
    requested_action   TEXT NOT NULL,
    resolved_action    TEXT NOT NULL DEFAULT '',
    resolved_target    TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'pending',
    task_id            TEXT NOT NULL DEFAULT '',
    last_error_code    TEXT NOT NULL DEFAULT '',
    last_error_message TEXT NOT NULL DEFAULT '',
    last_attempt_at    TIMESTAMP,
    synced_at          TIMESTAMP,
    PRIMARY KEY (list_kind, item_key),
    FOREIGN KEY (list_kind, item_key)
        REFERENCES saved_memberships(list_kind, item_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS saved_sync_migrations (
    name       TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Inside one `BEGIN IMMEDIATE` transaction, run the legacy import only when `saved_sync_migrations` lacks `legacy_saved_tables_v1`, then insert that marker after both lists copy successfully. Migrate rows with `INSERT OR IGNORE` into `saved_items` using a `LEFT JOIN content_cache` for metadata and `bilibili:<bvid>` only when no source metadata is recoverable. Insert memberships separately so one item can exist in both lists. Never delete legacy rows in this task; the marker prevents a removed normalized membership from being resurrected at the next startup.

- [ ] **Step 4: Implement DAO methods with parameterized SQL**

Use `SavedItemInput.item_key` as the only new-write key. `list_saved_memberships()` must left join `native_save_states` and return `sync_status="pending"` when no state row exists. Validate `list_kind` through one private `_saved_list_kind()` helper that raises `ValueError` for anything else.

- [ ] **Step 5: Preserve legacy Bilibili wrappers**

Change `add_to_watch_later()` / `add_to_favorites()` to construct `SavedItemInput(source_platform="bilibili", content_id=bvid)` and call the new DAO in addition to their legacy write. Change list/count/status wrappers to read the normalized tables so migrated and new records have one authority. Legacy remove wrappers delete both the normalized membership and matching legacy row; new generic removal of a Bilibili membership does the same, so compatibility tables cannot disagree with user-visible state.

- [ ] **Step 6: Run storage and compatibility tests**

Run: `.venv/bin/pytest tests/test_saved_sync_storage.py tests/test_watch_later_api.py tests/test_favorites_api.py -q`

Expected: PASS.

- [ ] **Step 7: Run MyPy/lint for touched files and commit**

Run: `.venv/bin/ruff check src/openbiliclaw/storage/database.py tests/test_saved_sync_storage.py`

Run: `.venv/bin/mypy src/openbiliclaw/saved_sync src/openbiliclaw/storage/database.py`

Expected: both PASS.

```bash
git add src/openbiliclaw/storage/database.py tests/test_saved_sync_storage.py tests/test_watch_later_api.py tests/test_favorites_api.py
git commit -m "feat: normalize local saved content storage"
```

---

### Task 3: Carry Canonical Identity Through Recommendation Outputs

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py:448-481`
- Modify: `src/openbiliclaw/discovery/candidate_pool.py:119-190`
- Modify: `src/openbiliclaw/storage/database.py:540-581,645-659,1573-1700,4778-4811`
- Modify: `src/openbiliclaw/api/models.py:251-270,870-910`
- Modify: `src/openbiliclaw/api/app.py:3980-4061,4630-4745`
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Test: `tests/test_saved_sync_identity_pipeline.py`
- Modify: `tests/test_watch_later_api.py`
- Modify: `tests/test_favorites_api.py`

**Interfaces:**
- Consumes: `make_item_key()` from Task 1
- Produces: `DiscoveredContent.item_key: str`
- Produces: recommendation/delight JSON fields `item_key`, `content_id`, `source_platform`, `content_url`, `content_type`
- Produces: `content_cache.item_key` and `recommendations.item_key` indexed columns

- [ ] **Step 1: Write a failing end-to-end identity round-trip test**

```python
def test_same_raw_content_id_survives_two_platform_recommendation_outputs(db: Database) -> None:
    rows = [
        DiscoveredContent(content_id="123", source_platform="twitter", title="x"),
        DiscoveredContent(content_id="123", source_platform="douyin", title="dy"),
    ]
    for item in rows:
        db.cache_content(item.item_key, **item.to_cache_kwargs())
    cached = db.conn.execute(
        "SELECT item_key, source_platform, content_id FROM content_cache "
        "WHERE content_id='123' ORDER BY item_key"
    ).fetchall()
    assert [tuple(row) for row in cached] == [
        ("douyin:123", "douyin", "123"),
        ("twitter:123", "twitter", "123"),
    ]
```

- [ ] **Step 2: Verify the test fails on the current `bvid` primary key path**

Run: `.venv/bin/pytest tests/test_saved_sync_identity_pipeline.py -q`

Expected: FAIL because `item_key` is absent or the second row overwrites/conflicts.

- [ ] **Step 3: Add item identity fields and storage-key compatibility**

Add `item_key` to `DiscoveredContent`; in `__post_init__`, derive it with `make_item_key()`. Pass `content_storage_key(source_platform, content_id, content_url)` as the existing `cache_content()` key: Bilibili keeps its raw BV key for legacy joins, while every non-Bilibili platform uses the namespaced item key. Keep raw platform IDs in `content_id` and expose raw Bilibili BV IDs through API `bvid`. Add `item_key TEXT NOT NULL DEFAULT ''` to `content_cache` and `recommendations`, backfill with canonical platform/content ID, and add unique/indexed lookups.

Do not reconstruct Bilibili URLs from an internal item key. `content_url` remains authoritative for non-Bilibili; Bilibili compatibility URL generation uses raw `content_id`/`bvid` only.

- [ ] **Step 4: Add identity to recommendation and delight responses**

Extend `RecommendationOut`, `PendingDelightOut`, watch-later/favorite items, reshuffle serialization, pending delight batch, runtime delight events, and all saved-list responses. Assert the five fields survive every response path:

```python
assert payload["item_key"] == "twitter:123"
assert payload["content_id"] == "123"
assert payload["source_platform"] == "twitter"
assert payload["content_url"] == "https://x.com/u/status/123"
assert payload["content_type"] == "tweet"
```

- [ ] **Step 5: Run focused identity/API tests**

Run: `.venv/bin/pytest tests/test_saved_sync_identity_pipeline.py tests/test_watch_later_api.py tests/test_favorites_api.py tests/test_api_app.py -q --tb=short`

Expected: PASS.

- [ ] **Step 6: Run lint/type checks and commit**

Run: `.venv/bin/ruff check src/openbiliclaw/discovery src/openbiliclaw/recommendation src/openbiliclaw/api tests/test_saved_sync_identity_pipeline.py`

Run: `.venv/bin/mypy src/openbiliclaw/discovery src/openbiliclaw/recommendation src/openbiliclaw/api`

Expected: PASS.

```bash
git add src/openbiliclaw/discovery src/openbiliclaw/recommendation src/openbiliclaw/storage/database.py src/openbiliclaw/api tests/test_saved_sync_identity_pipeline.py tests/test_watch_later_api.py tests/test_favorites_api.py
git commit -m "refactor: preserve canonical content identity"
```

---

### Task 4: Saved-Sync Configuration Contract

**Files:**
- Modify: `src/openbiliclaw/config.py:750-790,980-1040`
- Modify: `config.example.toml`
- Modify: `src/openbiliclaw/api/models.py:1260-1325`
- Modify: `src/openbiliclaw/api/app.py:8960-9075`
- Modify: `src/openbiliclaw/cli.py:9856-9920`
- Test: `tests/test_config.py`
- Test: `tests/test_saved_sync_config_api.py`

**Interfaces:**
- Produces: `SavedSyncConfig(auto_sync_enabled: bool = False)`
- Produces: `Config.saved_sync`
- Produces: API object `{ "saved_sync": { "auto_sync_enabled": false } }`

- [ ] **Step 1: Write failing default/round-trip tests**

```python
def test_saved_sync_defaults_off_and_round_trips(tmp_path: Path) -> None:
    cfg = Config()
    assert cfg.saved_sync.auto_sync_enabled is False
    cfg.saved_sync.auto_sync_enabled = True
    save_config(cfg, tmp_path / "config.toml")
    assert load_config(tmp_path / "config.toml").saved_sync.auto_sync_enabled is True


def test_config_api_exposes_and_updates_saved_sync(client: TestClient) -> None:
    assert client.get("/api/config").json()["saved_sync"] == {"auto_sync_enabled": False}
    assert client.put(
        "/api/config", json={"saved_sync": {"auto_sync_enabled": True}}
    ).status_code == 200
    assert client.get("/api/config").json()["saved_sync"]["auto_sync_enabled"] is True
```

- [ ] **Step 2: Run tests and verify missing config failure**

Run: `.venv/bin/pytest tests/test_config.py tests/test_saved_sync_config_api.py -q`

Expected: FAIL because `Config.saved_sync` and API fields do not exist.

- [ ] **Step 3: Implement config, API, example, and `config-show`**

```python
@dataclass
class SavedSyncConfig:
    """External platform save synchronization."""

    auto_sync_enabled: bool = False
```

Add `saved_sync: SavedSyncConfig = field(default_factory=SavedSyncConfig)` to `Config`, parse/save `[saved_sync]`, add Pydantic output/update fields, and print the resolved value in `config-show`. Reject non-boolean API values with 422 through Pydantic rather than truthy coercion.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_config.py tests/test_saved_sync_config_api.py tests/test_cli.py -q --tb=short`

Expected: PASS.

- [ ] **Step 5: Run lint/type checks and commit**

Run: `.venv/bin/ruff check src/openbiliclaw/config.py src/openbiliclaw/api src/openbiliclaw/cli.py tests/test_saved_sync_config_api.py`

Run: `.venv/bin/mypy src/openbiliclaw/config.py src/openbiliclaw/api src/openbiliclaw/cli.py`

Expected: PASS.

```bash
git add src/openbiliclaw/config.py config.example.toml src/openbiliclaw/api src/openbiliclaw/cli.py tests/test_config.py tests/test_saved_sync_config_api.py tests/test_cli.py
git commit -m "feat: add saved sync configuration"
```

---

### Task 5: Capability Router And Local-First Sync Service

**Files:**
- Create: `src/openbiliclaw/saved_sync/router.py`
- Create: `src/openbiliclaw/saved_sync/service.py`
- Create: `src/openbiliclaw/saved_sync/adapters/__init__.py`
- Test: `tests/test_saved_sync_router.py`
- Test: `tests/test_saved_sync_service.py`

**Interfaces:**
- Consumes: Task 1 contracts and Task 2 DAO
- Produces protocol: `NativeSaveAdapter.capability`, `save(item, route) -> NativeSaveResult`
- Consumes callback: `task_starter(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]`
- Produces: `NativeSaveRouter.register(adapter)`, `route(platform, requested_action) -> tuple[adapter, NativeSaveRoute]`
- Produces: `SavedSyncService.save_local(list_kind, item, note="", auto_sync=False) -> SavedMembershipResult`
- Produces: `SavedSyncService.create_sync_task(list_kind, item_keys, trigger) -> SavedSyncBatchResult`
- Produces: `SavedSyncService.run_sync_task(task_id) -> SavedSyncBatchResult`
- Produces: `SavedSyncService.get_sync_task(task_id) -> SavedSyncBatchResult`

- [ ] **Step 1: Write failing router and local-first behavior tests**

```python
class FakeAdapter:
    def __init__(self, capability: NativeSaveCapability, result_status: str) -> None:
        self.capability = capability
        self.result_status = result_status

    def target_label(self, action: NativeSaveAction) -> str:
        if self.capability.platform == "reddit":
            return "Reddit Saved"
        return "B站稍后再看" if action == "watch_later" else "B站 OpenBiliClaw 收藏夹"

    async def save(
        self, item: SavedItemInput, route: NativeSaveRoute
    ) -> NativeSaveResult:
        return NativeSaveResult(
            item_key=item.item_key,
            status=cast("NativeSaveStatus", self.result_status),
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )


class FailingBiliAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__(
            NativeSaveCapability("bilibili", True, True, True),
            "failed",
        )


async def test_watch_later_falls_back_to_favorite() -> None:
    adapter = FakeAdapter(
        NativeSaveCapability("reddit", True, False, False),
        result_status="synced",
    )
    router = NativeSaveRouter([adapter])
    _, route = router.route("reddit", "watch_later")
    assert route.resolved_action == "favorite"
    assert route.resolved_target == "Reddit Saved"


async def test_platform_failure_keeps_local_membership(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter([FailingBiliAdapter()]))
    item = SavedItemInput("bilibili", "BV1FAIL")
    local = service.save_local("watch_later", item, auto_sync=False)
    created = service.create_sync_task("watch_later", [item.item_key], "manual_single")
    result = await service.run_sync_task(created.task_id)
    assert db.get_saved_membership("watch_later", item.item_key) is not None
    assert local.saved is True
    assert result.items[0].status == "failed"
```

- [ ] **Step 2: Run tests and verify missing router/service failure**

Run: `.venv/bin/pytest tests/test_saved_sync_router.py tests/test_saved_sync_service.py -q`

Expected: FAIL during import.

- [ ] **Step 3: Implement the adapter protocol and deterministic router**

```python
class NativeSaveAdapter(Protocol):
    @property
    def capability(self) -> NativeSaveCapability: ...

    def target_label(self, action: NativeSaveAction) -> str: ...

    async def save(
        self, item: SavedItemInput, route: NativeSaveRoute
    ) -> NativeSaveResult: ...
```

Reject unregistered platforms as `unsupported`. Reject favorite routing when `supports_favorite=False`. Resolve watch-later fallback only when favorite is supported. The router never touches storage or config.

- [ ] **Step 4: Implement local-first orchestration**

`save_local()` writes the membership first. When `auto_sync=False`, create/update a `pending` native state and return without invoking an adapter. When true, call `create_sync_task()` only after the local transaction commits and schedule `run_sync_task()` through an injected background-task starter; never await platform I/O in the local-save request.

`create_sync_task()` generates one UUID, writes it into every selected native state, returns immediately with `pending` items, and is the only task-creation path for automatic and manual triggers. `run_sync_task()` reads only existing memberships for that task ID, groups by platform, writes `syncing`, awaits adapter results with per-platform sequential execution, and persists every item result independently. `get_sync_task()` reconstructs the batch from `list_native_save_states_by_task()` so popup close/reopen and desktop/mobile polling do not lose results.

Catch adapter exceptions at the item boundary and normalize them to `failed` without exposing response bodies. Do not schedule retries. Treat `synced` and `already_synced` as terminal success.

- [ ] **Step 5: Run router/service tests**

Run: `.venv/bin/pytest tests/test_saved_sync_router.py tests/test_saved_sync_service.py -q`

Expected: PASS.

- [ ] **Step 6: Run lint/type checks and commit**

Run: `.venv/bin/ruff check src/openbiliclaw/saved_sync tests/test_saved_sync_router.py tests/test_saved_sync_service.py`

Run: `.venv/bin/mypy src/openbiliclaw/saved_sync`

Expected: PASS.

```bash
git add src/openbiliclaw/saved_sync tests/test_saved_sync_router.py tests/test_saved_sync_service.py
git commit -m "feat: add native save routing service"
```

---

### Task 6: Bilibili Native Favorite And Watch-Later Adapter

**Files:**
- Modify: `src/openbiliclaw/bilibili/api.py:240-390,644-740`
- Create: `src/openbiliclaw/saved_sync/adapters/bilibili.py`
- Modify: `src/openbiliclaw/saved_sync/adapters/__init__.py`
- Test: `tests/test_bilibili_native_save.py`
- Test: `tests/test_saved_sync_bilibili_adapter.py`

**Interfaces:**
- Consumes: `BilibiliAPIClient`, Task 5 adapter protocol
- Produces: `BilibiliAPIClient.ensure_favorite_folder(title: str) -> FavoriteFolder`
- Produces: `BilibiliAPIClient.add_video_to_favorite(bvid: str, media_id: int) -> None`
- Produces: `BilibiliAPIClient.add_video_to_watch_later(bvid: str) -> None`
- Produces: `BilibiliNativeSaveAdapter`

- [ ] **Step 1: Write failing request-shape and result-mapping tests**

```python
async def test_add_video_to_watch_later_posts_aid_and_csrf() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=s; bili_jct=csrf; DedeUserID=1")
    client.get_video_info = AsyncMock(return_value=VideoInfo(bvid="BV1", aid=42))
    post = AsyncMock(return_value={})
    client._post_json = post
    await client.add_video_to_watch_later("BV1")
    post.assert_awaited_once_with(
        "/x/v2/history/toview/add", data={"aid": 42, "csrf": "csrf"}
    )


async def test_favorite_creates_openbiliclaw_folder_then_adds_video() -> None:
    client = SimpleNamespace(
        ensure_favorite_folder=AsyncMock(
            return_value=FavoriteFolder(media_id=7, title="OpenBiliClaw")
        ),
        add_video_to_favorite=AsyncMock(return_value=None),
        add_video_to_watch_later=AsyncMock(return_value=None),
    )
    adapter = BilibiliNativeSaveAdapter(client)
    result = await adapter.save(
        SavedItemInput("bilibili", "BV1"),
        NativeSaveRoute("favorite", "favorite", "B站 OpenBiliClaw 收藏夹"),
    )
    client.ensure_favorite_folder.assert_awaited_once_with("OpenBiliClaw")
    client.add_video_to_favorite.assert_awaited_once_with("BV1", 7)
    assert result.status == "synced"
```

- [ ] **Step 2: Run tests and verify methods are missing**

Run: `.venv/bin/pytest tests/test_bilibili_native_save.py tests/test_saved_sync_bilibili_adapter.py -q`

Expected: FAIL with missing methods/classes.

- [ ] **Step 3: Add authenticated POST support with CSRF extraction**

Implement `_post_json(path, data)` beside `_get_json()`. Extract `bili_jct` through `http.cookies.SimpleCookie`; missing `SESSDATA` or `bili_jct` raises `BilibiliAuthExpiredError` before a network call. Map HTTP failures to `BilibiliAPIError`, `code == -101` to auth expired, known rate-control codes to an error message containing the code but never the response body.

Use these existing Bilibili web endpoints and payloads:

```python
await self._post_json(
    "/x/v2/history/toview/add",
    data={"aid": info.aid, "csrf": self._csrf_token()},
)
await self._post_json(
    "/x/v3/fav/folder/add",
    data={"title": title, "intro": "", "privacy": 0, "csrf": self._csrf_token()},
)
await self._post_json(
    "/x/v3/fav/resource/deal",
    data={
        "rid": info.aid,
        "type": 2,
        "add_media_ids": str(media_id),
        "del_media_ids": "",
        "csrf": self._csrf_token(),
    },
)
```

`ensure_favorite_folder("OpenBiliClaw")` first reuses an exact-title folder, then creates and parses the returned folder ID. Empty/invalid returned IDs fail closed.

- [ ] **Step 4: Implement adapter status normalization**

Declare capability `(platform="bilibili", supports_favorite=True, supports_watch_later=True, supports_named_collection=True)`. Map expired auth to `login_required`, duplicate/already-exists application codes to `already_synced`, rate-control codes to `rate_limited`, and other safe failures to `failed`.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/pytest tests/test_bilibili_native_save.py tests/test_saved_sync_bilibili_adapter.py tests/test_bilibili_api.py -q`

Expected: PASS.

- [ ] **Step 6: Run lint/type checks and commit**

Run: `.venv/bin/ruff check src/openbiliclaw/bilibili/api.py src/openbiliclaw/saved_sync/adapters tests/test_bilibili_native_save.py tests/test_saved_sync_bilibili_adapter.py`

Run: `.venv/bin/mypy src/openbiliclaw/bilibili src/openbiliclaw/saved_sync`

Expected: PASS.

```bash
git add src/openbiliclaw/bilibili/api.py src/openbiliclaw/saved_sync/adapters tests/test_bilibili_native_save.py tests/test_saved_sync_bilibili_adapter.py
git commit -m "feat: sync Bilibili favorites and watch later"
```

---

### Task 7: Platform-Neutral Saved And Sync APIs

**Files:**
- Modify: `src/openbiliclaw/api/models.py:870-930,1280-1335`
- Modify: `src/openbiliclaw/api/app.py:4015-4120`
- Modify: `src/openbiliclaw/api/runtime_context.py:250-285,350-420,880-910`
- Test: `tests/test_saved_sync_api.py`
- Modify: `tests/test_api_auth.py`

**Interfaces:**
- Consumes: Task 5 `SavedSyncService`; Task 6 Bilibili adapter
- Produces endpoints: `/api/saved/{list_kind}`, `/remove`, `/status`, `/sync`
- Produces endpoint: `/api/saved-sync/tasks/{task_id}`
- Keeps legacy: `/api/watch-later`, `/api/favorites`

- [ ] **Step 1: Write failing API tests for local-first, manual sync, partial status, and auth**

```python
def test_save_defaults_to_local_pending(client: TestClient) -> None:
    response = client.post("/api/saved/watch_later", json={
        "source_platform": "bilibili",
        "content_id": "BV1LOCAL",
        "content_url": "https://www.bilibili.com/video/BV1LOCAL",
        "content_type": "video",
        "title": "local",
    })
    assert response.status_code == 200
    assert response.json()["saved"] is True
    assert response.json()["sync_status"] == "pending"


def test_manual_sync_ignores_auto_sync_toggle(client: TestClient) -> None:
    client.post("/api/saved/watch_later", json={
        "source_platform": "bilibili", "content_id": "BV1SYNC"
    })
    response = client.post(
        "/api/saved/watch_later/sync",
        json={"item_keys": ["bilibili:BV1SYNC"]},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["status"] in {"synced", "already_synced"}
```

- [ ] **Step 2: Run tests and verify 404**

Run: `.venv/bin/pytest tests/test_saved_sync_api.py -q`

Expected: FAIL with HTTP 404 for `/api/saved/...`.

- [ ] **Step 3: Construct the service in `RuntimeContext`**

Add `saved_sync_service` as a swappable component because its Bilibili client changes on config hot reload. Build a new router, register `BilibiliNativeSaveAdapter(new_bilibili_client)`, and inject `lambda name, coro: self.task_registry.track(name, coro)` as the service task starter. Assign the service only after all components build successfully.

- [ ] **Step 4: Add strict Pydantic request/response models and routes**

`SavedItemIn` requires platform/content identity for new routes. `SavedSyncRequest.item_keys` defaults to an empty list meaning all eligible entries. Reject invalid `list_kind` with 422, missing membership keys with per-item `failed/not_saved_locally`, and unauthenticated remote mutation requests through existing API auth middleware.

For auto sync, read `ctx.config.saved_sync.auto_sync_enabled` inside the save route. Return local success immediately with a `pending` task ID and never wait for Bilibili network I/O. Manual sync also creates and returns a task ID; graphical clients poll `/api/saved-sync/tasks/{task_id}`. Do not hide later `login_required` or `failed` results behind generic success wording.

- [ ] **Step 5: Preserve legacy routes through service delegation**

Legacy Bilibili POSTs build `SavedItemInput("bilibili", bvid)` and call `save_local(..., auto_sync=False)` to preserve old behavior. DELETE removes only local membership. GET/list translate normalized rows back to legacy response shape plus additive identity/sync fields.

- [ ] **Step 6: Run API and auth tests**

Run: `.venv/bin/pytest tests/test_saved_sync_api.py tests/test_watch_later_api.py tests/test_favorites_api.py tests/test_api_auth.py -q --tb=short`

Expected: PASS.

- [ ] **Step 7: Run lint/type checks and commit**

Run: `.venv/bin/ruff check src/openbiliclaw/api src/openbiliclaw/saved_sync tests/test_saved_sync_api.py`

Run: `.venv/bin/mypy src/openbiliclaw/api src/openbiliclaw/saved_sync`

Expected: PASS.

```bash
git add src/openbiliclaw/api src/openbiliclaw/saved_sync tests/test_saved_sync_api.py tests/test_watch_later_api.py tests/test_favorites_api.py tests/test_api_auth.py
git commit -m "feat: expose platform-neutral saved sync API"
```

---

### Task 8: Four-Surface Save, Sync, And Configuration UI

**Files:**
- Modify: `extension/popup/popup-api.js:646-700`
- Modify: `extension/popup/popup.js:553-760,4790-4815,4958-4970,6640-6880`
- Modify: `extension/popup/popup.html:4450-4690`
- Modify: `src/openbiliclaw/web/js/api.js:295-330`
- Modify: `src/openbiliclaw/web/js/views/saved.js`
- Modify: `src/openbiliclaw/web/js/views/recommend.js:430-490,1140-1195`
- Modify: `src/openbiliclaw/web/desktop/index.html:75-220`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:1415-1555,2387-2435`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css`
- Modify: `src/openbiliclaw/web/css/app.css`
- Test: `extension/tests/saved-sync-ui.test.ts`
- Modify: `extension/tests/web-watch-later.test.ts`
- Modify: `extension/tests/popup-api.test.ts`
- Test: `tests/test_saved_sync_frontend_contract.py`

**Interfaces:**
- Consumes: Task 7 HTTP endpoints and response shapes
- Produces helpers: `saveItem(listKind, item)`, `removeSavedItem(listKind, itemKey)`, `syncSavedItems(listKind, itemKeys)`
- Produces UI setting: `saved_sync.auto_sync_enabled`

- [ ] **Step 1: Write failing API-helper and static UI contract tests**

```javascript
test("save helpers send canonical identity and manual sync keys", async () => {
  await saveItem("watch_later", {
    item_key: "bilibili:BV1",
    source_platform: "bilibili",
    content_id: "BV1",
    content_url: "https://www.bilibili.com/video/BV1",
    content_type: "video",
  });
  await syncSavedItems("watch_later", ["bilibili:BV1"]);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/saved/watch_later");
  assert.deepEqual(JSON.parse(calls[1].options.body), { item_keys: ["bilibili:BV1"] });
});
```

Assert all three graphical surfaces contain the copy `保存时自动同步到对应平台`, the default unchecked state, list-level `同步未同步内容`, per-item sync status, and no platform-routing switch statement in front-end files.

- [ ] **Step 2: Run tests and verify missing helpers/elements**

Run: `cd extension && npm test -- --test-name-pattern="saved sync|watch-later"`

Run: `.venv/bin/pytest tests/test_saved_sync_frontend_contract.py -q`

Expected: FAIL for missing API helpers and DOM controls.

- [ ] **Step 3: Replace Bilibili-only save payloads with canonical item payloads**

Add one shared front-end normalizer per surface that reads `item_key`, `source_platform`, `content_id`, `content_url`, and `content_type`. The browser code never decides whether watch-later falls back to favorite; it sends `list_kind` only.

Keep optimistic UI limited to local save. Platform failure changes the sync badge/message but does not unpress the local save button.

- [ ] **Step 4: Implement saved-page manual synchronization**

Render:

- Page button: `同步未同步内容（N）`.
- Per item: target label plus `待同步 / 同步中 / 已同步 / 需要登录 / 同步失败`.
- Per failed item button: `重试同步`.
- Batch confirmation: item count and distinct platform names.
- Batch result: grouped `平台 成功/总数` text.

Disable only the active sync controls during a request. Refresh list state from the server after completion. `extension_required` copy instructs the user to connect the installed extension; it does not offer temporary browser automation.

- [ ] **Step 5: Fix “全部稍后看” ordering**

Replace the current `rememberDismissedDelight()` loop with one `Promise.allSettled` local-save batch over a snapshot of `state.activeDelights`. Remove only local-save successes from the queue. Keep failures visible. When auto sync is enabled, the backend save route creates sync work; the popup does not call platform APIs directly.

Display exact counts: `本地保存 N · 同步中 M · 失败 K`.

- [ ] **Step 6: Add the default-off config control on all settings surfaces**

The checkbox starts unchecked from API data. On false→true, show the exact warning from the design:

```text
开启后，在 OpenBiliClaw 点击收藏或稍后再看会修改对应平台账号中的收藏、书签、Saved、播放列表或稍后观看。
```

Cancel leaves the stored value false. Manual list sync remains enabled in both states.

- [ ] **Step 7: Run extension/mobile/desktop tests**

Run: `cd extension && npm test`

Run: `cd extension && npm run typecheck`

Run: `.venv/bin/pytest tests/test_saved_sync_frontend_contract.py tests/test_mobile_web_view_models.py tests/test_desktop_web_card_links.py -q`

Expected: PASS.

- [ ] **Step 8: Build extension and commit**

Run: `cd extension && npm run build`

Expected: PASS.

```bash
git add extension src/openbiliclaw/web tests/test_saved_sync_frontend_contract.py tests/test_mobile_web_view_models.py tests/test_desktop_web_card_links.py
git commit -m "feat: add saved item sync controls"
```

---

### Task 9: Documentation, Full Verification, And Authorized Bilibili E2E

**Files:**
- Modify: `docs/changelog.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/integrations.md`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/superpowers/specs/2026-07-11-cross-platform-native-save-design.md` only if implementation reveals a real contract correction
- Test: `tests/test_docs_saved_sync.py`

**Interfaces:**
- Consumes: all previous tasks
- Produces: verified Phase 1 deliverable and the stable adapter contract required by follow-on plans

- [ ] **Step 1: Write a failing documentation contract test**

```python
def test_saved_sync_docs_name_default_and_routes() -> None:
    config_doc = Path("docs/modules/config.md").read_text()
    integration_doc = Path("docs/modules/integrations.md").read_text()
    assert "[saved_sync]" in config_doc
    assert "auto_sync_enabled = false" in config_doc
    assert "OpenBiliClaw" in integration_doc
    assert "watch_later" in integration_doc
    assert "favorite" in integration_doc
```

- [ ] **Step 2: Run the docs test and verify it fails before updates**

Run: `.venv/bin/pytest tests/test_docs_saved_sync.py -q`

Expected: FAIL on missing documented config/contract.

- [ ] **Step 3: Update all required documentation and diagrams**

Document local-first ordering, default-off consent, manual sync, Bilibili targets, normalized identity/storage, API paths, configuration, CLI output, real state-changing E2E boundary, and the explicit fact that other platform adapters remain follow-on work. Update every architecture diagram listed in `CLAUDE.md`; do not add more than four README release-highlight bullets.

- [ ] **Step 4: Run focused then full automated verification**

Run:

```bash
.venv/bin/pytest tests/test_saved_sync_identity.py \
  tests/test_saved_sync_storage.py \
  tests/test_saved_sync_identity_pipeline.py \
  tests/test_saved_sync_config_api.py \
  tests/test_saved_sync_router.py \
  tests/test_saved_sync_service.py \
  tests/test_bilibili_native_save.py \
  tests/test_saved_sync_bilibili_adapter.py \
  tests/test_saved_sync_api.py \
  tests/test_saved_sync_frontend_contract.py \
  tests/test_docs_saved_sync.py -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q --tb=short
cd extension && npm test && npm run typecheck && npm run build
```

Expected: every command PASS. If unrelated pre-existing failures occur, verify against `origin/main`, record them, and do not edit unrelated files.

- [ ] **Step 5: Perform non-mutating browser verification first**

Using the installed extension browser and the same backend/config root:

1. Confirm auto sync defaults off on popup, desktop, and mobile settings.
2. Save one Bilibili item and confirm only local DB membership/state changes.
3. Confirm saved-page target reads `B站稍后再看` or `B站 OpenBiliClaw 收藏夹`.
4. Confirm bulk local save removes only successful items from the delight queue.

Expected: no Bilibili account state changes during this step.

- [ ] **Step 6: Obtain explicit authorization before state-changing E2E**

Ask the user to authorize Bilibili favorite/watch-later writes for named test BV IDs, or use a designated test account. Do not proceed on the user's real account without that authorization.

- [ ] **Step 7: Run authorized Bilibili account-write E2E**

Verify:

1. Manual favorite sync creates/reuses `OpenBiliClaw` and the video appears there.
2. Manual watch-later sync makes the video appear in Bilibili 稍后观看.
3. Repeat both and confirm `already_synced`/idempotent success.
4. Enable auto sync and confirm a new card action writes locally then remotely.
5. Remove local records and confirm platform records remain.
6. Log out and confirm `login_required` while local records remain.
7. Record task/result counts and DB states without credentials.

- [ ] **Step 8: Commit documentation and verification contract**

```bash
git add docs README.md README_EN.md tests/test_docs_saved_sync.py
git commit -m "docs: document native saved sync foundation"
```

---

## Follow-On Plan Gates

Do not write or execute the remaining platform adapter plans until Phase 1 proves all of these:

- One adapter can be registered without platform conditionals in API/UI code.
- Local-first behavior survives platform failure.
- Automatic and manual flows call the same service.
- Cross-platform item keys round-trip without collision.
- Saved-page statuses and batch results are stable on all surfaces.
- Bilibili real E2E validates the state-changing authorization and idempotency model.

After the gate passes, create separate implementation plans for:

1. YouTube, Xiaohongshu, and Zhihu extension-login adapters.
2. X, Reddit, and Douyin credential/API-first adapters with extension fallback only where required.

Each follow-on plan must include platform-specific capability reconnaissance, exact content-type support, real login cookie rules, named-container behavior, bounded rate limits, extension task tests, and authorized real E2E.
