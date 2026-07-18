# M71 Discover Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 `openbiliclaw discover` 从 stub 改成真实命令，读取画像、执行内容发现、展示发现摘要和预览，并保持 discovery 缓存链路闭环。

**Architecture:** 只改 CLI 编排和展示层，不动 `ContentDiscoveryEngine` 内部逻辑。先用测试锁住三条关键路径，再把 `discover()` 接到现有的 `SoulEngine` 与 `ContentDiscoveryEngine`，最后同步文档。

**Tech Stack:** Python 3.13, Typer, Rich, pytest, mypy, Ruff

---

### Task 1: Add failing CLI tests for discover command

**Files:**
- Modify: `tests/test_cli.py`

**Step 1: Write the failing tests**

在 `tests/test_cli.py` 增加 3 条测试：

```python
def test_discover_prints_init_guidance_when_profile_missing(...) -> None: ...
def test_discover_reports_empty_results(...) -> None: ...
def test_discover_displays_preview_rows(...) -> None: ...
```

断言重点：

- 未初始化画像：`exit_code == 1`，包含 `openbiliclaw init`
- 空结果：`exit_code == 0`，包含“没有发现到新内容”
- 成功：包含页面标题、发现条数、标题、UP 主、来源策略、相关性分数

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "discover_" -v`

Expected: FAIL because `discover()` is still a placeholder command.

**Step 3: Commit**

不要在这一步提交；先等实现转绿。

### Task 2: Implement discover command behavior

**Files:**
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write minimal implementation**

在 `src/openbiliclaw/cli.py` 中：

- 删除 `discover()` 的占位输出
- 构建 `SoulEngine` 并调用 `get_profile()`
- 构建 `ContentDiscoveryEngine`
- 调用 `discover(profile, limit=30)`
- 复用现有 Rich helper 输出：
  - 页面标题：`本次内容发现`
  - 摘要表：发现条数、缓存状态
  - 前 5 条预览
- 对 `SoulProfileNotInitializedError` 给出 init 引导
- 对空结果给出统一 info 状态块

**Step 2: Run focused tests to verify they pass**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "discover_" -v`

Expected: PASS

**Step 3: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: add discover cli command"
```

### Task 3: Update documentation

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

同步：

- `docs/v0.1-todolist.md`
  - 将 `openbiliclaw discover` 视为 `7.1` 已完成命令
- `docs/modules/cli.md`
  - 把 `discover` 从 `🔲 stub` 改为 `✅`
  - 增加 `openbiliclaw discover` 的命令说明与示例
- `docs/changelog.md`
  - 追加“补平 7.1 discover 命令”的变更记录

**Step 2: Review docs diff**

Run: `git diff -- docs/v0.1-todolist.md docs/modules/cli.md docs/changelog.md`

Expected: Only discover-related documentation changes.

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: update discover command status"
```

### Task 4: Run full verification

**Files:**
- Verify: `src/openbiliclaw/cli.py`
- Verify: `tests/test_cli.py`
- Verify: `docs/v0.1-todolist.md`
- Verify: `docs/modules/cli.md`
- Verify: `docs/changelog.md`

**Step 1: Run Ruff**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

**Step 2: Run mypy**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`

Expected: `Success: no issues found ...`

**Step 3: Run pytest**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected: All tests pass.

**Step 4: Commit any remaining fixups**

如果验证中出现小修复，单独提交：

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py docs/v0.1-todolist.md docs/modules/cli.md docs/changelog.md
git commit -m "fix: polish discover cli output"
```

**Step 5: Prepare branch for integration**

Run:

```bash
git status --short
git log --oneline --decorate -5
```

Expected: branch ready for review or merge.
