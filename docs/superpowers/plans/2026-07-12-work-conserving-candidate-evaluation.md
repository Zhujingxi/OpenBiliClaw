# Work-Conserving Candidate Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed-delay candidate-evaluation drain with a continuously refilled, concurrency-safe worker pool that stops at the servable inventory target.

**Architecture:** A single `CandidateEvalCoordinator` owns claims and runs up to three LLM-only evaluation workers. SQLite claim/result transitions use batch claim tokens, while the coordinator commits and admits completed batches serially so stale workers cannot overwrite new claims or push the pool over target.

**Tech Stack:** Python 3.11+, asyncio, SQLite, FastAPI/Pydantic, pytest/pytest-asyncio, Ruff, MyPy, vanilla JavaScript/Chrome MV3, Markdown.

## Global Constraints

- Keep `pool_target_count` and the existing raw ceiling as hard guards; do not lower `admission_min_score`.
- Keep all platform request intervals, risk-control cooldowns, daily budgets, and source-share calculations unchanged.
- Runtime evaluation uses one claim owner, up to three LLM-only workers, and one serial commit/admission lane.
- Each worker handles at most 30 candidates; total in-flight candidates remain capped at the existing 90-row evaluator hard cap.
- Every stale-sensitive write matches `id + status + claim_token`; old workers may log stale completion but may not mutate a re-claimed row.
- Effective evaluation workers are `min(candidate_eval_concurrency, max(1, llm.concurrency - 1))`.
- Normal throughput is event-driven; the 60-second tick remains only as a safety wake-up.
- Rate limits back off at 15/30/60/120/300 seconds unless the provider supplies a longer `Retry-After`.
- Three consecutive successfully evaluated batches with `cached=0` trigger a 60/120/300-second no-progress backoff and one replenishment request.
- Preserve CLI single-drain compatibility through the same claim/evaluate/complete/release primitives.
- Production changes follow TDD; observe each new regression fail before writing its implementation.
- Preserve unrelated user files, including the existing untracked `.playwright-cli/` directory.

## File Map

- `src/openbiliclaw/storage/database.py`: schema migration, atomic tokenized claim, conditional completion/release, stale-claim cleanup.
- `src/openbiliclaw/discovery/candidate_pipeline.py`: staged claim/evaluate/complete/release API and backward-compatible `drain_pending()`.
- `src/openbiliclaw/runtime/candidate_eval.py`: new event-driven coordinator, worker scheduling, serial commit lane, backoff and diagnostics.
- `src/openbiliclaw/runtime/refresh.py`: coordinator ownership, pool/supply callbacks, status merging and removal of the fixed-delay evaluator loop.
- `src/openbiliclaw/api/runtime_context.py`: effective concurrency calculation, coordinator construction and hot-reload lifecycle.
- `src/openbiliclaw/api/app.py`, `src/openbiliclaw/api/models.py`: wake integration and compatible runtime/config API fields.
- `src/openbiliclaw/config.py`, `config.example.toml`: persisted `candidate_eval_concurrency` setting.
- `extension/popup/popup.html`, `extension/popup/popup.js`, `src/openbiliclaw/web/desktop/index.html`, `src/openbiliclaw/web/desktop/assets/js/app.js`: graphical configuration surface.
- `tests/test_storage.py`, `tests/test_discovery_candidate_pipeline.py`, `tests/test_candidate_eval_coordinator.py`, `tests/test_refresh_runtime.py`, `tests/test_config.py`, `tests/test_api_app.py`, `tests/test_llm_service.py`, `tests/test_desktop_web_multimodal_settings.py`, `extension/tests/popup-settings.test.ts`: regressions.
- `docs/modules/runtime.md`, `docs/modules/discovery.md`, `docs/modules/storage.md`, `docs/modules/config.md`, `docs/changelog.md`, `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`: final contract.

---

### Task 1: Tokenize candidate claims and make stale writes harmless

**Files:**
- Modify: `src/openbiliclaw/storage/database.py:589-646,2086-2350,4850-4885`
- Test: `tests/test_storage.py`
- Test: `tests/test_discovery_candidate_pipeline.py:1560-1710`

