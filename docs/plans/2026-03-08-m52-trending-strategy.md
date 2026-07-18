# 5.2 排行榜策略 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 `TrendingStrategy` 能基于用户画像从 B 站全站榜和相关分区榜中筛出真正适合这个人的候选内容。

**Architecture:** `TrendingStrategy` 负责候选榜单内容收集，`ContentDiscoveryEngine.evaluate_content()` 负责统一做 LLM 评分和理由生成。分区选择也走结构化 LLM 输出，并保留默认 `rid` fallback。

**Tech Stack:** Python 3.11, asyncio, pytest, Ruff, MyPy

---

### Task 1: 为 TrendingStrategy 和评估入口写失败测试

**Files:**
- Create: `tests/test_trending_strategy.py`
- Modify: `tests/test_discovery_engine.py`

**Step 1: 写失败测试**

新增测试：
- `test_trending_strategy_fetches_global_and_related_rankings`
- `test_trending_strategy_filters_by_score_threshold`
- `test_trending_strategy_continues_when_one_ranking_fails`
- `test_evaluate_content_sets_score_and_reason`

**Step 2: 运行测试确认失败**

Run:
`PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_trending_strategy.py tests/test_discovery_engine.py -q`

Expected: FAIL

### Task 2: 实现分区选择与榜单抓取

**Files:**
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Test: `tests/test_trending_strategy.py`

**Step 1: 新增分区选择 prompt**

要求输出：

```json
{"rids": [36, 188, 119]}
```

**Step 2: 实现 TrendingStrategy 的 rid 选择**

实现：
- 固定包含 `rid=0`
- LLM 成功时补额外分区
- 失败时回退到默认分区

**Step 3: 实现榜单抓取与结果映射**

把 `get_ranking(rid)` 结果映射成 `DiscoveredContent`。

**Step 4: 跑针对性测试**

Run:
`PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_trending_strategy.py -q`

Expected: 部分测试转绿

### Task 3: 实现统一内容评估

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Test: `tests/test_trending_strategy.py`
- Test: `tests/test_discovery_engine.py`

**Step 1: 新增内容评估 prompt**

要求输出：

```json
{"score": 0.78, "reason": "一句中文理由"}
```

**Step 2: 实现 `ContentDiscoveryEngine.evaluate_content()`**

通过 `llm_service` 或等价注入做结构化评估，并回填 `score/reason`。

**Step 3: 让 TrendingStrategy 使用评估入口并应用阈值**

只保留高于阈值的内容。

**Step 4: 跑测试**

Run:
`PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_trending_strategy.py tests/test_discovery_engine.py -q`

Expected: PASS

### Task 4: 文档同步与全量验证

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/changelog.md`
- Optionally Modify: `docs/modules/discovery.md`

**Step 1: 更新任务状态**

将 `5.2 排行榜策略` 已完成项标记为完成。

**Step 2: 更新 changelog / 模块文档**

记录排行榜策略、分区选择、LLM 评分和阈值过滤。

**Step 3: 运行全量验证**

Run:
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected:
- Ruff 通过
- MyPy 通过
- Pytest 全通过

**Step 4: 提交**

```bash
git add docs/v0.1-todolist.md docs/changelog.md docs/modules/discovery.md src/openbiliclaw/discovery/strategies/strategies.py src/openbiliclaw/discovery/engine.py src/openbiliclaw/llm/prompts.py tests/test_trending_strategy.py tests/test_discovery_engine.py
git commit -m "feat: add trending discovery strategy"
```
