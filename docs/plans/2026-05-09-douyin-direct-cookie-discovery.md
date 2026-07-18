# Douyin Direct-Cookie Discovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add optional direct-cookie Douyin discovery sources for search, hot board, and creator posts without replacing the existing extension-based Douyin bootstrap path.

**Architecture:** Keep `init --yes-douyin` on the extension path. Add a backend `DouyinDirectClient` and a discovery strategy/producer that reads an opt-in cookie from environment, normalizes aweme items into `DiscoveredContent`, evaluates them through the existing LLM discovery scorer, and writes kept candidates to `content_cache` with `source_platform="douyin"`.

**Tech Stack:** Python 3.11+, `httpx`, Typer CLI, SQLite `content_cache`, existing `ContentDiscoveryEngine`, existing LLM evaluation service, Ruff, Pytest.

---

### Task 1: Add Douyin Source Config

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`
- Test: `tests/test_config.py`

**Step 1: Write the failing tests**

Add tests near the existing Xiaohongshu source config tests:

```python
def test_sources_douyin_defaults() -> None:
    config = build_config({})

    assert config.sources.douyin.enabled is False
    assert config.sources.douyin.mode == "direct"
    assert config.sources.douyin.cookie_env == "OPENBILICLAW_DOUYIN_COOKIE"
    assert config.sources.douyin.daily_search_budget == 30
    assert config.sources.douyin.daily_hot_budget == 5
    assert config.sources.douyin.daily_creator_budget == 20
    assert config.sources.douyin.request_interval_seconds == 2


def test_build_config_supports_sources_douyin(tmp_path: Path) -> None:
    raw = {
        "sources": {
            "douyin": {
                "enabled": True,
                "mode": "direct",
                "cookie_env": "CUSTOM_DY_COOKIE",
                "daily_search_budget": 12,
                "daily_hot_budget": 3,
                "daily_creator_budget": 7,
                "request_interval_seconds": 4,
            }
        }
    }

    config = build_config(raw)

    assert config.sources.douyin.enabled is True
    assert config.sources.douyin.cookie_env == "CUSTOM_DY_COOKIE"
    assert config.sources.douyin.daily_search_budget == 12
    assert config.sources.douyin.daily_hot_budget == 3
    assert config.sources.douyin.daily_creator_budget == 7
    assert config.sources.douyin.request_interval_seconds == 4
```

**Step 2: Run the focused tests**

Run:

```bash
uv run pytest tests/test_config.py::test_sources_douyin_defaults tests/test_config.py::test_build_config_supports_sources_douyin -q
```

Expected: FAIL because `SourcesConfig` has no `douyin` field.

**Step 3: Implement config dataclass and parsing**

Add:

```python
@dataclass
class DouyinSourceConfig:
    enabled: bool = False
    mode: str = "direct"
    cookie_env: str = "OPENBILICLAW_DOUYIN_COOKIE"
    daily_search_budget: int = 30
    daily_hot_budget: int = 5
    daily_creator_budget: int = 20
    request_interval_seconds: int = 2
```

Update `SourcesConfig`:

```python
douyin: DouyinSourceConfig = field(default_factory=DouyinSourceConfig)
```

Update `build_config()` to read `sources_raw.get("douyin", {})`.

Update config rendering to include `[sources.douyin]`.

Update `config.example.toml` with the new block.

**Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_config.py -q
```

Expected: PASS.

---

### Task 2: Add Aweme Normalization

**Files:**
- Create: `src/openbiliclaw/sources/douyin_direct.py`
- Test: `tests/test_douyin_direct.py`

**Step 1: Write the failing tests**

```python
from openbiliclaw.sources.douyin_direct import normalize_aweme_item


def test_normalize_aweme_item_maps_core_fields() -> None:
    item = {
        "aweme_id": "7123456789012345678",
        "desc": "一个测试视频",
        "author": {"nickname": "作者A", "sec_uid": "sec-1"},
        "video": {"cover": {"url_list": ["https://cover.example/a.jpg"]}, "duration": 12345},
        "statistics": {"digg_count": 88, "play_count": 999},
    }

    content = normalize_aweme_item(item, source_strategy="dy-direct-search")

    assert content is not None
    assert content.bvid == "dy:7123456789012345678"
    assert content.content_id == "7123456789012345678"
    assert content.content_url == "https://www.douyin.com/video/7123456789012345678"
    assert content.source_platform == "douyin"
    assert content.source_strategy == "dy-direct-search"
    assert content.title == "一个测试视频"
    assert content.author_name == "作者A"
    assert content.up_name == "作者A"
    assert content.cover_url == "https://cover.example/a.jpg"
    assert content.duration == 12
    assert content.like_count == 88
    assert content.view_count == 999
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_douyin_direct.py::test_normalize_aweme_item_maps_core_fields -q
```