**Interfaces:**
- Produces: `claim_discovery_candidates_for_eval(*, limit: int, claim_token: str | None = None) -> list[dict[str, Any]]`.
- Produces: `persist_claimed_discovery_candidate_evaluations(evaluations, *, claim_token: str) -> set[int]`.
- Produces: `reset_claimed_discovery_candidates_to_pending(candidate_ids, *, claim_token: str, reason: str, max_attempts: int, max_batch_attempts: int, increment_attempts: bool) -> int`.
- Preserves: existing un-tokenized wrappers for compatibility callers.

- [ ] **Step 1: Write failing schema and token ownership tests**

Add real-SQLite tests:

```python
def test_claim_assigns_one_token_and_stale_token_cannot_persist(tmp_path: Path) -> None:
    db = Database(tmp_path / "claims.db")
    db.initialize()
    db.enqueue_discovery_candidates([_candidate("one"), _candidate("two")])
    rows = db.claim_discovery_candidates_for_eval(limit=2, claim_token="claim-a")
    assert {row["claim_token"] for row in rows} == {"claim-a"}

    ids = [int(row["id"]) for row in rows]
    assert db.reset_claimed_discovery_candidates_to_pending(
        ids, claim_token="claim-a", reason="reload", max_attempts=5,
        max_batch_attempts=50, increment_attempts=False,
    ) == 2
    replacement = db.claim_discovery_candidates_for_eval(limit=2, claim_token="claim-b")
    updated = db.persist_claimed_discovery_candidate_evaluations(
        [_evaluation(row, score=0.9) for row in rows], claim_token="claim-a"
    )
    assert updated == set()
    assert {row["claim_token"] for row in replacement} == {"claim-b"}
```

Also assert startup/stale reset clears `claim_token`, terminal/evaluated completion clears it, and two claim calls never return the same row.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
.venv/bin/pytest -q tests/test_storage.py tests/test_discovery_candidate_pipeline.py \
  -k 'claim_token or stale_token or tokenized_claim' --tb=short
```

Expected: schema has no token column and conditional methods are missing.

- [ ] **Step 3: Add the additive schema migration**

Add `claim_token TEXT` to the create-table schema and `"claim_token": "TEXT"` to `_ensure_discovery_candidate_columns()`. Update every orphan/stale reset to set both `claimed_at = NULL` and `claim_token = NULL`.

- [ ] **Step 4: Implement atomic tokenized claims**

```python
token = str(claim_token or secrets.token_hex(16))
self._execute_write(
    f"UPDATE discovery_candidates SET status='evaluating', claimed_at=CURRENT_TIMESTAMP, "
    f"claim_token=?, eval_error='' WHERE id IN ({placeholders}) AND status='pending_eval'",
    (token, *ids),
)
claimed_rows = self.conn.execute(
    f"SELECT * FROM discovery_candidates WHERE id IN ({placeholders}) "
    "AND status='evaluating' AND claim_token=?",
    (*ids, token),
).fetchall()
```

- [ ] **Step 5: Implement conditional persist and release**

Use `WHERE id=? AND status='evaluating' AND claim_token=?` for every claimed evaluation write. Clear token/claimed time on completion. Return the exact updated ID set. Implement conditional release with the same predicate and existing attempt counters.

- [ ] **Step 6: Run focused storage tests and verify GREEN**

Run Step 2. Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/openbiliclaw/storage/database.py tests/test_storage.py \
  tests/test_discovery_candidate_pipeline.py
git commit -m "feat: tokenize discovery candidate claims"
```

---

### Task 2: Split the pipeline into claim, evaluate, complete, and release stages

**Files:**
- Modify: `src/openbiliclaw/discovery/candidate_pipeline.py:1-110,340-730`
- Test: `tests/test_discovery_candidate_pipeline.py`

**Interfaces:**
- Consumes: tokenized database methods from Task 1.
- Produces: `CandidateEvalClaim`, `CandidateEvalOutcome`.
- Produces: `claim_batch(limit)`, `evaluate_claim(claim, profile)`, `complete_claim(outcome)`, `release_claim(claim, ...)`.
- Preserves: `drain_pending(profile, batch_size=...) -> dict[str, int]`.

- [ ] **Step 1: Write failing staged-lifecycle tests**

