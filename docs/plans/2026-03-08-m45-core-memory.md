# Core Memory Loading Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现统一的 core memory 摘要与注入机制，让当前和未来的 LLM 任务都通过同一条 service 路径加载用户画像上下文。

**Architecture:** `MemoryManager` 负责产出裁剪后的结构化 core memory；`LLMService` 负责统一注入该摘要到任务 prompt；现有 `SocraticDialogue`、各 analyzer 和 builder 改为通过 service 执行，避免重复拼接上下文。整个实现保持 TDD，先锁定 memory summary 和 service 注入，再逐个适配调用方。

**Tech Stack:** Python 3.11, dataclasses, Typer, pytest, Ruff, mypy

---

### Task 1: Refine Core Memory Summary And Prompt Rendering

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_memory_manager.py`

**Step 1: Write the failing test**

```python
def test_get_core_memory_returns_trimmed_summary(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update(
        {
            "personality_portrait": "portrait",
            "core_traits": ["理性", "谨慎"],
            "values": ["成长", "真实"],
            "life_stage": "探索阶段",
            "deep_needs": ["被理解"],
        }
    )
    memory.get_layer("preference").data.update(
        {
            "interests": [
                {"name": "科技", "category": "知识", "weight": 0.9},
                {"name": "历史", "category": "知识", "weight": 0.8},
            ]
        }
    )
    memory.get_layer("awareness").data.update({"notes": [{"observation": "最近更专注。"}]})
    memory.get_layer("insight").data.update(
        {"hypotheses": [{"hypothesis": "可能在寻找掌控感。", "confidence": 0.7}]}
    )

    core = memory.get_core_memory()

    assert core["soul_summary"]["personality_portrait"] == "portrait"
    assert core["preference_summary"]["top_interests"][0]["name"] == "科技"
    assert core["recent_awareness"][0]["observation"] == "最近更专注。"
    assert core["active_insights"][0]["hypothesis"] == "可能在寻找掌控感。"
```

Add a second test for `render_core_memory_prompt()` section order and stable headings.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_memory_manager.py -v`  
Expected: FAIL because current `get_core_memory()` returns raw dicts

**Step 3: Write minimal implementation**

Implement in `src/openbiliclaw/memory/manager.py`:
- trimmed `get_core_memory()`
- stable top-N selection for interests / awareness / insights
- prompt rendering based on the summary object

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_memory_manager.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_memory_manager.py src/openbiliclaw/memory/manager.py
git commit -m "feat: summarize core memory"
```

### Task 2: Add Unified LLMService Core Memory Entry Points

**Files:**
- Modify: `src/openbiliclaw/llm/service.py`
- Test: `tests/test_llm_service.py`

**Step 1: Write the failing test**

```python
async def test_complete_with_core_memory_injects_core_memory() -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)

    await service.complete_with_core_memory(
        system_instruction="你是内容评估助手。",
        user_input="请评估这个视频。",
    )

    assert "## 用户画像" in registry.calls[0][0]["content"]
    assert "你是内容评估助手。" in registry.calls[0][0]["content"]
```

Add a second test for `complete_structured_task(..., json_mode=True)` passing `json_mode=True`.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_llm_service.py -v`  
Expected: FAIL because the methods do not exist

**Step 3: Write minimal implementation**

Implement in `src/openbiliclaw/llm/service.py`:
- `complete_with_core_memory(...)`
- `complete_structured_task(...)`
- keep existing `complete_socratic_dialogue()` as a thin wrapper

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_llm_service.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_llm_service.py src/openbiliclaw/llm/service.py
git commit -m "feat: add unified core memory llm service"
```

### Task 3: Adapt Socratic Dialogue To Unified Service Path

**Files:**
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Test: `tests/test_soul_dialogue.py`

**Step 1: Write the failing test**

```python
async def test_dialogue_uses_unified_service_entrypoint() -> None:
    service = FakeLLMService(response="继续说说你为什么喜欢这种视频。")
    dialogue = SocraticDialogue(llm=None, soul_engine=FakeSoulEngine(), llm_service=service)

    reply = await dialogue.respond("我最近喜欢讲得很透的纪录片")

    assert reply.startswith("继续说说")
    assert service.used_complete_with_core_memory is True
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_soul_dialogue.py -v`  
Expected: FAIL because dialogue still calls the old method

**Step 3: Write minimal implementation**

Update `src/openbiliclaw/soul/dialogue.py` to use `complete_with_core_memory(...)` or the new wrapped dialogue path without changing user-facing behavior.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_soul_dialogue.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_soul_dialogue.py src/openbiliclaw/soul/dialogue.py
git commit -m "feat: route dialogue through unified core memory service"
```