Expected: FAIL because module does not exist.

**Step 3: Implement minimal normalization**

Implement `normalize_aweme_item(item, source_strategy)` with small safe helpers:

- `_get_nested(mapping, path)`
- `_first_url(value)`
- `_to_int(value)`

Return `None` when `aweme_id` is missing.

**Step 4: Add edge-case tests**

Add tests for:

- missing `aweme_id` returns `None`
- `video.origin_cover.url_list[0]` fallback
- title fallback to `share_info.share_title`
- stat fallback from `stats`

**Step 5: Run tests**

Run:

```bash
uv run pytest tests/test_douyin_direct.py -q
```

Expected: PASS.

---

### Task 3: Add Direct Client Interface With Mocked HTTP

**Files:**
- Modify: `src/openbiliclaw/sources/douyin_direct.py`
- Test: `tests/test_douyin_direct.py`

**Step 1: Write failing tests for client behavior**

Use `httpx.MockTransport`:

```python
import httpx
import pytest

from openbiliclaw.sources.douyin_direct import DouyinDirectClient


@pytest.mark.asyncio
async def test_direct_client_search_normalizes_aweme_info() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/aweme/v1/web/general/search/single/" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"aweme_info": {"aweme_id": "1", "desc": "搜索结果", "author": {"nickname": "A"}}}
                ],
                "has_more": 0,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DouyinDirectClient(cookie="msToken=t;", http_client=http)
        items = await client.search_aweme("测试", limit=10)

    assert items[0]["aweme_id"] == "1"
```

**Step 2: Run focused test**

Run:

```bash
uv run pytest tests/test_douyin_direct.py::test_direct_client_search_normalizes_aweme_info -q
```

Expected: FAIL because `DouyinDirectClient` does not exist.

**Step 3: Implement client skeleton**

Implement:

```python
class DouyinDirectClient:
    def __init__(self, *, cookie: str, http_client: httpx.AsyncClient | None = None) -> None: ...
    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, Any]]: ...
    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, Any]]: ...
    async def get_creator_posts(self, sec_uid: str, *, limit: int = 30) -> list[dict[str, Any]]: ...
    async def aclose(self) -> None: ...
```

P0 may keep signing as a pluggable private method:

```python
def _signed_url(self, path: str, params: dict[str, Any]) -> str:
    return f"{self.BASE_URL}{path}?{urlencode(params)}"
```

This lets tests avoid signature details. Add a TODO comment referencing the `douyin-downloader` signing algorithm, then implement real `msToken` and `X-Bogus/a_bogus` port in Task 4.

**Step 4: Add hot and creator mocked tests**

Test paths:

- `/aweme/v1/web/hot/search/list/`
- `/aweme/v1/web/aweme/post/`

Expected: normalized list of raw aweme dicts or hot entries.

**Step 5: Run tests**

Run:

```bash
uv run pytest tests/test_douyin_direct.py -q
```

Expected: PASS.

---

### Task 4: Port Minimal Signing and Cookie Handling

**Files:**
- Modify: `src/openbiliclaw/sources/douyin_direct.py`
- Possibly create: `src/openbiliclaw/sources/douyin_signature.py`
- Test: `tests/test_douyin_direct.py`

**Step 1: Add tests that signing is optional and isolated**

Do not assert a real `X-Bogus` value. Assert that:

- cookie is attached to requests
- `msToken` from cookie is included when present
- missing cookie raises `DouyinDirectAuthError`
- signer failure raises `DouyinDirectSignatureError` only when direct fetch is attempted

**Step 2: Implement cookie parsing**

Add:

```python
def parse_cookie_header(cookie: str) -> dict[str, str]:
    ...
```

Keep raw cookie out of logs and reprs.

**Step 3: Implement signing adapter**

Start with the smallest viable port from `douyin-downloader`:

