# Local-First Inspiration Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/plans/2026-07-03-local-first-inspiration-grounding-spec.md`. This plan delivers the spec's **Phase 1 only** (content_cache evidence source, relevance-scored DAO, provider chain, budget accounting, `grounding_source` provenance, ledger, report stubs). Phase 2 (remaining evidence sources, echo caps, report substance) is a separate follow-up plan.

**Goal:** Reuse existing discovery content as the first inspiration grounding source, so external search providers are used only to fill local evidence gaps.

**Architecture:** Add a relevance-scored database DAO that returns local evidence rows, then add a `LocalInspirationProvider` implementing the existing `InspirationSearchProvider` interface. Put `local_cache` first in the configured provider chain, expose which provider served each search, exempt local hits from the external grounding budget, tag generated keywords with `grounding_source`, and extend dry-run/report ledgers with local hits, misses, and budget-aware saved external searches.

**Tech Stack:** Python dataclasses/protocols, SQLite DAO methods in `storage/database.py`, existing `ExaPreviewItem` / provider chain, pytest, Ruff, MyPy.

---

## Preconditions (blocking)

The worktree currently carries the entire prior inspiration implementation as
**uncommitted working-tree state** (~36 dirty files, ~7.5k lines, zero commits
ahead of main). Task 0 checkpoints it **before any other task**. Skipping
Task 0 means this plan's per-task `git add` commands (which touch the same hot
files: `database.py`, `inspiration_provider.py`, `keyword_planner.py`,
`cli.py`, `config.py`) would silently sweep unrelated prior work into commits
labeled as local-first features.

## File Structure

- Modify `src/openbiliclaw/storage/database.py`: add `search_local_inspiration_evidence()`, persist `grounding_source`, add the secondary-interest selection ledger, and add cohort report stub fields.
- Modify `src/openbiliclaw/discovery/inspiration_provider.py`: add `LocalInspirationProvider`, provider alias parsing, `last_search_provider` attribution, and local ledger merge support.
- Modify `src/openbiliclaw/runtime/keyword_planner.py`: local ledger fields, budget exemption for local hits, `external_searches_saved` formula, sampled-interest cooldown recording, `grounding_source` keyword metadata.
- Modify `src/openbiliclaw/config.py`: default `inspiration_search_backends` includes `local_cache`.
- Modify `config.example.toml`: document `local_cache`.
- Modify `src/openbiliclaw/api/runtime_context.py`: pass the database into `build_inspiration_search_provider()`.
- Modify `src/openbiliclaw/cli.py`: pass the database into dry-run provider construction.
- Test `tests/test_storage.py`, `tests/test_discovery_inspiration_provider.py`, `tests/test_config.py`, `tests/test_keyword_planner.py`.
- Docs `docs/modules/discovery.md`, `docs/modules/config.md`, `docs/changelog.md`.

---

### Task 0: Checkpoint The Existing Working Tree

**Files:**
- No new files; commits only.

- [ ] **Step 1: Confirm the full suite is green on the current tree**

```bash
uv run --extra dev pytest -q
```

Expected: pass (last known state: `3309 passed, 32 skipped`). Do not start
chunking commits on a red tree.

- [ ] **Step 2: Commit the prior work in functional chunks**

Group the ~36 dirty files into commits along module lines (adjust to what
`git status --short` actually shows):

1. `feat: add inspiration storage daos and cohort stats` — `storage/database.py` + storage tests
2. `feat: add inspiration providers and platform backends` — `discovery/inspiration.py`, `discovery/inspiration_provider.py` + provider tests
3. `feat: add inspiration stage to keyword planner` — `runtime/keyword_planner.py`, `llm/prompts.py`, `llm/service.py` + planner tests
4. `feat: wire inspiration config, cli, and runtime` — `config.py`, `cli.py`, `api/runtime_context.py`, `config.example.toml` + config/cli tests
5. `feat: hook label migration into profile consolidation` — `soul/consolidator.py`, `soul/engine.py` + tests
6. `docs: add inspiration specs and module docs` — all `docs/` changes + plan/spec files

- [ ] **Step 3: Verify the tree is clean and commits exist**

```bash
git status --short
git log --oneline main..HEAD
```

