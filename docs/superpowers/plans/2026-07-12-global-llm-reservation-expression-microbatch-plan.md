# Global LLM Reservation and Expression Microbatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `llm.concurrency=4` a true runtime-wide limit, reserve one slot from all background LLM work, run three candidate evaluators continuously, and start expression generation at 8 pending items or after a 3-second tail-batch deadline.

**Architecture:** Add one runtime-owned `LLMConcurrencyGate` containing a total priority semaphore and a shared background semaphore, then inject it into the main and Soul `LLMService` instances. Keep candidate evaluation work-conserving, but replace immediate post-commit copy with a single-flight microbatch scheduler whose durable pending count controls the 8-item/3-second trigger and whose callback result controls retry backoff.

**Tech Stack:** Python 3.11+, `asyncio`, dataclasses, FastAPI/Pydantic, SQLite-backed runtime state, pytest/pytest-asyncio, Ruff, MyPy, vanilla JavaScript extension and desktop settings.

## Global Constraints

- Default total LLM concurrency is exactly `4`; default derived background concurrency is exactly `3`.
- Background work may occupy at most `max(1, llm.concurrency - 1)` shared permits. Total concurrency `1` degrades without deadlocking.
- Interactive callers are `soul.dialogue`, `soul.dialogue.tools`, `soul.dialogue.tool_followup`, and `api.sentiment`; every other known caller is background. Unknown callers warn once and default to background.
- `bypass_semaphore=True` remains source-compatible but may bypass only the background gate; no call may bypass the total gate.
- Candidate evaluation default remains `3`, with effective workers `min(candidate_eval_concurrency, max(1, llm.concurrency - 1))`.
- API and OpenClaw discovery evaluation limits use the same derived background concurrency; remove hard-coded `2` and `4`.
- Expression copy starts at `8` pending items, waits at most `3.0` seconds for `1..7`, uses provider batches of at most `30`, and retains engine batch concurrency `2`.
- Zero-progress copy retries no sooner than `15.0` seconds. Repeated notifications do not extend the original collection deadline.
- Stop and hot reload cancel collection/copy tasks and release all permits. No SQLite transaction or candidate claim commit spans an LLM permit wait.
- Threshold comments cite the 2026-07-12 real-provider calibration and require recalibration after provider/model changes.
- Never log or commit provider credentials, prompts, Cookie values, or complete user profiles.

## File Map

- Create `src/openbiliclaw/llm/concurrency.py` for traffic classification and the shared total/background gate.
- Create `tests/test_llm_concurrency.py` for gate invariants, cancellation, and multi-service sharing.
- Modify `src/openbiliclaw/llm/service.py` so every provider call uses the shared gate.
- Modify `src/openbiliclaw/soul/engine.py` and `src/openbiliclaw/soul/dialogue.py` to reuse the injected gate/service.
- Modify `src/openbiliclaw/api/runtime_context.py`, `src/openbiliclaw/integrations/openclaw/bootstrap.py`, and `src/openbiliclaw/runtime/refresh.py` for runtime ownership, discovery fan-out, and status.
- Modify backend, extension, desktop, and config files so absent/invalid total concurrency defaults to 4.
- Modify `src/openbiliclaw/runtime/candidate_eval.py` for the expression microbatch state machine.
- Modify module/config/architecture/changelog docs required by `CLAUDE.md`.

---

### Task 1: Build the Runtime-Shareable LLM Concurrency Gate

**Files:**
- Create: `src/openbiliclaw/llm/concurrency.py`
- Create: `tests/test_llm_concurrency.py`
- Modify: `src/openbiliclaw/llm/service.py:1-285,350-680`
- Modify: `tests/test_llm_service.py:400-730`

**Interfaces:**
- Produces: `background_llm_concurrency(total_concurrency: object) -> int`.
- Produces: `LLMConcurrencyGate(total_concurrency: int)` with `slot(*, caller: str, priority: int, bypass_background: bool = False) -> AsyncContextManager[None]` and `status_payload() -> dict[str, int]`.
- Produces: `LLMService.concurrency_gate: LLMConcurrencyGate` for later runtime injection.
- Preserves: `PrioritySemaphore` is still importable from `openbiliclaw.llm.service`; public completion methods still accept `bypass_semaphore`.

- [ ] **Step 1: Write failing tests for capacity derivation and traffic classification**

```python
def test_background_llm_concurrency_reserves_one_slot() -> None:
    assert background_llm_concurrency(4) == 3
    assert background_llm_concurrency(3) == 2
    assert background_llm_concurrency(1) == 1
    assert background_llm_concurrency("bad") == 3


def test_traffic_classification_is_explicit() -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    assert gate.is_interactive("soul.dialogue") is True
    assert gate.is_interactive("soul.dialogue.tools") is True
    assert gate.is_interactive("soul.dialogue.tool_followup") is True
    assert gate.is_interactive("api.sentiment") is True
    assert gate.is_interactive("soul.dialogue_insight") is False
    assert gate.is_interactive("recommendation.write_expression") is False
```

- [ ] **Step 2: Write failing async tests for strict reservation and multi-service sharing**