- default query params
- `msToken` extraction
- user agent selection or fixed Chrome UA
- signed URL hook

If full `X-Bogus` port is too large for the first patch, keep `DouyinUrlSigner` as an injectable protocol and ship tests with a fake signer. Then add a separate smoke-only implementation before enabling `enabled=true` in docs.

**Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_douyin_direct.py -q
ruff check src/openbiliclaw/sources/douyin_direct.py tests/test_douyin_direct.py
```

Expected: PASS.

---

### Task 5: Add Douyin Direct Discovery Strategy

**Files:**
- Create: `src/openbiliclaw/discovery/strategies/douyin_direct.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Test: `tests/test_douyin_direct_strategy.py`

**Step 1: Write failing strategy tests**

Create a fake client:

```python
class FakeDouyinClient:
    async def search_aweme(self, keyword: str, *, limit: int = 30):
        return [{"aweme_id": "1", "desc": f"{keyword} 视频", "author": {"nickname": "A"}}]

    async def get_hot_board(self, *, limit: int = 30):
        return [{"aweme_id": "2", "desc": "热点视频", "author": {"nickname": "B"}}]

    async def get_creator_posts(self, sec_uid: str, *, limit: int = 30):
        return [{"aweme_id": "3", "desc": "作者视频", "author": {"nickname": "C"}}]
```

Test:

```python
@pytest.mark.asyncio
async def test_strategy_returns_douyin_discovered_content(profile: SoulProfile) -> None:
    strategy = DouyinDirectStrategy(
        client=FakeDouyinClient(),
        llm_service=FakeLLM(score=0.9),
        sources=("search", "hot", "creator"),
        seed_keywords=["机械键盘"],
        creator_sec_uids=["sec-1"],
    )

    items = await strategy.discover(profile, limit=10)

    assert {item.source_platform for item in items} == {"douyin"}
    assert {item.source_strategy for item in items} == {
        "dy-direct-search",
        "dy-direct-hot",
        "dy-direct-creator",
    }
```

**Step 2: Run focused test**

Run:

```bash
uv run pytest tests/test_douyin_direct_strategy.py -q
```

Expected: FAIL because strategy does not exist.

**Step 3: Implement strategy**

Implement `DouyinDirectStrategy(DiscoveryStrategy)`:

- `name` returns `"douyin_direct"`
- generate keywords from profile interests as fallback
- call selected client methods
- normalize raw aweme items with source labels
- dedupe by `content_id`
- evaluate with `ContentDiscoveryEngine.evaluate_content_batch`
- threshold default `0.65`

**Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_douyin_direct_strategy.py tests/test_douyin_direct.py -q
```

Expected: PASS.

---

### Task 6: Add CLI `discover --source douyin`

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing CLI tests**

Add tests that:

- `discover --source douyin` rejects when `[sources.douyin].enabled=false`
- missing env override and extension-synced cookie file prints a warning and exits nonzero
- with monkeypatched `DouyinDirectStrategy`, command writes/prints discovered count
- `--strategy` is ignored or rejected with clear messaging for Douyin

**Step 2: Run focused tests**

Run:

```bash
uv run pytest tests/test_cli.py -k "douyin and discover" -q
```

Expected: FAIL because source is not supported.

**Step 3: Implement `_run_douyin_discovery()`**

Mirror `_run_xhs_discovery()` shape:

- `_require_runtime_config()`
- load Soul profile
- load config
- read cookie from `config.sources.douyin.cookie_env`
- instantiate `DouyinDirectClient`
- instantiate `DouyinDirectStrategy`
- run strategy through `ContentDiscoveryEngine` or direct strategy call
- cache results through existing discovery engine cache path
- print count and source breakdown

Update `discover()` allowed source message to include `douyin`.

**Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_cli.py -k "douyin and discover" -q
```

Expected: PASS.

---

### Task 7: Add Runtime Quota and Optional Producer

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Create: `src/openbiliclaw/runtime/douyin_producer.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_douyin_producer.py`

**Step 1: Write quota family tests**

Add a database test or refresh-runtime test proving:

```python
database.cache_content(
    bvid="dy:1",
    title="抖音视频",
    source="dy-direct-search",
    source_platform="douyin",
    content_id="1",
    content_url="https://www.douyin.com/video/1",
)

counts = database.count_pool_candidates_by_source()
assert counts["douyin"] == 1
```