Expected: empty status (or only intentionally-unrelated leftovers, listed
explicitly); the checkpoint commits visible ahead of main.

---

### Task 1: Storage DAO For Local Evidence

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing DAO tests**

Add these methods to `TestDatabase` in `tests/test_storage.py`:

```python
def test_search_local_inspiration_evidence_returns_content_cache_rows(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVlocal1",
            content_id="BVlocal1",
            source_platform="bilibili",
            title="独立游戏 机制拆解：地图叙事如何成立",
            content_url="https://www.bilibili.com/video/BVlocal1",
            description="围绕独立游戏、关卡设计、叙事节奏的分析。",
            topic_group="独立游戏",
            pool_topic_label="独立游戏机制",
            pool_status="fresh",
        )

        rows = db.search_local_inspiration_evidence(
            "独立游戏 机制",
            limit=5,
            lookback_days=365,
        )

        assert rows
        assert rows[0]["title"] == "独立游戏 机制拆解：地图叙事如何成立"
        assert rows[0]["url"] == "https://www.bilibili.com/video/BVlocal1"
        assert rows[0]["source_table"] == "content_cache"
        assert rows[0]["source_platform"] == "bilibili"
        db.close()

def test_search_local_inspiration_evidence_matches_spaceless_cjk_query(self) -> None:
    # LLM-brainstormed Chinese probes often contain no delimiters; the DAO
    # must still match via CJK 2-gram tokens.
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVlocal1",
            content_id="BVlocal1",
            source_platform="bilibili",
            title="独立游戏 机制拆解：地图叙事如何成立",
            content_url="https://www.bilibili.com/video/BVlocal1",
            description="围绕独立游戏、关卡设计、叙事节奏的分析。",
            topic_group="独立游戏",
            pool_topic_label="独立游戏机制",
            pool_status="fresh",
        )

        rows = db.search_local_inspiration_evidence(
            "独立游戏机制",
            limit=5,
            lookback_days=365,
        )

        assert rows
        assert rows[0]["title"] == "独立游戏 机制拆解：地图叙事如何成立"
        db.close()

def test_search_local_inspiration_evidence_synthesizes_bilibili_url(self) -> None:
    # Legacy Bilibili cache rows can have bvid but blank content_url. They
    # should still be usable as local inspiration evidence.
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVlocal1",
            content_id="BVlocal1",
            source_platform="bilibili",
            title="独立游戏 机制拆解：地图叙事如何成立",
            content_url="",
            description="围绕独立游戏、关卡设计、叙事节奏的分析。",
            topic_group="独立游戏",
            pool_topic_label="独立游戏机制",
            pool_status="fresh",
        )

        rows = db.search_local_inspiration_evidence(
            "独立游戏 机制",
            limit=5,
            lookback_days=365,
        )

        assert rows
        assert rows[0]["url"] == "https://www.bilibili.com/video/BVlocal1"
        db.close()

def test_search_local_inspiration_evidence_excludes_single_weak_token_rows(self) -> None:
    # One weak token match ("独立" only) is not evidence; without this floor,
    # recency-ordered junk rows would satisfy the sufficiency rule and
    # suppress a useful external search.
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVlocal2",
            content_id="BVlocal2",
            source_platform="bilibili",
            title="独立音乐人访谈实录",
            content_url="https://www.bilibili.com/video/BVlocal2",
            description="音乐创作与巡演生活。",
            topic_group="音乐",
            pool_topic_label="独立音乐",
            pool_status="fresh",
        )

        rows = db.search_local_inspiration_evidence(
            "独立游戏 机制",
            limit=5,
            lookback_days=365,
        )

        assert rows == []
        db.close()
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
uv run --extra dev pytest tests/test_storage.py -q -k search_local_inspiration_evidence
```

Expected: fail with `AttributeError: 'Database' object has no attribute 'search_local_inspiration_evidence'`.

- [ ] **Step 3: Implement the DAO**

Add module-level helpers and the method to `src/openbiliclaw/storage/database.py`
(add `import re` if missing):

