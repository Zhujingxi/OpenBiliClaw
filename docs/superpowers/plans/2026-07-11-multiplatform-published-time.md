# Multi-Platform Published Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry trustworthy best-effort publication time from seven platform sources through discovery, storage, recommendation/delight APIs, and all four user surfaces without extra detail requests.

**Architecture:** A new dependency-light `openbiliclaw.published_time` module owns backend normalization and CLI formatting. Exact UTC RFC 3339 (`published_at`) and source-relative fallback text (`published_label`) travel together through `DiscoveredContent`, `discovery_candidates`, `content_cache`, and API payloads; browser surfaces implement the same display contract in their existing view-model/helper boundaries.

**Tech Stack:** Python 3.12, dataclasses, SQLite, FastAPI/Pydantic, vanilla JavaScript, TypeScript browser extension, Pytest, Node test runner, Ruff, MyPy.

**Design:** [`../specs/2026-07-11-multiplatform-published-time-design.md`](../specs/2026-07-11-multiplatform-published-time-design.md)

## Global Constraints

- Do not collect, store, or render coin counts.
- Do not request a detail page or detail API only to obtain publication time.
- Do not network-backfill legacy `content_cache` rows.
- Never substitute `discovered_at`, recommendation creation time, task creation time, or interaction time for publication time.
- Canonical exact values are UTC RFC 3339 `YYYY-MM-DDTHH:MM:SSZ`; relative source text is stored separately in `published_label`.
- `published_label` is whitespace-normalized, placeholder-filtered, plain text, and at most 64 characters.
- Empty rediscovery values preserve prior non-empty publication data.
- Missing or malformed time never rejects a candidate or fails a batch.
- Right-click native-menu tracking and true optimistic profile editing remain out of scope.
- The four-surface contract covers desktop Web, mobile Web, extension popup, and CLI.
- No dependency additions.
- Do not publish a release, reply to issue #75, or close it in this implementation.

---

## File Structure

- Create `src/openbiliclaw/published_time.py`: platform-neutral exact-time normalization, label sanitization, and CLI display formatting.
- Modify discovery/source adapters in place: select only semantically known source fields, then call the shared normalizer.
- Modify `src/openbiliclaw/discovery/engine.py` and `candidate_pool.py`: carry already-normalized fields only.
- Modify `src/openbiliclaw/storage/database.py`: fresh schema, migration, queue/cache persistence, empty-value preservation, and recommendation row projection.
- Modify API/runtime serializers in `api/models.py`, `api/app.py`, and `runtime/refresh.py`: additive string fields only.
- Modify desktop `web/desktop/assets/js/app.js`, mobile `web/js/view-models.js`/`views/recommend.js`, and popup `popup-helpers.js`/`popup.js`: normalize, format, and render.
- Modify `src/openbiliclaw/cli.py`: reuse the Python formatter in recommendation cards.
- Extend existing focused tests; add one test file per new cross-cutting helper/UI contract rather than growing unrelated suites.

---

### Task 1: Canonical Publication-Time Utility

**Files:**
- Create: `src/openbiliclaw/published_time.py`
- Create: `tests/test_published_time.py`

**Interfaces:**
- Produces: `PublishedTime(published_at: str, published_label: str)`
- Produces: `normalize_published_time(value: object = None, *, label: object = None, now: datetime | None = None) -> PublishedTime`
- Produces: `normalize_published_label(value: object) -> str`
- Produces: `format_published_time(published_at: object, published_label: object = "", *, now: datetime | None = None, local_tz: tzinfo | None = None) -> str`

- [ ] **Step 1: Write failing normalization and formatting tests**

```python
from datetime import UTC, datetime, timedelta, timezone

from openbiliclaw.published_time import (
    PublishedTime,
    format_published_time,
    normalize_published_label,
    normalize_published_time,
)


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_normalize_published_time_accepts_seconds_milliseconds_iso_and_rfc2822() -> None:
    expected = "2026-07-08T06:30:00Z"
    epoch = int(datetime(2026, 7, 8, 6, 30, tzinfo=UTC).timestamp())
    assert normalize_published_time(epoch, now=NOW).published_at == expected
    assert normalize_published_time(epoch * 1000, now=NOW).published_at == expected
    assert normalize_published_time("2026-07-08T14:30:00+08:00", now=NOW).published_at == expected
    assert normalize_published_time("Wed, 08 Jul 2026 06:30:00 +0000", now=NOW).published_at == expected
    assert normalize_published_time("20260708", now=NOW).published_at == "2026-07-08T00:00:00Z"


def test_normalize_published_time_keeps_safe_relative_label_only() -> None:
    assert normalize_published_time(label="  3   小时前  ") == PublishedTime("", "3 小时前")
    assert normalize_published_label("x" * 80) == "x" * 64
    for value in (None, "", "unknown", "未知", "N/A", True):
        assert normalize_published_time(value, label=value, now=NOW) == PublishedTime("", "")


def test_format_published_time_uses_local_time_and_relative_boundaries() -> None:
    local = timezone(timedelta(hours=8))
    assert format_published_time("2026-07-11T11:59:30Z", now=NOW, local_tz=local) == "刚刚"
    assert format_published_time("2026-07-11T11:30:00Z", now=NOW, local_tz=local) == "1 小时前"
    assert format_published_time("2026-07-09T12:00:00Z", now=NOW, local_tz=local) == "2 天前"
    assert format_published_time("2026-06-01T12:00:00Z", now=NOW, local_tz=local) == "6月1日"
    assert format_published_time("2025-06-01T12:00:00Z", now=NOW, local_tz=local) == "2025-06-01"
    assert format_published_time("", "2 years ago", now=NOW, local_tz=local) == "2 years ago"
    assert format_published_time("bad", "", now=NOW, local_tz=local) == ""


def test_format_published_time_never_emits_negative_relative_text() -> None:
    assert format_published_time("2026-07-11T12:03:00Z", now=NOW) == "刚刚"
    assert format_published_time("2026-07-20T12:00:00Z", now=NOW) == "7月20日"
```