**Step 2: Update source family**

Modify `_pool_source_family()` in `src/openbiliclaw/storage/database.py` if needed so `source_platform="douyin"` collapses to `"douyin"`.

**Step 3: Update refresh quotas**

Add:

```python
("douyin", 4),
```

to `_SOURCE_TARGET_SHARES`.

**Step 4: Implement producer only if needed for runtime**

`DouyinDirectProducer` should:

- skip when disabled
- skip when cookie missing
- enforce interval and daily budgets
- call direct strategy or client
- return diagnostics matching `XhsTaskProducer` style

**Step 5: Run tests**

Run:

```bash
uv run pytest tests/test_refresh_runtime.py tests/test_douyin_producer.py -q
```

Expected: PASS.

---

### Task 8: Add Optional Smoke Test

**Files:**
- Create: `tests/integration/test_douyin_direct_smoke.py`
- Modify: `docs/modules/discovery.md`

**Step 1: Write gated smoke test**

```python
@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("OPENBILICLAW_DOUYIN_SMOKE") != "1",
    reason="Douyin direct smoke requires explicit opt-in",
)
@pytest.mark.asyncio
async def test_douyin_direct_search_smoke() -> None:
    cookie = os.getenv("OPENBILICLAW_DOUYIN_COOKIE", "")
    assert cookie
    async with DouyinDirectClient(cookie=cookie) as client:
        items = await client.search_aweme("猫咪", limit=10)
    assert len(items) > 0
    assert all(str(item.get("aweme_id", "")).strip() for item in items)
```

**Step 2: Run without env**

Run:

```bash
uv run pytest tests/integration/test_douyin_direct_smoke.py -q
```

Expected: SKIPPED.

**Step 3: Run with real cookie**

Run:

```bash
OPENBILICLAW_DOUYIN_COOKIE='...' \
OPENBILICLAW_DOUYIN_SMOKE=1 \
uv run pytest tests/integration/test_douyin_direct_smoke.py -q
```

Expected: PASS and report item count in test logs.

---

### Task 9: Update Documentation

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`

**Step 1: Update module docs**

Document:

- Douyin discovery source count changed from 0 to 3 direct-cookie sources.
- `init --yes-douyin` still uses extension.
- `discover --source douyin` uses direct cookie only when enabled.

**Step 2: Update config docs**

Add `[sources.douyin]` table and warn not to commit cookie values.

**Step 3: Update CLI docs**

Add `discover --source douyin` examples and failure modes.

**Step 4: Update architecture diagrams**

Add:

```text
Douyin direct cookie -> DouyinDirectClient -> DiscoveredContent -> content_cache
Douyin extension bootstrap -> behavior events -> Soul profile
```

**Step 5: Run docs-sensitive checks**

Run:

```bash
ruff check src/ tests/
uv run pytest tests/test_config.py tests/test_douyin_direct.py tests/test_douyin_direct_strategy.py -q
```

Expected: PASS.

---

### Task 10: Final Verification

**Files:**
- All changed files

**Step 1: Static checks**

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
```

Expected: PASS.

**Step 2: Unit tests**

Run:

```bash
uv run pytest tests/test_config.py tests/test_douyin_direct.py tests/test_douyin_direct_strategy.py tests/test_cli.py -q
```

Expected: PASS.

**Step 3: Optional smoke**

Run only with explicit cookie:

```bash
OPENBILICLAW_DOUYIN_COOKIE='...' \
OPENBILICLAW_DOUYIN_SMOKE=1 \
uv run pytest tests/integration/test_douyin_direct_smoke.py -q
```

Expected: PASS with at least one item from `search_aweme("猫咪")`.

**Step 4: Commit**

Commit in logical chunks:

```bash
git add src/openbiliclaw/config.py config.example.toml tests/test_config.py docs/modules/config.md
git commit -m "feat(config): add douyin direct source settings"

git add src/openbiliclaw/sources/douyin_direct.py tests/test_douyin_direct.py
git commit -m "feat(douyin): add direct cookie client"

git add src/openbiliclaw/discovery/strategies/douyin_direct.py src/openbiliclaw/cli.py tests/test_douyin_direct_strategy.py tests/test_cli.py
git commit -m "feat(discovery): add douyin direct discovery"

git add docs README.md README_EN.md
git commit -m "docs: document douyin direct discovery"
```