```python
_LOCAL_EVIDENCE_CJK_RE = re.compile(r"[一-鿿]")


def _escape_like_term(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _local_evidence_tokens(query: str) -> list[str]:
    parts = [
        part.strip()
        for part in re.split(r"[\s,，。:：/|]+", query)
        if len(part.strip()) >= 2
    ]
    if not parts:
        parts = [query]
    tokens: list[str] = []
    for part in parts:
        tokens.append(part)
        # Spaceless CJK phrases rarely repeat verbatim in titles; 2-grams
        # give LIKE something to bite on.
        if len(part) >= 4 and _LOCAL_EVIDENCE_CJK_RE.search(part):
            tokens.extend(part[i : i + 2] for i in range(len(part) - 1))
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered
```

```python
def search_local_inspiration_evidence(
    self,
    query: str,
    *,
    limit: int = 10,
    lookback_days: int = 30,
) -> list[dict[str, object]]:
    clean_query = str(query or "").strip()
    if not clean_query:
        return []
    tokens = _local_evidence_tokens(clean_query)
    if not tokens:
        return []
    like_terms = [f"%{_escape_like_term(token)}%" for token in tokens[:12]]
    where = " OR ".join(
        "title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\'"
        for _ in like_terms
    )
    params: list[object] = []
    for term in like_terms:
        params.extend([term, term])
    params.append(f"-{max(1, int(lookback_days))} days")
    sql = f"""
        SELECT
            title,
            COALESCE(
                NULLIF(content_url, ''),
                CASE
                    WHEN COALESCE(bvid, '') != ''
                    THEN 'https://www.bilibili.com/video/' || bvid
                    ELSE ''
                END
            ) AS url,
            description,
            source_platform,
            content_id,
            pool_topic_label AS topic_label,
            discovered_at AS created_at
        FROM content_cache
        WHERE ({where})
          AND COALESCE(pool_status, '') NOT IN ('purged_by_dislike')
          AND datetime(COALESCE(NULLIF(discovered_at, ''), '1970-01-01')) >= datetime('now', ?)
        ORDER BY discovered_at DESC
        LIMIT 200
    """
    rows = self.conn.execute(sql, params).fetchall()
    scored: list[tuple[int, str, dict[str, object]]] = []
    for row in rows:
        title = str(row["title"] or "").strip()
        url = str(row["url"] or "").strip()
        if not title or not url:
            continue
        haystack = f"{title} {str(row['description'] or '')}"
        match_count = sum(1 for token in tokens if token in haystack)
        # Row quality floor: one weak token match is not evidence.
        if len(tokens) >= 2 and match_count < 2 and clean_query not in haystack:
            continue
        scored.append(
            (
                match_count,
                str(row["created_at"] or ""),
                {
                    "title": title,
                    "url": url,
                    "highlights": [str(row["description"] or "").strip()],
                    "source_table": "content_cache",
                    "source_platform": str(row["source_platform"] or ""),
                    "content_id": str(row["content_id"] or ""),
                    "topic_label": str(row["topic_label"] or ""),
                    "created_at": str(row["created_at"] or ""),
                },
            )
        )
    # Relevance first, recency as tiebreaker — never recency alone.
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [payload for _, _, payload in scored[: max(1, int(limit))]]
```

- [ ] **Step 4: Run the DAO tests and verify they pass**

```bash
uv run --extra dev pytest tests/test_storage.py -q -k search_local_inspiration_evidence
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/openbiliclaw/storage/database.py tests/test_storage.py
git commit -m "feat: add local inspiration evidence dao"
```

---

### Task 2: LocalInspirationProvider

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration_provider.py`
- Test: `tests/test_discovery_inspiration_provider.py`

- [ ] **Step 1: Write the failing provider tests**

```python
async def test_local_inspiration_provider_maps_database_rows_to_previews() -> None:
    class DB:
        def search_local_inspiration_evidence(self, query: str, *, limit: int, lookback_days: int):
            assert query == "独立游戏 机制"
            assert limit == 3
            assert lookback_days == 30
            return [
                {
                    "title": "独立游戏机制拆解",
                    "url": "https://example.test/game",
                    "highlights": ["地图叙事", "关卡设计"],
                    "source_table": "content_cache",
                    "source_platform": "bilibili",
                    "topic_label": "独立游戏",
                }
            ]

    provider = LocalInspirationProvider(DB(), min_results=1)

    assert await provider.search("独立游戏 机制", limit=3) == [
        ExaPreviewItem(
            title="独立游戏机制拆解",
            url="https://example.test/game",
            highlights=("地图叙事", "关卡设计"),
        )
    ]
    ledger = provider.grounding_ledger()
    assert ledger["local_hits"] == 1
    assert ledger["local_misses"] == 0
    assert ledger["local_sources"] == {"content_cache": 1}


