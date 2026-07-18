# Unified Discovery Candidate Pool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the unified discovery candidate pool described in `docs/plans/2026-06-04-unified-discovery-candidate-pool-spec.md`: every source fetches and normalizes candidates first, then a shared mixed-source evaluator and shared admission path decide what reaches `content_cache`.

**Architecture:** Keep `content_cache` as the formal recommendation pool and add `discovery_candidates` as a durable raw/evaluation staging table. Existing source strategies become fetch-only producers for the primary path; a new candidate pipeline drains mixed-source pending rows, reuses `ContentDiscoveryEngine` batch scoring, applies shared acceptance/capacity guards, then writes accepted items through the existing cache/admission machinery. `RecommendationEngine.classify_pool_backlog()` remains as a recovery path for legacy rows, not the normal XHS path.

**Tech Stack:** Python dataclasses / asyncio / SQLite / FastAPI, existing discovery and recommendation engines, existing source producers, pytest, Ruff, MyPy.

---

## Source Spec

- Spec: `docs/plans/2026-06-04-unified-discovery-candidate-pool-spec.md`
- Key boundary: this plan only changes discovery candidate supply and admission into the recommendation pool. It does not change user behavior cognition, recommendation serving/ranking UI, or `RecommendationEngine.serve()`.

## Design Decisions

- **Formal pool stays `content_cache`:** frontend availability, "可换" counts, recommendation serving, copy precompute, delight scoring, and MMR continue to read `content_cache`.
- **Pending table is inventory, not user-visible capacity:** `discovery_candidates` rows count as raw material and diagnostics only until accepted and cached.
- **Capacity gate comes first:** when `pool_available_count >= pool_target_count`, scheduled discovery must not call source producers, enqueue new candidates, or run normal LLM evaluation.
- **Evaluate mixed batches by item metadata:** the prompt must carry `source_platform`, `source_strategy`, `content_type`, and source context per item. Batch-level platform becomes `mixed`.
- **Incremental migration:** add pending pipeline while the old direct path still works, then move Bilibili, XHS, Douyin, and YouTube one source family at a time.
- **Reuse before inventing:** admission should reuse `DiscoveredContent.to_cache_kwargs()`, `ContentDiscoveryEngine` scoring, existing source-aware identity, existing recent-view exclusion, existing pool franchise/style/topic guards, and existing precompute/cap enforcement.

---

### Task 1: Lock Capacity Semantics Before Adding Pending Inventory

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Test: `tests/test_refresh_runtime.py`

**Step 1: Write failing tests for global available deficit**

Add tests near the existing source quota tests in `tests/test_refresh_runtime.py`:

```python
def test_source_requested_count_is_bounded_by_global_available_deficit() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=98,
            source_available_counts={"bilibili": 10},
            source_raw_counts={"bilibili": 10},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=100,
        pool_source_shares={"bilibili": 1},
    )

    assert controller._source_requested_count("bilibili") == 2
```

Add producer gate coverage:

```python
@pytest.mark.asyncio
async def test_non_bili_producer_not_called_when_global_pool_is_full() -> None:
    xhs = _FakeXhsProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=100,
            source_available_counts={"xiaohongshu": 0},
            source_raw_counts={"xiaohongshu": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        xhs_producer=xhs,
        pool_target_count=100,
        pool_source_shares={"bilibili": 8, "xiaohongshu": 1},
    )

    await controller._tick_xhs_producer()

    assert xhs.calls == []
```

**Step 2: Run tests and verify failure**

```bash
pytest tests/test_refresh_runtime.py -k "global_available_deficit or non_bili_producer_not_called" -q
```

Expected: first test fails because `_source_requested_count()` does not include `global_available_deficit`; producer gate may already pass if `_source_deficit()` returns zero after the fix only.

**Step 3: Implement the capacity bound**

In `ContinuousRefreshController._source_requested_count()`, compute:

```python
global_available_deficit = max(
    0,
    int(self.pool_target_count)
    - int(self.database.count_pool_candidates(xhs_self_nickname=self._xhs_self_nickname())),
)
if global_available_deficit <= 0:
    return 0
return max(0, min(available_deficit, raw_headroom, global_available_deficit))
```

Keep the existing fallback behavior for databases that do not accept `xhs_self_nickname`.

**Step 4: Run focused tests**

```bash
pytest tests/test_refresh_runtime.py -k "source_requested_count or producer_not_called or pool_at_cap" -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py tests/test_refresh_runtime.py
git commit -m "fix: bound source discovery by global pool deficit"
```

---

### Task 2: Add Durable `discovery_candidates` Storage

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Create: `src/openbiliclaw/discovery/candidate_pool.py`
- Test: `tests/test_discovery_candidate_store.py`

**Step 1: Write failing storage tests**

Create `tests/test_discovery_candidate_store.py`:

```python
from pathlib import Path

from openbiliclaw.discovery.candidate_pool import (
    DiscoveryCandidateWrite,
    discovered_content_to_candidate_write,
    row_to_discovered_content,
)
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.storage.database import Database


def test_enqueue_discovery_candidates_dedupes_by_source_key(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    item = DiscoveredContent(
        title="XHS note",
        content_id="note-1",
        content_url="https://www.xiaohongshu.com/explore/note-1?xsec_token=abc",
        source_platform="xiaohongshu",
        source_strategy="xhs-extension-search",
        author_name="author",
    )

    first = db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
    second = db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])

    assert first == 1
    assert second == 0
    counts = db.count_discovery_candidates_by_status()
    assert counts["pending_eval"] == 1


def test_claim_pending_candidates_interleaves_sources(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    writes = [
        DiscoveryCandidateWrite(
            candidate_key=f"bilibili:BV{i}",
            source_platform="bilibili",
            source_strategy="search",
            content_id=f"BV{i}",
            content_url=f"https://www.bilibili.com/video/BV{i}",
            title=f"Bili {i}",
        )
        for i in range(3)
    ] + [
        DiscoveryCandidateWrite(
            candidate_key=f"youtube:yt{i}",
            source_platform="youtube",
            source_strategy="yt_search",
            content_id=f"yt{i}",
            content_url=f"https://www.youtube.com/watch?v=yt{i}",
            title=f"YT {i}",
        )
        for i in range(3)
    ]
    db.enqueue_discovery_candidates(writes)

    rows = db.claim_discovery_candidates_for_eval(limit=4)

    assert len(rows) == 4
    assert {row["source_platform"] for row in rows} == {"bilibili", "youtube"}
    assert db.count_discovery_candidates_by_status()["evaluating"] == 4
```