- [ ] **Step 2: Run the new tests and confirm the module is missing**

Run: `.venv/bin/pytest -q tests/test_published_time.py`

Expected: collection fails with `ModuleNotFoundError: No module named 'openbiliclaw.published_time'`.

- [ ] **Step 3: Implement the complete platform-neutral utility**

```python
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from email.utils import parsedate_to_datetime

_PLACEHOLDERS = {"", "unknown", "none", "null", "n/a", "na", "未知", "暂无"}
_SPACE_RE = re.compile(r"\s+")
_MAX_LABEL_LENGTH = 64


@dataclass(frozen=True, slots=True)
class PublishedTime:
    published_at: str = ""
    published_label: str = ""


def normalize_published_label(value: object) -> str:
    if not isinstance(value, str):
        return ""
    label = _SPACE_RE.sub(" ", value).strip()
    if label.lower() in _PLACEHOLDERS:
        return ""
    return label[:_MAX_LABEL_LENGTH]


def _as_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        seconds = float(value) / 1000 if abs(float(value)) >= 1_000_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text or text.lower() in _PLACEHOLDERS:
        return None
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return _as_datetime(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def normalize_published_time(
    value: object = None,
    *,
    label: object = None,
    now: datetime | None = None,
) -> PublishedTime:
    parsed = _as_datetime(value)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if parsed is not None:
        lower = datetime(1970, 1, 1, tzinfo=UTC)
        upper = current + timedelta(days=366)
        if lower <= parsed <= upper:
            return PublishedTime(
                parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                normalize_published_label(label),
            )
    return PublishedTime("", normalize_published_label(label))


def format_published_time(
    published_at: object,
    published_label: object = "",
    *,
    now: datetime | None = None,
    local_tz: tzinfo | None = None,
) -> str:
    normalized = normalize_published_time(published_at, label=published_label, now=now)
    if not normalized.published_at:
        return normalized.published_label
    published = datetime.fromisoformat(normalized.published_at.replace("Z", "+00:00"))
    current = (now or datetime.now(UTC)).astimezone(UTC)
    diff = current - published
    if -timedelta(minutes=5) <= diff < timedelta(minutes=1):
        return "刚刚"
    if timedelta(0) <= diff < timedelta(hours=24):
        return f"{max(1, int(diff.total_seconds() // 3600))} 小时前"
    if timedelta(0) <= diff < timedelta(days=7):
        return f"{int(diff.total_seconds() // 86400)} 天前"
    local = published.astimezone(local_tz)
    local_now = current.astimezone(local_tz)
    if local.year == local_now.year:
        return f"{local.month}月{local.day}日"
    return local.strftime("%Y-%m-%d")
```

- [ ] **Step 4: Run focused tests and static checks**

Run: `.venv/bin/pytest -q tests/test_published_time.py && .venv/bin/ruff check src/openbiliclaw/published_time.py tests/test_published_time.py && .venv/bin/mypy src/openbiliclaw/published_time.py`

Expected: all tests pass; Ruff and MyPy exit 0.

- [ ] **Step 5: Commit the utility**

```bash
git add src/openbiliclaw/published_time.py tests/test_published_time.py
git commit -m "feat: normalize cross-platform publication time"
```

---

### Task 2: Unified Models, Candidate Queue, And Cache Persistence

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py:414-523,2218-2241`
- Modify: `src/openbiliclaw/discovery/candidate_pool.py:37-74,155-202,233-280`
- Modify: `src/openbiliclaw/recommendation/engine.py:2556-2604`
- Modify: `src/openbiliclaw/storage/database.py:540-635,1573-1948,4414-4477,4778-4840`
- Modify: `tests/test_discovered_content.py`
- Modify: `tests/test_discovery_candidate_pipeline.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_recommendation_engine.py`

**Interfaces:**
- Consumes: `normalize_published_time()` from Task 1.
- Produces: `DiscoveredContent.published_at: str` and `.published_label: str`.
- Produces: matching `DiscoveryCandidateWrite` fields and SQLite columns on `discovery_candidates`/`content_cache`.

- [ ] **Step 1: Add failing round-trip, migration, and empty-preservation tests**

```python
def test_discovered_content_cache_kwargs_include_publication_time() -> None:
    item = DiscoveredContent(
        bvid="BV1TIME",
        published_at="2026-07-08T06:30:00Z",
        published_label="3 天前",
    )
    assert item.to_cache_kwargs()["published_at"] == "2026-07-08T06:30:00Z"
    assert item.to_cache_kwargs()["published_label"] == "3 天前"


def test_candidate_roundtrip_preserves_publication_time(database: Database) -> None:
    item = DiscoveredContent(
        bvid="BV1TIME",
        title="时间测试",
        published_at="2026-07-08T06:30:00Z",
        published_label="3 天前",
    )
    database.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
    row = database.claim_discovery_candidates_for_eval(limit=1)[0]
    restored = row_to_discovered_content(row)
    assert restored.published_at == "2026-07-08T06:30:00Z"
    assert restored.published_label == "3 天前"


