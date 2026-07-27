"""Real Windows HKCU Run and frozen-bundle autostart lifecycle tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.runtime import autostart

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows HKCU")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "OpenBiliClaw"
_FROZEN_EXE_ENV = "OPENBILICLAW_FROZEN_EXE"


def _winreg() -> Any:
    import winreg

    return winreg


def _read_run_value() -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, _VALUE_NAME)
    except FileNotFoundError:
        return None
    return str(value)


def _write_run_value(value: str) -> None:
    winreg = _winreg()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, value)


def _delete_run_value() -> None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
    except FileNotFoundError:
        pass


@pytest.fixture(autouse=True)
def _preserve_real_run_value() -> Iterator[None]:
    """Never leak the E2E value or destroy a developer's existing registration."""
    previous = _read_run_value()
    _delete_run_value()
    try:
        yield
    finally:
        _delete_run_value()
        if previous is not None:
            _write_run_value(previous)


def _compile_marker_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "OpenBiliClaw.exe"
    source = """
using System;
using System.IO;
public static class Program {
    public static void Main(string[] args) {
        var marker = Environment.GetEnvironmentVariable("OBC_AUTOSTART_E2E_MARKER");
        if (!String.IsNullOrWhiteSpace(marker)) {
            File.WriteAllText(marker, String.Join("|", args));
        }
    }
}
"""
    command = (
        f"Add-Type -TypeDefinition @'\n{source}\n'@ "
        f"-OutputAssembly '{executable}' -OutputType ConsoleApplication"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    assert executable.exists()
    return executable


def _run_registered_command(value: str, *, marker: Path | None = None) -> None:
    env = os.environ.copy()
    if marker is not None:
        env["OBC_AUTOSTART_E2E_MARKER"] = str(marker)
    subprocess.run(value, check=True, env=env, timeout=120)


def _manager_for(executable: Path):
    from openbiliclaw.runtime.autostart.windows import WindowsRunManager

    return WindowsRunManager(frozen=True, executable=executable)


def test_real_hkcu_direct_registration_launches_and_unregisters(tmp_path: Path) -> None:
    executable = _compile_marker_executable(tmp_path)
    marker = tmp_path / "direct.marker"
    manager = _manager_for(executable)

    manager.register(Config())

    expected = f'"{executable}"'
    assert _read_run_value() == expected
    _run_registered_command(expected, marker=marker)
    assert marker.read_text(encoding="utf-8") == ""

    manager.unregister()
    assert _read_run_value() is None


def test_real_hkcu_legacy_missing_script_still_launches_then_reconcile_removes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _compile_marker_executable(tmp_path)
    marker = tmp_path / "legacy.marker"
    missing_script = tmp_path / "missing" / "openbiliclaw-autostart.pyw"
    legacy_value = f'"{executable}" "{missing_script}"'
    manager = _manager_for(executable)
    _write_run_value(legacy_value)

    assert manager.is_registered() is True
    _run_registered_command(legacy_value, marker=marker)
    assert marker.read_text(encoding="utf-8") == str(missing_script)

    cfg = Config()
    cfg.autostart.enabled = False
    monkeypatch.setattr(autostart, "get_manager", lambda: manager)

    assert autostart.reconcile(cfg) is None
    assert _read_run_value() is None


def test_real_hkcu_enabled_legacy_registration_migrates_to_direct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _compile_marker_executable(tmp_path)
    legacy_script = tmp_path / "openbiliclaw-autostart.pyw"
    legacy_script.write_text("", encoding="utf-8")
    _write_run_value(f'"{executable}" "{legacy_script}"')
    manager = _manager_for(executable)
    cfg = Config()
    cfg.autostart.enabled = True
    monkeypatch.setattr(autostart, "get_manager", lambda: manager)

    assert autostart.reconcile(cfg) is None
    assert _read_run_value() == f'"{executable}"'
    assert legacy_script.exists() is False


def test_real_hkcu_disabled_reconcile_removes_broken_hidden_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "missing" / "OpenBiliClaw.exe"
    missing_script = tmp_path / "missing" / "openbiliclaw-autostart.pyw"
    _write_run_value(f'"{executable}" "{missing_script}"')
    manager = _manager_for(executable)
    cfg = Config()
    cfg.autostart.enabled = False
    monkeypatch.setattr(autostart, "get_manager", lambda: manager)

    assert manager.is_registered() is False
    assert autostart.reconcile(cfg) is None
    assert _read_run_value() is None


@pytest.mark.skipif(not os.environ.get(_FROZEN_EXE_ENV), reason="requires built desktop bundle")
def test_real_frozen_bundle_covers_enabled_and_disabled_legacy_upgrade(tmp_path: Path) -> None:
    """Run the real PyInstaller executable through the exact HKCU command shape."""
    executable = Path(os.environ[_FROZEN_EXE_ENV]).resolve()
    assert executable.exists()
    profile = tmp_path / "profile"
    env = os.environ.copy()
    env.pop(_FROZEN_EXE_ENV, None)
    env["OPENBILICLAW_PROJECT_ROOT"] = str(profile)
    env["OPENBILICLAW_SELFTEST"] = "1"

    # First self-test seeds a valid packaged profile with autostart disabled.
    subprocess.run([str(executable)], check=True, env=env, timeout=180)
    config_path = profile / "config.toml"
    cfg = load_config(config_path)
    cfg.autostart.enabled = True
    save_config(cfg, config_path, autostart_authoritative=True)

    # Enabled + no item: the real frozen entry registers the executable directly.
    subprocess.run([str(executable)], check=True, env=env, timeout=180)
    direct_value = f'"{executable}"'
    assert _read_run_value() == direct_value

    # Enabled + historical two-path item: launching it still runs the app, whose
    # startup reconciliation migrates the registry value to direct format.
    missing_script = profile / "data" / "autostart" / "missing.pyw"
    legacy_value = f'"{executable}" "{missing_script}"'
    _write_run_value(legacy_value)
    subprocess.run(legacy_value, check=True, env=env, timeout=180)
    assert _read_run_value() == direct_value

    # Disabled + historical item: this login may already have launched once, but
    # startup removes the Run value so every later login stays stopped.
    cfg = load_config(config_path)
    cfg.autostart.enabled = False
    save_config(cfg, config_path, autostart_authoritative=True)
    _write_run_value(legacy_value)
    subprocess.run(legacy_value, check=True, env=env, timeout=180)
    assert _read_run_value() is None