**Step 2: Run test and verify failure**

```bash
pytest tests/test_discovery_candidate_store.py -q
```

Expected: FAIL because `candidate_pool.py` and database methods do not exist.

**Step 3: Add candidate DTO helpers**

Create `src/openbiliclaw/discovery/candidate_pool.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openbiliclaw.discovery.engine import DiscoveredContent


PENDING_EVAL = "pending_eval"
EVALUATING = "evaluating"
EVALUATED = "evaluated"
CACHED = "cached"
REJECTED_LOW_SCORE = "rejected_low_score"
FAILED_EVAL = "failed_eval"


@dataclass(frozen=True)
class DiscoveryCandidateWrite:
    candidate_key: str
    source_platform: str
    source_strategy: str
    content_id: str
    content_url: str
    title: str
    author_name: str = ""
    description: str = ""
    cover_url: str = ""
    content_type: str = ""
    duration: int = 0
    view_count: int = 0
    like_count: int = 0
    tags: list[str] = field(default_factory=list)
    source_context: str = ""
    candidate_tier: str = "primary"
    raw_payload: dict[str, Any] = field(default_factory=dict)
```

Add helpers:

```python
def candidate_key_for(item: DiscoveredContent) -> str:
    platform = (item.source_platform or "bilibili").strip().lower() or "bilibili"
    content_id = (item.content_id or item.bvid or "").strip()
    if content_id:
        return f"{platform}:{content_id}"
    url = (item.content_url or "").strip()
    if url:
        return f"{platform}:url:{url}"
    return f"{platform}:title:{item.title}:{item.author_name or item.up_name}"


def discovered_content_to_candidate_write(
    item: DiscoveredContent,
    *,
    source_context: str = "",
    raw_payload: dict[str, Any] | None = None,
) -> DiscoveryCandidateWrite:
    platform = item.source_platform or ("bilibili" if item.bvid else "")
    content_type = "note" if platform == "xiaohongshu" else "video"
    return DiscoveryCandidateWrite(
        candidate_key=candidate_key_for(item),
        source_platform=platform,
        source_strategy=item.source_strategy,
        content_type=content_type,
        content_id=item.content_id or item.bvid,
        content_url=item.content_url,
        title=item.title,
        author_name=item.author_name or item.up_name,
        description=item.description,
        cover_url=item.cover_url,
        duration=item.duration,
        view_count=item.view_count,
        like_count=item.like_count,
        tags=list(item.tags),
        source_context=source_context,
        candidate_tier=item.candidate_tier,
        raw_payload=dict(raw_payload or {}),
    )
```

Add `row_to_discovered_content(row)` to reconstruct `DiscoveredContent` from database rows.

**Step 4: Add table and indexes**

In `src/openbiliclaw/storage/database.py`, extend `SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_key TEXT NOT NULL UNIQUE,
    source_platform TEXT NOT NULL DEFAULT '',
    source_strategy TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    content_id TEXT NOT NULL DEFAULT '',
    content_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    author_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0,
    view_count INTEGER NOT NULL DEFAULT 0,
    like_count INTEGER NOT NULL DEFAULT 0,
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_context TEXT NOT NULL DEFAULT '',
    candidate_tier TEXT NOT NULL DEFAULT 'primary',
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending_eval',
    relevance_score REAL NOT NULL DEFAULT 0.0,
    relevance_reason TEXT NOT NULL DEFAULT '',
    topic_key TEXT NOT NULL DEFAULT '',
    topic_group TEXT NOT NULL DEFAULT '',
    style_key TEXT NOT NULL DEFAULT '',
    franchise_key TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    evaluated_at TIMESTAMP,
    cached_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_status_seen
    ON discovery_candidates(status, last_seen_at, id);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_source_status
    ON discovery_candidates(source_platform, status, last_seen_at);
```

**Step 5: Add database methods**

Add methods to `Database`:

```python
def enqueue_discovery_candidates(self, candidates: Sequence[Any]) -> int: ...
def claim_discovery_candidates_for_eval(self, *, limit: int) -> list[dict[str, Any]]: ...
def update_discovery_candidate_evaluations(self, evaluations: Sequence[Mapping[str, Any]]) -> int: ...
def mark_discovery_candidate_cached(self, candidate_id: int) -> None: ...
def reject_discovery_candidate(self, candidate_id: int, *, status: str, reason: str = "") -> None: ...
def count_discovery_candidates_by_status(self) -> dict[str, int]: ...
def count_discovery_candidates_by_source_status(self) -> dict[str, dict[str, int]]: ...
def count_discovery_pending_raw_material_by_source(self) -> dict[str, int]: ...
```

Implementation rules:

- `enqueue_discovery_candidates()` uses `INSERT ... ON CONFLICT(candidate_key) DO UPDATE SET last_seen_at=CURRENT_TIMESTAMP` but does not reset evaluated/cached statuses.
- `claim_discovery_candidates_for_eval()` selects `pending_eval` rows with a fair source interleave. Start with SQL ordered by `source_platform, last_seen_at, id`, then interleave in Python; mark claimed rows `evaluating` in one transaction.
- `update_discovery_candidate_evaluations()` moves rows to `evaluated`, writes score/reason/topic/style/franchise, and sets `evaluated_at`.
- Counts must return empty dicts instead of raising when no rows exist.