```python
async def test_staged_claim_evaluate_complete_matches_legacy_drain(tmp_path: Path) -> None:
    pipeline, db = _pipeline_with_candidates(tmp_path, count=3, score=0.9)
    claim = pipeline.claim_batch(limit=3)
    assert claim is not None and claim.token and len(claim.rows) == 3
    outcome = await pipeline.evaluate_claim(claim, _profile())
    result = await pipeline.complete_claim(outcome)
    assert result == {"evaluated": 3, "cached": 3, "rejected": 0, "stale": 0}
    assert db.count_pool_candidates() == 3


async def test_complete_claim_admits_only_ids_persisted_by_its_token(tmp_path: Path) -> None:
    pipeline, db = _pipeline_with_candidates(tmp_path, count=2, score=0.9)
    claim = pipeline.claim_batch(limit=2)
    assert claim is not None
    outcome = await pipeline.evaluate_claim(claim, _profile())
    db.reset_claimed_discovery_candidates_to_pending(
        [int(claim.rows[0]["id"])], claim_token=claim.token, reason="race",
        max_attempts=5, max_batch_attempts=50, increment_attempts=False,
    )
    result = await pipeline.complete_claim(outcome)
    assert result["cached"] == 1
    assert result["stale"] == 1
```

- [ ] **Step 2: Run staged tests and verify RED**

```bash
.venv/bin/pytest -q tests/test_discovery_candidate_pipeline.py \
  -k 'staged_claim or complete_claim or legacy_drain_uses_stages' --tb=short
```

Expected: staged types and methods are undefined.

- [ ] **Step 3: Add exact staged types**

```python
@dataclass(frozen=True)
class CandidateEvalClaim:
    token: str
    rows: tuple[dict[str, Any], ...]
    items: tuple[DiscoveredContent, ...]


@dataclass(frozen=True)
class CandidateEvalOutcome:
    claim: CandidateEvalClaim
    scores: tuple[float, ...]
    elapsed_seconds: float
```

- [ ] **Step 4: Extract LLM-only evaluation**

```python
async def evaluate_claim(self, claim: CandidateEvalClaim, profile: Any) -> CandidateEvalOutcome:
    started = self.time_fn()
    scores = await self.discovery_engine.evaluate_content_batch(
        list(claim.items), profile, source_context="mixed", batch_size=len(claim.items)
    )
    if len(scores) != len(claim.items):
        raise ValueError(
            f"evaluation returned {len(scores)} scores for {len(claim.items)} candidates"
        )
    return CandidateEvalOutcome(claim, tuple(map(float, scores)), self.time_fn() - started)
```

This method performs no SQLite writes and holds no drain lock.

- [ ] **Step 5: Extract token-aware completion and release**

Move normalization, viewed/threshold decisions, persistence and admission into `complete_claim()`. Filter accepted pairs by the exact updated-ID set. Return `stale=len(claim.rows)-len(updated_ids)`. Delegate `release_claim()` to the tokenized reset method.

- [ ] **Step 6: Rebuild legacy drain through the staged API**

Keep `_drain_lock` around the compatibility call only. Claim, evaluate, complete; on exception release the token, re-raise cancellation, and preserve existing waiting/error/attempt semantics.

- [ ] **Step 7: Run the complete pipeline module**

```bash
.venv/bin/pytest -q tests/test_discovery_candidate_pipeline.py --tb=short
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 2**

```bash
git add src/openbiliclaw/discovery/candidate_pipeline.py \
  tests/test_discovery_candidate_pipeline.py
git commit -m "refactor: stage discovery candidate evaluation"
```

---

### Task 3: Add the work-conserving coordinator happy path

**Files:**
- Create: `src/openbiliclaw/runtime/candidate_eval.py`
- Create: `tests/test_candidate_eval_coordinator.py`

**Interfaces:**
- Consumes: staged pipeline API from Task 2.
- Produces: `CandidateEvalSnapshot(available, target, pending_eval, evaluating, evaluated)`.
- Produces: `notify(reason)`, `run_forever()`, `stop()`, `status_payload()`.
- Produces: `effective_candidate_eval_workers(configured, llm_concurrency) -> int`.

- [ ] **Step 1: Write failing concurrency/refill tests**

```python
@pytest.mark.asyncio
async def test_three_workers_refill_fast_slot_without_waiting_for_slow_slots() -> None:
    pipeline = FakeStagedPipeline(candidate_count=120)
    coordinator = _coordinator(pipeline, worker_count=3, batch_size=30)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("test")
    await pipeline.wait_for_started(3)
    assert pipeline.max_in_flight == 3
    pipeline.finish_claim(0, cached=10)
    await pipeline.wait_for_started(4)
    assert pipeline.claims[1].done() is False
    assert pipeline.claims[2].done() is False
    await coordinator.stop()
    await task
