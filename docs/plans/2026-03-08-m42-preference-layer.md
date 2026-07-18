# M4.2 偏好层 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an LLM-backed preference layer that extracts, merges, decays, and persists user preferences from recent events.

**Architecture:** Add a dedicated `PreferenceAnalyzer` responsible for prompt construction, structured extraction, normalization, and merge logic. Keep SQLite as the event source of truth and connect the analyzer through `SoulEngine.analyze_events()`.

**Tech Stack:** Python 3.14, sqlite3, pytest, mypy, ruff, existing LLM service/provider stack

---

### Task 1: Add failing tests for preference analysis parsing and merge behavior

**Files:**
- Create: `tests/test_preference_analyzer.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Create: `src/openbiliclaw/soul/preference_analyzer.py`

**Step 1: Write the failing tests**

Add tests covering:

```python
def test_analyze_events_parses_structured_preference_output() -> None:
    ...

def test_merge_preferences_applies_decay_and_deduplicates_tags() -> None:
    ...

def test_invalid_json_response_raises_preference_analysis_error() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_preference_analyzer.py -q`
Expected: FAIL because analyzer/prompt builder does not exist.

**Step 3: Write minimal implementation**

- Add preference prompt builder
- Add `PreferenceAnalyzer`
- Add JSON parse/normalize/merge helpers

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_preference_analyzer.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_preference_analyzer.py src/openbiliclaw/llm/prompts.py src/openbiliclaw/soul/preference_analyzer.py
git commit -m "feat: add preference analyzer"
```

### Task 2: Wire preference analysis into SoulEngine

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_soul_engine.py`

**Step 1: Write the failing tests**

Add tests covering:

```python
@pytest.mark.asyncio
async def test_analyze_events_updates_preference_layer(tmp_path: Path) -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_soul_engine.py -q`
Expected: FAIL because `SoulEngine.analyze_events()` does not update preferences.

**Step 3: Write minimal implementation**

- Inject/use `PreferenceAnalyzer`
- Load existing preference layer
- Analyze current batch
- Save updated preference layer back through memory storage

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_soul_engine.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_soul_engine.py src/openbiliclaw/soul/engine.py src/openbiliclaw/memory/manager.py
git commit -m "feat: wire preference analysis into soul engine"
```

### Task 3: Run full verification

**Files:**
- Modify: none unless verification exposes issues

**Step 1: Run lint**

Run: `ruff check src/ tests/`
Expected: PASS

**Step 2: Run type check**

Run: `mypy src/`
Expected: PASS

**Step 3: Run tests**

Run: `pytest -q`
Expected: PASS

**Step 4: Commit final fixes if needed**

```bash
git add <files>
git commit -m "fix: polish preference layer verification issues"
```
