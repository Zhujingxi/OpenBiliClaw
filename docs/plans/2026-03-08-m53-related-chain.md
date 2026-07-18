# 5.3 相关推荐链策略 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现一个可独立运行的 `RelatedChainStrategy`，能从近期高价值视频种子出发沿相关推荐链发现并筛选内容。

**Architecture:** `RelatedChainStrategy` 先从事件层提取视频种子，再按需用偏好线索和其它策略结果补种子，随后调用 `get_related_videos()` 扩展有限深度的相关推荐链，并统一复用 `ContentDiscoveryEngine.evaluate_content()` 做相关性评分和过滤。

**Tech Stack:** Python 3.13, asyncio, pytest, mypy, Ruff, Typer-free service layer, existing Bilibili API client and discovery engine.

---

### Task 1: 为事件种子选择写失败测试

**Files:**
- Modify: `tests/test_related_chain_strategy.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`

**Step 1: Write the failing test**

新增测试，验证 `RelatedChainStrategy` 会优先从最近事件中提取带 `bvid` 的视频种子。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_related_chain_strategy.py::test_related_chain_uses_event_seeds_first -q`

Expected: FAIL because `RelatedChainStrategy` is still a stub.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/discovery/strategies/strategies.py` 中为 `RelatedChainStrategy` 添加依赖注入和事件种子选择辅助方法。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_related_chain_strategy.py src/openbiliclaw/discovery/strategies/strategies.py
git commit -m "feat: add event seed selection for related discovery"
```

### Task 2: 为偏好补种子和策略兜底写失败测试

**Files:**
- Modify: `tests/test_related_chain_strategy.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`

**Step 1: Write the failing test**

新增测试，验证事件种子不足时会按顺序走偏好补种子和 `SearchStrategy` / `TrendingStrategy` 兜底。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_related_chain_strategy.py::test_related_chain_falls_back_to_seed_strategies -q`

Expected: FAIL because fallback seed sources are not implemented.

**Step 3: Write minimal implementation**

实现偏好补种子和可选策略兜底逻辑，控制种子数量上限。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_related_chain_strategy.py src/openbiliclaw/discovery/strategies/strategies.py
git commit -m "feat: add fallback seed sources for related strategy"
```

### Task 3: 为相关推荐扩展和去重写失败测试

**Files:**
- Modify: `tests/test_related_chain_strategy.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`

**Step 1: Write the failing test**

新增测试，验证策略会调用 `get_related_videos()`，排除原始种子并按 `bvid` 去重。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_related_chain_strategy.py::test_related_chain_fetches_and_dedupes_related_videos -q`

Expected: FAIL because discovery still returns an empty list.

**Step 3: Write minimal implementation**

实现相关推荐拉取、候选映射和内部去重。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_related_chain_strategy.py src/openbiliclaw/discovery/strategies/strategies.py
git commit -m "feat: expand related recommendation chains"
```

### Task 4: 为评分过滤和错误容错写失败测试

**Files:**
- Modify: `tests/test_related_chain_strategy.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Modify: `src/openbiliclaw/discovery/engine.py`

**Step 1: Write the failing test**

新增测试，验证低分内容会被过滤、单个种子 related 请求失败不会中断整体。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_related_chain_strategy.py::test_related_chain_filters_by_score_and_tolerates_failures -q`

Expected: FAIL because scoring/filtering path is not wired.

**Step 3: Write minimal implementation**

接通 `ContentDiscoveryEngine.evaluate_content()`，实现阈值过滤和异常容错。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_related_chain_strategy.py src/openbiliclaw/discovery/strategies/strategies.py src/openbiliclaw/discovery/engine.py
git commit -m "feat: score and filter related chain results"
```

### Task 5: 回归 DiscoveryEngine 集成

**Files:**
- Modify: `tests/test_discovery_engine.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`

**Step 1: Write the failing test**

新增集成型单测，验证注册 `RelatedChainStrategy` 后 `ContentDiscoveryEngine.discover()` 可返回相关推荐链结果。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_discovery_engine.py::test_discovery_engine_runs_related_chain_strategy -q`

Expected: FAIL because strategy is not yet fully wired.

**Step 3: Write minimal implementation**

补齐必要的小型适配，确保 engine 能注册并运行 `RelatedChainStrategy`。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_discovery_engine.py src/openbiliclaw/discovery/strategies/strategies.py
git commit -m "feat: wire related chain into discovery engine"
```

### Task 6: 更新文档

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/changelog.md`

**Step 1: Update task status**

把 `5.3` 的 checklist 从 `[ ]` 更新为 `[x]`，并在模块文档中补 `RelatedChainStrategy` 的职责、种子来源和运行边界。

**Step 2: Verify docs**

Run: `rg -n "5\\.3|RelatedChainStrategy|相关推荐链" docs/v0.1-todolist.md docs/modules/discovery.md docs/changelog.md`

Expected: updated references appear in all three files.

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/discovery.md docs/changelog.md
git commit -m "docs: update related chain discovery docs"
```

### Task 7: 全量验证

**Files:**
- Verify only

**Step 1: Run Ruff**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

**Step 2: Run mypy**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`

Expected: `Success: no issues found ...`

**Step 3: Run pytest**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected: full suite passes.

**Step 4: Commit final fixups if needed**

If verification required any small fixes, commit them with a focused message before handing off.
