# M2.1 LLM Providers Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add robust OpenAI, Claude, DeepSeek, and Ollama providers with retries, timeouts, and normalized errors.

**Architecture:** Normalize provider behavior around the existing `LLMProvider.complete()` contract. Use shared OpenAI-compatible request handling for OpenAI, DeepSeek, and Ollama, and keep Anthropic-specific translation in `ClaudeProvider` while mapping all provider failures into a small set of shared exceptions.

**Tech Stack:** Python 3.11+, openai, anthropic, asyncio, pytest, monkeypatch

---

### Task 1: Add failing provider tests

**Files:**
- Create: `tests/test_llm_providers.py`
- Test: `tests/test_llm_providers.py`

**Step 1: Write the failing test**

Add tests for:
- OpenAI success response normalization
- OpenAI retry on transient failure
- OpenAI timeout mapping
- Claude success response normalization
- Claude exception mapping
- DeepSeek provider metadata
- Ollama provider defaults without API key
- `health_check()` success and failure behavior

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_providers.py -q
```

Expected: FAIL because new exceptions / Ollama provider / retry behavior do not exist yet

**Step 3: Write minimal implementation**

Implement only the provider behaviors needed by the tests.

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_providers.py -q
```

Expected: PASS

### Task 2: Add shared provider errors and OpenAI-compatible handling

**Files:**
- Modify: `src/openbiliclaw/llm/base.py`
- Modify: `src/openbiliclaw/llm/openai_provider.py`
- Create: `src/openbiliclaw/llm/ollama_provider.py`
- Modify: `src/openbiliclaw/llm/__init__.py`
- Test: `tests/test_llm_providers.py`

**Step 1: Run the focused failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_providers.py -q
```

Expected: FAIL on missing classes or missing retry/error mapping

**Step 2: Implement the smallest shared provider layer**

Add:
- normalized provider exceptions
- bounded retry for transient failures
- timeout mapping
- Ollama provider defaulting to local OpenAI-compatible endpoint

**Step 3: Re-run provider tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_providers.py -q
```

Expected: PASS for OpenAI-compatible providers

### Task 3: Add Claude exception mapping and response normalization

**Files:**
- Modify: `src/openbiliclaw/llm/claude_provider.py`
- Test: `tests/test_llm_providers.py`

**Step 1: Add focused Claude failures**

Ensure tests cover:
- system message extraction
- content concatenation
- timeout and generic error mapping
- retry on transient provider failure

**Step 2: Run the Claude-focused test subset**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_providers.py -q -k claude
```

Expected: FAIL until ClaudeProvider handles these cases

**Step 3: Implement minimal Claude support**

Map Anthropic failures into shared exceptions and preserve unified `LLMResponse`.

**Step 4: Re-run provider tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_providers.py -q
```

Expected: PASS

### Task 4: Run full project verification

**Files:**
- Modify: none beyond previous tasks
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
git add src/openbiliclaw/llm/base.py src/openbiliclaw/llm/openai_provider.py src/openbiliclaw/llm/claude_provider.py src/openbiliclaw/llm/ollama_provider.py src/openbiliclaw/llm/__init__.py tests/test_llm_providers.py docs/plans/2026-03-08-m21-llm-providers-design.md docs/plans/2026-03-08-m21-llm-providers.md
git commit -m "feat: add robust llm providers"
```