```python
async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


async def test_three_background_calls_leave_one_interactive_slot() -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def background() -> None:
        async with gate.slot(caller="discovery.evaluate_single", priority=1):
            await release.wait()

    background_tasks = [asyncio.create_task(background()) for _ in range(4)]
    await _wait_until(lambda: gate.status_payload()["llm_background_in_flight"] == 3)

    async def interactive() -> None:
        async with gate.slot(caller="soul.dialogue", priority=0):
            entered.set()

    interactive_task = asyncio.create_task(interactive())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert gate.status_payload()["llm_background_in_flight"] == 3
    release.set()
    await asyncio.gather(*background_tasks, interactive_task)


def test_two_services_share_one_gate() -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    registry = cast(Any, object())
    memory = cast(Any, object())
    left = LLMService(registry=registry, memory=memory, concurrency_gate=gate)
    right = LLMService(registry=registry, memory=memory, concurrency_gate=gate)
    assert left.concurrency_gate is right.concurrency_gate
```

- [ ] **Step 3: Run the new tests and confirm the missing-module/API failure**

Run: `pytest tests/test_llm_concurrency.py -q`

Expected: FAIL during collection because `openbiliclaw.llm.concurrency` and `LLMConcurrencyGate` do not exist.

- [ ] **Step 4: Implement the focused gate module**

```python
_INTERACTIVE_CALLERS = frozenset(
    {"soul.dialogue", "soul.dialogue.tools", "soul.dialogue.tool_followup", "api.sentiment"}
)
_KNOWN_BACKGROUND_PREFIXES = (
    "discovery", "recommendation", "soul", "sources", "runtime",
    "yt_search", "pool_purge", "eval",
)


def background_llm_concurrency(total_concurrency: object) -> int:
    total = _coerce_total(total_concurrency)
    return max(1, total - 1)


class LLMConcurrencyGate:
    def __init__(self, total_concurrency: int) -> None:
        self.total_concurrency = _coerce_total(total_concurrency)
        self.background_concurrency = background_llm_concurrency(self.total_concurrency)
        self._total = PrioritySemaphore(self.total_concurrency)
        self._background = asyncio.Semaphore(self.background_concurrency)
        self._total_in_flight = 0
        self._background_in_flight = 0
        self._warned_unknown_callers: set[str] = set()

    def is_interactive(self, caller: str) -> bool:
        return caller.strip() in _INTERACTIVE_CALLERS

    @asynccontextmanager
    async def slot(self, *, caller: str, priority: int, bypass_background: bool = False):
        is_background = not (self.is_interactive(caller) or bypass_background)
        if is_background:
            self._warn_if_unknown(caller)
            async with self._background:
                self._background_in_flight += 1
                try:
                    async with self._total_slot(priority):
                        yield
                finally:
                    self._background_in_flight -= 1
            return
        async with self._total_slot(priority):
            yield
```

Move the existing cancellation-safe `PrioritySemaphore` into this file. `_total_slot()` increments/decrements total diagnostics around `PrioritySemaphore.slot()`. `_warn_if_unknown()` recognizes the background prefixes, warns once for empty/unmatched tags, and leaves them classified as background. `status_payload()` returns total/background configured and active counts.

- [ ] **Step 5: Route all `LLMService` provider calls through the gate**

```python
@dataclass
class LLMService:
    registry: SupportsComplete
    memory: MemoryManager
    usage_recorder: object | None = None
    module_overrides: Mapping[str, ModuleOverride] = field(default_factory=dict)
    concurrency: int = DEFAULT_LLM_CONCURRENCY
    concurrency_gate: LLMConcurrencyGate | None = None

    def __post_init__(self) -> None:
        self.concurrency = _coerce_concurrency(self.concurrency)
        if self.concurrency_gate is None:
            self.concurrency_gate = LLMConcurrencyGate(self.concurrency)

    @asynccontextmanager
    async def _llm_slot(self, *, caller: str, bypass_background: bool = False):
        gate = cast("LLMConcurrencyGate", self.concurrency_gate)
        async with gate.slot(
            caller=caller,
            priority=self._resolve_priority(caller),
            bypass_background=bypass_background,
        ):
            yield
```

Replace the normal, multimodal, and tool/delegated completion semaphore paths with `_llm_slot()`. Replace the old total bypass with:

```python
async with self._llm_slot(caller=caller, bypass_background=bypass_semaphore):
    response = await _do_llm_call()
```

Update the docstring: legacy `bypass_semaphore=True` skips only background admission and always respects the total gate.

Add the specific dialogue prefix before the broad `soul` prefix in `_PRIORITY_MAP`:

```python
_PRIORITY_MAP = {
    "soul.dialogue": 0,
    "recommendation.write_expression": 1,
    "discovery.evaluate_batch": 1,
    "soul": 2,
    "xhs": 2,
}
```

Assert `_resolve_priority("soul.dialogue.tools") == 0` and that one unmatched caller logs exactly one warning across repeated calls while remaining background-limited.

Add a parameterized regression over current source caller tags:

```python
@pytest.mark.parametrize(
    "caller",
    [
        "discovery.douyin.keyword_gen",
        "discovery.evaluate_single",
        "discovery.explore.queries",
        "discovery.keyword_inspiration",
        "discovery.keyword_planner",
        "discovery.search.queries",
        "discovery.x.keyword_gen",
        "eval.query_quality",
        "eval.relevance",
        "eval.scenario_gen",
        "eval.specificity",
        "pool_purge.llm_agent",
        "recommendation.evaluate_batch",
        "recommendation.expression",
        "recommendation.write_expression",
        "runtime.bilibili_extension_search.queries",
        "soul.avoidance_speculate",
        "soul.awareness",
        "soul.category_migration",
        "soul.consolidation",
        "soul.core_update",
        "soul.dialogue_insight",
        "soul.insight",
        "soul.preference",
        "soul.preference.chunk",
        "soul.profile_build",
        "soul.role_update",
        "soul.speculate",
        "soul.values_update",
        "sources.xhs.keyword_gen",
        "yt_search.generate_queries",
    ],
)
def test_current_background_callers_are_classified(caller: str) -> None:
    assert LLMConcurrencyGate(total_concurrency=4).is_interactive(caller) is False
```