```

Also test worker formula, target stop, and evaluated-before-LLM admission.

- [ ] **Step 2: Run coordinator tests and verify RED**

```bash
.venv/bin/pytest -q tests/test_candidate_eval_coordinator.py --tb=short
```

Expected: coordinator module is absent.

- [ ] **Step 3: Implement focused coordinator types**

```python
@dataclass(frozen=True)
class CandidateEvalSnapshot:
    available: int
    target: int
    pending_eval: int
    evaluating: int
    evaluated: int


def effective_candidate_eval_workers(configured: int, llm_concurrency: int) -> int:
    desired = max(1, min(8, int(configured)))
    global_limit = max(1, int(llm_concurrency))
    return min(desired, max(1, global_limit - 1))
```

Coordinator state owns event, generation, worker task-to-claim map, status and diagnostics. It never accesses `database.conn`.

- [ ] **Step 4: Implement level-triggered notification**

```python
def notify(self, reason: str) -> None:
    self._generation += 1
    self._last_wake_reason = str(reason)
    self._wake_event.set()
```

Before waiting, re-read snapshot and generation. Continue immediately when durable work remains; use 60 seconds only as an idle timeout.

- [ ] **Step 5: Implement serial completion and immediate refill**

The coordinator main task is the commit lane. Wait `FIRST_COMPLETED`, pop each result, await `complete_claim()`, record canonical outcome, then fill that open slot after completion has committed. Never wait for other slow workers.

- [ ] **Step 6: Run happy-path tests and verify GREEN**

Run Step 2. Expected: max three batches, immediate fourth claim, target respected.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/openbiliclaw/runtime/candidate_eval.py tests/test_candidate_eval_coordinator.py
git commit -m "feat: add continuous candidate evaluation coordinator"
```

---

### Task 4: Add cancellation, backoff, and no-progress guarantees

**Files:**
- Modify: `src/openbiliclaw/runtime/candidate_eval.py`
- Modify: `src/openbiliclaw/llm/base.py:35-85`
- Modify: `tests/test_candidate_eval_coordinator.py`
- Test: `tests/test_discovery_candidate_pipeline.py`
- Test: `tests/test_llm_service.py:126-250`

**Interfaces:**
- Consumes: coordinator from Task 3 and token-aware release from Tasks 1-2.
- Produces: states `idle|running|waiting_supply|backoff|paused|stopping`.
- Produces: deterministic retry timing and safe shutdown.

- [ ] **Step 1: Write failing race/recovery tests**

```python
@pytest.mark.asyncio
async def test_notify_during_clear_to_wait_boundary_is_not_lost() -> None:
    coordinator, pipeline = _coordinator(candidate_count=0)
    pipeline.notify_on_next_idle_snapshot = lambda: pipeline.enqueue_and_notify(coordinator, 30)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await pipeline.wait_for_started(1)
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_stop_cancels_workers_and_releases_only_their_tokens() -> None:
    coordinator, pipeline = _coordinator(candidate_count=90, workers=3)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await pipeline.wait_for_started(3)
    await coordinator.stop()
    await task
    assert pipeline.released_tokens == pipeline.started_tokens
    assert pipeline.pending_eval == 90
```

Also cover rate-limit sequence, `no_provider`/authentication pause until config notification, three zero-cache completions, supply single-flight, unexpected worker errors, stale completion, target reached with workers in flight, and a real-SQLite `test_sqlite_random_completion_soak` that randomizes completion order while asserting no overflow/duplicate/orphan rows.

- [ ] **Step 2: Run recovery tests and verify RED**

```bash
.venv/bin/pytest -q tests/test_candidate_eval_coordinator.py \
  -k 'not_lost or stop_cancels or backoff or no_progress or waiting_supply or stale' \
  --tb=short
```

Expected: stop cleanup, state transitions and backoff are absent.

- [ ] **Step 3: Implement stop/cancellation cleanup**

`stop()` sets `stopping`, prohibits new claims, sets the wake event, cancels worker tasks, gathers them with `return_exceptions=True`, and releases every unfinished claim with its token exactly once. `run_forever()` repeats this cleanup in `finally` when hot reload cancels the top-level task.

- [ ] **Step 4: Implement classified backoff**

