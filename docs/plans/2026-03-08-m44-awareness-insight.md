# Awareness And Insight Layers Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现可持续运行的觉察层与洞察层，包括结构化生成、增量去重、持久化，以及显式反馈驱动的洞察状态更新。

**Architecture:** 新增 `AwarenessAnalyzer` 与 `InsightAnalyzer` 作为独立分析器，分别处理近期观察和解释性假设；`SoulEngine` 负责编排最近事件查询、调用分析器、合并持久化以及反馈驱动的洞察更新。整个实现保持 TDD，先锁定结构化输出和去重/合并规则，再接通 `SoulEngine` 行为。

**Tech Stack:** Python 3.11, dataclasses, Typer, Rich, pytest, Ruff, mypy

---

### Task 1: Add Awareness Analyzer Tests

**Files:**
- Create: `src/openbiliclaw/soul/awareness_analyzer.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Test: `tests/test_awareness_analyzer.py`

**Step 1: Write the failing test**

```python
async def test_awareness_analyzer_builds_notes_from_recent_events() -> None:
    registry = FakeRegistry(
        json.dumps(
            [
                {
                    "date": "2026-03-08",
                    "observation": "最近连续浏览高信息密度内容。",
                    "trend": "更偏向深度解释而非轻量消遣。",
                    "emotion_guess": "可能处于主动吸收和整理信息的阶段。",
                }
            ],
            ensure_ascii=False,
        )
    )
    analyzer = AwarenessAnalyzer(registry)

    notes = await analyzer.analyze(
        events=[{"event_type": "view", "title": "AI 工具实测"}],
        preference={},
        soul_profile={},
    )

    assert notes[0].observation.startswith("最近连续浏览")
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_awareness_analyzer.py::test_awareness_analyzer_builds_notes_from_recent_events -v`  
Expected: FAIL with missing module or missing `AwarenessAnalyzer`

**Step 3: Write minimal implementation**

Create `AwarenessAnalyzer` with:
- `analyze(...)`
- JSON parsing
- `AwarenessNote` construction

Add `build_awareness_prompt(...)` in `src/openbiliclaw/llm/prompts.py`.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_awareness_analyzer.py::test_awareness_analyzer_builds_notes_from_recent_events -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_awareness_analyzer.py src/openbiliclaw/soul/awareness_analyzer.py src/openbiliclaw/llm/prompts.py
git commit -m "feat: add awareness analyzer"
```

### Task 2: Add Awareness Validation And Deduplication Tests

**Files:**
- Modify: `src/openbiliclaw/soul/awareness_analyzer.py`
- Test: `tests/test_awareness_analyzer.py`

**Step 1: Write the failing test**

```python
async def test_awareness_analyzer_raises_on_invalid_json() -> None:
    analyzer = AwarenessAnalyzer(FakeRegistry("not-json"))
    with pytest.raises(AwarenessGenerationError):
        await analyzer.analyze(events=[{"title": "AI"}], preference={}, soul_profile={})
```

Add a second test for same-day duplicate notes:

```python
def test_merge_awareness_notes_deduplicates_same_day_observation() -> None:
    existing = [AwarenessNote(date="2026-03-08", observation="最近连续浏览高信息密度内容。")]
    incoming = [AwarenessNote(date="2026-03-08", observation="最近连续浏览高信息密度内容。")]
    merged = analyzer.merge_notes(existing, incoming)
    assert len(merged) == 1
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_awareness_analyzer.py -v`  
Expected: FAIL on invalid JSON or duplicate handling

**Step 3: Write minimal implementation**

Implement:
- `AwarenessGenerationError`
- empty content / invalid JSON / non-list validation
- `merge_notes(existing, incoming)`
- same-day exact-observation deduplication

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_awareness_analyzer.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_awareness_analyzer.py src/openbiliclaw/soul/awareness_analyzer.py
git commit -m "feat: validate and merge awareness notes"
```

### Task 3: Add Insight Analyzer Tests

**Files:**
- Create: `src/openbiliclaw/soul/insight_analyzer.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Test: `tests/test_insight_analyzer.py`

**Step 1: Write the failing test**

```python
async def test_insight_analyzer_builds_hypotheses_from_awareness() -> None:
    registry = FakeRegistry(
        json.dumps(
            [
                {
                    "hypothesis": "用户可能通过深度内容获得掌控感。",
                    "evidence": ["最近连续浏览高信息密度内容。"],
                    "confidence": 0.62,
                }
            ],
            ensure_ascii=False,
        )
    )
    analyzer = InsightAnalyzer(registry)

    insights = await analyzer.analyze(
        awareness_notes=[AwarenessNote(date="2026-03-08", observation="最近连续浏览高信息密度内容。")],
        preference={},
        soul_profile={},
    )

    assert insights[0].validated is False
    assert insights[0].confidence == 0.62
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_insight_analyzer.py::test_insight_analyzer_builds_hypotheses_from_awareness -v`  
Expected: FAIL with missing module or missing `InsightAnalyzer`

**Step 3: Write minimal implementation**

Create `InsightAnalyzer` with:
- `analyze(...)`
- JSON parsing
- `InsightHypothesis` construction
- local default `validated=False`, `created_at` set by code