def test_candidate_rediscovery_preserves_existing_time_when_new_values_are_empty(database: Database) -> None:
    first = discovered_content_to_candidate_write(
        DiscoveredContent(bvid="BV1TIME", title="A", published_at="2026-07-08T06:30:00Z")
    )
    second = discovered_content_to_candidate_write(DiscoveredContent(bvid="BV1TIME", title="A"))
    database.enqueue_discovery_candidates([first])
    database.enqueue_discovery_candidates([second])
    row = database.conn.execute(
        "SELECT published_at, published_label FROM discovery_candidates WHERE candidate_key = ?",
        (first.candidate_key,),
    ).fetchone()
    assert row["published_at"] == "2026-07-08T06:30:00Z"


def test_content_cache_empty_rediscovery_does_not_erase_publication_time(database: Database) -> None:
    database.cache_content("BV1TIME", title="A", published_at="2026-07-08T06:30:00Z")
    database.cache_content("BV1TIME", title="A", published_at="")
    row = database.conn.execute(
        "SELECT published_at, published_label FROM content_cache WHERE bvid='BV1TIME'"
    ).fetchone()
    assert row["published_at"] == "2026-07-08T06:30:00Z"
```

- [ ] **Step 2: Run the focused tests and confirm missing fields/columns**

Run: `.venv/bin/pytest -q tests/test_discovered_content.py tests/test_discovery_candidate_pipeline.py tests/test_database.py -k 'publication or published'`

Expected: failures mention missing `published_at`, `published_label`, or SQLite columns.

- [ ] **Step 3: Thread fields through dataclasses and every row reconstruction point**

Add to both dataclasses and mappings:

```python
published_at: str = ""
published_label: str = ""
```

Add to `DiscoveredContent.to_cache_kwargs()`, `discovered_content_to_candidate_write()`, `row_to_discovered_content()`, `ContentDiscoveryEngine._load_cached_backfill()`, and `RecommendationEngine._rows_to_discovered()`:

```python
"published_at": self.published_at,
"published_label": self.published_label,
```

or, at row reads:

```python
published_at=str(row.get("published_at", "") or ""),
published_label=str(row.get("published_label", "") or ""),
```

- [ ] **Step 4: Add fresh schema and legacy migration columns**

Add to both `CREATE TABLE` blocks:

```sql
published_at    TEXT NOT NULL DEFAULT '',
published_label TEXT NOT NULL DEFAULT '',
```

Add to `_ensure_content_cache_multisource_columns()` and `_ensure_discovery_candidate_columns()`:

```python
"published_at": "TEXT NOT NULL DEFAULT ''",
"published_label": "TEXT NOT NULL DEFAULT ''",
```

- [ ] **Step 5: Persist normalized values and preserve non-empty values on rediscovery**

Before binding database writes:

```python
published = normalize_published_time(
    kwargs.get("published_at"),
    label=kwargs.get("published_label"),
)
```

Use `published.published_at` and `published.published_label` in inserts. In `content_cache` upsert:

```sql
published_at = COALESCE(NULLIF(excluded.published_at, ''), content_cache.published_at, ''),
published_label = COALESCE(NULLIF(excluded.published_label, ''), content_cache.published_label, ''),
```

When `INSERT OR IGNORE` finds an existing candidate, update only rediscovery metadata required by this feature:

```sql
UPDATE discovery_candidates
SET last_seen_at = CURRENT_TIMESTAMP,
    published_at = COALESCE(NULLIF(?, ''), published_at, ''),
    published_label = COALESCE(NULLIF(?, ''), published_label, '')
WHERE candidate_key = ?
```

- [ ] **Step 6: Project publication fields from recommendation history rows**

Add to `Database.get_recommendations()`:

```sql
COALESCE(c.published_at, '') AS published_at,
COALESCE(c.published_label, '') AS published_label,
```

Add a regression assertion to `test_get_recommendations_rows_carry_card_metadata_columns` for both values.

- [ ] **Step 7: Run model/storage suites**

Run: `.venv/bin/pytest -q tests/test_discovered_content.py tests/test_discovery_candidate_pipeline.py tests/test_database.py tests/test_recommendation_engine.py`

Expected: all selected tests pass.

- [ ] **Step 8: Commit unified persistence**

```bash
git add src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/candidate_pool.py src/openbiliclaw/recommendation/engine.py src/openbiliclaw/storage/database.py tests/test_discovered_content.py tests/test_discovery_candidate_pipeline.py tests/test_database.py tests/test_recommendation_engine.py
git commit -m "feat: persist publication time through discovery"
```

---

### Task 3: Native Backend Platform Normalizers

**Files:**
- Modify: `src/openbiliclaw/discovery/strategies/search.py:584-629`
- Modify: `src/openbiliclaw/discovery/strategies/trending.py:220-281`
- Modify: `src/openbiliclaw/discovery/strategies/related_chain.py:429-486`
- Modify: `src/openbiliclaw/discovery/x_normalize.py:106-176`
- Modify: `src/openbiliclaw/youtube/client.py:483-564`
- Modify: `src/openbiliclaw/sources/douyin_direct.py:55-102`
- Modify: `src/openbiliclaw/sources/zhihu_tasks.py:190-259`
- Modify: `src/openbiliclaw/sources/reddit_tasks.py:181-241`
- Modify: `tests/test_search_strategy.py`
- Modify: `tests/test_trending_strategy.py`
- Modify: `tests/test_related_chain_strategy.py`
- Modify: `tests/test_x_normalize.py`
- Modify: `tests/test_youtube_discovery_strategy.py`
- Modify: `tests/test_douyin_direct.py`
- Modify: `tests/test_zhihu_tasks.py`
- Modify: `tests/test_reddit_tasks.py`

**Interfaces:**
- Consumes: `normalize_published_time()` and `DiscoveredContent` publication fields.
- Produces: normalized exact/fallback values for Bilibili, X, YouTube, Douyin, Zhihu, and Reddit backend paths.

- [ ] **Step 1: Add failing per-platform mapping assertions**

Use existing mapping tests and add the source-native field to their fixtures:

```python
assert mapped.published_at == "2026-07-08T06:30:00Z"
```

Concrete fixture keys:

```python
# Bilibili search/trending/related
{"pubdate": 1783492200}