```python
_RATE_LIMIT_BACKOFF_SECONDS = (15.0, 30.0, 60.0, 120.0, 300.0)
_NO_PROGRESS_BACKOFF_SECONDS = (60.0, 120.0, 300.0)
```

Add a public machine-readable classifier beside the existing user-facing description:

```python
def classify_llm_failure_kind(exc: BaseException) -> str | None:
    """Return rate_limited/no_provider/auth_failed/timeout/invalid_response."""
```

Reuse the existing cycle-safe exception walk and marker sets; keep `describe_llm_failure()` unchanged for copy. Add `tests/test_llm_service.py` cases for auth, timeout and invalid-response chains. In the coordinator, use this classifier for state transitions. Rate limits release the token and park claims to the deadline; `_retry_after_seconds(exc)` walks the exception chain and uses a positive numeric `retry_after` attribute when present. `no_provider`/`auth_failed` enters paused until config/manual/presence notification. Timeout, invalid response and unexpected errors release, log at the existing appropriate severity, and use bounded transient backoff without killing the coordinator. Add a calibration comment tying 15 seconds to the scheduler minimum and requiring recalibration after provider/model changes.

- [ ] **Step 5: Implement no-progress and supply single-flight**

Increment the zero-cache streak only for successfully committed batches with `evaluated > 0`; reset on `cached > 0`. At streak 3, request supply once, record rejection reasons, and apply 60/120/300-second delay. When queued/in-flight/evaluated material is below `worker_count * batch_size` and available is below target, request supply once and set `waiting_supply`; clear only after request completion or a newer candidate notification.

- [ ] **Step 6: Run coordinator and pipeline tests**

```bash
.venv/bin/pytest -q tests/test_candidate_eval_coordinator.py \
  tests/test_discovery_candidate_pipeline.py tests/test_llm_service.py --tb=short
```

Expected: all pass with no pending-task warnings.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/openbiliclaw/runtime/candidate_eval.py tests/test_candidate_eval_coordinator.py \
  src/openbiliclaw/llm/base.py tests/test_llm_service.py \
  tests/test_discovery_candidate_pipeline.py
git commit -m "fix: harden candidate evaluation concurrency"
```

---

### Task 5: Persist and expose candidate evaluation concurrency

**Files:**
- Modify: `src/openbiliclaw/config.py:437-515,1280-1320,2528-2560`
- Modify: `config.example.toml`
- Modify: `src/openbiliclaw/api/models.py:171-205,1210-1240`
- Modify: `src/openbiliclaw/api/app.py:4463-4485,9210-9235,9970-10040`
- Modify: `extension/popup/popup.html:5660-5710`
- Modify: `extension/popup/popup.js:6685-6710,6885-6915`
- Modify: `src/openbiliclaw/web/desktop/index.html:545-565`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js:5090-5110,6070-6090`
- Test: `tests/test_config.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_desktop_web_multimodal_settings.py`
- Test: `extension/tests/popup-settings.test.ts`

**Interfaces:**
- Produces: `DiscoveryConfig.candidate_eval_concurrency: int = 3`, valid `1..8`.
- Produces: matching GET/PUT and popup/desktop settings fields.
- Produces: compatible runtime status diagnostics.

- [ ] **Step 1: Write failing config/API/surface tests**

Assert default 3, TOML round-trip 5, invalid 0/9 fallback 3, GET/PUT behavior, and both surfaces:

```python
assert config.discovery.candidate_eval_concurrency == 3
assert response.json()["config"]["discovery"]["candidate_eval_concurrency"] == 3
```

```typescript
assert.match(popupJs, /setVal\("cfgCandidateEvalConcurrency", cfg\.discovery\?\.candidate_eval_concurrency\)/);
assert.match(popupJs, /candidate_eval_concurrency: getInt\("cfgCandidateEvalConcurrency", 3\)/);
```

- [ ] **Step 2: Run settings tests and verify RED**

```bash
.venv/bin/pytest -q tests/test_config.py tests/test_api_app.py \
  tests/test_desktop_web_multimodal_settings.py \
  -k 'candidate_eval_concurrency or multimodal_discovery_controls' --tb=short
cd extension && node --test --experimental-strip-types \
  --test-name-pattern='candidate evaluation concurrency|multimodal discovery' \
  tests/popup-settings.test.ts
```

Expected: field and controls are absent.

- [ ] **Step 3: Add normalized persistence and API round-trip**

