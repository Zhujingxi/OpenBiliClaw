"""Windows HKCU Run autostart manager."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .command import build_launch_spec, resolve_pythonw

if TYPE_CHECKING:
    from openbiliclaw.config import Config

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "OpenBiliClaw"
_SCRIPT_NAME = "openbiliclaw-autostart.pyw"


def _load_winreg() -> Any:
    import winreg  # type: ignore[import-not-found]

    return winreg


def _quote_windows_arg(value: Path | str) -> str:
    return f'"{value}"'


def _script_from_run_value(value: str) -> Path | None:
    matches = re.findall(r'"([^"]+)"', value)
    if len(matches) >= 2:
        return Path(matches[1])
    return None


class WindowsRunManager:
    """Manage OpenBiliClaw in HKCU Run using a pythonw ``.pyw`` launcher."""

    mechanism = "windows_run"

    def __init__(self, *, winreg_module: Any | None = None) -> None:
        self._winreg = winreg_module if winreg_module is not None else _load_winreg()

    def _script_path(self, config: Config) -> Path:
        return config.data_path / "autostart" / _SCRIPT_NAME

    def _run_value(self) -> str | None:
        try:
            with self._winreg.OpenKey(
                self._winreg.HKEY_CURRENT_USER,
                _RUN_KEY,
                0,
                self._winreg.KEY_READ,
            ) as key:
                value, _value_type = self._winreg.QueryValueEx(key, _VALUE_NAME)
        except FileNotFoundError:
            return None
        return str(value)

    def _write_run_value(self, value: str) -> None:
        with self._winreg.CreateKey(self._winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            self._winreg.SetValueEx(key, _VALUE_NAME, 0, self._winreg.REG_SZ, value)

    def _delete_run_value(self) -> None:
        try:
            with self._winreg.OpenKey(
                self._winreg.HKEY_CURRENT_USER,
                _RUN_KEY,
                0,
                self._winreg.KEY_SET_VALUE,
            ) as key:
                self._winreg.DeleteValue(key, _VALUE_NAME)
        except FileNotFoundError:
            return

    def register(self, config: Config) -> None:
        spec = build_launch_spec(config)
        script_path = self._script_path(config)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            "\n".join(
                [
                    "import os",
                    "import sys",
                    "",
                    f"for key, value in {spec.env!r}.items():",
                    "    os.environ[key] = value",
                    f"os.chdir({str(spec.working_dir)!r})",
                    'os.execv(sys.executable, [sys.executable, "-m", "openbiliclaw.cli", "start"])',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        run_value = f"{_quote_windows_arg(resolve_pythonw())} {_quote_windows_arg(script_path)}"
        self._write_run_value(run_value)

    def unregister(self) -> None:
        value = self._run_value()
        self._delete_run_value()
        script_path = _script_from_run_value(value) if value else None
        if script_path is not None and script_path.exists():
            script_path.unlink()

    def is_registered(self) -> bool:
        value = self._run_value()
        if not value:
            return False
        script_path = _script_from_run_value(value)
        return script_path is not None and script_path.exists()
