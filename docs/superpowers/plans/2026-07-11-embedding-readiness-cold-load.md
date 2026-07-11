# Embedding Cold-Load Readiness Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop ordinary health checks from reporting a local Ollama cold load as an embedding outage while keeping guided initialization strictly gated on a confirmed vector.

**Architecture:** Cache the raw embedding probe outcome as `ready`, `failed`, or `timed_out`, then interpret that outcome per caller. `/api/health` treats only a timed-out loopback Ollama probe as optimistically available; init status and init POST pass `strict=True`, so the same cached timeout remains not-ready without firing a second probe.

**Tech Stack:** Python 3.11, FastAPI, asyncio, Pytest, Ruff, MyPy.

## Global Constraints

- Do not change `HealthResponse`, `InitStatusOut`, or any public API field.
- Only loopback Ollama timeouts may be optimistic in ordinary health; remote Ollama and non-Ollama providers stay strict.
- A provider returning `False`, an empty vector, or an exception remains a confirmed failure in every caller.
- `GET /api/init-status` and `POST /api/init` must require a confirmed successful vector.
- Preserve the 30-second success TTL, 8-second failure/timeout TTL, and single-flight lock.
- Do not manage or rewrite external Ollama process environments from application code.
- Preserve the user's unrelated `extension/popup/popup.js` working-tree change.
- Update `docs/modules/runtime.md`, `docs/modules/init.md`, and the current `docs/changelog.md` block; no architecture diagram, CLI, config, or installer docs are required.

---

### Task 1: Lock the split health/init timeout contract with failing tests

**Files:**
- Modify: `tests/test_api_app.py:2080-2110`
- Modify: `tests/test_api_app.py:10440-10610`

**Interfaces:**
- Consumes: existing `GET /api/health`, `GET /api/init-status`, `POST /api/init`, and `_EMBEDDING_PROBE_TIMEOUT_SECONDS` test seam.
- Produces: regression coverage proving a single cached timeout is optimistic only for ordinary local-Ollama health.

- [ ] **Step 1: Replace the old globally-strict health timeout test with a local-Ollama health contract**

Replace `test_health_endpoint_strict_when_probe_times_out` with:

```python
def test_health_endpoint_treats_loopback_ollama_timeout_as_cold_load(
    self, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    import openbiliclaw.api.app as appmod

    monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.01)

    class _SlowProbeService:
        async def probe(self) -> bool:
            await asyncio.sleep(0.2)
            return True

    class EmbeddingSoulEngine:
        def __init__(self) -> None:
            self._embedding_service = _SlowProbeService()

    app = create_app(
        memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
    )
    app.state.runtime_context.config.llm.embedding.provider = "ollama"
    app.state.runtime_context.config.llm.embedding.base_url = (
        "http://127.0.0.1:11434/v1"
    )
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["embedding_ready"] is True
```

- [ ] **Step 2: Add the remote-Ollama timeout guard test**

Add beside the local timeout test:

```python
def test_health_endpoint_keeps_remote_ollama_timeout_not_ready(
    self, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    import openbiliclaw.api.app as appmod

    monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.01)

    class _SlowProbeService:
        async def probe(self) -> bool:
            await asyncio.sleep(0.2)
            return True

    class EmbeddingSoulEngine:
        def __init__(self) -> None:
            self._embedding_service = _SlowProbeService()

    app = create_app(
        memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
    )
    app.state.runtime_context.config.llm.embedding.provider = "ollama"
    app.state.runtime_context.config.llm.embedding.base_url = "http://ollama:11434/v1"
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["embedding_ready"] is False
```

- [ ] **Step 3: Add the shared-cache strict init-status test**

Add to `TestGuidedInitEndpoints`:

```python
def test_init_status_keeps_cached_loopback_ollama_timeout_strict(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    import openbiliclaw.api.app as appmod
    from openbiliclaw.llm import ollama_diagnostics as od

    monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.01)

    async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
        return od.DIAG_PROVIDER_ERROR, "cold loading"

    monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)

    class _SlowProbeService:
        def __init__(self) -> None:
            self.calls = 0

        async def probe(self) -> bool:
            self.calls += 1
            await asyncio.sleep(0.2)
            return True

    service = _SlowProbeService()
    prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["xiaohongshu"])
    app, _ = self._make_app(
        tmp_path,
        prereqs=prereqs,
        embedding_provider="ollama",
    )
    app.state.runtime_context.soul_engine._embedding_service = service
    app.state.runtime_context.config.llm.embedding.base_url = (
        "http://127.0.0.1:11434/v1"
    )

    with TestClient(app) as client:
        health = client.get("/api/health").json()
        status = client.get("/api/init-status").json()

    assert health["embedding_ready"] is True
    assert status["prerequisites"]["embedding_ready"] is False
    assert status["can_start"] is False
    assert status["reason"] == "embedding_not_ready"
    assert service.calls == 1
```

- [ ] **Step 4: Add the strict POST timeout test**