Keep the list synchronized with `rg -o 'caller\s*=\s*"[^"]+"' src/openbiliclaw` during final verification. Remove the internal `bypass_semaphore=True` from `complete_socratic_dialogue()`; its `soul.dialogue*` caller tag is sufficient.

- [ ] **Step 6: Add cancellation and concurrency-one tests**

```python
async def test_total_concurrency_one_degrades_without_deadlock() -> None:
    gate = LLMConcurrencyGate(total_concurrency=1)
    assert gate.background_concurrency == 1
    async with asyncio.timeout(1):
        async with gate.slot(caller="discovery.evaluate_single", priority=1):
            pass


async def test_cancelled_background_waiter_does_not_leak_permit() -> None:
    gate = LLMConcurrencyGate(total_concurrency=2)
    entered = asyncio.Event()
    never = asyncio.Event()

    async def hold() -> None:
        async with gate.slot(caller="discovery.evaluate_single", priority=1):
            entered.set()
            await never.wait()

    task = asyncio.create_task(hold())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with asyncio.timeout(1):
        async with gate.slot(caller="recommendation.write_expression", priority=1):
            pass
```

- [ ] **Step 7: Run focused tests and commit**

Run: `pytest tests/test_llm_concurrency.py tests/test_llm_service.py -q`

Expected: PASS, including priority FIFO, strict total/background capacity, cancellation, multi-service sharing, and concurrency-one behavior.

```bash
git add src/openbiliclaw/llm/concurrency.py src/openbiliclaw/llm/service.py tests/test_llm_concurrency.py tests/test_llm_service.py
git commit -m "feat: add shared llm concurrency gate"
```

---

### Task 2: Share One Gate Across API, OpenClaw, Soul, and Dialogue

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py:125-180`
- Modify: `src/openbiliclaw/soul/dialogue.py:85-235`
- Modify: `src/openbiliclaw/api/runtime_context.py:360-445,920-970`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py:55-130`
- Modify: `src/openbiliclaw/runtime/refresh.py:260-300,500-540`
- Modify: `src/openbiliclaw/api/models.py:170-205`
- Modify: `src/openbiliclaw/cli.py:520-730,7000-7060,8810-9020`
- Test: `tests/test_soul_dialogue.py`, `tests/test_api_app.py`, `tests/test_openclaw_adapter.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `LLMConcurrencyGate` and `LLMService.concurrency_gate` from Task 1.
- Produces: `SoulEngine` constructor parameter `llm_concurrency_gate: LLMConcurrencyGate | None = None`.
- Produces: `ContinuousRefreshController.llm_concurrency_gate: Any | None` and four LLM status fields.
- Guarantees: one runtime generation owns one gate shared by main recommendation/discovery and Soul services.

- [ ] **Step 1: Write failing composition tests**

```python
def test_soul_engine_reuses_injected_gate(memory, registry) -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    engine = SoulEngine(
        llm=registry,
        memory=memory,
        llm_concurrency=4,
        llm_concurrency_gate=gate,
    )
    assert engine._llm_service.concurrency_gate is gate


def test_dialogue_fallback_reuses_soul_service(soul_engine) -> None:
    dialogue = SocraticDialogue(llm=None, soul_engine=soul_engine, session="test")
    assert dialogue._build_service() is soul_engine._llm_service
```

Extend API/OpenClaw construction fakes to capture `concurrency_gate` and assert the main `LLMService` and `SoulEngine` receive the identical object.

- [ ] **Step 2: Run composition tests and verify failure**

Run: `pytest tests/test_soul_dialogue.py tests/test_api_app.py tests/test_openclaw_adapter.py -q`

Expected: FAIL because Soul lacks the argument, dialogue builds a service, and runtime composition does not create a gate.

- [ ] **Step 3: Inject the gate and reuse the Soul service**

```python
self._llm_concurrency_gate = llm_concurrency_gate
self._llm_service = LLMService(
    registry=llm,
    memory=memory,
    usage_recorder=usage_recorder,
    module_overrides=self._module_overrides,
    concurrency=llm_concurrency,
    concurrency_gate=llm_concurrency_gate,
)
```

At the beginning of `SocraticDialogue._build_service()`:

```python
shared_service = getattr(self._soul_engine, "_llm_service", None)
if isinstance(shared_service, LLMService):
    return shared_service
```

Remove `bypass_semaphore=True` from dialogue tool and API sentiment call sites. Their explicit caller tags classify them as interactive.

- [ ] **Step 4: Create one gate in API and OpenClaw composition roots**

```python
llm_concurrency = _llm_concurrency_from_config(new_config)
new_llm_gate = LLMConcurrencyGate(total_concurrency=llm_concurrency)
new_llm_service = LLMService(
    registry=new_registry,
    memory=self.memory_manager,
    usage_recorder=new_usage_recorder,
    module_overrides=new_module_overrides,
    concurrency=llm_concurrency,
    concurrency_gate=new_llm_gate,
)
new_soul_engine = SoulEngine(
    llm=new_registry,
    memory=self.memory_manager,
    usage_recorder=new_usage_recorder,
    module_overrides=new_module_overrides,
    llm_concurrency=llm_concurrency,
    llm_concurrency_gate=new_llm_gate,
)
new_runtime_controller.llm_concurrency_gate = new_llm_gate
```

In `build_openclaw_adapter_services()`, create `llm_gate = LLMConcurrencyGate(llm_concurrency)` and pass `concurrency_gate=llm_gate` to its main service plus `llm_concurrency_gate=llm_gate` to its Soul engine. Do not use module-global gate state for either long-running runtime; hot reload creates a new gate only after old tasks stop.

- [ ] **Step 5: Share an explicitly scoped gate across multi-service CLI commands**

```python
def _build_cli_llm_concurrency_gate() -> LLMConcurrencyGate:
    config = load_config()
    return LLMConcurrencyGate(total_concurrency=config.llm.concurrency)


