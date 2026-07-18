# M71 Chat Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 `openbiliclaw chat` 从 stub 改成交互式 REPL，对接 `SocraticDialogue`，让用户可以在终端进行连续的苏格拉底式对话。

**Architecture:** 只改 CLI 入口层，不动 `SocraticDialogue` 内核行为。先用测试锁住未初始化画像、单轮对话和退出路径，再把 `chat()` 接到 `SoulEngine` 与 `SocraticDialogue`，最后同步文档。

**Tech Stack:** Python 3.13, Typer, Rich, pytest, mypy, Ruff

---

### Task 1: Add failing CLI tests for chat command

**Files:**
- Modify: `tests/test_cli.py`

**Step 1: Write the failing tests**

在 `tests/test_cli.py` 增加 3 条测试：

```python
def test_chat_prints_init_guidance_when_profile_missing(...) -> None: ...
def test_chat_runs_single_turn_and_prints_reply(...) -> None: ...
def test_chat_exits_cleanly_on_exit_command(...) -> None: ...
```

断言重点：

- 缺画像：`exit_code == 1`，包含 `openbiliclaw init`
- 单轮消息：包含 `阿花：` 和 fake reply
- 输入 `exit`：正常退出并输出 `对话结束`

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "chat_" -v`

Expected: FAIL because `chat()` is still a placeholder command.

### Task 2: Implement interactive chat REPL

**Files:**
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write minimal implementation**

在 `src/openbiliclaw/cli.py` 中：

- 删除 `chat()` 的占位输出
- 读取 `SoulProfile`，未初始化时提示 `init`
- 构建 `SocraticDialogue`
- 输出标题与退出提示
- 用 `typer.prompt("你")` 或等价方式读取输入
- 输入 `exit` / `quit` / 空字符串时结束
- 每轮输出 `阿花：{reply}`
- `KeyboardInterrupt` / `EOFError` 视为正常退出，输出 `对话结束`

**Step 2: Run focused tests to verify they pass**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "chat_" -v`

Expected: PASS

**Step 3: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: add chat cli command"
```

### Task 3: Update documentation

**Files:**
- Modify: `docs/modules/cli.md`
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

同步：

- `docs/modules/cli.md`
  - 将 `chat` 从 `🔲 stub` 改为 `✅`
  - 增加命令示例和退出方式说明
- `docs/v0.1-todolist.md`
  - 简要注明 `chat` 已接通
- `docs/changelog.md`
  - 追加 `7.1 chat 命令补平` 条目

**Step 2: Review docs diff**

Run: `git diff -- docs/modules/cli.md docs/v0.1-todolist.md docs/changelog.md`

Expected: Only chat-related documentation changes.

**Step 3: Commit**

```bash
git add docs/modules/cli.md docs/v0.1-todolist.md docs/changelog.md
git commit -m "docs: update chat command status"
```

### Task 4: Run full verification

**Files:**
- Verify: `src/openbiliclaw/cli.py`
- Verify: `tests/test_cli.py`
- Verify: `docs/modules/cli.md`
- Verify: `docs/v0.1-todolist.md`
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
git add src/openbiliclaw/cli.py tests/test_cli.py docs/modules/cli.md docs/v0.1-todolist.md docs/changelog.md
git commit -m "fix: polish chat cli interaction"
```

**Step 5: Prepare branch for integration**

Run:

```bash
git status --short
git log --oneline --decorate -5
```

Expected: branch ready for review or merge.