# X
{"createdAtISO": "2026-07-08T06:30:00Z"}

# YouTube exact and fallback cases
{"publishedAt": "2026-07-08T06:30:00Z"}
{"publishedTimeText": {"simpleText": "3 days ago"}}

# Douyin
{"create_time": 1783492200}

# Zhihu extension/producer row
{"published_at": 1783492200}

# Reddit listing/rdt row
{"created_utc": 1783492200}
```

For YouTube fallback assert `published_at == ""` and `published_label == "3 days ago"`. Add missing-field assertions that both fields remain empty and the candidate is still returned.

- [ ] **Step 2: Run source tests and verify failures**

Run: `.venv/bin/pytest -q tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_related_chain_strategy.py tests/test_x_normalize.py tests/test_youtube_discovery_strategy.py tests/test_douyin_direct.py tests/test_zhihu_tasks.py tests/test_reddit_tasks.py -k 'map or normalize or publication or published'`

Expected: publication assertions fail while existing content assertions remain green.

- [ ] **Step 3: Map Bilibili exact timestamps in all three strategy constructors**

In search, trending, and related mapping functions:

```python
published = normalize_published_time(item.get("pubdate") or item.get("publish_time"))
return DiscoveredContent(
    # existing fields
    published_at=published.published_at,
    published_label=published.published_label,
)
```

- [ ] **Step 4: Map X, YouTube, and Douyin native fields**

```python
# x_normalize.py
published = normalize_published_time(
    raw.get("createdAtISO") or raw.get("createdAt"),
    label=raw.get("createdAtLocal"),
)

# youtube/client.py
published = normalize_published_time(
    raw.get("timestamp")
    or raw.get("release_timestamp")
    or raw.get("upload_date")
    or raw.get("publishedAt"),
    label=_extract_text(raw.get("publishedTimeText") or ""),
)

# sources/douyin_direct.py
published = normalize_published_time(item.get("create_time"))
```

Pass both normalized fields into each `DiscoveredContent` constructor.

- [ ] **Step 5: Map extension/CLI-fed Zhihu and Reddit source rows**

```python
# zhihu_tasks.py
published = normalize_published_time(
    item.get("published_at") or item.get("created_time"),
    label=item.get("published_label"),
)

# reddit_tasks.py
published = normalize_published_time(
    item.get("published_at") or item.get("created_utc"),
    label=item.get("published_label"),
)
```

Do not read Zhihu `interaction_time`. Pass both normalized values into `DiscoveredContent`.

- [ ] **Step 6: Run platform suites**

Run: `.venv/bin/pytest -q tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_related_chain_strategy.py tests/test_x_normalize.py tests/test_youtube_discovery_strategy.py tests/test_douyin_direct.py tests/test_zhihu_tasks.py tests/test_reddit_tasks.py`

Expected: all selected tests pass.

- [ ] **Step 7: Commit backend adapters**

```bash
git add src/openbiliclaw/discovery/strategies/search.py src/openbiliclaw/discovery/strategies/trending.py src/openbiliclaw/discovery/strategies/related_chain.py src/openbiliclaw/discovery/x_normalize.py src/openbiliclaw/youtube/client.py src/openbiliclaw/sources/douyin_direct.py src/openbiliclaw/sources/zhihu_tasks.py src/openbiliclaw/sources/reddit_tasks.py tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_related_chain_strategy.py tests/test_x_normalize.py tests/test_youtube_discovery_strategy.py tests/test_douyin_direct.py tests/test_zhihu_tasks.py tests/test_reddit_tasks.py
git commit -m "feat: capture native platform publication time"
```

---

### Task 4: Logged-In Extension Source Extraction

**Files:**
- Modify: `extension/src/content/bili/task-executor.ts:9-29,151-193`
- Modify: `extension/src/content/xhs/bootstrap.ts:11-19,600-656,780-825`
- Modify: `extension/src/content/xhs/passive.ts:46-60,150-220`
- Modify: `extension/src/main/dy-fetch-tap.ts:32-64,161-195,247-288`
- Modify: `extension/src/content/zhihu/task-executor.ts:19-40,328-520`
- Modify: `extension/src/content/reddit/task-executor.ts:20-39,131-182`
- Modify: `src/openbiliclaw/sources/douyin_plugin_search.py:65-100`
- Modify: `src/openbiliclaw/api/app.py:6625-6688,6991-7072`
- Modify: `extension/tests/bili-task-executor.test.ts`
- Modify: `extension/tests/xhs-passive.test.ts`
- Modify: `extension/tests/xhs-task-executor.test.ts`
- Modify: `extension/tests/dy-fetch-tap.test.ts`
- Modify: `extension/tests/zhihu-task-executor.test.ts`
- Modify: `extension/tests/reddit-task-executor.test.ts`
- Modify: `tests/test_douyin_plugin_search.py`
- Modify: `tests/test_api_bili_tasks.py`
- Modify: `tests/test_api_xhs_ingest.py`

**Interfaces:**
- Produces extension result fields `published_at?: string | number` and `published_label?: string`.
- Consumes backend normalization from Task 1 at the API/producer boundary.
- Produces candidate rows with canonical publication fields through Task 2.

- [ ] **Step 1: Add failing extension extraction tests**

Add source-realistic fixture fields and assertions:

```typescript
// Bilibili rendered card
assert.equal(videos[0]?.published_label, "3小时前");

