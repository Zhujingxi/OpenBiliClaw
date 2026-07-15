# Task 7 Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all four Task 7 review blockers without amending commit `978f7e25`, while preserving secret safety, layered configuration semantics, concurrent unrelated writes, and exact unrelated TOML bytes.

**Architecture:** A shared endpoint validator will be consumed by structural validation and runtime factories. Disk loading will keep separate base and effective authority selections. A path-keyed config-write boundary will coordinate model and ordinary API writers, with a final semantic authority check and source rebase immediately before replacement. The source editor will maintain multiline TOML lexical state before interpreting physical lines as table headers.

**Tech Stack:** Python 3.11+, frozen dataclasses, `tomllib`, `asyncio`, `threading`, Ruff, MyPy strict, pytest.

## Global Constraints

- Write each regression first and observe the expected failure before production edits.
- Endpoint failures expose only stable field paths/codes/messages, never submitted URL, userinfo, query, fragment, credential, exception cause, or context.
- `config.local.toml` remains read-only to the model service; local legacy authority blocks explicit migration saves until converted.
- In-process writers share one canonical boundary. There is no cross-process file lock: the final reread sees only external changes already visible before that read, and an external writer can still race in the reread-to-replace window.
- Preserve every unrelated TOML byte, including CRLF and header-looking lines inside valid multiline basic or literal strings.
- Do not implement Task 8 API/UI model-editor composition.

---

### Task 1: Universal secret-safe native endpoints

**Files:**
- Create: `src/openbiliclaw/model_config/endpoints.py`
- Modify: `src/openbiliclaw/model_config/validation.py`
- Modify: `src/openbiliclaw/model_config/service.py`
- Modify: `src/openbiliclaw/llm/connection_factory.py`
- Test: `tests/test_model_config_service.py`
- Test: `tests/test_llm_connection_factory.py`

**Interfaces:**
- Produces: `validated_native_base_url(value: str) -> str`, accepting empty values and returning a populated safe HTTP(S) endpoint unchanged.
- Consumes: every populated Chat/Embedding `base_url` before public projection, persistence, credential lookup, proxy policy, token callbacks, or SDK construction.

- [x] **Step 1: Write failing read/save and factory-ordering tests**

```python
with pytest.raises(ModelConfigValidationError) as raised:
    service.read()
assert submitted_url not in repr(raised.value)

with pytest.raises(LLMProviderError):
    build_chat_adapter(connection, options)
assert callbacks == []
```

- [x] **Step 2: Run focused tests and verify they fail because official, Gemini, DashScope, and Ollama endpoints are not universally validated**

Run: `.venv/bin/python -m pytest tests/test_model_config_service.py tests/test_llm_connection_factory.py -q`

- [x] **Step 3: Add the shared validator and map failures to `invalid_endpoint` field issues**

```python
def validated_native_base_url(value: str) -> str:
    if not value:
        return ""
    # Reject whitespace/control, backslash, userinfo, query, fragment,
    # non-HTTP(S), malformed host/port, and invalid DNS labels.
    return value
```

- [x] **Step 4: Validate persisted state before `_public_models` and validate factories before credential/proxy callbacks**

- [x] **Step 5: Run the endpoint slices and verify GREEN**

---

### Task 2: Effective local/base legacy authority

**Files:**
- Modify: `src/openbiliclaw/model_config/_service_storage.py`
- Modify: `src/openbiliclaw/model_config/service.py`
- Test: `tests/test_model_config_service.py`

**Interfaces:**
- Produces: `DiskState.base_source` plus an effective selection derived from the merged base/local model layers.
- Preserves: `persisted_models` from base selection and effective `models`, migration report/state/result, local legacy blocking, and effective override provenance.

- [x] **Step 1: Write failing local-only legacy, base+local legacy, native precedence, and native patch tests**

```python
snapshot = ModelConfigService(base, coordinator, local_path=local).read()
assert snapshot.source == "legacy"
assert snapshot.migration is not None
assert snapshot.override_paths == ("llm.deepseek.model",)
```

- [x] **Step 2: Run the local-layer slice and verify the current base-source gate fails**

- [x] **Step 3: Select base and merged effective authorities separately**

```python
base_selection = _select_models(base_raw, environment)
effective_selection = _select_models(_deep_merge(base_raw, local_raw), environment)
```

- [x] **Step 4: Keep effective local legacy saves blocked and key legacy backup creation to `base_source`**

- [x] **Step 5: Run the local-layer slice and verify GREEN**

---

### Task 3: Canonical write coordination and final rebase