Add `_DEFAULT_CANDIDATE_EVAL_CONCURRENCY = 3`, dataclass field, 1..8 load normalization, serializer, `DiscoveryConfigOut`, GET mapping and PUT limit entry:

```python
"candidate_eval_concurrency": (_DEFAULT_CANDIDATE_EVAL_CONCURRENCY, 1, 8),
```

Add the field and concurrency-reservation explanation to `config.example.toml`.

- [ ] **Step 4: Add desktop and popup controls**

Add numeric `候选评估并发`, min 1/max 8/default 3, beside multimodal controls. Wire load/save like `multimodal_batch_size`. Mobile Web has no config surface; CLI `config-show` already serializes the field, so those surfaces need no code.

- [ ] **Step 5: Add runtime status model fields**

Extend `RuntimeStatusResponse` with `candidate_eval_state`, workers, in-flight, pending, backoff-until, last-error, last-batch-seconds, last-cached and last-rejected, using empty/zero defaults. Include them in pool status payload when present; degraded runtime uses defaults.

- [ ] **Step 6: Run settings/API tests and verify GREEN**

Run Step 2. Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/openbiliclaw/config.py config.example.toml src/openbiliclaw/api/models.py \
  src/openbiliclaw/api/app.py tests/test_config.py tests/test_api_app.py \
  src/openbiliclaw/web/desktop/index.html src/openbiliclaw/web/desktop/assets/js/app.js \
  tests/test_desktop_web_multimodal_settings.py extension/popup/popup.html \
  extension/popup/popup.js extension/tests/popup-settings.test.ts
git commit -m "feat: configure candidate evaluation concurrency"
```

---

### Task 6: Wire lifecycle, wake paths, and canonical status

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py:280-540,1070-1210,1800-1900`
- Modify: `src/openbiliclaw/api/runtime_context.py:322-355,630-670,800-850,930-955`
- Modify: `src/openbiliclaw/api/app.py:4210-4355,4380-4450,7180-7510`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_api_app.py`
- Test: `tests/test_openclaw_adapter.py`

**Interfaces:**
- Consumes: coordinator/config from Tasks 3-5.
- Produces: exactly one coordinator per runtime, settled before replacement.
- Produces: post-commit wake signals after enqueue and inventory consumption.
- Preserves: explicit CLI/OpenClaw one-shot semantics.

- [ ] **Step 1: Write failing lifecycle/wake tests**

```python
async def test_runtime_starts_one_candidate_eval_coordinator() -> None:
    controller = _controller_with_fake_coordinator()
    task = asyncio.create_task(controller.run_forever())
    await controller.candidate_eval_coordinator.started.wait()
    assert controller.candidate_eval_coordinator.run_calls == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_bili_task_result_notifies_after_commit(client, coordinator) -> None:
    response = client.post("/api/sources/bili/task-result", json=_bili_result())
    assert response.status_code == 200
    assert coordinator.notifications[-1] == "candidate_enqueued:bilibili"
```

Cover XHS, a generic producer, reshuffle/append consumption, init/config resume, runtime status, and hot-reload ordering (`old stopped` before `new constructed`).

- [ ] **Step 2: Run runtime/API tests and verify RED**

```bash
.venv/bin/pytest -q tests/test_refresh_runtime.py tests/test_api_app.py \
  tests/test_openclaw_adapter.py \
  -k 'candidate_eval_coordinator or candidate_enqueued or inventory_consumed or rebuild_stops' \
  --tb=short