// Xiaohongshu initial-state note
assert.equal(notes[0]?.published_at, 1783492200000);

// Douyin API aweme
assert.equal(items[0]?.published_at, 1783492200);

// Zhihu answer/article
assert.equal(item?.published_at, 1783492200);
assert.notEqual(item?.published_at, item?.interaction_time);

// Reddit listing child
assert.equal(item?.published_at, 1783492200);
```

For DOM fixtures without a date element assert that the optional properties are absent.

- [ ] **Step 2: Run focused extension tests and verify failures**

Run: `cd extension && node --test --experimental-strip-types tests/bili-task-executor.test.ts tests/xhs-passive.test.ts tests/xhs-task-executor.test.ts tests/dy-fetch-tap.test.ts tests/zhihu-task-executor.test.ts tests/reddit-task-executor.test.ts`

Expected: assertions fail because publication fields are not emitted.

- [ ] **Step 3: Preserve only semantically known fields in extension result types**

Add to each relevant item interface:

```typescript
published_at?: string | number;
published_label?: string;
```

Map source fields without guessing:

```typescript
// Bilibili rendered search: relative label only
const PUBLISHED_SELECTOR = ".bili-video-card__info--date, .so-icon.time, .pubdate";
const publishedLabel = textFrom(first(card, PUBLISHED_SELECTOR));

// Xiaohongshu state note
const publishedAt = firstPathString(rawNote, [
  ["create_time"], ["createTime"], ["publish_time"], ["publishTime"],
  ["noteCard", "time"], ["note_card", "time"],
]);

// Douyin
const publishedAt = pickNumber(aweme.create_time);

// Zhihu content object
const publishedAt = num(content.created_time ?? content.created ?? target.created_time);

// Reddit listing data
const publishedAt = num(data.created_utc);
```

Only attach properties when values are non-empty/defined. Do not derive publication time from DOM observation time or task time.

- [ ] **Step 4: Preserve extension values through Bilibili, XHS, and Douyin backend ingestion**

In `_cache_bili_search_videos()` and `_cache_xhs_notes()`:

```python
published = normalize_published_time(
    item.get("published_at") or item.get("pubdate"),
    label=item.get("published_label"),
)
```

Pass both fields to `DiscoveredContent`. In `plugin_search_item_to_aweme()` preserve:

```python
"create_time": item.get("published_at") or item.get("create_time"),
```

so `normalize_aweme_item()` performs the canonical conversion.

- [ ] **Step 5: Add failing backend ingestion tests, then make them pass**

Extend existing payload fixtures with:

```python
"published_at": 1783492200,
"published_label": "3小时前",
```

Assert queued candidate rows contain `2026-07-08T06:30:00Z` and the label. Run:

Run: `.venv/bin/pytest -q tests/test_douyin_plugin_search.py tests/test_api_bili_tasks.py tests/test_api_xhs_ingest.py -k 'published or publication or enqueue'`

Expected after implementation: all selected tests pass.

- [ ] **Step 6: Run extension typecheck and focused suites**

Run: `cd extension && npm run typecheck && node --test --experimental-strip-types tests/bili-task-executor.test.ts tests/xhs-passive.test.ts tests/xhs-task-executor.test.ts tests/dy-fetch-tap.test.ts tests/zhihu-task-executor.test.ts tests/reddit-task-executor.test.ts`

Expected: typecheck exits 0; all selected Node tests pass.

- [ ] **Step 7: Commit extension-backed adapters**

```bash
git add extension/src/content/bili/task-executor.ts extension/src/content/xhs/bootstrap.ts extension/src/content/xhs/passive.ts extension/src/main/dy-fetch-tap.ts extension/src/content/zhihu/task-executor.ts extension/src/content/reddit/task-executor.ts src/openbiliclaw/sources/douyin_plugin_search.py src/openbiliclaw/api/app.py extension/tests/bili-task-executor.test.ts extension/tests/xhs-passive.test.ts extension/tests/xhs-task-executor.test.ts extension/tests/dy-fetch-tap.test.ts extension/tests/zhihu-task-executor.test.ts extension/tests/reddit-task-executor.test.ts tests/test_douyin_plugin_search.py tests/test_api_bili_tasks.py tests/test_api_xhs_ingest.py
git commit -m "feat: retain publication time from extension sources"
```

---

### Task 5: Recommendation And Delight API Contracts

**Files:**
- Modify: `src/openbiliclaw/api/models.py:102-133,251-269`
- Modify: `src/openbiliclaw/api/app.py:3188-3218,3983-4013,4590-4725`
- Modify: `src/openbiliclaw/runtime/refresh.py:888-942,2338-2370`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_refresh_runtime.py`

**Interfaces:**
- Consumes normalized database/model fields from Tasks 2-4.
- Produces additive `published_at: str = ""` and `published_label: str = ""` in recommendation/delight HTTP and runtime events.

- [ ] **Step 1: Add failing API and runtime event assertions**

```python
def assert_publication(payload: dict[str, object]) -> None:
    assert payload["published_at"] == "2026-07-08T06:30:00Z"
    assert payload["published_label"] == "3 days ago"
```

Use that assertion in tests for:

- `GET /api/recommendations`
- `POST /api/recommendations/reshuffle`
- `GET /api/delight/pending`
- `GET /api/delight/pending-batch`
- `POST /api/delight/trigger` published runtime event
- `RefreshRuntime._publish_delight_if_available()`

