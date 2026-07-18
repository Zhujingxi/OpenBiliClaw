# M2.3 Prompt 管理与调用适配 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a reusable prompt layer, inject core memory automatically before LLM calls, and wire the existing Socratic dialogue flow through the new adapter.

**Architecture:** Keep provider and registry logic unchanged. Add a small prompt builder module plus an LLM service facade that owns prompt assembly, core-memory injection, and final registry calls. Then update dialogue code to consume that shared path instead of returning placeholders.

**Tech Stack:** Python 3.11+, dataclasses, pytest, Typer, existing LLM registry

---

### Task 1: Add failing tests for prompt building and memory rendering

**Files:**
- Create: `tests/test_llm_prompts.py`
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_llm_prompts.py`

**Step 1: Write the failing tests**

Add tests covering:
- core memory text rendering from `MemoryManager`
- fallback rendering when no soul/profile data exists
- prompt builder output order: system prompt, history, current user message
- Socratic prompt includes injected memory context

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_prompts.py -q
```

Expected: FAIL because prompt helpers do not exist yet

**Step 3: Write minimal implementation**

Implement:
- `MemoryManager.render_core_memory_prompt()`
- `src/openbiliclaw/llm/prompts.py`
- `build_socratic_dialogue_prompt(...)`

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_prompts.py -q
```

Expected: PASS

### Task 2: Add failing tests for the LLM service facade

**Files:**
- Create: `tests/test_llm_service.py`
- Create: `src/openbiliclaw/llm/service.py`
- Modify: `src/openbiliclaw/llm/__init__.py`
- Test: `tests/test_llm_service.py`

**Step 1: Write the failing tests**

Cover:
- service calls the registry with prompt-builder messages
- core memory is injected automatically
- empty LLM content raises a clear service-layer error

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_service.py -q
```

Expected: FAIL because the service facade does not exist yet

**Step 3: Write minimal implementation**

Implement a small service object or function set that:
- accepts registry + memory manager
- builds messages using prompt helpers
- delegates to `LLMRegistry.complete(...)`
- validates non-empty response content

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_service.py -q
```

Expected: PASS

### Task 3: Add failing dialogue integration tests

**Files:**
- Create: `tests/test_soul_dialogue.py`
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Test: `tests/test_soul_dialogue.py`

**Step 1: Write the failing tests**

Cover:
- `respond()` appends the user turn and generated agent turn
- dialogue uses the shared LLM service path
- service failure returns a graceful fallback reply
- `clear_history()` still resets the in-memory turns

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_soul_dialogue.py -q
```

Expected: FAIL because dialogue still returns a placeholder string

**Step 3: Write minimal implementation**

Update `SocraticDialogue` to:
- build prompts through the new prompt/service layer
- call the LLM service
- log and degrade cleanly on failure

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_soul_dialogue.py -q
```

Expected: PASS

### Task 4: Run the full project quality gate

**Files:**
- Create: `src/openbiliclaw/llm/prompts.py`
- Create: `src/openbiliclaw/llm/service.py`
- Modify: `src/openbiliclaw/llm/__init__.py`
- Modify: `src/openbiliclaw/memory/manager.py`
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Create: `tests/test_llm_prompts.py`
- Create: `tests/test_llm_service.py`
- Create: `tests/test_soul_dialogue.py`
- Test: full local gate

**Step 1: Run the full quality gate**

Run:

```bash
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m pytest -q
```

Expected: all commands pass

**Step 2: Commit**

```bash
git add src/openbiliclaw/llm/prompts.py src/openbiliclaw/llm/service.py src/openbiliclaw/llm/__init__.py src/openbiliclaw/memory/manager.py src/openbiliclaw/soul/dialogue.py tests/test_llm_prompts.py tests/test_llm_service.py tests/test_soul_dialogue.py docs/plans/2026-03-08-m23-prompt-management-design.md docs/plans/2026-03-08-m23-prompt-management.md
git commit -m "feat: add prompt management and llm service"
```