async def test_local_inspiration_provider_misses_when_below_sufficiency() -> None:
    class DB:
        def search_local_inspiration_evidence(self, query: str, *, limit: int, lookback_days: int):
            return [
                {
                    "title": "只有一条",
                    "url": "https://example.test/one",
                    "highlights": [],
                    "source_table": "content_cache",
                    "source_platform": "bilibili",
                    "topic_label": "",
                }
            ]

    provider = LocalInspirationProvider(DB(), min_results=2)

    assert await provider.search("独立游戏 机制", limit=3) == []
    assert provider.grounding_ledger()["local_hits"] == 0
    assert provider.grounding_ledger()["local_misses"] == 1
```

Note: the provider ledger deliberately has **no** `external_searches_saved`
field — that number is computed at the planner stage level (Task 4), because
only the planner knows the remaining budget.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
uv run --extra dev pytest tests/test_discovery_inspiration_provider.py -q -k local_inspiration_provider
```

Expected: fail because `LocalInspirationProvider` is not defined.

- [ ] **Step 3: Implement `LocalInspirationProvider`**

Add to `src/openbiliclaw/discovery/inspiration_provider.py`:

```python
class LocalInspirationProvider:
    """Use existing local discovery assets as inspiration-only grounding."""

    backend_alias = "local_cache"

    def __init__(
        self,
        database: object,
        *,
        lookback_days: int = 30,
        min_results: int = 2,
        min_distinct_sources: int = 1,
    ) -> None:
        self._database = database
        self._lookback_days = max(1, int(lookback_days))
        self._min_results = max(1, int(min_results))
        self._min_distinct_sources = max(1, int(min_distinct_sources))
        self._ledger = self._new_ledger()

    @staticmethod
    def _new_ledger() -> dict[str, object]:
        return {"local_hits": 0, "local_misses": 0, "local_sources": {}}

    def begin_stage(self) -> None:
        self._ledger = self._new_ledger()

    def grounding_ledger(self) -> dict[str, object]:
        return {
            "local_hits": _ledger_int(self._ledger.get("local_hits", 0)),
            "local_misses": _ledger_int(self._ledger.get("local_misses", 0)),
            "local_sources": dict(
                cast("dict[str, int]", self._ledger.get("local_sources", {}))
            ),
        }

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        getter = getattr(self._database, "search_local_inspiration_evidence", None)
        if not callable(getter):
            self._ledger["local_misses"] = _ledger_int(self._ledger.get("local_misses", 0)) + 1
            return []
        rows = getter(query, limit=max(1, int(limit)), lookback_days=self._lookback_days)
        previews: list[ExaPreviewItem] = []
        distinct_sources: set[str] = set()
        source_counts: dict[str, int] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            title = _clean_title(row.get("title"))
            url = _first_text(row.get("url"), row.get("content_url"))
            if not title or not url:
                continue
            source_table = _first_text(row.get("source_table")) or "local"
            source_platform = _first_text(row.get("source_platform"))
            topic_label = _first_text(row.get("topic_label"))
            distinct_sources.add("|".join([source_table, source_platform, topic_label]))
            source_counts[source_table] = source_counts.get(source_table, 0) + 1
            previews.append(
                ExaPreviewItem(
                    title=title,
                    url=url,
                    highlights=tuple(_clean_highlights(row.get("highlights"))),
                )
            )
        previews = _dedupe_previews(previews, limit=max(1, int(limit)))
        if len(previews) < self._min_results or len(distinct_sources) < self._min_distinct_sources:
            self._ledger["local_misses"] = _ledger_int(self._ledger.get("local_misses", 0)) + 1
            return []
        self._ledger["local_hits"] = _ledger_int(self._ledger.get("local_hits", 0)) + 1
        ledger_sources = cast("dict[str, int]", self._ledger.setdefault("local_sources", {}))
        for source, count in source_counts.items():
            ledger_sources[source] = int(ledger_sources.get(source, 0)) + count
        return previews
```