Also assert `RecommendationOut(id=1, bvid="BV1").model_dump()` and `PendingDelightOut(bvid="BV1").model_dump()` default both fields to empty strings.

- [ ] **Step 2: Run focused API tests and verify missing fields**

Run: `.venv/bin/pytest -q tests/test_api_app.py tests/test_refresh_runtime.py -k 'recommendation or reshuffle or delight'`

Expected: new publication assertions fail.

- [ ] **Step 3: Add additive Pydantic fields and serializers**

Add to both output models:

```python
published_at: str = ""
published_label: str = ""
```

Add to `_serialize_recommendation_items()` and `GET /api/recommendations` row serialization:

```python
published_at=str(getattr(item.content, "published_at", "") or ""),
published_label=str(getattr(item.content, "published_label", "") or ""),
```

or row equivalents.

- [ ] **Step 4: Add fields to every delight output/event path**

At singular pending, batch, manual trigger, and proactive runtime publish boundaries:

```python
"published_at": str(candidate.get("published_at", "") or ""),
"published_label": str(candidate.get("published_label", "") or ""),
```

Do not format relative time in the API; clients own display relative to their current clock.

- [ ] **Step 5: Run API/runtime suites**

Run: `.venv/bin/pytest -q tests/test_api_app.py tests/test_refresh_runtime.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit API contracts**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py src/openbiliclaw/runtime/refresh.py tests/test_api_app.py tests/test_refresh_runtime.py
git commit -m "feat: expose publication time in recommendation APIs"
```

---

### Task 6: Desktop Web Cards And Middle-Click Regression

**Files:**
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:683-752,1956-1985,2011-2156,4960-5167`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css`
- Modify: `src/openbiliclaw/web/desktop/index.html`
- Create: `tests/test_desktop_web_published_time.py`
- Modify: `tests/test_desktop_web_card_links.py`

**Interfaces:**
- Consumes API publication fields from Task 5.
- Produces desktop helper `formatPublishedTime(item, now = Date.now()) -> string`; exact-time tooltips use the normalized `published_at` value directly.

- [ ] **Step 1: Write failing desktop static-contract tests**

```python
def test_desktop_normalizers_carry_publication_fields() -> None:
    assert "published_at: String(item?.published_at ?? \"\")" in APP_JS
    assert "published_label: String(item?.published_label ?? \"\")" in APP_JS


def test_desktop_formats_and_renders_publication_time_on_grid_and_delight() -> None:
    assert "function formatPublishedTime(item, now = Date.now())" in APP_JS
    assert "const published = formatPublishedTime(item);" in APP_JS
    assert 'class="published-time"' in APP_JS
    assert 'id="delightPublished"' in INDEX_HTML
    assert ".published-time" in APP_CSS


def test_middle_click_uses_the_same_open_recommendation_path_as_left_click() -> None:
    assert 'cover.addEventListener("click", () => openRecommendation(item, card));' in APP_JS
    assert re.search(
        r'cover\.addEventListener\("auxclick", \(event\) => \{\s*'
        r'if \(event\.button === 1\) openRecommendation\(item, card\);',
        APP_JS,
    )
```

- [ ] **Step 2: Run tests and verify missing publication helpers**

Run: `.venv/bin/pytest -q tests/test_desktop_web_published_time.py tests/test_desktop_web_card_links.py`

Expected: publication tests fail; the new precise middle-click assertion already passes.

- [ ] **Step 3: Normalize and format exact/fallback time**

Add to `normalizeRecommendation()` and `normalizeDelight()`:

```javascript
published_at: String(item?.published_at ?? "").trim(),
published_label: String(item?.published_label ?? "").replace(/\s+/g, " ").trim().slice(0, 64),
```

Implement:

```javascript
function formatPublishedTime(item, now = Date.now()) {
  const parsed = Date.parse(String(item?.published_at || ""));
  if (Number.isFinite(parsed)) {
    const diff = now - parsed;
    if (diff >= -300_000 && diff < 60_000) return "刚刚";
    if (diff >= 0 && diff < 86_400_000) return `${Math.max(1, Math.floor(diff / 3_600_000))} 小时前`;
    if (diff >= 0 && diff < 604_800_000) return `${Math.floor(diff / 86_400_000)} 天前`;
    const date = new Date(parsed);
    const current = new Date(now);
    if (date.getFullYear() === current.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`;
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }
  return String(item?.published_label || "").replace(/\s+/g, " ").trim().slice(0, 64);
}
```

- [ ] **Step 4: Render muted publication time in recommendation and delight metadata**

Append escaped publication text to `recommendationMetaHtml(item)` only when non-empty. Add a dedicated `#delightPublished` element next to the delight source/meta and set via `textContent`, `title`, and `hidden`. Use CSS:

```css
.published-time { color: var(--muted); white-space: nowrap; }
```

For exact dates, set `title` to `new Date(item.published_at).toLocaleString()`; fallback labels use no title.

- [ ] **Step 5: Run desktop contracts and syntax check**

Run: `.venv/bin/pytest -q tests/test_desktop_web_published_time.py tests/test_desktop_web_card_links.py tests/test_desktop_web_card_metadata.py && node --check src/openbiliclaw/web/desktop/assets/js/app.js`

Expected: tests pass; Node syntax check exits 0.

- [ ] **Step 6: Commit desktop UI**

```bash
git add src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/web/desktop/assets/css/app.css src/openbiliclaw/web/desktop/index.html tests/test_desktop_web_published_time.py tests/test_desktop_web_card_links.py
git commit -m "feat: show publication time on desktop cards"
```