```

Expected: runtime still uses fixed loop and API creates best-effort drains.

- [ ] **Step 3: Construct one coordinator per runtime**

```python
worker_count = effective_candidate_eval_workers(
    new_config.discovery.candidate_eval_concurrency,
    llm_concurrency,
)
```

Construct with callbacks for readiness snapshot, profile, work gate, `request_replenishment(reason="candidate_supply")`, event publishing and shared pipeline. Inject into controller. Mirror in OpenClaw bootstrap.

- [ ] **Step 4: Replace the fixed evaluator loop**

Remove `_loop_candidate_eval()` from `run_forever()` and add one `asyncio.create_task(coordinator.run_forever(), name="candidate_eval")` child to the controller's gathered task list. `drain_discovery_candidates_once()` becomes a durable notification for API runtime; runtimes without a coordinator retain one-shot fallback.

- [ ] **Step 5: Make hot reload await cleanup**

Keep the existing `refresh_loop` as the registry-owned top-level task. Its `run_forever()` `finally` cancels and gathers the `candidate_eval` child; the child's `finally` then cancels workers and releases tokens. Add an ordering assertion proving `BackgroundTaskRegistry.cancel_all()` does not return and `_rebuild_components()` does not run until this nested cleanup finishes. Do not separately register the child, which would create two cancellation owners.

- [ ] **Step 6: Replace best-effort drains with notifications**

After candidate enqueue transactions, notify Bili/XHS/Douyin/YouTube/X/Zhihu/Reddit and passive-ingest paths. After bootstrap/reshuffle/append/feedback publishes canonical pool status, notify `inventory_consumed` when below target.

- [ ] **Step 7: Merge canonical diagnostics**

Merge `coordinator.status_payload()` into controller runtime status and `refresh.pool_updated`; use DB readiness counts for pending/available, never worker estimates.

- [ ] **Step 8: Run runtime/API tests and verify GREEN**

Run Step 2. Expected: all selected tests pass; old coordinator does not survive rebuild.

- [ ] **Step 9: Commit Task 6**

```bash
git add src/openbiliclaw/runtime/refresh.py src/openbiliclaw/api/runtime_context.py \
  src/openbiliclaw/api/app.py src/openbiliclaw/integrations/openclaw/bootstrap.py \
  tests/test_refresh_runtime.py tests/test_api_app.py tests/test_openclaw_adapter.py
git commit -m "feat: run candidate evaluation continuously"
```

---

### Task 7: Synchronize docs and verify the complete feature

**Files:**
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`

**Interfaces:**
- Consumes: final Tasks 1-6 behavior.
- Produces: product/architecture contract matching code.

- [ ] **Step 1: Update required module/config docs**

Document tokenized lifecycle, one claim owner, desired-three-worker/effective formula, serial commit lane, event/generation wake, safety tick, error states, runtime diagnostics, high-throughput profile, popup/desktop setting, automatic CLI `config-show` inclusion, and mobile-settings exclusion. State that producer limits and admission thresholds are unchanged. Add a changelog bullet.

- [ ] **Step 2: Update architecture and README diagrams**

Replace periodic-drain wording with `CandidateEvalCoordinator`; show `discovery_candidates -> tokenized claim -> parallel workers -> serial commit/admission -> content_cache`. Do not expand release highlights beyond four bullets.

- [ ] **Step 3: Run formatting, lint, focused tests, and extension checks**

```bash
.venv/bin/ruff format src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/pytest -q tests/test_storage.py tests/test_discovery_candidate_pipeline.py \
  tests/test_candidate_eval_coordinator.py tests/test_refresh_runtime.py \
  tests/test_config.py tests/test_api_app.py tests/test_openclaw_adapter.py \
  tests/test_desktop_web_multimodal_settings.py --tb=short
cd extension && npm test && npm run typecheck && npm run build
```

Expected: all commands exit 0.

- [ ] **Step 4: Run typing and full backend verification**

```bash
.venv/bin/mypy src/
.venv/bin/pytest -q --tb=short
```

Expected: both exit 0; coverage remains above 70% in the coverage job.

- [ ] **Step 5: Run a 50-pass concurrency soak**

```bash
for i in $(seq 1 50); do
  .venv/bin/pytest -q tests/test_candidate_eval_coordinator.py \
    -k 'sqlite_random_completion_soak' --tb=short || exit 1
done
```

Expected: every run passes; no overflow, duplicate admission, orphan evaluating row or SQLite lock error.

- [ ] **Step 6: Inspect final integrity**

```bash
git diff --check
git status --short
```

Expected: only intentional changes plus pre-existing `.playwright-cli/`.

- [ ] **Step 7: Commit Task 7**

```bash
git add docs/modules/runtime.md docs/modules/discovery.md docs/modules/storage.md \
  docs/modules/config.md docs/changelog.md docs/architecture.md docs/spec.md \
  README.md README_EN.md
git commit -m "docs: document continuous candidate evaluation"
```

- [ ] **Step 8: Record fresh completion evidence**

```text
effective workers (llm=4, eval=3): 3
maximum observed eval batches: 3
maximum in-flight candidates: 90
fixed post-drain sleep while backlog exists: 0s
stale-token writes accepted: 0
pool overflow under soak: 0
```

Do not claim completion without fresh success from targeted tests, Ruff, MyPy, extension checks, full pytest and the soak.
