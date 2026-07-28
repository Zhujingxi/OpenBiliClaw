"""Backend helper CLIs must not open console windows on Windows.

A packaged Windows install runs the backend window-less, so every console
executable it spawns (``rdt``, ``git``, ``agent-browser``, ``taskkill`` …)
would otherwise pop a black terminal — users reported several flashing in a
row whenever a settings save re-ran the Reddit probes.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw import proc
from openbiliclaw.proc import CREATE_NO_WINDOW, no_window_kwargs

if TYPE_CHECKING:
    import pytest

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "openbiliclaw"

# Commands the user invoked from their own terminal: they already own a
# console and some of them prompt interactively, so hiding the window would
# break them instead of fixing anything.
_INTERACTIVE_MODULES = {
    "cli.py",
    "llm/codex_auth.py",
    "eval/optimizer.py",
}

_SPAWN_FUNCS = {"run", "Popen", "check_output", "call", "check_call"}

# Scope note: this scan covers ``src/`` only. ``packaging/entry.py`` (the
# desktop shell, which is the window-less parent) was reviewed by hand — its
# spawns are macOS-only (``osascript`` / ``open``) and the Windows branch uses
# ``os.startfile``, so it opens no console. Re-check it by hand if a
# cross-platform spawn is ever added there.


def test_rdt_credential_ttl_stays_under_rdt_cli_browser_refresh() -> None:
    """We must stop calling rdt before rdt would shell out to ``uv``.

    ``rdt_cli`` refreshes a credential older than its own TTL by spawning
    ``uv run --with browser-cookie3 …``. That grandchild is out of reach of
    ``no_window_kwargs()`` and would pop the console window this module
    exists to prevent, so our staleness gate has to fire first.
    """
    import pytest

    from openbiliclaw.sources import reddit_tasks

    rdt_auth = pytest.importorskip("rdt_cli.auth")

    assert reddit_tasks._RDT_CLI_BROWSER_REFRESH_TTL_SECONDS == rdt_auth._CREDENTIAL_TTL_SECONDS, (
        "rdt-cli changed its credential TTL; re-derive our margin"
    )
    assert reddit_tasks.RDT_CREDENTIAL_TTL_SECONDS < rdt_auth._CREDENTIAL_TTL_SECONDS


def test_source_auth_advertises_the_ttl_the_reddit_gate_enforces() -> None:
    """The settings-page badge must expire when we actually stop calling rdt.

    ``providers.py`` carried a literal copy of the 7-day TTL and kept
    advertising it after the gate moved 6h earlier — a green "凭据就绪" badge
    for six hours in which the backend had already given up on rdt.
    """
    from openbiliclaw.api.source_auth import providers
    from openbiliclaw.sources import reddit_tasks

    assert providers._rdt_ttl_seconds() == reddit_tasks.RDT_CREDENTIAL_TTL_SECONDS


def test_no_window_kwargs_is_empty_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proc.os, "name", "posix")
    assert no_window_kwargs() == {}


def test_no_window_kwargs_hides_console_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proc.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)
    assert no_window_kwargs() == {"creationflags": CREATE_NO_WINDOW}


def test_reddit_command_runner_hides_console_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``rdt`` / ``opencli`` probes were the ones users actually saw pop up."""
    from openbiliclaw.sources import reddit_tasks

    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.setattr(proc.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)
    monkeypatch.setattr(reddit_tasks, "_default_which", lambda _name: None)
    monkeypatch.setattr(reddit_tasks.subprocess, "run", fake_run)

    reddit_tasks._subprocess_run(["opencli", "daemon", "status"], timeout=5)

    assert captured["creationflags"] == CREATE_NO_WINDOW


def _spawn_calls(tree: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        value = node.func.value
        is_subprocess_module = isinstance(value, ast.Name) and value.id == "subprocess"
        if (attr in _SPAWN_FUNCS and is_subprocess_module) or attr in {
            "create_subprocess_exec",
            "create_subprocess_shell",
        }:
            calls.append(node)
    return calls


def _hides_window(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "creationflags":
            return True
        # ``**no_window_kwargs()``
        if (
            keyword.arg is None
            and isinstance(keyword.value, ast.Call)
            and isinstance(keyword.value.func, ast.Name)
            and keyword.value.func.id == "no_window_kwargs"
        ):
            return True
    return False


def _posix_only_calls(tree: ast.AST) -> set[int]:
    """Spawn calls in the ``else`` of an ``os.name == "nt"`` branch.

    Those never run on Windows, so they need no flag — the sibling Windows
    branch (the ``if`` body) is still checked.
    """
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        test_source = ast.dump(node.test)
        if "os" not in test_source and "platform" not in test_source:
            continue
        if not any(
            isinstance(child, ast.Attribute) and child.attr in {"name", "platform"}
            for child in ast.walk(node.test)
        ):
            continue
        for branch in node.orelse:
            exempt.update(call.lineno for call in _spawn_calls(branch))
    return exempt


def test_every_backend_spawn_site_hides_the_console_window() -> None:
    """Guard the whole backend, not only the sites fixed once by hand."""
    offenders: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(_SRC_ROOT).as_posix()
        if relative in _INTERACTIVE_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        posix_only = _posix_only_calls(tree)
        for call in _spawn_calls(tree):
            if call.lineno not in posix_only and not _hides_window(call):
                offenders.append(f"{relative}:{call.lineno}")

    assert offenders == [], (
        "these process spawns would open a console window on Windows; "
        "add **no_window_kwargs(): " + ", ".join(offenders)
    )