---

### Task 7: Mobile Web And Extension Popup Cards

**Files:**
- Modify: `src/openbiliclaw/web/js/view-models.js:310-455,1133-1154`
- Modify: `src/openbiliclaw/web/js/views/recommend.js:360-420,1040-1080`
- Modify: `src/openbiliclaw/web/css/app.css`
- Modify: `tests/test_mobile_web_view_models.py`
- Modify: `tests/test_mobile_web_card_stats.py`
- Modify: `extension/popup/popup-helpers.js:242-390`
- Modify: `extension/popup/popup.js:2463-2575,5130-5200`
- Modify: `extension/popup/popup.html`
- Create: `extension/tests/popup-published-time.test.ts`

**Interfaces:**
- Consumes API fields from Task 5.
- Produces `formatPublishedTime(item, now = Date.now()) -> string` in mobile view models and popup helpers.

- [ ] **Step 1: Add failing mobile view-model tests**

```javascript
const exact = normalizeRecommendation({
  id: 1,
  bvid: "BV1",
  published_at: "2026-07-11T09:00:00Z",
  published_label: "fallback",
});
assert.equal(exact.published_at, "2026-07-11T09:00:00Z");
assert.equal(formatPublishedTime(exact, Date.parse("2026-07-11T12:00:00Z")), "3 小时前");
assert.equal(formatPublishedTime({ published_at: "", published_label: "3 days ago" }), "3 days ago");
assert.equal(formatPublishedTime({ published_at: "", published_label: "" }), "");
```

Add static assertions that both recommendation and delight templates call `formatPublishedTime()` and render `card-published-time` only when non-empty.

- [ ] **Step 2: Add failing popup helper/renderer tests**

```typescript
import { formatPublishedTime, normalizeDelightCandidate, normalizeRecommendation } from "../popup/popup-helpers.js";

test("popup publication time prefers exact time and falls back to label", () => {
  const now = Date.parse("2026-07-11T12:00:00Z");
  assert.equal(formatPublishedTime(normalizeRecommendation({ id: 1, bvid: "BV1", published_at: "2026-07-11T09:00:00Z" }), now), "3 小时前");
  assert.equal(formatPublishedTime(normalizeDelightCandidate({ bvid: "BV1", published_label: "3 days ago" }), now), "3 days ago");
});
```

Read `popup.js` as text and assert recommendation and delight render paths call `appendPublishedTime(...)`.

- [ ] **Step 3: Run focused tests and verify failures**

Run: `.venv/bin/pytest -q tests/test_mobile_web_view_models.py tests/test_mobile_web_card_stats.py -k 'published or publication' && cd extension && node --test --experimental-strip-types tests/popup-published-time.test.ts`

Expected: missing export/helper assertions fail.

- [ ] **Step 4: Implement mobile normalization, formatter, and card rendering**

Add normalized fields to `normalizeRecommendation()` and `normalizeDelightCandidate()`. Export the same boundary formatter used in Task 6, then render:

```javascript
const published = formatPublishedTime(item);
const publishedHtml = published
  ? `<span class="card-published-time">${esc(published)}</span>`
  : "";
```

Place it in the existing source/author metadata block for both recommendation and delight. Add muted CSS with existing tokens.

- [ ] **Step 5: Implement popup normalization, helper, and safe DOM append**

Export `formatPublishedTime()` from `popup-helpers.js`. In `popup.js` use a DOM-only renderer:

```javascript
function appendPublishedTime(parent, item) {
  const text = formatPublishedTime(item);
  if (!text) return;
  const time = document.createElement("span");
  time.className = "recommendation-published-time";
  time.textContent = text;
  if (item.published_at && Number.isFinite(Date.parse(item.published_at))) {
    time.title = new Date(item.published_at).toLocaleString();
  }
  parent.append(time);
}
```

Call it from recommendation and delight card builders; add muted CSS in `popup.html`.

- [ ] **Step 6: Run mobile and extension suites**

Run: `.venv/bin/pytest -q tests/test_mobile_web_view_models.py tests/test_mobile_web_card_stats.py && cd extension && npm run typecheck && npm test`

Expected: mobile tests pass; extension typecheck and full Node suite pass.

- [ ] **Step 7: Commit mobile and popup surfaces**

```bash
git add src/openbiliclaw/web/js/view-models.js src/openbiliclaw/web/js/views/recommend.js src/openbiliclaw/web/css/app.css tests/test_mobile_web_view_models.py tests/test_mobile_web_card_stats.py extension/popup/popup-helpers.js extension/popup/popup.js extension/popup/popup.html extension/tests/popup-published-time.test.ts
git commit -m "feat: show publication time on mobile and popup cards"
```

---

### Task 8: CLI Recommendation Output

**Files:**
- Modify: `src/openbiliclaw/cli.py:414-428`
- Modify: `tests/test_cli.py:2344-2400`

**Interfaces:**
- Consumes: `format_published_time()` from Task 1 and `Recommendation.content` publication fields from Task 2.
- Produces: optional `发布时间` row in `openbiliclaw recommend` output.

- [ ] **Step 1: Add a failing CLI output assertion**

In `test_recommend_displays_results_and_marks_them_presented`, construct the fake content with:

```python
published_at="2026-07-08T06:30:00Z",
published_label="3 days ago",
```

Freeze formatting by testing `_print_recommendation_card()` directly with a monkeypatched formatter, or assert the absolute output using a publication date older than the current year:

```python
published_at="2020-07-08T06:30:00Z"
assert "发布时间" in result.stdout
assert "2020-07-08" in result.stdout
```