(Only count `local_sources` for hits — misses return no evidence, so their
row counts would inflate the mix.)

- [ ] **Step 4: Import the provider in the test file and run the tests**

```bash
uv run --extra dev pytest tests/test_discovery_inspiration_provider.py -q -k local_inspiration_provider
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/inspiration_provider.py tests/test_discovery_inspiration_provider.py
git commit -m "feat: add local inspiration provider"
```

---

### Task 3: Provider Chain, Attribution, And Ledger Merge

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration_provider.py`
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_discovery_inspiration_provider.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing chain and fallback tests**

```python
def test_build_inspiration_search_provider_puts_local_cache_first() -> None:
    provider = build_inspiration_search_provider(
        ["local_cache", "exa"],
        database=object(),
        runner=lambda args, timeout: asyncio.sleep(0),
    )

    assert isinstance(provider, FallbackInspirationSearchProvider)
    assert [p.__class__.__name__ for p in provider._providers] == [
        "LocalInspirationProvider",
        "McporterExaInspirationProvider",
    ]


async def test_local_insufficiency_falls_through_to_next_provider() -> None:
    # Behavioral coverage of the gap-fill rule, not just chain construction.
    # Adapt construction to the existing FallbackInspirationSearchProvider
    # test patterns in this file.
    class EmptyDB:
        def search_local_inspiration_evidence(self, query: str, *, limit: int, lookback_days: int):
            return []

    class StubProvider:
        backend_alias = "stub"

        def begin_stage(self) -> None:
            pass

        async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
            return [
                ExaPreviewItem(title="外部证据", url="https://example.test/ext", highlights=())
            ]

    provider = FallbackInspirationSearchProvider(
        [LocalInspirationProvider(EmptyDB()), StubProvider()]
    )

    result = await provider.search("独立游戏 机制", limit=3)

    assert [item.title for item in result] == ["外部证据"]
    assert provider.last_search_provider == "stub"
    assert provider.grounding_ledger()["local_misses"] == 1


async def test_last_search_provider_reports_local_when_local_serves() -> None:
    class DB:
        def search_local_inspiration_evidence(self, query: str, *, limit: int, lookback_days: int):
            return [
                {
                    "title": "本地证据",
                    "url": "https://example.test/local",
                    "highlights": [],
                    "source_table": "content_cache",
                    "source_platform": "bilibili",
                    "topic_label": "",
                }
            ]

    provider = FallbackInspirationSearchProvider(
        [LocalInspirationProvider(DB(), min_results=1)]
    )

    await provider.search("独立游戏 机制", limit=3)

    assert provider.last_search_provider == "local_cache"


async def test_local_sufficient_hit_does_not_augment_to_external_provider() -> None:
    class DB:
        def search_local_inspiration_evidence(self, query: str, *, limit: int, lookback_days: int):
            return [
                {
                    "title": "本地证据",
                    "url": "https://example.test/local",
                    "highlights": [],
                    "source_table": "content_cache",
                    "source_platform": "bilibili",
                    "topic_label": "",
                }
            ]

    class ExternalProvider:
        backend_alias = "exa"

        def __init__(self) -> None:
            self.called = False

        def begin_stage(self) -> None:
            pass

        async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
            self.called = True
            return [ExaPreviewItem(title="外部证据", url="https://example.test/ext")]

    external = ExternalProvider()
    provider = FallbackInspirationSearchProvider(
        [LocalInspirationProvider(DB(), min_results=1), external]
    )

    result = await provider.search("独立游戏 机制", limit=3)

    assert [item.title for item in result] == ["本地证据"]
    assert external.called is False
    assert provider.last_search_provider == "local_cache"
```

- [ ] **Step 2: Run and verify failure**

```bash
uv run --extra dev pytest tests/test_discovery_inspiration_provider.py -q -k "local_cache_first or falls_through or last_search_provider"
```

Expected: fail — no `database` parameter, no `local_cache` alias, no
`last_search_provider` attribute.

- [ ] **Step 3: Add provider alias, constructor wiring, and attribution**

Modify `build_inspiration_search_provider()` signature:

```python
def build_inspiration_search_provider(
    backends: object = None,
    *,
    runner: Callable[[list[str], float], Awaitable[str]] | None = None,
    timeout_seconds: float = 6.0,
    database: object | None = None,
    platform_backends: list[PlatformSearchBackend] | None = None,
    platforms_per_probe: int = 2,
    riskcontrolled_probe_budget: int = 4,
    pages_per_probe: int = 1,
) -> InspirationSearchProvider | None:
```

Handle the backend (skip silently when no database is supplied):

```python
if backend == "local_cache":
    if database is not None:
        providers.append(LocalInspirationProvider(database))