def _build_soul_engine(
    *, llm_concurrency_gate: LLMConcurrencyGate | None = None
) -> Any:
    config = load_config()
    gate = llm_concurrency_gate or LLMConcurrencyGate(config.llm.concurrency)
    return SoulEngine(
        llm=_build_registry(),
        memory=_build_memory_manager(),
        llm_concurrency=config.llm.concurrency,
        llm_concurrency_gate=gate,
    )
```

Add the same optional `llm_concurrency_gate` keyword to recommendation and discovery builders and pass it to their `LLMService`. In commands that construct two services (`recommend`, `feedback`, `delight`, and the XHS/Zhihu/Reddit/YouTube/X/Douyin discovery runners), create one local gate and pass it to both builders:

```python
gate = _build_cli_llm_concurrency_gate()
soul_engine = _build_soul_engine(llm_concurrency_gate=gate)
recommendation_engine = _build_recommendation_engine(llm_concurrency_gate=gate)
```

Use `discovery_engine = _build_discovery_engine(llm_concurrency_gate=gate)` in source-discovery commands. Single-service commands may let their builder create a private gate. Add a `recommend` construction test asserting Soul and recommendation services receive the same object. Existing `_build_dialogue()` reuses Soul's service through Step 3. Explicit command scope avoids caching asyncio primitives across event loops.

- [ ] **Step 6: Merge gate diagnostics into runtime status**

```python
gate_status = getattr(self.llm_concurrency_gate, "status_payload", None)
if callable(gate_status):
    with suppress(Exception):
        payload.update(gate_status())
```

Add to `RuntimeStatusResponse`:

```python
llm_total_concurrency: int = 0
llm_background_concurrency: int = 0
llm_total_in_flight: int = 0
llm_background_in_flight: int = 0
```

- [ ] **Step 7: Run runtime composition tests and commit**

Run: `pytest tests/test_soul_dialogue.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py -q`

Expected: PASS; API/OpenClaw share one gate per runtime, fallback dialogue reuses Soul's service, and status reports gate capacity/usage.

```bash
git add src/openbiliclaw/soul/engine.py src/openbiliclaw/soul/dialogue.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/api/models.py src/openbiliclaw/cli.py tests/test_soul_dialogue.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py
git commit -m "fix: share llm gate across runtime services"
```

---

### Task 3: Set Default Four and Align Discovery Fan-Out

**Files:**
- Modify: `src/openbiliclaw/config.py:100-115`
- Modify: `src/openbiliclaw/llm/service.py:15-25`
- Modify: `src/openbiliclaw/api/models.py:1055-1070`
- Modify: `src/openbiliclaw/api/runtime_context.py:525-540,885-905`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py:60-155,285-305`
- Modify: `src/openbiliclaw/runtime/candidate_eval.py:25-45`
- Modify: `config.example.toml:48-56,495-505`
- Modify: `extension/popup/popup.html:4920-4930`, `extension/popup/popup.js:6545-6752`
- Modify: `src/openbiliclaw/web/desktop/index.html:275-285`, `src/openbiliclaw/web/desktop/assets/js/app.js:5110-5940`
- Test: `tests/test_config.py`, `tests/test_api_app.py`, `tests/test_openclaw_adapter.py`, `tests/test_desktop_web_multimodal_settings.py`, `extension/tests/popup-settings.test.ts`

**Interfaces:**
- Consumes: `background_llm_concurrency()` from Task 1.
- Produces: absent/invalid LLM concurrency values normalize to `4` on every surface.
- Produces: API and OpenClaw `DiscoveryConcurrencyController.llm_evaluation_concurrency == background_llm_concurrency(llm_concurrency)`.
- Preserves: explicitly saved valid values such as 3 remain 3 and derive background capacity 2.

- [ ] **Step 1: Change tests first to require default 4 and derived discovery limits**

```python
def test_default_llm_concurrency_is_four(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")
    assert config.llm.concurrency == 4


def test_explicit_llm_concurrency_three_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[llm]\nconcurrency = 3\n", encoding="utf-8")
    config = load_config(path)
    assert config.llm.concurrency == 3
    assert background_llm_concurrency(config.llm.concurrency) == 2
```

Change the API construction assertion from discovery evaluation concurrency 2 to 3 under default total 4. Assert `captured_openclaw_concurrency.llm_evaluation_concurrency == 3` under the same default.

- [ ] **Step 2: Run focused tests and verify old-default failures**

Run: `pytest tests/test_config.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_desktop_web_multimodal_settings.py -q`

Expected: FAIL where backend, API schema, runtime construction, popup, and desktop still default to 3 or use hard-coded discovery limits.

- [ ] **Step 3: Change backend, API, and example defaults**