### Task 4: Adapt Builders And Analyzers To Unified Service Entry Point

**Files:**
- Modify: `src/openbiliclaw/soul/profile_builder.py`
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`
- Modify: `src/openbiliclaw/soul/awareness_analyzer.py`
- Modify: `src/openbiliclaw/soul/insight_analyzer.py`
- Test: `tests/test_profile_builder.py`
- Test: `tests/test_preference_analyzer.py`
- Test: `tests/test_awareness_analyzer.py`
- Test: `tests/test_insight_analyzer.py`

**Step 1: Write the failing test**

Add one minimal regression test per analyzer/builder verifying the unified service path receives core memory context.

Example:

```python
async def test_profile_builder_uses_unified_service_with_core_memory() -> None:
    service = FakeLLMService(content=VALID_PROFILE_JSON)
    builder = ProfileBuilder(registry=None, llm_service=service)

    await builder.build(history=[{"title": "AI 视频"}], preference={})

    assert service.used_complete_structured_task is True
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_profile_builder.py tests/test_preference_analyzer.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py -v`  
Expected: FAIL because builders/analyzers still call registry directly

**Step 3: Write minimal implementation**

Refactor builders/analyzers so they:
- accept either a registry-backed service or a service-compatible collaborator
- delegate execution to `LLMService` unified methods
- keep JSON parsing and business validation inside each analyzer/builder

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_profile_builder.py tests/test_preference_analyzer.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_profile_builder.py tests/test_preference_analyzer.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py src/openbiliclaw/soul/profile_builder.py src/openbiliclaw/soul/preference_analyzer.py src/openbiliclaw/soul/awareness_analyzer.py src/openbiliclaw/soul/insight_analyzer.py
git commit -m "feat: unify analyzer core memory injection"
```

### Task 5: Update Tracking Docs And Run Full Verification

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/modules/llm.md`
- Modify: `docs/changelog.md`

**Step 1: Run targeted tests**

Run: `PYTHONPATH=src pytest tests/test_memory_manager.py tests/test_llm_service.py tests/test_soul_dialogue.py tests/test_profile_builder.py tests/test_preference_analyzer.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py -q`  
Expected: PASS

**Step 2: Run project verification**

Run:
- `PYTHONPATH=src ruff check src/ tests/`
- `PYTHONPATH=src mypy src/`
- `PYTHONPATH=src pytest -q`

Expected: all PASS

**Step 3: Update docs**

Mark `4.5` completed in `docs/v0.1-todolist.md` and update:
- `docs/modules/memory.md` for summarized core memory
- `docs/modules/llm.md` for unified service path
- `docs/changelog.md` for M4.5 delivery

**Step 4: Review final diff**

Run: `git status --short` and `git diff --stat`

Expected: only intended files changed

**Step 5: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/memory.md docs/modules/llm.md docs/changelog.md src/openbiliclaw/memory/manager.py src/openbiliclaw/llm/service.py src/openbiliclaw/soul/dialogue.py src/openbiliclaw/soul/profile_builder.py src/openbiliclaw/soul/preference_analyzer.py src/openbiliclaw/soul/awareness_analyzer.py src/openbiliclaw/soul/insight_analyzer.py tests/test_memory_manager.py tests/test_llm_service.py tests/test_soul_dialogue.py tests/test_profile_builder.py tests/test_preference_analyzer.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py
git commit -m "feat: unify core memory loading and injection"
```