```

Extend aliases in `_normalize_search_backends()`:

```python
"local": "local_cache",
"cache": "local_cache",
"local_cache": "local_cache",
"local-cache": "local_cache",
```

In `FallbackInspirationSearchProvider`:

- add `self.last_search_provider: str | None = None`;
- at the start of each `search()` call reset it to `None`; when a provider
  returns a non-empty result, set it to
  `getattr(provider, "backend_alias", provider.__class__.__name__)`.
  (The planner's grounding loop awaits searches sequentially, so reading the
  attribute after each `await` is safe; if the loop ever becomes concurrent
  this must move into the return value.)
- in `_should_augment_results()`, return `False` when
  `getattr(provider, "backend_alias", "") == "local_cache"`. A sufficient
  local hit must be terminal; otherwise the existing low-result augmentation
  path would still spend external searches after local evidence was accepted.

- [ ] **Step 4: Merge local ledger fields in `FallbackInspirationSearchProvider.grounding_ledger()`**

Merge `local_hits`, `local_misses`, and `local_sources` from child provider
ledgers using `_ledger_int`. Do **not** compute or merge
`external_searches_saved` here — Task 4 computes it at the planner level.

- [ ] **Step 5: Update config default**

In `src/openbiliclaw/config.py`, set default inspiration backends to:

```python
["local_cache", "platform_sources", "exa", "you"]
```

Update `tests/test_config.py` expected defaults and TOML render assertions.

- [ ] **Step 6: Pass database from runtime and CLI**

In `src/openbiliclaw/api/runtime_context.py` and `src/openbiliclaw/cli.py`,
pass the active `Database` instance as `database=database` or
`database=self.database` to `build_inspiration_search_provider()`.

- [ ] **Step 7: Run focused tests**

```bash
uv run --extra dev pytest tests/test_discovery_inspiration_provider.py tests/test_config.py -q
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add src/openbiliclaw/discovery/inspiration_provider.py src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py tests/test_discovery_inspiration_provider.py tests/test_config.py
git commit -m "feat: use local cache before external inspiration search"
```

---

### Task 4: Planner Budget Accounting, Saved Formula, And Keyword Provenance

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/runtime/keyword_planner.py`
- Test: `tests/test_keyword_planner.py`

- [ ] **Step 1: Write the failing planner tests**

Use a fake provider that serves probes locally:

```python
class LocalLedgerProvider:
    last_search_provider: str | None = None

    def begin_stage(self) -> None:
        pass

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        self.last_search_provider = "local_cache"
        return [
            ExaPreviewItem(
                title="本地证据标题",
                url="https://example.test/local",
                highlights=("本地证据摘要",),
            )
        ]

    def grounding_ledger(self) -> dict[str, object]:
        return {
            "local_hits": 1,
            "local_misses": 0,
            "local_sources": {"content_cache": 1},
        }
```

Assert three behaviors:

1. **Ledger merge + saved formula.** With per-stage budget `B` and zero
   external searches issued:

```python
assert report["grounding_ledger"]["local_hits"] == 1
assert report["grounding_ledger"]["local_sources"] == {"content_cache": 1}
# saved = min(local_hits, max(0, B - external_searches_issued)) = min(1, B) = 1
assert report["grounding_ledger"]["external_searches_saved"] == 1
```

2. **Budget exemption.** A probe whose search was served by `local_cache`
   (per `last_search_provider`) does not increment the ledger's external
   `searches` counter and does not consume
   `inspiration_max_probe_searches_per_stage`:

```python
assert report["grounding_ledger"]["searches"] == 0
```

3. **Keyword provenance.** Generated keyword rows / dry-run keyword entries
   carry `grounding_source` metadata (`"local_cache"` here; `"mixed"` when a
   branch's probes were served by different providers; `"none"` when no
   grounding evidence was found). Persisted rows must expose the same value:

