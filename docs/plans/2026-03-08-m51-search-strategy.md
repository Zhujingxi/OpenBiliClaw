# 5.1 搜索策略 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 `SearchStrategy` 基于用户画像自动生成搜索词、调用 B 站搜索并返回可直接供后续发现引擎使用的 `DiscoveredContent`。

**Architecture:** 在 `SearchStrategy` 中注入 `LLMService` 和 `BilibiliAPIClient`，统一通过结构化任务生成查询词，再串行执行搜索、去重并映射成发现结果。`ContentDiscoveryEngine` 只做轻量编排，不提前引入内容评分和缓存写入。

**Tech Stack:** Python 3.11, Typer project structure, asyncio, pytest, Ruff, MyPy

---

### Task 1: 为 SearchStrategy 写失败测试

**Files:**
- Modify: `tests/test_discovery_engine.py`
- Create: `tests/test_search_strategy.py`

**Step 1: 写 query 生成和搜索调用的失败测试**

添加测试：
- `test_search_strategy_uses_llm_queries_and_searches_each_query`
- `test_search_strategy_deduplicates_results_by_bvid`
- `test_search_strategy_falls_back_when_llm_returns_invalid_json`
- `test_search_strategy_continues_when_single_query_fails`

**Step 2: 运行单测确认失败**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_search_strategy.py -q`

Expected: FAIL，提示 `SearchStrategy` 尚未实现依赖注入或 discover 逻辑。

**Step 3: 提交测试骨架**

```bash
git add tests/test_search_strategy.py tests/test_discovery_engine.py
git commit -m "test: add search strategy coverage"
```

### Task 2: 实现 query 生成与 fallback

**Files:**
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Test: `tests/test_search_strategy.py`

**Step 1: 实现关键词 prompt builder**

在 `llm/prompts.py` 增加搜索关键词生成 prompt，要求输出：

```json
{"queries": ["关键词1", "关键词2"]}
```

**Step 2: 实现 SearchStrategy 的 query 生成方法**

添加最小实现：
- 调 `llm_service.complete_structured_task()`
- 解析 JSON
- 清洗 query
- 坏 JSON 时回退到本地 query

**Step 3: 运行针对性测试**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_search_strategy.py -q`

Expected: 至少 query 相关测试转绿。

**Step 4: 提交**

```bash
git add src/openbiliclaw/discovery/strategies/strategies.py src/openbiliclaw/llm/prompts.py tests/test_search_strategy.py
git commit -m "feat: add search query generation"
```

### Task 3: 实现搜索执行、去重与映射

**Files:**
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Test: `tests/test_search_strategy.py`
- Test: `tests/test_discovery_engine.py`

**Step 1: 实现 search 执行和结果映射**

实现：
- 每个 query 调 `bilibili_client.search()`
- 结果转 `DiscoveredContent`
- 缺失 `bvid` 的项跳过

**Step 2: 实现全局按 `bvid` 去重和 limit 截断**

保证多个 query 的重复结果只保留一份。

**Step 3: 让 ContentDiscoveryEngine 能注册并运行 SearchStrategy**

只做必要适配，避免大改 engine。

**Step 4: 运行发现层测试**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_search_strategy.py tests/test_discovery_engine.py -q`

Expected: PASS

**Step 5: 提交**

```bash
git add src/openbiliclaw/discovery/strategies/strategies.py src/openbiliclaw/discovery/engine.py tests/test_search_strategy.py tests/test_discovery_engine.py
git commit -m "feat: execute search strategy discovery"
```

### Task 4: 文档同步与全量验证

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/changelog.md`

**Step 1: 更新任务状态**

将 `5.1 搜索策略` 的已完成项标记为完成。

**Step 2: 追加 changelog**

记录 `SearchStrategy`、query 生成、去重映射、engine 集成。

**Step 3: 运行全量验证**

Run:
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`
- `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected:
- Ruff: `All checks passed!`
- MyPy: `Success: no issues found ...`
- Pytest: 全部通过

**Step 4: 提交**

```bash
git add docs/v0.1-todolist.md docs/changelog.md
git commit -m "docs: update m51 search strategy status"
```
