# 4.5 核心记忆加载收口 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 收口 `4.5`，让 Soul 相关结构化任务不再能绕过 core memory 注入链路。

**Architecture:** 删除 `ProfileBuilder`、`PreferenceAnalyzer`、`AwarenessAnalyzer`、`InsightAnalyzer` 对原始 `complete()` fallback 的支持，统一只接受 `complete_structured_task()` 接口。`SoulEngine` 保持通过 `LLMService` 注入这一能力。

**Tech Stack:** Python 3.11, asyncio, pytest, Ruff, MyPy

---

### Task 1: 写失败测试锁住“禁止绕过 core memory”

**Files:**
- Modify: `tests/test_profile_builder.py`
- Modify: `tests/test_preference_analyzer.py`
- Modify: `tests/test_awareness_analyzer.py`
- Modify: `tests/test_insight_analyzer.py`

**Step 1: 新增失败测试**

分别新增测试，验证只提供 `complete()` 的 fake registry 不再满足构造要求或调用要求。

**Step 2: 跑针对性测试确认失败**

Run:
`PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_profile_builder.py tests/test_preference_analyzer.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py -q`

Expected: FAIL

### Task 2: 收口类型和实现

**Files:**
- Modify: `src/openbiliclaw/soul/profile_builder.py`
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`
- Modify: `src/openbiliclaw/soul/awareness_analyzer.py`
- Modify: `src/openbiliclaw/soul/insight_analyzer.py`

**Step 1: 定义统一窄接口**

在上述模块里统一使用 `SupportsCoreMemoryTask` 风格的协议，只保留：
- `complete_structured_task(...)`

**Step 2: 删除 fallback**

删除 `_complete()` 中直接调用 `registry.complete(..., json_mode=True)` 的逻辑，调用点统一直连 `complete_structured_task(...)`。

**Step 3: 跑针对性测试**

Run:
`PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_profile_builder.py tests/test_preference_analyzer.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py -q`

Expected: PASS

### Task 3: 文档与全量验证

**Files:**
- Modify: `docs/changelog.md`

**Step 1: 追加 changelog**

记录 `4.5` 的收口：删除 fallback，统一强制经 `LLMService` / core memory 注入。

**Step 2: 跑全量验证**

Run:
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected:
- Ruff 通过
- MyPy 通过
- Pytest 全通过

**Step 3: 提交**

```bash
git add docs/changelog.md src/openbiliclaw/soul/profile_builder.py src/openbiliclaw/soul/preference_analyzer.py src/openbiliclaw/soul/awareness_analyzer.py src/openbiliclaw/soul/insight_analyzer.py tests/test_profile_builder.py tests/test_preference_analyzer.py tests/test_awareness_analyzer.py tests/test_insight_analyzer.py
git commit -m "fix: tighten core memory task injection"
```
