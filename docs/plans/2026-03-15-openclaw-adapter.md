# OpenClaw Adapter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an OpenClaw adapter and skill layer that exposes OpenBiliClaw learning and recommendation capabilities without changing the current core architecture.

**Architecture:** Keep `memory/`, `soul/`, `discovery/`, `recommendation/`, and `runtime/` as the domain core. Add `src/openbiliclaw/integrations/openclaw/` as a thin integration layer with four responsibilities: bootstrap shared services, translate DTOs, expose stable operations, and wrap those operations as protocol-neutral OpenClaw skill descriptors.

**Tech Stack:** Python 3.11, dataclasses, existing OpenBiliClaw runtime/services, Pytest, Ruff, MyPy

---

### Task 1: Create the integration package skeleton and adapter contracts

**Files:**
- Create: `src/openbiliclaw/integrations/__init__.py`
- Create: `src/openbiliclaw/integrations/openclaw/__init__.py`
- Create: `src/openbiliclaw/integrations/openclaw/errors.py`
- Create: `src/openbiliclaw/integrations/openclaw/schemas.py`
- Test: `tests/test_openclaw_adapter.py`

**Step 1: Write the failing tests**

Add adapter contract tests that assert:

- profile/running-status/recommendation DTOs serialize only the intended public fields
- invalid feedback types raise `AdapterValidationError`
- comment feedback without `note` raises `AdapterValidationError`

Example test shape:

```python
def test_feedback_request_rejects_comment_without_note() -> None:
    with pytest.raises(AdapterValidationError):
        FeedbackRequest(recommendation_id=7, feedback_type="comment", note="")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_openclaw_adapter.py -k "validation or dto" -v`

Expected: FAIL because the integration package and DTO classes do not exist yet.

**Step 3: Write minimal implementation**

In `src/openbiliclaw/integrations/openclaw/errors.py`, add:

```python
class OpenClawAdapterError(Exception):
    pass


class AdapterInitializationError(OpenClawAdapterError):
    pass


class AdapterValidationError(OpenClawAdapterError):
    pass


class AdapterOperationError(OpenClawAdapterError):
    pass
```

In `src/openbiliclaw/integrations/openclaw/schemas.py`, add dataclasses for:

- `ProfileResponse`
- `RecommendationItem`
- `RecommendationResponse`
- `FeedbackRequest`
- `FeedbackResponse`
- `RuntimeStatusResponse`
- `SyncAccountResponse`