```python
row = db.conn.execute(
    "SELECT grounding_source FROM discovery_keywords WHERE keyword = ?",
    (generated_keyword,),
).fetchone()
assert row["grounding_source"] == "local_cache"
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
uv run --extra dev pytest tests/test_keyword_planner.py -q -k "local_ledger or grounding_source"
```

Expected: fail — the planner ledger has no local fields, counts every search
against the budget, and does not tag keyword provenance.

- [ ] **Step 3: Implement**

In `src/openbiliclaw/storage/database.py`, add a persistent metadata column:

```python
_DISCOVERY_KEYWORD_METADATA_COLUMNS["grounding_source"] = "TEXT NOT NULL DEFAULT ''"
```

Thread it through `insert_pending_keywords()`:

- add `_metadata_text(metadata.get("grounding_source"))` to the row tuple;
- add `grounding_source` to the `INSERT INTO discovery_keywords` column list;
- add the corresponding `?` parameter marker to the VALUES list.

In `_new_grounding_ledger()` add:

```python
"local_hits": 0,
"local_misses": 0,
"external_searches_saved": 0,
"local_sources": {},
```

In `_merge_provider_grounding_ledger()` merge `local_hits`, `local_misses`,
and `local_sources` using `_ledger_int`.

In the grounding loop:

- after each awaited provider search, read
  `getattr(provider, "last_search_provider", None)`; when it equals
  `"local_cache"`, do not count that search against the per-stage budget and
  do not increment the external `searches` counter (`searches` keeps meaning
  "budget-consuming external searches");
- record the serving alias in a local side-map keyed by
  `(branch.branch_id, seed_query)`. Do not mutate `BrainstormBranch`; it is a
  frozen dataclass.

At stage-ledger finalization compute:

```python
ledger["external_searches_saved"] = min(
    _ledger_int(ledger.get("local_hits", 0)),
    max(0, stage_budget - _ledger_int(ledger.get("searches", 0))),
)
```

At keyword insertion, derive `grounding_source` from the side-map for the seed
queries attached to the expansion's branch (single alias, `"mixed"`, or
`"none"`).

- [ ] **Step 4: Run the tests and verify they pass**

```bash
uv run --extra dev pytest tests/test_keyword_planner.py -q -k "local_ledger or grounding_source"
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/runtime/keyword_planner.py tests/test_keyword_planner.py
git commit -m "feat: budget-aware local grounding ledger and keyword provenance"
```

---

### Task 5: Report Stubs And Documentation

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`
- Modify: `config.example.toml`

- [ ] **Step 1: Add report stub fields to cohort stats**

In `get_keyword_cohort_stats()`, include empty-safe fields:

```python
"claim_counts_by_day": {},
"claim_counts_by_platform": {},
"claim_counts_by_source_interest": {},
"grounding_mix": {},
"duplicate_rate_by_grounding_source": {},
```

Return empty dicts rather than omitting the fields when provenance has not
accumulated. These are **Phase 1 stubs**: the spec requires the real
aggregation (Phase 2) to land before the 14-day gate decision — a gate
verdict computed on stubs is not valid.

- [ ] **Step 2: Document config**

In `config.example.toml`, update:

```toml
inspiration_search_backends = ["local_cache", "platform_sources", "exa", "you"]
```

Comment:

```toml
# local_cache first reuses existing content_cache evidence (Phase 1; more
# local sources in Phase 2). External providers are used only when local
# evidence is insufficient, and local hits do not consume the external
# grounding budget.
```

- [ ] **Step 3: Update module docs**

In `docs/modules/discovery.md`, add one paragraph:

```markdown
`local_cache` 是 search-backed inspiration 的第一层 grounding provider：它从
`content_cache`（Phase 1；`discovery_candidates` / keyword yield / probe cache
为 Phase 2）按相关性打分抽取标题 / URL / 摘要作为 evidence；证据不足时才
fallback 到平台源 / Exa / You.com。本地命中不消耗外部搜索预算；生成的关键词带
`grounding_source` 溯源，用于后续 echo-chamber（本地 grounding 重复率）观测。
该 provider 不写候选池，只减少外部搜索次数和账号风险。
```

- [ ] **Step 4: Update changelog**

Add a bullet under the current version entry:

```markdown
- **Local-first inspiration grounding（Phase 1）**：query inspiration provider
  链新增 `local_cache`，优先复用本地 `content_cache` evidence（相关性打分 +
  CJK 2-gram 匹配），本地命中不消耗外部搜索预算；ledger 新增
  local_hits / external_searches_saved，关键词带 `grounding_source` 溯源。
