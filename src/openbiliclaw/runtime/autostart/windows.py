"""Windows HKCU Run autostart manager."""

from __future__ import annotations

import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .command import build_launch_spec, resolve_pythonw

if TYPE_CHECKING:
    from openbiliclaw.config import Config

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "OpenBiliClaw"
_SCRIPT_NAME = "openbiliclaw-autostart.pyw"
_PACKAGED_EXE_NAME = "openbiliclaw.exe"


def _load_winreg() -> Any:
    import winreg

    return winreg


def _quote_windows_arg(value: Path | str) -> str:
    return f'"{value}"'


def _quoted_paths(value: str) -> list[Path]:
    return [Path(match) for match in re.findall(r'"([^"]+)"', value)]


def _paths_from_run_value(value: str) -> tuple[Path, Path] | None:
    paths = _quoted_paths(value)
    if len(paths) >= 2:
        return paths[0], paths[1]
    return None


def _script_from_run_value(value: str) -> Path | None:
    paths = _paths_from_run_value(value)
    if paths is None:
        return None
    return paths[1]


class WindowsRunManager:
    """Manage OpenBiliClaw in HKCU Run.

    Source installs use a ``pythonw`` + ``.pyw`` launcher. Frozen desktop
    installs register the packaged executable directly: passing the ``.pyw`` as
    an argument to a PyInstaller executable never made it interpret the script,
    and a missing script could therefore still launch the app while status
    incorrectly reported ``registered=False``.
    """

    mechanism = "windows_run"

    def __init__(
        self,
        *,
        winreg_module: Any | None = None,
        frozen: bool | None = None,
        executable: str | Path | None = None,
    ) -> None:
        self._winreg = winreg_module if winreg_module is not None else _load_winreg()
        self._frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        self._executable = Path(sys.executable if executable is None else executable)

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
        previous_value = self._run_value()
        if self._frozen:
            self._write_run_value(_quote_windows_arg(self._executable))
            legacy_script = _script_from_run_value(previous_value) if previous_value else None
            if legacy_script is not None and legacy_script.exists():
                with suppress(OSError):
                    legacy_script.unlink()
            return

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
        run_value = (
            f"{_quote_windows_arg(resolve_pythonw(self._executable))} "
            f"{_quote_windows_arg(script_path)}"
        )
        self._write_run_value(run_value)

    def unregister(self) -> None:
        value = self._run_value()
        self._delete_run_value()
        script_path = _script_from_run_value(value) if value else None
        if script_path is not None and script_path.exists():
            script_path.unlink()

    def refresh_if_needed(self, config: Config) -> bool:
        """Migrate a frozen legacy Run value to the direct executable format."""
        if not self._frozen:
            return False
        desired_value = _quote_windows_arg(self._executable)
        if self._run_value() == desired_value:
            return False
        self.register(config)
        return True

    def is_registered(self) -> bool:
        value = self._run_value()
        if not value:
            return False
        quoted_paths = _quoted_paths(value)
        if (
            quoted_paths
            and quoted_paths[0].name.casefold() == _PACKAGED_EXE_NAME
            and quoted_paths[0].exists()
        ):
            # Legacy frozen registrations included a second .pyw argument.
            # Windows still launches OpenBiliClaw.exe when that script is gone
            # because the packaged entry ignores argv, so the executable alone
            # is the effective registration.
            return True
        paths = _paths_from_run_value(value)
        if paths is None:
            return False
        executable_path, script_path = paths
        return executable_path.exists() and script_path.exists()