**Step 6: Run focused tests**

```bash
pytest tests/test_discovery_candidate_store.py -q
pytest tests/test_storage.py -q
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/discovery/candidate_pool.py tests/test_discovery_candidate_store.py
git commit -m "feat: add discovery candidate store"
```

---

### Task 3: Make Batch Evaluation Truly Mixed-Source

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Test: `tests/test_llm_prompts.py`
- Test: `tests/test_discovery_engine.py`

**Step 1: Write prompt test**

In `tests/test_llm_prompts.py`, add:

```python
def test_batch_content_evaluation_prompt_allows_per_item_platforms() -> None:
    messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["systems"]},
        source_platform="mixed",
        source_context="mixed",
        content_items=[
            {
                "content_id": "BV1",
                "source_platform": "bilibili",
                "source_strategy": "search",
                "content_type": "video",
                "title": "Bili item",
            },
            {
                "content_id": "xhs1",
                "source_platform": "xiaohongshu",
                "source_strategy": "xhs-extension-search",
                "content_type": "note",
                "title": "XHS item",
            },
        ],
    )

    system = messages[0]["content"]
    user = messages[1]["content"]

    assert "<source_platform>\n\nmixed\n\n</source_platform>" in user
    assert '"source_platform": "bilibili"' in user
    assert '"source_platform": "xiaohongshu"' in user
    assert "Do not lower or raise preference score merely because" in system
```

**Step 2: Write engine payload test**

In `tests/test_discovery_engine.py`, add an async LLM fake and assert the user prompt contains per-item platforms when `_evaluate_batch()` receives mixed sources.

```python
async def test_evaluate_batch_sends_per_item_platform_metadata() -> None:
    llm = _RecordingLLM(
        response=[
            {"content_id": "BV1", "score": 0.8, "reason": "ok", "topic_group": "tech", "style_key": "deep_dive"},
            {"content_id": "xhs1", "score": 0.7, "reason": "ok", "topic_group": "life", "style_key": "lifestyle"},
        ]
    )
    engine = ContentDiscoveryEngine(llm_service=llm)
    profile = _build_profile()

    await engine._evaluate_batch(
        [
            DiscoveredContent(bvid="BV1", title="Bili", source_platform="bilibili", source_strategy="search"),
            DiscoveredContent(
                content_id="xhs1",
                title="XHS",
                source_platform="xiaohongshu",
                source_strategy="xhs-extension-search",
                content_url="https://www.xiaohongshu.com/explore/xhs1",
            ),
        ],
        profile,
        source_context="mixed",
    )

    assert '"source_platform": "bilibili"' in llm.calls[-1]["user_input"]
    assert '"source_platform": "xiaohongshu"' in llm.calls[-1]["user_input"]
    assert "<source_platform>\n\nmixed\n\n</source_platform>" in llm.calls[-1]["user_input"]
```

**Step 3: Run tests and verify failure**

```bash
pytest tests/test_llm_prompts.py -k batch_content_evaluation_prompt -q
pytest tests/test_discovery_engine.py -k per_item_platform -q
```

Expected: FAIL until prompt rules and `_evaluate_batch()` payload are updated.

**Step 4: Update prompt static rules**

In `_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT`, add a permanent rule:

```text
When content_batch items include source_platform/source_strategy/content_type,
use those per-item fields as the authoritative platform context. Do not lower
or raise preference score merely because content comes from a different
platform; score every item against the same Soul-profile rubric.
```

Do not interpolate dynamic values into the system prompt.

**Step 5: Update `_evaluate_batch()` payload**

In `ContentDiscoveryEngine._evaluate_batch()`, build each item with:

```python
platform = c.source_platform or ("bilibili" if c.bvid else "")
content_items = [
    {
        "bvid": c.bvid,
        "content_id": c.content_id or c.bvid,
        "content_url": c.content_url,
        "source_platform": platform or "bilibili",
        "source_strategy": c.source_strategy,
        "source_context": source_context or c.source_strategy,
        "content_type": "note" if platform == "xiaohongshu" else "video",
        "title": c.title,
        "up_name": c.up_name,
        "author_name": c.author_name or c.up_name,
        "description": (c.description or "")[:200],
        "duration": c.duration,
        "view_count": c.view_count,
    }
    for c in batch
]
source_platform = "mixed" if len({item["source_platform"] for item in content_items}) > 1 else content_items[0]["source_platform"]
```

Pass that `source_platform` to `build_batch_content_evaluation_prompt()`.

**Step 6: Run focused tests**

```bash
pytest tests/test_llm_prompts.py -k batch_content_evaluation_prompt -q
pytest tests/test_discovery_engine.py -k "evaluate_batch or batch" -q
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/llm/prompts.py src/openbiliclaw/discovery/engine.py tests/test_llm_prompts.py tests/test_discovery_engine.py
git commit -m "feat: evaluate mixed-source discovery batches"
```

---

### Task 4: Add Candidate Pipeline For Evaluate-And-Admit

