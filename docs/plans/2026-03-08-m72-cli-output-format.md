# M72 CLI Output Format Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 统一 `OpenBiliClaw` CLI 的终端输出风格，用 Rich 把可用命令和 stub 命令都整理成清晰、稳定、非技术用户也能快速理解的展示。

**Architecture:** 只重构 `src/openbiliclaw/cli.py` 的展示层，不改业务流程。先抽一组最小公共渲染 helper，再把各命令切到统一样式，最后补回归测试和文档更新。

**Tech Stack:** Python 3.13, Typer, Rich, pytest, mypy, Ruff

---

### Task 1: Add shared CLI rendering helpers

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

在 `tests/test_cli.py` 新增或扩展断言，先锁住一个 stub 命令应展示统一占位态。例如：

```python
def test_discover_uses_placeholder_panel(runner: CliRunner) -> None:
    result = runner.invoke(app, ["discover"])
    assert "功能开发中" in result.stdout
    assert "内容发现" in result.stdout
```

**Step 2: Run test to verify it fails**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py::test_discover_uses_placeholder_panel -v`

Expected: FAIL because current output is still ad-hoc and helper does not exist yet.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/cli.py` 增加最小公共 helper：

- `_print_page_title(title: str, subtitle: str = "")`
- `_print_status_panel(kind: Literal["success", "warning", "error", "info", "stub"], title: str, body: str)`
- `_print_key_value_table(title: str, rows: list[tuple[str, str]])`
- `_print_placeholder(feature: str, next_step: str = "")`

控制 helper 数量，避免把 `cli.py` 做成小型 UI 框架。

**Step 4: Run test to verify it passes**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py::test_discover_uses_placeholder_panel -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: add shared cli rendering helpers"
```

### Task 2: Restyle stub commands with unified placeholder output

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing tests**

为 `start`、`discover`、`chat` 写输出测试：

```python
def test_start_uses_stub_output(runner: CliRunner) -> None: ...
def test_chat_uses_stub_output(runner: CliRunner) -> None: ...
```

断言至少包含：

- 功能名称
- “功能开发中”
- 一个明确的下一步提示

**Step 2: Run tests to verify they fail**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "stub_output or placeholder" -v`

Expected: FAIL

**Step 3: Write minimal implementation**

将这 3 个命令改为复用 `_print_placeholder(...)`，保持原有业务空壳不变，只统一展示。

**Step 4: Run tests to verify they pass**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "stub_output or placeholder" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: unify stub command output"
```

### Task 3: Restyle profile and recommend output

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing tests**

为 `profile` 和 `recommend` 增加更明确的结构断言：

```python
def test_profile_displays_section_titles(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None: ...
def test_recommend_displays_card_fields(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None: ...
```

断言包含：

- `profile`: `人格描述`、`核心特质`、`价值观`、`当前阶段`、`深层需求`
- `recommend`: 标题、`UP主`、`推荐理由`、`BV号`

**Step 2: Run tests to verify they fail**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "profile_displays_section_titles or recommend_displays_card_fields" -v`

Expected: FAIL because current output is plain stacked text.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/cli.py` 中：

- 为 `profile` 使用 section panel 或 grid table
- 为 `recommend` 增加推荐卡片 helper，例如 `_print_recommendation_card(item)`
- 保持原有数据读取和 presented 标记逻辑不变

**Step 4: Run tests to verify they pass**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "profile_displays_section_titles or recommend_displays_card_fields" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: restyle profile and recommendation output"
```

### Task 4: Restyle status-oriented commands

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing tests**

补以下命令的结构断言：

- `init`
- `feedback`
- `config-show`
- `auth status`
- `health-check`
- `browser status`

每个测试只锁住高信号文本和区块标题，不依赖具体 ANSI 颜色。

**Step 2: Run tests to verify they fail**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "init or feedback or config_show or auth_status or health_check or browser_status" -v`

Expected: At least one FAIL because outputs are not yet unified.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/cli.py` 中统一这些命令：

- `init`：标题、阶段标题、结果摘要、部分完成状态块
- `feedback`：成功/错误状态块
- `config-show`：键值表 + guidance 区块
- `auth status`：认证状态块 + 基本身份信息
- `health-check`：provider 状态表
- `browser status`：浏览器可用性状态块

必要时轻量重构现有 `_print_auth_status()`、`_print_browser_status()`，但不要改其业务判断。

**Step 4: Run tests to verify they pass**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -k "init or feedback or config_show or auth_status or health_check or browser_status" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: unify status command output"
```

### Task 5: Update docs for M72

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Step 1: Write docs changes**

更新：

- `docs/v0.1-todolist.md`：勾选 `7.2` 的输出格式条目
- `docs/modules/cli.md`：说明 CLI 已采用统一 Rich 输出风格，列出关键命令展示方式
- `docs/changelog.md`：追加 `7.2` 变更摘要

**Step 2: Review docs diff**

Run: `git diff -- docs/v0.1-todolist.md docs/modules/cli.md docs/changelog.md`

Expected: Only documentation changes for `7.2`.

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: update cli output format status"
```

### Task 6: Run full verification

**Files:**
- Verify: `src/openbiliclaw/cli.py`
- Verify: `tests/test_cli.py`
- Verify: `docs/v0.1-todolist.md`
- Verify: `docs/modules/cli.md`
- Verify: `docs/changelog.md`

**Step 1: Run Ruff**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

**Step 2: Run mypy**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`

Expected: `Success: no issues found ...`

**Step 3: Run pytest**

Run: `PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected: All tests pass.

**Step 4: Commit any remaining fixups**

如果验证中出现小修复，单独提交：

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py docs/v0.1-todolist.md docs/modules/cli.md docs/changelog.md
git commit -m "fix: polish cli output formatting"
```

**Step 5: Prepare branch for integration**

Run:

```bash
git status --short
git log --oneline --decorate -5
```

Expected: Only intentional files changed; branch ready for review or merge.