```

- [ ] **Step 5: Run docs-adjacent tests**

```bash
uv run --extra dev pytest tests/test_config.py tests/test_keyword_planner.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/openbiliclaw/storage/database.py config.example.toml docs/modules/discovery.md docs/modules/config.md docs/changelog.md
git commit -m "docs: document local-first inspiration grounding"
```

---

### Task 6: Final Verification

**Files:**
- No new implementation files.

- [ ] **Step 1: Format and lint changed files**

```bash
uv run --extra dev ruff format src/openbiliclaw/storage/database.py src/openbiliclaw/discovery/inspiration_provider.py src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py src/openbiliclaw/runtime/keyword_planner.py tests/test_discovery_inspiration_provider.py tests/test_config.py tests/test_keyword_planner.py tests/test_storage.py
uv run --extra dev ruff check src/openbiliclaw/storage/database.py src/openbiliclaw/discovery/inspiration_provider.py src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py src/openbiliclaw/runtime/keyword_planner.py tests/test_discovery_inspiration_provider.py tests/test_config.py tests/test_keyword_planner.py tests/test_storage.py
```

Expected: all checks pass.

- [ ] **Step 2: Type-check changed source files**

```bash
uv run --extra dev mypy src/openbiliclaw/storage/database.py src/openbiliclaw/discovery/inspiration_provider.py src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py src/openbiliclaw/runtime/keyword_planner.py
```

Expected: success.

- [ ] **Step 3: Run focused tests**

```bash
uv run --extra dev pytest tests/test_discovery_inspiration_provider.py tests/test_config.py tests/test_keyword_planner.py tests/test_storage.py -q
```

Expected: pass.

- [ ] **Step 4: Run a real dry-run smoke and eyeball evidence quality**

```bash
OPENBILICLAW_PROJECT_ROOT=/Users/white/workspace/OpenBiliClaw \
OPENBILICLAW_SOURCES_TWITTER_ENABLED=true \
uv run openbiliclaw keyword-inspiration-dry-run \
  -p bilibili -p xiaohongshu -p douyin -p youtube -p twitter -p zhihu -p reddit \
  --limit 3 --interest-limit 4 > .tmp/keyword_inspiration_local_first_smoke.json
```

Expected:

- command exits 0;
- JSON report contains `grounding_ledger.local_hits` / `local_misses`,
  `external_searches_saved`, and `local_sources`;
- platform keyword lists are present with `grounding_source` metadata;
- **manually inspect the local evidence titles against their probes** — the
  sufficiency rule is only trustworthy once relevance scoring has been
  sanity-checked on real data (spec rollout step 2). Report the observed
  local hit/miss mix and 2-3 example evidence rows.

- [ ] **Step 5: Run full pytest**

```bash
uv run --extra dev pytest -q
```

Expected: pass.

- [ ] **Step 6: Final commit**

```bash
git status --short
git commit --allow-empty -m "test: verify local-first inspiration grounding"
```

Use `--allow-empty` only if every prior task was already committed and this
step has no remaining file changes.

## Self-Review

- Spec coverage: Phase 1 scope matches the spec's Phasing section exactly
  (content_cache-only DAO with relevance rules, provider + chain, attribution
  + budget exemption, `grounding_source` provenance, budget-aware saved
  formula, report stubs); Phase 2 items are named and deferred, not silently
  dropped.
- Review fixes encoded: Task 0 checkpoints the dirty tree before any
  feature commit; the DAO scores relevance and handles spaceless CJK with a
  quality floor (junk evidence can no longer suppress external grounding);
  `external_searches_saved` is planner-computed with the budget formula
  instead of a counter identical to `local_hits`; the echo-chamber loop is
  measurable from day one via `grounding_source`.
- Hygiene scan: no unresolved markers; each task has red → green commands
  and a commit.