**Files:**
- Create: `src/openbiliclaw/config_write.py`
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/model_config/_service_storage.py`
- Modify: `src/openbiliclaw/model_config/service.py`
- Test: `tests/test_model_config_service.py`
- Test: `tests/test_api_config_transactional.py`

**Interfaces:**
- Produces: `config_write_boundary(path)` with one path-keyed async transaction lock and synchronous disk critical section.
- Produces: a private authority fingerprint on `DiskState` for final semantic source checks.
- Consumes: ordinary `save_config`, all app config transaction paths, and model service precommit/swap/rollback.

- [x] **Step 1: Write a deterministic failing service-vs-ordinary-save test**

```python
await coordinator.build_entered.wait()
ordinary = load_config(path)
ordinary.language = "en"
save_config(ordinary, path)
coordinator.build_release.set()
assert tomllib.loads(path.read_text())["general"]["language"] == "en"
```

- [x] **Step 2: Write a failing authority-change conflict test**

```python
assert result.conflict is True
assert coordinator.swap_calls == 0
```

- [x] **Step 3: Run both tests and verify stale whole-document rendering loses the ordinary edit and misses the late authority change**

- [x] **Step 4: Add the shared boundary, wrap `save_config` and app writers, then re-read under the boundary immediately before replacement**

```python
latest = self._read_state()
if latest.revision != state.revision or latest.authority_fingerprint != state.authority_fingerprint:
    return conflict(latest)
payload = _render_document(latest.original, persisted)
```

- [x] **Step 5: Run concurrency/config transaction tests and verify GREEN**

---

### Task 4: Multiline-aware TOML source scanner

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Test: `tests/test_model_config_service.py`

**Interfaces:**
- Produces: private line scanner state `None | "basic" | "literal"` used before `_toml_header_root`.
- Preserves: exact bytes of unrelated multiline values and only recognizes real table headers outside multiline strings.

- [x] **Step 1: Write failing triple-double and triple-single regression tests containing `[models]`, `[llm]`, and `[[models.chat.connections]]` lines**

- [x] **Step 2: Run the scanner slice and verify current physical-line parsing mutates or rejects valid TOML**

- [x] **Step 3: Add escaped-quote-aware multiline basic scanning and quote-run-aware literal scanning**

```python
for line in lines:
    root = _toml_header_root(line) if multiline_state is None else None
    multiline_state = _next_multiline_state(line, multiline_state)
```

- [x] **Step 4: Run renderer tests and verify exact-byte preservation GREEN**

---

### Task 5: Documentation, review, verification, and commit

**Files:**
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/changelog.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `.superpowers/sdd/task-7-report.md` (ignored report)

- [x] **Step 1: Document universal endpoint safety, effective local authority, in-process coordination, the explicit cross-process race boundary, and multiline preservation**

- [x] **Step 2: Re-review the full modified range for adjacent endpoint, layering, concurrency, rollback, scanner, repr, and exception-chain leaks**

- [x] **Step 3: Run fresh verification**

```bash
.venv/bin/python -m pytest tests/test_model_config_service.py tests/test_api_config_transactional.py tests/test_config.py tests/test_llm_connection_factory.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy src/
.venv/bin/python -m ruff check <Task 7 paths>
.venv/bin/python -m ruff format --check <Task 7 paths>
git diff --check
```

- [x] **Step 4: Update the ignored report with RED/GREEN and exact command results**

- [x] **Step 5: Stage the reviewed scope and create a new Conventional Commit without amending `978f7e25`**

```bash
git commit -m "fix: harden transactional model config saves"
```

---

### Task 6: Persisted endpoint validation under local array shadowing

**Files:**
- Modify: `src/openbiliclaw/model_config/service.py`
- Test: `tests/test_model_config_service.py`
- Modify: `docs/modules/config.md`
- Modify: `docs/architecture.md`
- Modify: `docs/changelog.md`
- Modify: `.superpowers/sdd/task-7-report.md` (ignored report)

**Interfaces:**
- Reads: endpoint-only validation independently covers `DiskState.persisted_models` and effective `DiskState.models` before public projection.
- Saves: endpoint-only validation covers `persisted` after both the initial split and canonical rebase, while effective models still receive full validation.
- Preserves: base records may rely on a valid local layer for credentials or other full-validation fields; only unsafe base endpoints fail independently.

- [x] **Step 1: Reproduce safe-local-array shadowing of unsafe base Chat and Embedding endpoints**

- [x] **Step 2: Add a split-save defense regression that bypasses the read guard and proves no build/write/swap**

- [x] **Step 3: Add endpoint-only persisted checks at read, initial split, and rebase boundaries; keep effective full validation**

- [x] **Step 4: Pin the valid base-missing-credential/local-supplied-credential case**

- [x] **Step 5: Run focused and full verification, update the ignored report, and create a new non-amended commit**