Add beside the init-status test:

```python
def test_init_post_rejects_cached_loopback_ollama_timeout(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    import openbiliclaw.api.app as appmod
    from openbiliclaw.llm import ollama_diagnostics as od

    monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.01)

    async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
        return od.DIAG_PROVIDER_ERROR, "cold loading"

    monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)

    class _SlowProbeService:
        async def probe(self) -> bool:
            await asyncio.sleep(0.2)
            return True

    prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["xiaohongshu"])
    app, db = self._make_app(
        tmp_path,
        prereqs=prereqs,
        embedding_provider="ollama",
    )
    app.state.runtime_context.soul_engine._embedding_service = _SlowProbeService()
    app.state.runtime_context.config.autostart.manage_ollama = False

    with TestClient(app) as client:
        response = client.post("/api/init", json={"sources": ["xiaohongshu"]})

    assert response.status_code == 409
    assert response.json()["error"] == "embedding_not_ready"
    assert db.get_latest_init_run()["status"] == "idle"
```

- [ ] **Step 5: Run the focused tests and verify RED**

Run:

```bash
pytest tests/test_api_app.py \
  -k "loopback_ollama_timeout or remote_ollama_timeout" -vv
```

Expected: the local health test and shared health/init test FAIL because current timeout handling always caches `False`; the strict assertions remain green.

---

### Task 2: Implement raw three-state caching and caller-specific interpretation

**Files:**
- Modify: `src/openbiliclaw/api/app.py:170-186`
- Modify: `src/openbiliclaw/api/app.py:1860-2028`
- Modify: `src/openbiliclaw/api/app.py:2090-2120`
- Modify: `src/openbiliclaw/api/app.py:2410-2425`
- Test: `tests/test_api_app.py`

**Interfaces:**
- Consumes: `Literal`, current config at `ctx.config.llm.embedding`, `_embedding_ollama_target()`, and `runtime.ollama_supervisor.is_loopback()`.
- Produces: `_health_embedding_ready(*, strict: bool = False) -> bool`, backed by `_EmbeddingProbeOutcome = Literal["ready", "failed", "timed_out"]`.

- [ ] **Step 1: Define the raw outcome type and align timeout calibration comments**

Near the readiness constants add:

```python
_EmbeddingProbeOutcome = Literal["ready", "failed", "timed_out"]
```

Replace the contradictory optimistic/strict constant comments with a calibrated statement: 15 seconds bounds the HTTP endpoint, local bge-m3 cold loads were observed at 16–29 seconds on 2026-07-11, and only ordinary loopback-Ollama health interprets that timeout optimistically.

- [ ] **Step 2: Store raw outcomes and add the interpreter**

Replace the boolean cache initialization with:

```python
_embedding_probe_outcome: _EmbeddingProbeOutcome = "failed"
_embedding_ready_checked_at = float("-inf")
```

Add inside `create_app()` beside `_embedding_ollama_target()`:

```python
def _embedding_probe_ttl(outcome: _EmbeddingProbeOutcome) -> float:
    return (
        _EMBEDDING_READY_TTL_SECONDS
        if outcome == "ready"
        else _EMBEDDING_FAIL_TTL_SECONDS
    )

def _embedding_probe_result(
    outcome: _EmbeddingProbeOutcome, *, strict: bool
) -> bool:
    if outcome == "ready":
        return True
    if outcome != "timed_out" or strict:
        return False
    emb = getattr(getattr(getattr(ctx, "config", None), "llm", None), "embedding", None)
    provider = str(getattr(emb, "provider", "") or "").strip().lower()
    if provider != "ollama":
        return False
    from openbiliclaw.runtime.ollama_supervisor import is_loopback

    base_url, _ = _embedding_ollama_target()
    return is_loopback(base_url)
```

- [ ] **Step 3: Rewrite the readiness helper around the raw cache**

Change the signature and nonlocal declaration:

```python
async def _health_embedding_ready(*, strict: bool = False) -> bool:
    nonlocal _embedding_probe_outcome, _embedding_ready_checked_at
```

Use `_embedding_probe_ttl(_embedding_probe_outcome)` for both cache checks. On a cache hit, return:

```python
return _embedding_probe_result(_embedding_probe_outcome, strict=strict)
```

After the real probe, assign only raw outcomes:

```python
try:
    ready = bool(await asyncio.wait_for(probe(), timeout=_EMBEDDING_PROBE_TIMEOUT_SECONDS))
    outcome: _EmbeddingProbeOutcome = "ready" if ready else "failed"
except TimeoutError:
    logger.debug(
        "Embedding readiness probe timed out; ordinary loopback-Ollama health "
        "treats this as cold-loading while init remains strict"
    )
    outcome = "timed_out"
except Exception:
    logger.debug("Embedding readiness probe errored", exc_info=True)
    outcome = "failed"
_embedding_probe_outcome = outcome
_embedding_ready_checked_at = time.monotonic()
return _embedding_probe_result(outcome, strict=strict)
```

