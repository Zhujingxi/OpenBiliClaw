# Issue #95 Responses `store=false` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenAI Responses API calls work with gateways that require the standard `store` parameter to be explicitly false.

**Architecture:** Keep endpoint selection and retry behavior unchanged. Add `store=False` to the top-level keyword arguments assembled by `OpenAIProvider._complete_via_responses()`, so initial calls and existing fallback calls all remain stateless.

**Tech Stack:** Python 3.11+, OpenAI Python SDK 2.28.0, pytest/pytest-asyncio, Ruff, MyPy.

## Global Constraints

- Follow TDD: observe the focused regression test fail before editing production code.
- Do not add a configuration field or change Chat Completions requests.
- Do not change the provider-wide retry policy in this issue.
- Update `docs/modules/llm.md` and the active `docs/changelog.md` release entry.

---

### Task 1: Lock the stateless Responses request contract

**Files:**
- Modify: `tests/test_llm_providers.py`
- Modify: `src/openbiliclaw/llm/openai_provider.py:237-242`

**Interfaces:**
- Consumes: `OpenAIProvider.complete(messages, api_flavor="responses")` and `AsyncOpenAI.responses.create(**kwargs)`.
- Produces: every Responses SDK call receives the standard keyword argument `store=False`.

- [ ] **Step 1: Write the failing regression assertion**

Add the storage assertion to the existing request-mapping test after the other
captured request fields:

```python
assert captured["store"] is False
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
uv run pytest tests/test_llm_providers.py::test_openai_provider_responses_flavor_maps_params_and_usage -q
```

Expected: FAIL with `KeyError: 'store'`, proving the current request omits the field.

- [ ] **Step 3: Implement the minimal request change**

Add the standard SDK argument to `_complete_via_responses()`:

```python
kwargs: dict[str, Any] = {
    "model": effective_model,
    "input": input_messages,
    "max_output_tokens": max_tokens,
    "temperature": temperature,
    "store": False,
}
```

- [ ] **Step 4: Run the focused test to verify GREEN**

Run:

```bash
uv run pytest tests/test_llm_providers.py::test_openai_provider_responses_flavor_maps_params_and_usage -q
```

Expected: `1 passed`.

### Task 2: Document and verify the compatibility behavior

**Files:**
- Modify: `docs/modules/llm.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: the Responses behavior established in Task 1.
- Produces: user-facing documentation that `api_flavor="responses"` sends `store=false` for official and compatible endpoints.

- [ ] **Step 1: Update the LLM feature table**

Extend the existing issue #72 Responses API row to state that every Responses
request explicitly sends `store=false`, supporting gateways backed by the
ChatGPT/Codex Responses endpoint.

- [ ] **Step 2: Update the active changelog release**

Add one bullet under v0.3.161 describing issue #95, the missing `store` root
cause, the stateless request fix, and the provider regression coverage.

- [ ] **Step 3: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_llm_providers.py -q
uv run pytest -q
uv run ruff check src/ tests/
uv run mypy src/
```

Expected: every command exits 0 with no test, lint, or type-check failures.

- [ ] **Step 4: Commit the implementation**

```bash
git add src/openbiliclaw/llm/openai_provider.py tests/test_llm_providers.py docs/modules/llm.md docs/changelog.md
git commit -m "fix: disable Responses API storage"
```