Add `build_insight_prompt(...)` in `src/openbiliclaw/llm/prompts.py`.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_insight_analyzer.py::test_insight_analyzer_builds_hypotheses_from_awareness -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_insight_analyzer.py src/openbiliclaw/soul/insight_analyzer.py src/openbiliclaw/llm/prompts.py
git commit -m "feat: add insight analyzer"
```

### Task 4: Add Insight Merge And Feedback Update Tests

**Files:**
- Modify: `src/openbiliclaw/soul/insight_analyzer.py`
- Modify: `src/openbiliclaw/soul/engine.py`
- Test: `tests/test_insight_analyzer.py`
- Test: `tests/test_soul_engine.py`

**Step 1: Write the failing test**

```python
def test_merge_insights_combines_matching_hypotheses() -> None:
    existing = [
        InsightHypothesis(
            hypothesis="用户可能通过深度内容获得掌控感。",
            evidence=["最近连续浏览高信息密度内容。"],
            confidence=0.55,
            validated=False,
            created_at="2026-03-08",
        )
    ]
    incoming = [
        InsightHypothesis(
            hypothesis="用户可能通过深度内容获得掌控感。",
            evidence=["偏好层显示 depth_preference 很高。"],
            confidence=0.68,
            validated=False,
            created_at="2026-03-08",
        )
    ]
    merged = analyzer.merge_insights(existing, incoming)
    assert len(merged) == 1
    assert "偏好层显示 depth_preference 很高。" in merged[0].evidence
    assert merged[0].confidence == 0.68
```

Add a second `SoulEngine.update_from_feedback()` test:

```python
async def test_update_from_feedback_persists_feedback_event_and_marks_insight_validated() -> None:
    ...
    await engine.update_from_feedback(
        {"hypothesis": "用户可能通过深度内容获得掌控感。", "signal": "confirm"}
    )
    assert insight_layer.data[0]["validated"] is True
    assert memory.query_events(event_types=["feedback"])[0]["event_type"] == "feedback"
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_insight_analyzer.py tests/test_soul_engine.py -v`  
Expected: FAIL on merge logic or feedback update

**Step 3: Write minimal implementation**

Implement:
- `InsightGenerationError`
- `merge_insights(existing, incoming)`
- confidence merge (take higher bounded value)
- evidence de-duplication
- `SoulEngine.update_from_feedback()`:
  - persist feedback event
  - update matching insight `validated` / confidence

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_insight_analyzer.py tests/test_soul_engine.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_insight_analyzer.py tests/test_soul_engine.py src/openbiliclaw/soul/insight_analyzer.py src/openbiliclaw/soul/engine.py
git commit -m "feat: merge insight hypotheses and update from feedback"
```

### Task 5: Wire SoulEngine Generation And Persistence

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_soul_engine.py`

**Step 1: Write the failing test**

```python
async def test_generate_awareness_note_saves_awareness_layer(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    await memory.propagate_event({"event_type": "view", "title": "AI 工具实测"})
    engine = SoulEngine(llm=..., memory=memory)

    note = await engine.generate_awareness_note()

    assert "高信息密度" in note
    assert memory.get_layer("awareness").data["notes"][0]["observation"]
```

Add a second test for `generate_insight()` writing `insight` layer.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_soul_engine.py -v`  
Expected: FAIL because methods still return empty strings

**Step 3: Write minimal implementation**

Implement in `SoulEngine`:
- initialize analyzers
- query recent events from `MemoryManager`
- load current awareness/insight layer data
- generate + merge + save
- return the primary generated note/hypothesis text, or empty string when skipped

Store shapes:
- `awareness` layer: `{"notes": [...]}` or equivalent stable list container
- `insight` layer: `{"hypotheses": [...]}` or equivalent stable list container

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_soul_engine.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_soul_engine.py src/openbiliclaw/soul/engine.py src/openbiliclaw/memory/manager.py
git commit -m "feat: persist awareness and insight layers"
```

### Task 6: Run Full Verification And Update Tracking Docs

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/changelog.md`

**Step 1: Run targeted tests**

Run: `PYTHONPATH=src pytest tests/test_awareness_analyzer.py tests/test_insight_analyzer.py tests/test_soul_engine.py -q`  
Expected: PASS

**Step 2: Run project verification**

Run:
- `PYTHONPATH=src ruff check src/ tests/`
- `PYTHONPATH=src mypy src/`
- `PYTHONPATH=src pytest -q`

Expected: all PASS

**Step 3: Update docs**

Mark `4.4` completed in `docs/v0.1-todolist.md` and add module/changelog entries that mention:
- `AwarenessAnalyzer`
- `InsightAnalyzer`
- `SoulEngine.generate_awareness_note()`
- `SoulEngine.generate_insight()`
- `update_from_feedback()`

**Step 4: Review final diff**

Run: `git status --short` and `git diff --stat`

Expected: only intended files changed

**Step 5: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/soul.md docs/changelog.md src/openbiliclaw/soul/awareness_analyzer.py src/openbiliclaw/soul/insight_analyzer.py src/openbiliclaw/soul/engine.py src/openbiliclaw/llm/prompts.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py tests/test_soul_engine.py
git commit -m "feat: implement awareness and insight layers"
```