Include `__post_init__()` validation for feedback DTOs.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_openclaw_adapter.py -k "validation or dto" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/integrations/__init__.py src/openbiliclaw/integrations/openclaw/__init__.py src/openbiliclaw/integrations/openclaw/errors.py src/openbiliclaw/integrations/openclaw/schemas.py tests/test_openclaw_adapter.py
git commit -m "feat: add openclaw adapter contracts"
```

### Task 2: Implement adapter operations over the existing runtime core

**Files:**
- Create: `src/openbiliclaw/integrations/openclaw/operations.py`
- Modify: `tests/test_openclaw_adapter.py`

**Step 1: Write the failing tests**

Add tests that assert:

- `get_profile()` reads `SoulEngine.get_profile()` and returns a trimmed `ProfileResponse`
- `get_runtime_status()` merges refresh status and account sync status
- `sync_account()` delegates to `AccountSyncService.sync_now()`
- `recommend()` optionally calls runtime refresh and returns normalized recommendation items
- `submit_feedback()` writes recommendation feedback, records event metadata, triggers immediate cognition update, then runs post-feedback refresh hooks

Example test shape:

```python
@pytest.mark.asyncio
async def test_submit_feedback_records_event_and_runs_refresh() -> None:
    adapter = OpenClawAdapter(services=fake_services)
    result = await adapter.submit_feedback(
        FeedbackRequest(recommendation_id=7, feedback_type="like", note="")
    )
    assert result.ok is True
    assert fake_services.database.updated == [(7, "like", "")]
    assert fake_services.memory.events[0]["event_type"] == "feedback"
    assert fake_services.runtime.refresh_after_feedback_called is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_openclaw_adapter.py -k "get_profile or runtime_status or sync_account or recommend or submit_feedback" -v`

Expected: FAIL because `OpenClawAdapter` and its async operations do not exist yet.

**Step 3: Write minimal implementation**

In `src/openbiliclaw/integrations/openclaw/operations.py`:

- add `OpenClawAdapterServices` protocol or dataclass-facing dependency contract
- add `OpenClawAdapter` with async methods:
  - `sync_account()`
  - `get_profile()`
  - `recommend(limit: int = 5, refresh_if_needed: bool = True)`
  - `submit_feedback(request: FeedbackRequest)`
  - `get_runtime_status()`
- normalize internal models into schema DTOs
- translate runtime/engine exceptions into `AdapterOperationError`

For feedback handling, mirror the existing API/CLI semantics:

- call `database.update_recommendation_feedback(...)`
- append a `feedback` event through `memory_manager.propagate_event(...)`
- call `soul_engine.record_immediate_feedback_cognition(...)` if present
- call `soul_engine.process_feedback_batch_if_needed()`
- call `runtime_controller.refresh_after_feedback()` if present

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_openclaw_adapter.py -k "get_profile or runtime_status or sync_account or recommend or submit_feedback" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/integrations/openclaw/operations.py tests/test_openclaw_adapter.py
git commit -m "feat: add openclaw adapter operations"
```

### Task 3: Add a bootstrap module that reuses current dependency wiring

**Files:**
- Create: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Modify: `src/openbiliclaw/integrations/openclaw/__init__.py`
- Modify: `tests/test_openclaw_adapter.py`

**Step 1: Write the failing tests**

Add tests that assert:

- `build_openclaw_adapter_services()` constructs the shared runtime dependencies once
- the returned services object exposes `config`, `database`, `memory_manager`, `soul_engine`, `recommendation_engine`, `runtime_controller`, and `account_sync_service`
- `build_openclaw_adapter()` returns a ready-to-use `OpenClawAdapter`

Example test shape:

```python
def test_build_openclaw_adapter_returns_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openbiliclaw.integrations.openclaw.bootstrap.load_config", fake_load_config)
    adapter = build_openclaw_adapter()
    assert isinstance(adapter, OpenClawAdapter)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_openclaw_adapter.py -k "build_openclaw_adapter" -v`

Expected: FAIL because bootstrap helpers do not exist yet.

**Step 3: Write minimal implementation**

In `src/openbiliclaw/integrations/openclaw/bootstrap.py`:

- add `OpenClawAdapterServices` dataclass
- implement `build_openclaw_adapter_services()`
- implement `build_openclaw_adapter()`
- follow the initialization order already used in `src/openbiliclaw/api/app.py`
- reuse a shared `Database` instance when constructing `MemoryManager`