```python
# config.py and llm/service.py
DEFAULT_LLM_CONCURRENCY = 4


class LLMConfigOut(BaseModel):
    default_provider: str = "deepseek"
    concurrency: int = 4
```

Set `config.example.toml` to `concurrency = 4`; explain that the derived background limit is 3. Keep `candidate_eval_concurrency = 3`.

- [ ] **Step 4: Replace discovery hard-codes with the shared helper**

```python
background_concurrency = background_llm_concurrency(llm_concurrency)
concurrency = DiscoveryConcurrencyController(
    bilibili_request_concurrency=2,
    llm_evaluation_concurrency=background_concurrency,
)
candidate_eval_workers = effective_candidate_eval_workers(
    int(getattr(discovery_cfg, "candidate_eval_concurrency", 3)),
    llm_concurrency,
)
```

Use the same derived value in OpenClaw, preserving unrelated Bilibili/search limits. Implement `effective_candidate_eval_workers()` through `background_llm_concurrency()` so the formula has one source.

- [ ] **Step 5: Change extension and desktop fallbacks/placeholders to 4**

```javascript
setVal("cfgLlmConcurrency", cfg.llm?.concurrency ?? 4);
concurrency: getInt("cfgLlmConcurrency", 4),
```

```javascript
setInput("llmConcurrency", llm.concurrency ?? 4);
concurrency: getIntInput("llmConcurrency", 4),
```

Change both HTML placeholders to `4`. Update exact-source assertions. Candidate-evaluation fallbacks stay 3.

- [ ] **Step 6: Run backend and frontend settings tests**

Run: `pytest tests/test_config.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_desktop_web_multimodal_settings.py -q`

Run: `cd extension && npm test`

Expected: PASS; explicit 3 is preserved, absent/invalid is 4, and API/OpenClaw discovery fan-out is 3.

- [ ] **Step 7: Commit the aligned defaults**

```bash
git add src/openbiliclaw/config.py src/openbiliclaw/llm/service.py src/openbiliclaw/api/models.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/runtime/candidate_eval.py config.example.toml extension/popup/popup.html extension/popup/popup.js extension/tests/popup-settings.test.ts src/openbiliclaw/web/desktop/index.html src/openbiliclaw/web/desktop/assets/js/app.js tests/test_config.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_desktop_web_multimodal_settings.py
git commit -m "perf: align default llm and discovery concurrency"
```

---

### Task 4: Add the 8-Item/3-Second Expression Microbatch Scheduler

**Files:**
- Modify: `src/openbiliclaw/runtime/candidate_eval.py:10-90,100-175,235-410`
- Modify: `tests/test_candidate_eval_coordinator.py:1-470`

**Interfaces:**
- Consumes: `CandidateEvalSnapshot.committed_pending` as the durable expression-copy queue count.
- Preserves: `post_commit_callback() -> int | Awaitable[int]`; a legacy `None` result normalizes to zero progress.
- Produces constructor arguments: `post_commit_min_items: int = 8`, `post_commit_max_wait_seconds: float = 3.0`, `post_commit_zero_progress_backoff_seconds: float = 15.0`.
- Produces status: `expression_pending_count`, `expression_batch_state`, `expression_batch_deadline`.
- Guarantees: one scheduler/copy task, one fixed deadline per tail batch, threshold wake, coalesced rerun, and cancellation-safe stop.

- [ ] **Step 1: Add deterministic microbatch test helpers**

```python
@dataclass
class _PendingCount:
    value: int


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


def _microbatch_coordinator(
    *,
    pending: _PendingCount,
    callback: Any,
    time_fn: Any = time.monotonic,
) -> CandidateEvalCoordinator:
    pipeline = _FakeStagedPipeline(candidate_count=0)
    return CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=0,
            target=600,
            pending_eval=0,
            evaluating=0,
            evaluated=0,
            committed_pending=pending.value,
        ),
        profile_provider=lambda: object(),
        post_commit_callback=callback,
        time_fn=time_fn,
        safety_wake_seconds=0.01,
    )
```

- [ ] **Step 2: Write failing tests for threshold and tail deadline**

```python
async def test_expression_copy_starts_immediately_at_eight_pending() -> None:
    pending = _PendingCount(8)
    started = asyncio.Event()
    coordinator = _microbatch_coordinator(
        pending=pending,
        callback=lambda: started.set() or 8,
    )
    coordinator._request_post_commit()
    await asyncio.wait_for(started.wait(), timeout=0.1)


async def test_expression_tail_waits_until_three_second_deadline() -> None:
    clock = _FakeClock()
    pending = _PendingCount(3)
    started = asyncio.Event()
    coordinator = _microbatch_coordinator(
        pending=pending,
        callback=lambda: started.set() or 3,
        time_fn=clock,
    )

    async def expire(timeout: float) -> None:
        clock.advance(timeout)
        raise TimeoutError

    coordinator._wait_post_commit_signal = expire
    coordinator._request_post_commit()
    await asyncio.wait_for(started.wait(), timeout=0.1)
    assert clock.now == 3.0
```

- [ ] **Step 3: Add race, coalescing, retry, and stop tests**