Keep the existing service-missing `False` and legacy-no-probe `True` branches unchanged.

- [ ] **Step 4: Make both init call sites explicit strict consumers**

In both initialized and pre-init branches of `GET /api/init-status`, replace calls with:

```python
await _health_embedding_ready(strict=True)
```

and in the `asyncio.gather()` branch use:

```python
_health_embedding_ready(strict=True),
```

In the `POST /api/init` critical-section revalidation use:

```python
if _embedding_required_for_init() and not await _health_embedding_ready(strict=True):
```

Leave `GET /api/health` on the default non-strict call.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
pytest tests/test_api_app.py \
  -k "embedding_ready or embedding_not_ready or loopback_ollama_timeout or remote_ollama_timeout" -vv
```

Expected: all selected tests PASS; the shared-cache test reports one provider call.

- [ ] **Step 6: Run the complete API app test module**

Run:

```bash
pytest tests/test_api_app.py -q
```

Expected: all tests PASS with no new warning/error tracebacks.

- [ ] **Step 7: Commit the code and regression tests**

```bash
git add src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "fix: distinguish embedding cold loads from outages"
```

---

### Task 3: Synchronize runtime/init documentation and changelog

**Files:**
- Modify: `docs/modules/runtime.md:141`
- Modify: `docs/modules/init.md:42`
- Modify: `docs/changelog.md:8-25`

**Interfaces:**
- Consumes: Task 2's raw three-state cache and strict init call sites.
- Produces: documentation matching the unchanged public API and new timeout interpretation.

- [ ] **Step 1: Update runtime readiness semantics**

In `docs/modules/runtime.md`, replace the sentence that says every timeout is not-ready with text stating:

```text
探测缓存保存 `ready / failed / timed_out` 原始三态而非调用方布尔值：普通 `/api/health`
仅把 loopback Ollama 的 `timed_out` 解释为冷加载中的乐观可用，避免插件横幅误报；远程或
非 Ollama provider 超时仍为 `false`。成功沿用 30s TTL，失败/超时用 8s TTL 重探。
```

- [ ] **Step 2: Document strict initialization**

In `docs/modules/init.md`, extend the embedding readiness paragraph with:

```text
`/api/init-status` 与 `POST /api/init` 显式使用 strict 解释，同一缓存结果为 `timed_out`
时仍下发 `embedding_ready=false` / 返回 409，只有真实非空向量成功才放行；普通 health 的
本地 Ollama 冷加载容忍不会渗入初始化门禁。
```

- [ ] **Step 3: Add the current-version changelog bullet**

Under the current v0.3.162 block add:

```text
- **本地 embedding 冷加载不再误报停服**：readiness probe 改为缓存成功、明确失败与超时三态；普通 health 仅容忍 loopback Ollama 冷加载超时，初始化仍严格等待真实向量成功，远端超时、404/500 和空向量继续报告未就绪。
```

- [ ] **Step 4: Verify documentation scope and consistency**

Run:

```bash
rg -n "timed_out|冷加载|strict|embedding_ready" \
  docs/modules/runtime.md docs/modules/init.md docs/changelog.md
git diff --check
```

Expected: all three files describe split health/init semantics; no whitespace errors; architecture, CLI, config, README, and installer docs remain untouched.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/modules/runtime.md docs/modules/init.md docs/changelog.md
git commit -m "docs: explain embedding cold-load readiness semantics"
```

---

### Task 4: Run repository verification and preserve unrelated work

**Files:**
- Verify: `src/openbiliclaw/api/app.py`
- Verify: `tests/test_api_app.py`
- Verify: `docs/modules/runtime.md`
- Verify: `docs/modules/init.md`
- Verify: `docs/changelog.md`
- Preserve: `extension/popup/popup.js`

**Interfaces:**
- Consumes: completed Tasks 1–3.
- Produces: fresh evidence that runtime behavior, typing, lint, docs, and the full test suite pass.

- [ ] **Step 1: Run formatting and lint checks without rewriting unrelated files**

Run:

```bash
ruff format --check src/ tests/
ruff check src/ tests/
```

Expected: both commands exit 0. If only touched Python files need formatting, run
`ruff format src/openbiliclaw/api/app.py tests/test_api_app.py`, then repeat both checks.

- [ ] **Step 2: Run strict type checking**

Run:

```bash
mypy src/
```

Expected: exit 0 with no type errors.

- [ ] **Step 3: Run the complete test suite**

Run:

```bash
pytest
```

Expected: exit 0; all collected tests pass or existing optional integration tests skip according to their declared guards.

- [ ] **Step 4: Verify final diff and unrelated-file preservation**

Run:

```bash
git status --short
git diff --check HEAD~2..HEAD
git diff --stat HEAD~2..HEAD
git diff -- extension/popup/popup.js
```

Expected: implementation commits contain only the intended backend/test/docs files; `extension/popup/popup.js` remains modified exactly as it was before this work and is not staged or committed.