**Files:**
- Create: `src/openbiliclaw/discovery/candidate_pipeline.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Test: `tests/test_discovery_candidate_pipeline.py`

**Step 1: Write failing pipeline tests**

Create `tests/test_discovery_candidate_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_pipeline_evaluates_mixed_pending_and_caches_accepted(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates([
        DiscoveryCandidateWrite(
            candidate_key="bilibili:BV1",
            source_platform="bilibili",
            source_strategy="search",
            content_id="BV1",
            content_url="https://www.bilibili.com/video/BV1",
            title="Bili",
        ),
        DiscoveryCandidateWrite(
            candidate_key="youtube:yt1",
            source_platform="youtube",
            source_strategy="yt_search",
            content_id="yt1",
            content_url="https://www.youtube.com/watch?v=yt1",
            title="YT",
        ),
    ])
    llm = _ScoringLLM([
        {"content_id": "BV1", "score": 0.80, "reason": "fit", "topic_group": "tech", "style_key": "deep_dive"},
        {"content_id": "yt1", "score": 0.40, "reason": "weak", "topic_group": "misc", "style_key": "light_chat"},
    ])
    discovery_engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    pipeline = DiscoveryCandidatePipeline(database=db, discovery_engine=discovery_engine)

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result["evaluated"] == 2
    assert result["cached"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM content_cache WHERE bvid='BV1'").fetchone()[0] == 1
    assert db.count_discovery_candidates_by_status()["cached"] == 1
    assert db.count_discovery_candidates_by_status()["rejected_low_score"] == 1
```

Add capacity race coverage:

```python
@pytest.mark.asyncio
async def test_pipeline_stops_admission_when_pool_reaches_target(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    _seed_visible_pool_row(db, "already-ready")
    db.enqueue_discovery_candidates([...two high-score writes...])
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=ContentDiscoveryEngine(llm_service=_AllHighLLM(), database=db),
        pool_target_count=1,
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result["evaluated"] == 0
    assert result["cached"] == 0
    assert db.count_discovery_candidates_by_status()["pending_eval"] == 2
```

**Step 2: Run test and verify failure**

```bash
pytest tests/test_discovery_candidate_pipeline.py -q
```

Expected: FAIL because pipeline does not exist.

**Step 3: Add public cache/admission wrapper**

In `ContentDiscoveryEngine`, add:

```python
def cache_evaluated_results(self, results: list[DiscoveredContent]) -> int:
    before = 0
    if self._database is not None:
        try:
            before = int(self._database.count_pool_raw_material_candidates())
        except Exception:
            before = 0
    self._cache_results(results)
    if self._database is None:
        return 0
    try:
        after = int(self._database.count_pool_raw_material_candidates())
    except Exception:
        return len(results)
    return max(0, after - before)
```

If count-delta is too brittle in tests, return the number of attempted results and let the pipeline mark cached rows by checking `content_cache` after each write.

**Step 4: Implement `DiscoveryCandidatePipeline`**

Create `src/openbiliclaw/discovery/candidate_pipeline.py` with:

```python
@dataclass
class DiscoveryCandidatePipeline:
    database: Any
    discovery_engine: ContentDiscoveryEngine
    pool_target_count: int = 300
    score_thresholds: dict[str, float] = field(default_factory=_default_score_thresholds)

    async def drain_pending(self, *, profile: Any, batch_size: int = 30) -> dict[str, int]:
        if self._pool_full():
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        rows = self.database.claim_discovery_candidates_for_eval(limit=batch_size)
        if not rows:
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        items = [row_to_discovered_content(row) for row in rows]
        scores = await self.discovery_engine.evaluate_content_batch(
            items,
            profile,
            source_context="mixed",
            batch_size=batch_size,
        )
        self._persist_evaluations(rows, items, scores)
        accepted = self._accepted_items(rows, items, scores)
        cached = self._admit_until_full(rows, accepted)
        rejected = len(rows) - cached
        return {"evaluated": len(rows), "cached": cached, "rejected": rejected}
```

Implementation rules:

- `enqueue_candidates(items, source_context="")` converts `DiscoveredContent`
  items with `discovered_content_to_candidate_write()` and calls
  `database.enqueue_discovery_candidates()`.
- `produce_and_enqueue(profile, strategies, limit, strategy_limits=None,
  pool_snapshot=None)` calls `discovery_engine.produce_candidates()`, then
  `enqueue_candidates()`, and returns the number of newly enqueued rows. This
  is the runtime refresh bridge used by Task 6.
- `_pool_full()` uses `database.count_pool_candidates(xhs_self_nickname=...)` when available.
- `_accepted_items()` applies thresholds by strategy family:
  - search-like `0.65`
  - trending-like `0.60`
  - related-chain `0.65`
  - explore `0.55-0.60`
  - plugin/feed backfill `0.60`
- `_admit_until_full()` re-reads pool availability before admission and after each cache write.
- For accepted rows left over after cap is reached, keep status `evaluated`.
- For low-score rows, mark `rejected_low_score`.

**Step 5: Run focused tests**

```bash
pytest tests/test_discovery_candidate_pipeline.py -q
pytest tests/test_discovery_engine.py -k cache_evaluated -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/discovery/candidate_pipeline.py src/openbiliclaw/discovery/engine.py tests/test_discovery_candidate_pipeline.py
git commit -m "feat: add discovery candidate evaluation pipeline"
```

---

### Task 5: Add Fetch-Only Discovery Engine Path

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/discovery/strategies/trending.py`
- Modify: `src/openbiliclaw/discovery/strategies/related_chain.py`
- Modify: `src/openbiliclaw/discovery/strategies/explore.py`
- Test: `tests/test_discovery_engine.py`
- Test: existing strategy tests as needed

**Step 1: Write failing tests**

In `tests/test_discovery_engine.py`, add:

```python
@pytest.mark.asyncio
async def test_produce_candidates_does_not_evaluate_or_cache() -> None:
    strategy = _FakeStrategy(name="search", items=[DiscoveredContent(bvid="BV1", title="Raw")])
    db = _RecordingDatabase()
    llm = _FailingLLM()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    engine.register_strategy(strategy)

    items = await engine.produce_candidates(_build_profile(), strategies=["search"], limit=10)

    assert [item.bvid for item in items] == ["BV1"]
    assert db.cached == []
    assert llm.calls == []
```

Add strategy-specific tests for Bilibili strategies that currently lack `llm_evaluation`:

```python
@pytest.mark.asyncio
async def test_trending_strategy_can_return_raw_candidates_without_llm() -> None:
    strategy = TrendingStrategy(..., llm_evaluation=False)
    items = await strategy.discover(_build_profile(), limit=5)
    assert items
    assert llm.calls == []
```

**Step 2: Run tests and verify failure**

```bash
pytest tests/test_discovery_engine.py -k produce_candidates -q
pytest tests/test_trending_strategy.py -k without_llm -q
```

Expected: FAIL because `produce_candidates()` and Bilibili fetch-only switches do not exist.

**Step 3: Add `llm_evaluation` switch to Bilibili strategies**

For `TrendingStrategy`, `RelatedChainStrategy`, and `ExploreStrategy`, add:

```python
llm_evaluation: bool = True
```

Before calling `evaluate_content_batch()`, branch:

```python
if not self.llm_evaluation:
    return candidates[:limit]
```

Keep existing scoring thresholds unchanged when `llm_evaluation=True`.

**Step 4: Add `ContentDiscoveryEngine.produce_candidates()`**

In `ContentDiscoveryEngine`, add:

```python
async def produce_candidates(
    self,
    profile: SoulProfile,
    strategies: list[str] | None = None,
    limit: int = 30,
    *,
    fully_parallel: bool = False,
    strategy_limits: dict[str, int] | None = None,
    pool_snapshot: Any | None = None,
) -> list[DiscoveredContent]:
    active = self._strategies if strategies is None else [
        s for s in self._strategies if s.name in strategies
    ]
    with _temporary_strategy_llm_evaluation(active, enabled=False):
        raw = await self._run_strategies(
            active,
            profile=profile,
            limit=max(1, min(limit, self._backfill_target_count)),
            fully_parallel=fully_parallel,
            strategy_limits=strategy_limits,
            pool_snapshot=pool_snapshot,
        )
    return self._merge_duplicates(raw)[:limit]
```

Use a small context manager to restore `llm_evaluation` attributes after the run. Strategies without that attribute should be left untouched.

**Step 5: Run focused tests**

```bash
pytest tests/test_discovery_engine.py -k produce_candidates -q
pytest tests/test_trending_strategy.py tests/test_discovery_engine.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/trending.py src/openbiliclaw/discovery/strategies/related_chain.py src/openbiliclaw/discovery/strategies/explore.py tests/test_discovery_engine.py tests/test_trending_strategy.py
git commit -m "feat: add fetch-only discovery candidate production"
```

---

### Task 6: Wire Bilibili Runtime Refresh Through Pending Pool

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_api_app.py` or `tests/test_api_runtime_context.py` if present

**Step 1: Write failing runtime tests**

In `tests/test_refresh_runtime.py`, add a fake pipeline:

```python
class _FakeCandidatePipeline:
    def __init__(self) -> None:
        self.enqueued: list[tuple[list[str], int]] = []
        self.drains: list[int] = []

    async def produce_and_enqueue(
        self,
        *,
        profile: object,
        strategies: list[str],
        limit: int,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: object | None = None,
    ) -> int:
        self.enqueued.append((list(strategies), limit))
        return limit

    async def drain_pending(self, *, profile: object, batch_size: int = 30) -> dict[str, int]:
        self.drains.append(batch_size)
        return {"evaluated": batch_size, "cached": 3, "rejected": 0}
```

Test:

```python
@pytest.mark.asyncio
async def test_refresh_plan_uses_candidate_pipeline_when_available() -> None:
    pipeline = _FakeCandidatePipeline()
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager({"last_event_refresh_at": "", "last_trending_refresh_at": "", "last_explore_refresh_at": "", "last_processed_event_id": 0}),
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )

    await controller.force_refresh()

    assert pipeline.enqueued
    assert pipeline.drains
    assert discovery.calls == []
```

**Step 2: Run test and verify failure**

```bash
pytest tests/test_refresh_runtime.py -k candidate_pipeline -q
```

Expected: FAIL because the controller has no `discovery_candidate_pipeline`.

**Step 3: Extend controller**

Add dataclass field:

```python
discovery_candidate_pipeline: Any | None = None
```

Add a small public drain helper for API/XHS one-shot triggers:

```python
async def drain_discovery_candidates_once(self, *, batch_size: int | None = None) -> dict[str, int]:
    if self.discovery_candidate_pipeline is None:
        return {"evaluated": 0, "cached": 0, "rejected": 0}
    if self.database.count_pool_candidates(xhs_self_nickname=self._xhs_self_nickname()) >= self.pool_target_count:
        return {"evaluated": 0, "cached": 0, "rejected": 0}
    profile = await self.soul_engine.get_profile()
    return await self.discovery_candidate_pipeline.drain_pending(
        profile=profile,
        batch_size=batch_size or self.discovery_limit,
    )
```

In `_run_refresh_plan()`, replace the direct `discover_fn(profile, **discover_kwargs)` path when pipeline is present:

```python
if self.discovery_candidate_pipeline is not None:
    discovered_count = await self.discovery_candidate_pipeline.produce_and_enqueue(
        profile=profile,
        strategies=strategies,
        limit=effective_limit,
        strategy_limits=strategy_limits,
        pool_snapshot=pool_snapshot,
    )
    drain_result = await self.discovery_candidate_pipeline.drain_pending(
        profile=profile,
        batch_size=effective_limit,
    )
    discovered = [object()] * int(discovered_count)
else:
    discovered = await discover_fn(profile, **discover_kwargs)
```

Keep current event/status behavior:

- `last_discovered_count` should use enqueued/evaluated count, not just cached count.
- `last_replenished_count` remains derived from before/after frontend availability.
- Precompute should still run if anything was cached or if there is backlog.

**Step 4: Wire pipeline in runtime context**

In `src/openbiliclaw/api/runtime_context.py`, after constructing `new_discovery_engine`, create:

```python
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline

new_candidate_pipeline = DiscoveryCandidatePipeline(
    database=self.database,
    discovery_engine=new_discovery_engine,
    pool_target_count=new_config.scheduler.pool_target_count,
)
```

Pass it into `ContinuousRefreshController(...)`.

**Step 5: Run focused tests**

```bash
pytest tests/test_refresh_runtime.py -k "candidate_pipeline or force_refresh or pool_at_cap" -q
pytest tests/test_api_app.py -k runtime -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py src/openbiliclaw/api/runtime_context.py tests/test_refresh_runtime.py tests/test_api_app.py
git commit -m "feat: route bilibili refresh through discovery candidate pool"
```

---

### Task 7: Move XHS Ingest From Direct Cache To Pending Candidates

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_xhs_ingest.py`
- Test: `tests/test_xhs_self_filter_e2e.py`

**Step 1: Write failing XHS ingest tests**

Update the current tests that assert XHS rich notes immediately appear in `content_cache`. Add new assertions:

```python
def test_xhs_notes_enqueue_discovery_candidates_not_content_cache(xhs_task_client) -> None:
    app_client, db, _memory = xhs_task_client
    response = app_client.post(
        "/api/sources/xhs/observed-urls",
        json={
            "page_type": "search",
            "urls": ["https://www.xiaohongshu.com/explore/note-1?xsec_token=abc"],
            "notes": [
                {
                    "url": "https://www.xiaohongshu.com/explore/note-1?xsec_token=abc",
                    "title": "Coffee note",
                    "author": "Creator",
                    "cover_url": "https://img.example/cover.jpg",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    cache_row = db.conn.execute("SELECT bvid FROM content_cache WHERE bvid='note-1'").fetchone()
    pending_row = db.conn.execute(
        "SELECT source_platform, source_strategy, status FROM discovery_candidates WHERE content_id='note-1'"
    ).fetchone()
    assert cache_row is None
    assert pending_row["source_platform"] == "xiaohongshu"
    assert pending_row["source_strategy"] == "xhs-extension-search"
    assert pending_row["status"] == "pending_eval"
```

Keep tests proving self-authored notes are filtered before enqueue.

**Step 2: Run XHS tests and verify failure**

```bash
pytest tests/test_api_xhs_ingest.py -k "enqueue_discovery_candidates or xhs_notes" -q
```

Expected: FAIL because `_cache_xhs_notes()` writes `content_cache`.

**Step 3: Rename and change the helper**

In `src/openbiliclaw/api/app.py`, replace `_cache_xhs_notes()` with `_enqueue_xhs_notes()`:

```python
def _enqueue_xhs_notes(
    database: Any,
    notes: list[dict[str, Any]],
    page_type: str,
    self_info: dict[str, str] | None = None,
) -> int:
    ...
    item = DiscoveredContent(
        title=title,
        up_name=author,
        author_name=author,
        cover_url=cover_url,
        source_strategy=f"xhs-extension-{page_type}",
        source_platform="xiaohongshu",
        content_id=note_id,
        content_url=best_url,
    )
    writes.append(discovered_content_to_candidate_write(item, raw_payload=note))
    ...
    return database.enqueue_discovery_candidates(writes)
```

Do not spawn `_classify_new_pool_items()` for the normal path. That task remains for legacy `content_cache` rows only.

**Step 4: Update endpoints**

In `/api/sources/xhs/observed-urls` and `/api/sources/xhs/task-result`, call `_enqueue_xhs_notes()` instead of `_cache_xhs_notes()`.

Keep these existing behaviors unchanged:

- `save_xhs_observed_urls()`
- `_backfill_xhs_tokens()`
- self-info persistence and self-authored filtering
- bootstrap event propagation for `bootstrap_profile`

**Step 5: Trigger candidate drain when possible**

After enqueue, if `ctx.runtime_controller` or the candidate pipeline is available, trigger one non-blocking drain or refresh:

```python
if enqueued and getattr(ctx.runtime_controller, "discovery_candidate_pipeline", None) is not None:
    asyncio.create_task(ctx.runtime_controller.drain_discovery_candidates_once())
```

If adding a dedicated controller method is too much for this task, leave drain to the existing refresh loop and document that XHS pending rows are evaluated on the next tick.

**Step 6: Run focused tests**

```bash
pytest tests/test_api_xhs_ingest.py -q
pytest tests/test_xhs_self_filter_e2e.py -q
```

Expected: PASS after updating expected storage location from `content_cache` to `discovery_candidates` for normal discovery rows.

**Step 7: Commit**

```bash
git add src/openbiliclaw/api/app.py tests/test_api_xhs_ingest.py tests/test_xhs_self_filter_e2e.py
git commit -m "feat: enqueue xhs discovery candidates before evaluation"
```

---

### Task 8: Move Douyin And YouTube Producers To Pending Candidates

**Files:**
- Modify: `src/openbiliclaw/runtime/douyin_producer.py`
- Modify: `src/openbiliclaw/runtime/youtube_producer.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Test: `tests/test_douyin_discovery_service.py`
- Test: `tests/test_youtube_producer.py`
- Test: `tests/test_youtube_discovery_strategy.py`

**Step 1: Write failing Douyin producer test**

In `tests/test_douyin_discovery_service.py` or producer tests, assert runtime options are fetch-only:

```python
@pytest.mark.asyncio
async def test_douyin_runtime_producer_requests_fetch_only_candidates() -> None:
    seen_options = []

    async def discover(_profile, options):
        seen_options.append(options)
        return DouyinDiscoveryResult(items=[_douyin_item("v1")], cached=False, source_counts={"dy-plugin-search": 1})

    producer = DouyinDiscoveryProducer(soul_engine=_FakeSoulEngine(), discover=discover, min_interval_minutes=0)

    await producer.produce_if_due(limit=5)

    assert seen_options[0].cache is False
    assert seen_options[0].evaluate is False
```

**Step 2: Write failing YouTube producer/builder test**

In `tests/test_youtube_discovery_strategy.py`, assert the builder calls `produce_candidates()` or selected strategies with `llm_evaluation=False`, then enqueues through the pipeline.

Use a fake discovery engine:

```python
class _FakeDiscoveryEngineWithProduce:
    def __init__(self) -> None:
        self.produce_calls = []

    def register_strategy(self, strategy) -> None:
        self.strategy = strategy

    async def produce_candidates(self, profile, strategies=None, limit=30, **kwargs):
        self.produce_calls.append((strategies, limit, kwargs))
        return [DiscoveredContent(content_id="yt1", source_platform="youtube", source_strategy="yt_search", title="YT")]
```

Expected: current builder calls `discover()`, so the test fails.

**Step 3: Change Douyin runtime options**

In `DouyinDiscoveryProducer.produce_if_due()`, build options with:

```python
cache=False
evaluate=False
```

Return `discovered` count. Do not claim `cached=True`.

In `build_douyin_discovery_producer()`, keep `DouyinDiscoveryService`, but pass results to the candidate pipeline at the runtime layer rather than relying on `ContentDiscoveryEngine.discover()` cache side effects.

**Step 4: Change YouTube builder discovery callable**

In `build_youtube_discovery_producer()`'s `_discover()`, replace:

```python
raw_items = await discovery_engine.discover(...)
```

with:

```python
raw_items = await discovery_engine.produce_candidates(
    profile,
    strategies=[strategy],
    limit=max(1, int(result_limit)),
)
```

The producer should return raw `items`; the controller/pipeline enqueues them.

**Step 5: Add producer-to-pipeline handoff**

There are two acceptable implementation shapes. Pick one and keep it consistent:

1. Runtime controller owns producer handoff:
   - `_tick_douyin_producer()` receives raw `items` from the producer result.
   - It calls `discovery_candidate_pipeline.enqueue_candidates(items, source_context="douyin")`.
   - It then calls `drain_pending()` if the pool is still below target.

2. Producers own handoff:
   - Add optional `candidate_pipeline` field to `DouyinDiscoveryProducer` and `YoutubeDiscoveryProducer`.
   - `produce_if_due()` enqueues returned items itself.

Prefer option 1 because `ContinuousRefreshController` already owns quota/capacity decisions.

For option 1, update the producer result dicts to include raw items without
making them part of public API responses:

```python
return {
    "discovered": len(result.items),
    "items": result.items,
    "source_counts": dict(result.source_counts),
    "reason": "ok",
}
```

Then `_tick_douyin_producer()` / `_tick_youtube_producer()` should:

```python
result = await produce_fn(limit=limit)
items = list(result.get("items", [])) if isinstance(result, dict) else []
if items and self.discovery_candidate_pipeline is not None:
    self.discovery_candidate_pipeline.enqueue_candidates(
        items,
        source_context="douyin",  # use "youtube" in _tick_youtube_producer()
    )
    await self.drain_discovery_candidates_once(batch_size=limit)
```

Keep the existing skip/no-op behavior when the producer returns no items.

**Step 6: Run focused tests**

```bash
pytest tests/test_douyin_discovery_service.py tests/test_douyin_producer.py -q
pytest tests/test_youtube_producer.py tests/test_youtube_discovery_strategy.py -q
pytest tests/test_refresh_runtime.py -k "douyin_producer or youtube_producer" -q
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/openbiliclaw/runtime/douyin_producer.py src/openbiliclaw/runtime/youtube_producer.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/runtime/refresh.py tests/test_douyin_discovery_service.py tests/test_youtube_producer.py tests/test_youtube_discovery_strategy.py tests/test_refresh_runtime.py
git commit -m "feat: route douyin and youtube candidates through pending pool"
```

---

### Task 9: Extend Runtime Status And Raw Inventory Counts

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/api/models.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_storage.py`
- Test: `tests/test_api_app.py`

**Step 1: Write failing readiness/count tests**

Extend `tests/test_storage.py`:

```python
def test_count_pool_readiness_includes_pending_discovery_candidates(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    _seed_visible(db, "BV-ready")
    db.enqueue_discovery_candidates([
        DiscoveryCandidateWrite(
            candidate_key="youtube:yt-pending",
            source_platform="youtube",
            source_strategy="yt_search",
            content_id="yt-pending",
            content_url="https://www.youtube.com/watch?v=yt-pending",
            title="Pending",
        )
    ])

    readiness = db.count_pool_readiness()

    assert readiness["available"] == 1
    assert readiness["raw"] == 2
    assert readiness["pending_eval"] == 1
    assert readiness["evaluated_pending"] == 0
```

Extend runtime status test:

```python
async def test_refresh_pool_status_includes_pending_candidate_breakdown() -> None:
    database = _FakeDatabase([], pool_count=1, pool_raw_count=3, pool_pending_count=2)
    database.discovery_status_counts = {"pending_eval": 1, "evaluated": 1}
    ...
    assert pool_events[0]["pool_pending_eval_count"] == 1
    assert pool_events[0]["pool_evaluated_pending_count"] == 1
```

**Step 2: Run tests and verify failure**

```bash
pytest tests/test_storage.py -k pending_discovery_candidates -q
pytest tests/test_refresh_runtime.py -k pending_candidate_breakdown -q
```

Expected: FAIL because status payload only has `available/raw/pending`.

**Step 3: Include pending table in raw counts**

Update these database methods to include `discovery_candidates` rows with status in `pending_eval`, `evaluating`, or `evaluated`:

- `count_pool_raw_material_candidates()`
- `count_pool_raw_material_by_source()`
- `count_pool_readiness()`

Return expanded readiness:

```python
{
    "available": ...,
    "raw": ...,
    "pending": pending_eval + evaluated_pending,
    "pending_eval": ...,
    "evaluated_pending": ...,
}
```

Keep backward compatibility in `ContinuousRefreshController._pool_readiness_counts()` by defaulting missing keys to zero.

**Step 4: Publish expanded status**

In `_pool_count_payload()`, include:

```python
"pool_pending_eval_count": int(counts.get("pending_eval", 0)),
"pool_evaluated_pending_count": int(counts.get("evaluated_pending", 0)),
```

Keep existing `pool_pending_count` for current UI compatibility.

**Step 5: Run focused tests**

```bash
pytest tests/test_storage.py -k "pool_readiness or raw_material" -q
pytest tests/test_refresh_runtime.py -k pool_status -q
pytest tests/test_api_app.py -k runtime_status -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/api/models.py tests/test_storage.py tests/test_refresh_runtime.py tests/test_api_app.py
git commit -m "feat: expose pending discovery inventory status"
```

---

### Task 10: Keep Legacy Classification As Recovery Only

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_recommendation_engine.py`
- Test: `tests/test_api_xhs_ingest.py`

**Step 1: Write/adjust tests**

Keep existing tests proving `classify_pool_backlog()` can still classify old rows already in `content_cache`.

Add an API-level assertion that normal XHS observed-url ingest no longer spawns `_classify_new_pool_items()` when it enqueues pending candidates.

**Step 2: Make fallback explicit**

Update docstrings/logging:

- `RecommendationEngine.classify_pool_backlog()` docstring: "legacy/recovery path for rows already in `content_cache` without evaluation metadata."
- `_classify_new_pool_items()` in `api/app.py`: only used for startup repair or explicit legacy maintenance, not normal XHS ingest.

Do not remove `classify_pool_backlog()`; it protects existing user databases.

**Step 3: Run focused tests**

```bash
pytest tests/test_recommendation_engine.py -k classify_pool_backlog -q
pytest tests/test_api_xhs_ingest.py -k classify -q
```

Expected: PASS.

**Step 4: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py src/openbiliclaw/api/app.py tests/test_recommendation_engine.py tests/test_api_xhs_ingest.py
git commit -m "docs: mark pool backlog classification as legacy fallback"
```

---

### Task 11: Documentation And Architecture Sync

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/youtube.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`

**Step 1: Update discovery module docs**

Document:

- `discovery_candidates` pending table
- `DiscoveryCandidatePipeline`
- `ContentDiscoveryEngine.produce_candidates()`
- mixed-source batch evaluation
- source-specific fetch-only behavior
- capacity contract

**Step 2: Update runtime docs**

Document:

- refresh plan now does `produce -> enqueue -> evaluate -> admit -> precompute`
- `pool_available_count` vs raw/pending/evaluated pending
- stop rule when pool is at target

**Step 3: Update source docs**

Update XHS/extension docs:

- observed URLs and task results enqueue candidates, not direct `content_cache` rows
- self-authored filtering still happens before enqueue
- token backfill still preserves linkability

Update YouTube/Douyin docs:

- steady-state producers return raw normalized candidates
- shared pending pipeline evaluates/adopts candidates

**Step 4: Update architecture diagrams/text**

Update `docs/architecture.md`, `docs/spec.md`, and README architecture blocks to show:

```text
Source producers -> discovery_candidates -> mixed evaluator -> shared admission -> content_cache
```

Do not touch unrelated generated HTML diagram files unless this implementation task explicitly regenerates them.

**Step 5: Run doc checks**

```bash
rg -n "content_cache|discovery_candidates|classify_pool_backlog|produce_candidates|pending" docs README.md README_EN.md
git diff --check
```

Expected: docs mention the new flow consistently; no whitespace errors.

**Step 6: Commit**

```bash
git add docs/modules/discovery.md docs/modules/recommendation.md docs/modules/runtime.md docs/modules/youtube.md docs/modules/extension.md docs/architecture.md docs/spec.md README.md README_EN.md docs/changelog.md
git commit -m "docs: document unified discovery candidate pool"
```

---

### Task 12: Full Verification

**Files:**
- No planned source changes unless failures reveal a real issue.

**Step 1: Run formatting and lint**

```bash
ruff format src/ tests/
ruff check src/ tests/
```

Expected: PASS.

**Step 2: Run type checks**

```bash
mypy src/
```

Expected: PASS.

**Step 3: Run focused test suites**

```bash
pytest tests/test_discovery_candidate_store.py tests/test_discovery_candidate_pipeline.py -q
pytest tests/test_discovery_engine.py tests/test_llm_prompts.py -q
pytest tests/test_refresh_runtime.py -q
pytest tests/test_api_xhs_ingest.py tests/test_recommendation_engine.py -q
pytest tests/test_youtube_producer.py tests/test_youtube_discovery_strategy.py tests/test_douyin_discovery_service.py -q
```

Expected: PASS.

**Step 4: Run full tests**

```bash
pytest
```

Expected: PASS.

**Step 5: Manual smoke**

Run local daemon/API smoke if credentials are available:

```bash
openbiliclaw config-show
openbiliclaw start
openbiliclaw recommend
```

Manual checks:

- With `pool_available_count >= pool_target_count`, refresh does not call source producers and pending evaluator does not spend LLM calls.
- XHS observed-url request creates `discovery_candidates` rows and no immediate `content_cache` row.
- A pending mixed Bilibili/XHS/YouTube batch evaluates with per-item platform metadata.
- Accepted rows enter `content_cache` with non-empty `source_platform`, `content_id`, `content_url`, `style_key`, and `topic_group`.
- `get_pool_candidates()` only serves evaluated/cached rows with copy fields.

**Step 6: Final commit if verification changes files**

```bash
git status --short
git add <only related files>
git commit -m "test: verify unified discovery candidate pool"
```

---

## Execution Notes

- Do not delete or rewrite `RecommendationEngine.classify_pool_backlog()` during this migration. It is the safety net for old databases and failed direct-ingest rows.
- Keep `content_cache.bvid` compatibility: for non-Bilibili content, continue using `content_id` as `bvid` when writing cache rows, as the current DB schema and recommendation joins still depend on `bvid`.
- Avoid changing recommendation serving until the pending pipeline is stable.
- If a task exposes a broader pre-existing bug, fix it in a separate commit before continuing the migration task.
- The current worktree may contain unrelated generated diagram HTML edits. Do not include those files unless the documentation task explicitly regenerates and verifies them.

Plan complete. Use `superpowers:executing-plans` to implement it task-by-task.