```python
async def test_reaching_eight_wakes_without_extending_deadline() -> None:
    pending = _PendingCount(1)
    copy_started = asyncio.Event()
    coordinator = _microbatch_coordinator(
        pending=pending,
        callback=lambda: copy_started.set() or 8,
    )
    pending.value = 1
    coordinator._request_post_commit()
    first_deadline = coordinator.expression_batch_deadline
    pending.value = 7
    coordinator._request_post_commit()
    assert coordinator.expression_batch_deadline == first_deadline
    pending.value = 8
    coordinator._request_post_commit()
    await asyncio.wait_for(copy_started.wait(), timeout=0.1)


async def test_copy_notifications_coalesce_to_one_followup() -> None:
    pending = _PendingCount(8)
    copy_started = asyncio.Event()
    copy_release = asyncio.Event()
    copy_calls = 0
    copy_in_flight = 0
    max_copy_in_flight = 0

    async def copy() -> int:
        nonlocal copy_calls, copy_in_flight, max_copy_in_flight
        copy_calls += 1
        copy_in_flight += 1
        max_copy_in_flight = max(max_copy_in_flight, copy_in_flight)
        copy_started.set()
        await copy_release.wait()
        copy_in_flight -= 1
        pending.value = 0 if copy_calls >= 2 else 8
        return 8

    coordinator = _microbatch_coordinator(pending=pending, callback=copy)
    loop_task = asyncio.create_task(coordinator.run_forever())
    coordinator._request_post_commit()
    await copy_started.wait()
    coordinator._request_post_commit()
    coordinator._request_post_commit()
    copy_release.set()
    await _wait_until(lambda: copy_calls == 2)
    assert max_copy_in_flight == 1
    await coordinator.stop()
    await loop_task


async def test_zero_progress_uses_fifteen_second_backoff() -> None:
    clock = _FakeClock()
    pending = _PendingCount(8)
    copy_calls = 0

    def copy() -> int:
        nonlocal copy_calls
        copy_calls += 1
        return 0

    coordinator = _microbatch_coordinator(pending=pending, callback=copy, time_fn=clock)
    loop_task = asyncio.create_task(coordinator.run_forever())
    coordinator._request_post_commit()
    await _wait_until(lambda: copy_calls == 1)
    assert coordinator.expression_batch_state == "backoff"
    assert coordinator.expression_batch_deadline == 15.0
    await coordinator.stop()
    await loop_task


async def test_stop_cancels_collecting_expression_task() -> None:
    pending = _PendingCount(1)
    coordinator = _microbatch_coordinator(pending=pending, callback=lambda: 1)
    coordinator._request_post_commit()
    await asyncio.sleep(0)
    assert coordinator.expression_batch_state == "collecting"
    await coordinator.stop()
    assert coordinator._post_commit_task is None
    assert coordinator.expression_batch_state == "idle"
```

- [ ] **Step 4: Run coordinator tests and verify immediate-hook failures**

Run: `pytest tests/test_candidate_eval_coordinator.py -q`

Expected: FAIL because the current hook starts on the first item and has no collection deadline/state/backoff.

- [ ] **Step 5: Add calibrated constants and state**

```python
# Calibrated 2026-07-12 against the configured real provider: 2/6-item
# structured-copy batches fell back after malformed JSON while an 8-item
# smoke was stable. Recalibrate after any provider/model change.
_POST_COMMIT_MIN_ITEMS = 8
_POST_COMMIT_MAX_WAIT_SECONDS = 3.0
_POST_COMMIT_ZERO_PROGRESS_BACKOFF_SECONDS = 15.0
```

```python
self.post_commit_min_items = max(1, int(post_commit_min_items))
self.post_commit_max_wait_seconds = max(0.0, float(post_commit_max_wait_seconds))
self.post_commit_zero_progress_backoff_seconds = max(
    self.post_commit_max_wait_seconds,
    float(post_commit_zero_progress_backoff_seconds),
)
self._post_commit_signal = asyncio.Event()
self._post_commit_deadline = 0.0
self._post_commit_retry_not_before = 0.0
self.expression_batch_state = "idle"
```

- [ ] **Step 6: Replace immediate execution with a fixed-deadline scheduler**

```python
def _request_post_commit(self) -> None:
    if self.post_commit_callback is None or self._stopping:
        return
    self._post_commit_requested = True
    self._post_commit_signal.set()
    if self._post_commit_task is None:
        self._post_commit_task = asyncio.create_task(
            self._run_post_commit_batch(), name="candidate_eval:post_commit"
        )


async def _run_post_commit_batch(self) -> int:
    self._post_commit_requested = False
    pending = max(0, self._snapshot().committed_pending)
    if pending <= 0:
        self.expression_batch_state = "idle"
        return 0
    now = self.time_fn()
    if self._post_commit_retry_not_before > now:
        waiting_for_retry = True
        self.expression_batch_state = "backoff"
        self._post_commit_deadline = self._post_commit_retry_not_before
    elif pending < self.post_commit_min_items:
        waiting_for_retry = False
        self.expression_batch_state = "collecting"
        if self._post_commit_deadline <= now:
            self._post_commit_deadline = now + self.post_commit_max_wait_seconds
    else:
        waiting_for_retry = False
        self._post_commit_deadline = now

    while self.time_fn() < self._post_commit_deadline:
        self._post_commit_signal.clear()
        pending = max(0, self._snapshot().committed_pending)
        if not waiting_for_retry and pending >= self.post_commit_min_items:
            break
        try:
            await self._wait_post_commit_signal(self._post_commit_deadline - self.time_fn())
        except TimeoutError:
            break

    self.expression_batch_state = "running"
    self._post_commit_deadline = 0.0
    result = self.post_commit_callback()
    completed = await result if inspect.isawaitable(result) else result
    return max(0, int(completed or 0))
```

`_wait_post_commit_signal(timeout)` uses `asyncio.wait_for(self._post_commit_signal.wait(), timeout=max(0.0, timeout))`. The deadline is never recomputed after a signal.