Export these helpers from `src/openbiliclaw/integrations/openclaw/__init__.py`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_openclaw_adapter.py -k "build_openclaw_adapter" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/integrations/openclaw/bootstrap.py src/openbiliclaw/integrations/openclaw/__init__.py tests/test_openclaw_adapter.py
git commit -m "feat: add openclaw adapter bootstrap"
```

### Task 4: Wrap adapter operations as protocol-neutral OpenClaw skills

**Files:**
- Create: `src/openbiliclaw/integrations/openclaw/skill.py`
- Create: `tests/test_openclaw_skill.py`
- Modify: `src/openbiliclaw/integrations/openclaw/__init__.py`

**Step 1: Write the failing tests**

Add tests that assert:

- `build_openclaw_skills(adapter)` returns descriptors with the exact names:
  - `openbiliclaw_sync_account`
  - `openbiliclaw_get_profile`
  - `openbiliclaw_recommend`
  - `openbiliclaw_submit_feedback`
  - `openbiliclaw_get_runtime_status`
- each descriptor handler delegates to the matching adapter method
- adapter validation/operation errors become structured failure results instead of raw tracebacks

Example test shape:

```python
@pytest.mark.asyncio
async def test_recommend_skill_delegates_to_adapter() -> None:
    adapter = FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_recommend")
    payload = await skill.handler({"limit": 3, "refresh_if_needed": True})
    assert payload["items"][0]["title"] == "测试视频"
    assert adapter.calls == [("recommend", 3, True)]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_openclaw_skill.py -v`

Expected: FAIL because the skill descriptor layer does not exist yet.

**Step 3: Write minimal implementation**

In `src/openbiliclaw/integrations/openclaw/skill.py`:

- add `OpenClawSkillDescriptor`
- implement `build_openclaw_skills(adapter)`
- give each descriptor a `name`, `description`, `input_schema`, and async `handler`
- keep the skill layer free of direct database/engine construction

If an OpenClaw SDK import is optional, isolate it behind `try/except ModuleNotFoundError` and keep descriptor generation as the default path.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_openclaw_skill.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/integrations/openclaw/skill.py src/openbiliclaw/integrations/openclaw/__init__.py tests/test_openclaw_skill.py
git commit -m "feat: add openclaw skill wrappers"
```

### Task 5: Document the integration layer

**Files:**
- Create: `docs/modules/integrations.md`
- Modify: `docs/architecture.md`
- Modify: `docs/index.md`
- Modify: `docs/changelog.md`

**Step 1: Write the failing check**

Confirm the docs gap:

- there is no module doc for the new integration layer
- architecture docs do not mention OpenClaw adapter
- index docs do not link the new module doc
- changelog does not mention the OpenClaw integration

**Step 2: Run check to verify it fails**

Run: `rg -n "OpenClaw|integrations/openclaw|openbiliclaw_recommend" docs/modules/integrations.md docs/architecture.md docs/index.md docs/changelog.md`

Expected: missing-file or missing-match errors

**Step 3: Write minimal implementation**

Document:

- adapter overview, implemented features, public API, config notes, and design decisions in `docs/modules/integrations.md`
- the new integration layer in `docs/architecture.md`
- navigation entry in `docs/index.md`
- a concise changelog entry in `docs/changelog.md`

**Step 4: Run check to verify it passes**

Run: `rg -n "OpenClaw|integrations/openclaw|openbiliclaw_recommend" docs/modules/integrations.md docs/architecture.md docs/index.md docs/changelog.md`

Expected: all required matches exist

**Step 5: Commit**

```bash
git add docs/modules/integrations.md docs/architecture.md docs/index.md docs/changelog.md
git commit -m "docs: add openclaw integration docs"
```

### Task 6: Run the verification gate

**Files:**
- Verify: `src/openbiliclaw/integrations/openclaw/*.py`
- Verify: `tests/test_openclaw_adapter.py`
- Verify: `tests/test_openclaw_skill.py`
- Verify: `docs/modules/integrations.md`

**Step 1: Run targeted tests**

Run: `pytest tests/test_openclaw_adapter.py tests/test_openclaw_skill.py -v`

Expected: PASS

**Step 2: Run lint on touched Python files**

Run: `ruff check src/openbiliclaw/integrations tests/test_openclaw_adapter.py tests/test_openclaw_skill.py`

Expected: PASS

**Step 3: Run formatting check or formatter**

Run: `ruff format src/openbiliclaw/integrations tests/test_openclaw_adapter.py tests/test_openclaw_skill.py`

Expected: formatter completes without error

**Step 4: Run type check on source tree**

Run: `mypy src/`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/integrations tests/test_openclaw_adapter.py tests/test_openclaw_skill.py docs/modules/integrations.md docs/architecture.md docs/index.md docs/changelog.md
git commit -m "feat: add openclaw adapter integration"
```