Add a second card with both fields empty and assert no blank `发布时间` row is printed for that card.

- [ ] **Step 2: Run focused CLI test and verify failure**

Run: `.venv/bin/pytest -q tests/test_cli.py::test_recommend_displays_results_and_marks_them_presented`

Expected: output lacks `发布时间`.

- [ ] **Step 3: Reuse the Python formatter in `_print_recommendation_card()`**

```python
from openbiliclaw.published_time import format_published_time


published = format_published_time(
    getattr(item.content, "published_at", ""),
    getattr(item.content, "published_label", ""),
)
rows = [
    ("标题", item.content.title or "（无标题）"),
    ("UP主", item.content.up_name or item.content.author_name or "（未知）"),
]
if published:
    rows.append(("发布时间", published))
rows.extend([
    ("推荐理由", item.expression or "（暂无）"),
])
```

Keep existing rows and ordering intact except for inserting publication time after author metadata.

- [ ] **Step 4: Run CLI tests**

Run: `.venv/bin/pytest -q tests/test_cli.py -k 'recommend'`

Expected: all recommendation CLI tests pass.

- [ ] **Step 5: Commit CLI output**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: show publication time in CLI recommendations"
```

---

### Task 9: Documentation, Architecture Sync, And Full Verification

**Files:**
- Modify: `docs/changelog.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/plans/2026-07-05-issue-75-desktop-ux-spec.md`
- Modify: `docs/plans/2026-07-05-issue-75-desktop-ux-plan.md`

**Interfaces:**
- Consumes all implementation behavior from Tasks 1-8.
- Produces authoritative documentation distinguishing publication, discovery, and recommendation times.

- [ ] **Step 1: Update module and changelog documentation**

Add a concise current-version changelog bullet with this content:

```markdown
- **多平台发布时间补齐（issue #75 后续）**：Bilibili、小红书、抖音、YouTube、X、知乎和 Reddit 的可靠发布时间现贯穿统一候选池、缓存与推荐/惊喜出口；精确时间按本地相对日期展示，平台仅提供相对时间时保留原文，缺失时隐藏。旧缓存不联网回填，投币数明确不做。
```

Document public fields `published_at`/`published_label`, storage empty-preservation, extension source extraction, and CLI `发布时间` output in the matching module docs.

- [ ] **Step 2: Synchronize all mandatory architecture entry points**

Update `docs/architecture.md`, `docs/spec.md` §3, and the matching top diagrams in both README files so the discovery/cache/API flow names publication metadata alongside existing duration/engagement metadata. Keep Chinese and English README diagrams structurally identical.

- [ ] **Step 3: Close the stale issue #75 documentation decision**

In the 2026-07-05 spec/plan, replace “publish time deferred” with a cross-link to the new design/implementation plan, retain “coin count rejected by maintainer decision,” and record that the middle-click UI path now has an exact regression test. Do not claim the GitHub issue itself is closed.

- [ ] **Step 4: Run formatting, lint, typing, and complete automated suites**

Run:

```bash
.venv/bin/ruff format src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
.venv/bin/pytest
cd extension
npm run typecheck
npm test
```

Expected: every command exits 0. Record exact pass counts in the final handoff; deprecation warnings may be reported but must not hide failures.

- [ ] **Step 5: Run real-browser smoke checks on all browser surfaces**

Start an isolated local backend/test fixture without real user Cookie or production data. Verify:

1. Desktop recommendation and delight cards: exact, fallback-label, and missing-time cases.
2. Desktop middle-click: native new tab plus status line/toast through `openRecommendation()`.
3. Mobile recommendation and delight cards: same labels and no empty placeholder.
4. Extension popup recommendation and delight cards: same labels, escaped text, exact-time tooltip.
5. No browser flow makes a new detail request solely for publication time.

Capture screenshots or a concise browser-check log for the handoff. Do not add generated screenshots to git unless the repository already tracks the chosen evidence location.

- [ ] **Step 6: Review the complete branch against the design**

Run:

```bash
git diff --check <base>...HEAD
git log --oneline <base>..HEAD
git diff --stat <base>...HEAD
```

Expected: no whitespace errors; commits correspond to Tasks 1-8 plus docs; no coin field, new dependency, config field, release bump, issue mutation, or unrelated refactor appears.

- [ ] **Step 7: Commit documentation and verification updates**

```bash
git add docs/changelog.md docs/modules/discovery.md docs/modules/runtime.md docs/modules/extension.md docs/modules/cli.md docs/architecture.md docs/spec.md README.md README_EN.md docs/plans/2026-07-05-issue-75-desktop-ux-spec.md docs/plans/2026-07-05-issue-75-desktop-ux-plan.md
git commit -m "docs: document multiplatform publication time"
```

---

## Completion Criteria

- Seven platforms populate exact or fallback publication metadata when their current source payload contains it.
- Unknown time stays absent; no discovery/task/recommendation timestamp is substituted.
- Candidate and cache rediscovery preserve prior non-empty values.
- Recommendation, reshuffle, pending delight, delight batch, and runtime events expose both additive fields.
- Desktop, mobile, popup, and CLI obey the same display thresholds and hide empty values.
- Desktop middle-click has an exact same-handler regression test.
- Right-click tracking, optimistic profile rollback, coin count, detail fetch, historical network backfill, release work, and GitHub mutation remain absent.
- Required module docs, changelog, architecture/spec diagrams, README diagrams, and issue #75 planning docs are synchronized.
- Backend tests/lint/types, extension tests/typecheck, and real-browser smoke checks all pass.