- [ ] **Step 7: Settle from durable remaining count and apply backoff**

```python
completed = task.result()
remaining = max(0, self._snapshot().committed_pending)
if remaining > 0 and completed <= 0:
    self._post_commit_retry_not_before = (
        self.time_fn() + self.post_commit_zero_progress_backoff_seconds
    )
    self.expression_batch_state = "backoff"
else:
    self._post_commit_retry_not_before = 0.0
    self.expression_batch_state = "idle"
rerun = self._post_commit_requested or remaining > 0
self._post_commit_requested = False
if rerun and not self._stopping:
    self._request_post_commit()
```

Exception paths log and take the same zero-progress backoff. Cancellation resets the signal, deadlines, state, and task reference. Add status fields from the durable snapshot.

- [ ] **Step 8: Run coordinator tests and commit**

Run: `pytest tests/test_candidate_eval_coordinator.py -q`

Expected: PASS; 8 starts immediately, 1–7 share one deadline, signals coalesce, zero progress waits 15 seconds, and stop leaves no task.

```bash
git add src/openbiliclaw/runtime/candidate_eval.py tests/test_candidate_eval_coordinator.py
git commit -m "perf: microbatch candidate expression copy"
```

---

### Task 5: Wire Copy Results and Runtime State End to End

**Files:**
- Modify: `src/openbiliclaw/api/runtime_context.py:850-910`
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py:250-310`
- Modify: `src/openbiliclaw/api/models.py:180-205`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_openclaw_adapter.py`
- Modify: `tests/test_candidate_eval_coordinator.py`
- Modify: `tests/test_recommendation_engine.py`

**Interfaces:**
- Consumes: Task 4's integer `post_commit_callback()` result and status fields.
- Produces: API/OpenClaw callbacks return the count from `_safe_precompute_pool_copy()`.
- Produces: API fields `expression_pending_count: int`, `expression_batch_state: str`, and `expression_batch_deadline: float`.
- Guarantees: projected inventory remains `available + committed_pending`; expression collection/copy never blocks evaluation refill.

- [ ] **Step 1: Write failing runtime tests for copy-result propagation and status**

```python
async def test_post_commit_callback_returns_completed_copy_count(runtime_context) -> None:
    runtime_context.runtime_controller._safe_precompute_pool_copy.return_value = 8
    callback = captured_candidate_coordinator_kwargs["post_commit_callback"]
    assert await callback() == 8


def test_runtime_status_exposes_expression_microbatch_state(client) -> None:
    payload = client.get("/api/runtime/status").json()
    assert payload["expression_pending_count"] >= 0
    assert payload["expression_batch_state"] in {
        "idle", "collecting", "running", "backoff"
    }
    assert payload["expression_batch_deadline"] >= 0.0
```

- [ ] **Step 2: Run runtime construction/status tests and verify failure**

Run: `pytest tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_candidate_eval_coordinator.py -q`

Expected: FAIL because callbacks return `None` and the response model lacks expression fields.

- [ ] **Step 3: Return completed counts from both runtime callbacks**

```python
async def _precompute_committed_candidates() -> int:
    profile = await new_soul_engine.get_profile()
    if profile is None:
        return 0
    before = int(_candidate_eval_snapshot().available)
    completed = int(
        await new_runtime_controller._safe_precompute_pool_copy(profile=profile)  # noqa: SLF001
    )
    await new_runtime_controller._publish_precompute_replenishment_if_needed(  # noqa: SLF001
        before_pool_count=before
    )
    return completed
```

In OpenClaw's defensive `getattr` path, store `completed = int(await precompute(profile=profile))` when callable, otherwise 0; publish as before and `return completed`.

- [ ] **Step 4: Add expression fields to the response model and fixtures**

```python
expression_pending_count: int = 0
expression_batch_state: str = "idle"
expression_batch_deadline: float = 0.0
```

Update exact response dictionaries in `tests/test_api_app.py`. Confirm frontend runtime-status parsing tolerates these new keys.

- [ ] **Step 5: Prove microbatch copy does not block evaluator refill**

```python
async def test_expression_copy_does_not_block_worker_refill_at_threshold() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=120)
    copy_started = asyncio.Event()
    copy_release = asyncio.Event()

    async def copy() -> int:
        copy_started.set()
        await copy_release.wait()
        return 8

    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=0,
            target=600,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated=0,
            committed_pending=pipeline.available,
        ),
        profile_provider=lambda: object(),
        worker_count=3,
        batch_size=30,
        post_commit_callback=copy,
        safety_wake_seconds=0.01,
    )
    task = asyncio.create_task(coordinator.run_forever())
    await pipeline.wait_for_started(3)
    pipeline.finish(0, cached=8)
    await asyncio.wait_for(copy_started.wait(), timeout=1)
    await pipeline.wait_for_started(4)
    assert copy_release.is_set() is False
    copy_release.set()
    await coordinator.stop()
    await task
```

Keep the other two evaluation workers blocked while batch four starts and expression copy is still running.

Extend the recommendation-engine batch test to capture provider payload item counts and active copy workers:

```python
assert max(recorded_expression_batch_sizes) <= 30
assert peak_expression_workers == 2
```

Use at least 60 pending copy rows so both 30-item workers are exercised. This guards the existing 30/2 limits while the new 8/3 trigger changes only scheduling.

- [ ] **Step 6: Run focused end-to-end backend tests and commit**

Run: `pytest tests/test_candidate_eval_coordinator.py tests/test_recommendation_engine.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_refresh_runtime.py -q`

Expected: PASS; callback counts drive retry/rerun, status validates through Pydantic, and refill remains work-conserving.

```bash
git add src/openbiliclaw/api/runtime_context.py src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/api/models.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_candidate_eval_coordinator.py tests/test_recommendation_engine.py
git commit -m "feat: expose expression microbatch runtime state"
```

---

### Task 6: Synchronize Documentation and Verify Real Concurrency

**Files:**
- Modify: `docs/modules/llm.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/changelog.md`
- Reference: `docs/superpowers/specs/2026-07-12-global-llm-reservation-expression-microbatch-design.md`

**Interfaces:**
- Consumes: final names/defaults from Tasks 1–5.
- Produces: documentation matching the mandatory `CLAUDE.md` module, changelog, config, and four-diagram checklist.
- Verifies: deterministic concurrency, full backend/extension quality gates, temporary-database real provider calls, and read-only public Bilibili source flow.

- [ ] **Step 1: Update module/config documentation with exact contracts**

Add this behavior to `docs/modules/llm.md` and `docs/modules/config.md`:

```text
默认 llm.concurrency=4。同一 runtime 的所有 LLMService 共享一个
LLMConcurrencyGate：总 provider 并发最多 4，后台并发最多
max(1, llm.concurrency-1)=3。soul.dialogue* 与 api.sentiment 只经过
总 gate；其它 caller 还必须取得后台 permit。显式 concurrency=1 时无法保留交互槽。
```

Document candidate worker derivation and expression `idle | collecting | running | backoff` states in discovery/recommendation module public status sections. Document 8/3/30/2 and 15-second zero-progress retry with calibration provenance.

- [ ] **Step 2: Update all required architecture diagrams**

Use the same relationship in `docs/architecture.md`, `docs/spec.md`, `README.md`, and `README_EN.md`:

```text
interactive ─────────────────────────┐
                                    ├─ shared total gate (4) ─ provider
background ─ shared background (3) ─┘
       ├─ candidate evaluation (≤3)
       ├─ expression copy (batch≤30, fan-out≤2)
       └─ soul/discovery background tasks
```

Keep README CN/EN diagrams synchronized. Do not add an internal-only release-highlight bullet.

- [ ] **Step 3: Add changelog entry and scan stale defaults**

Add one current-version bullet describing runtime-wide total 4/background 3 reservation and 8-item/3-second copy batching.

Run: `rg -n "默认.*3|concurrency.?=.?3|全局.*3" docs README.md README_EN.md config.example.toml`

Expected: remaining matches refer only to explicit legacy configuration or candidate concurrency, not default total LLM concurrency.

- [ ] **Step 4: Run format, lint, types, and focused tests**

Run: `ruff format src/ tests/`

Run: `ruff check src/ tests/`

Run: `mypy src/`

Run: `pytest tests/test_llm_concurrency.py tests/test_llm_service.py tests/test_candidate_eval_coordinator.py tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_soul_dialogue.py tests/test_config.py tests/test_refresh_runtime.py -q`

Expected: all commands exit 0.

- [ ] **Step 5: Run full backend and extension suites**

Run: `pytest -q`

Run: `cd extension && npm test && npm run typecheck && npm run build`

Expected: backend passes with only documented skips; extension tests, type checks, and production build exit 0.

- [ ] **Step 6: Run a 50-round deterministic race soak**

```bash
for i in {1..50}; do
  pytest tests/test_llm_concurrency.py tests/test_candidate_eval_coordinator.py -q || exit 1
done
```

Expected: 50/50 pass without leaked tasks, over-release, deadline extension, or parallel copy tasks.

- [ ] **Step 7: Run a real provider smoke with temporary state**

Create untracked `/tmp/openbiliclaw-llm-gate-smoke.py` using `load_config()` only for the existing provider/model/key, but place `Database` and `MemoryManager` under `TemporaryDirectory()`. Build one shared `LLMConcurrencyGate(4)` and two `LLMService` instances. Start three background structured calls and one `soul.dialogue` call. Record only sanitized timestamps, caller tags, active counts, durations, non-empty response flags, and errors.

The script must assert:

```python
assert peak_total <= 4
assert peak_background <= 3
assert dialogue_started_before_any_background_finished
assert every_call_has_nonempty_response_or_explicit_error
```

Then run candidate evaluation → commit → expression copy in a separate temporary SQLite database with 8 admitted candidates. Assert immediate threshold trigger, zero remaining claim tokens, and copied rows servable. Repeat with a 1–7 item tail and assert copy begins at approximately 3 seconds. Delete the script after recording results.

- [ ] **Step 8: Run a read-only real Bilibili source smoke**

Fetch one public ranking/search page without account mutation, enqueue at most 8 rows into the temporary database, and execute evaluation/copy. Record fetch latency, actual evaluation peak, copy batch sizes, structured-output fallbacks, and servable count. Do not write Cookie, follow, favorite, history, or production database state.

- [ ] **Step 9: Commit documentation**

```bash
git add docs/modules/llm.md docs/modules/discovery.md docs/modules/recommendation.md docs/modules/config.md docs/architecture.md docs/spec.md README.md README_EN.md docs/changelog.md
git commit -m "docs: explain global llm reservation and copy batching"
```

- [ ] **Step 10: Inspect the final branch**

Run: `git status --short && git log --oneline --decorate -10 && git diff main HEAD --check`

Expected: clean worktree, focused commits present, and no whitespace errors.
