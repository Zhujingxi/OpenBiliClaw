"""Tests for boot autostart runtime helpers."""

import plistlib
import sys
from pathlib import Path

import pytest

from openbiliclaw.config import Config, save_config


def test_active_env_managed_inputs_detects_known_external_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime.autostart.guards import active_env_managed_inputs

    cfg = Config()
    cfg.sources.douyin.cookie_env = "CUSTOM_DOUYIN_COOKIE"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", "/tmp/openbiliclaw")
    monkeypatch.setenv("OPENBILICLAW_LLM_DEFAULT_PROVIDER", "ollama")
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD", "secret")
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-key")
    monkeypatch.setenv("CUSTOM_DOUYIN_COOKIE", "sid=1")

    assert active_env_managed_inputs(cfg) == [
        "CUSTOM_DOUYIN_COOKIE",
        "GOOGLE_API_KEY",
        "OPENBILICLAW_API_AUTH_PASSWORD",
        "OPENBILICLAW_LLM_DEFAULT_PROVIDER",
    ]


def test_active_env_managed_inputs_ignores_empty_or_project_root_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime.autostart.guards import active_env_managed_inputs

    cfg = Config()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", "/tmp/openbiliclaw")
    monkeypatch.setenv("GEMINI_API_KEY", "")

    assert active_env_managed_inputs(cfg) == []


def test_autostart_shadowed_detects_config_local_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime.autostart.guards import autostart_shadowed

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = Config()
    cfg.autostart.enabled = False
    save_config(cfg, autostart_authoritative=True)
    (tmp_path / "config.local.toml").write_text(
        "[autostart]\nenabled = true\n",
        encoding="utf-8",
    )

    assert autostart_shadowed(False) is True


def test_autostart_shadowed_false_when_effective_matches_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime.autostart.guards import autostart_shadowed

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = Config()
    cfg.autostart.enabled = True
    save_config(cfg, autostart_authoritative=True)

    assert autostart_shadowed(True) is False


def test_build_launch_spec_uses_python_module_and_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime.autostart.command import build_launch_spec

    ollama_bin = tmp_path / "bin" / "ollama"
    ollama_bin.parent.mkdir()
    ollama_bin.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: str(ollama_bin) if name == "ollama" else None)

    spec = build_launch_spec(Config())

    assert spec.argv == [sys.executable, "-m", "openbiliclaw.cli", "start"]
    assert spec.working_dir == tmp_path
    assert spec.env["OPENBILICLAW_PROJECT_ROOT"] == str(tmp_path)
    assert str(ollama_bin.parent) in spec.env["PATH"].split(":")


def test_resolve_pythonw_falls_back_when_missing(tmp_path: Path) -> None:
    from openbiliclaw.runtime.autostart.command import resolve_pythonw

    python_exe = tmp_path / "python.exe"
    python_exe.write_text("", encoding="utf-8")

    assert resolve_pythonw(python_exe) == python_exe


def test_unsupported_autostart_status_has_none_mechanism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import autostart

    monkeypatch.setattr(autostart.sys, "platform", "aix")
    monkeypatch.setattr(autostart.docker_runtime, "is_running_in_container", lambda: False)

    status = autostart.status()

    assert status.supported is False
    assert status.registered is False
    assert status.platform == "aix"
    assert status.mechanism == "none"
    assert status.reason == "unsupported_platform"


def test_docker_autostart_status_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import autostart

    monkeypatch.setattr(autostart.docker_runtime, "is_running_in_container", lambda: True)

    status = autostart.status()

    assert status.supported is False
    assert status.mechanism == "none"
    assert status.reason == "unsupported_docker_runtime"


def test_macos_launch_agent_register_writes_plist_and_creates_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime.autostart.macos import MacOSLaunchAgentManager

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = Config()
    cfg.logging.directory = str(tmp_path / "logs")
    manager = MacOSLaunchAgentManager(home=tmp_path / "home")

    manager.register(cfg)

    plist_path = tmp_path / "home" / "Library" / "LaunchAgents" / "com.openbiliclaw.daemon.plist"
    payload = plistlib.loads(plist_path.read_bytes())
    assert manager.mechanism == "launchd"
    assert manager.is_registered() is True
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is False
    assert payload["ProgramArguments"] == [sys.executable, "-m", "openbiliclaw.cli", "start"]
    assert payload["WorkingDirectory"] == str(tmp_path)
    assert payload["EnvironmentVariables"]["OPENBILICLAW_PROJECT_ROOT"] == str(tmp_path)
    assert (tmp_path / "logs").is_dir()


def test_macos_launch_agent_unregister_is_idempotent(tmp_path: Path) -> None:
    from openbiliclaw.runtime.autostart.macos import MacOSLaunchAgentManager

    manager = MacOSLaunchAgentManager(home=tmp_path)

    manager.unregister()
    assert manager.is_registered() is False

    manager.register(Config())
    assert manager.is_registered() is True
    manager.unregister()
    manager.unregister()

    assert manager.is_registered() is False


class _FakeWinregKey:
    def __enter__(self) -> "_FakeWinregKey":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 1
    KEY_READ = 2
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def CreateKey(self, root: object, path: str) -> _FakeWinregKey:  # noqa: N802
        return _FakeWinregKey()

    def OpenKey(self, root: object, path: str, reserved: int, access: int) -> _FakeWinregKey:  # noqa: N802
        return _FakeWinregKey()

    def SetValueEx(  # noqa: N802
        self, key: _FakeWinregKey, name: str, reserved: int, reg_type: int, value: str
    ) -> None:
        self.values[name] = value

    def QueryValueEx(self, key: _FakeWinregKey, name: str) -> tuple[str, int]:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def DeleteValue(self, key: _FakeWinregKey, name: str) -> None:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_windows_run_register_writes_registry_and_pyw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime.autostart.windows import WindowsRunManager

    fake_winreg = _FakeWinreg()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = Config()
    cfg.data_dir = str(tmp_path / "data")
    manager = WindowsRunManager(winreg_module=fake_winreg)

    manager.register(cfg)

    script = tmp_path / "data" / "autostart" / "openbiliclaw-autostart.pyw"
    assert manager.mechanism == "windows_run"
    assert script.exists()
    assert "OPENBILICLAW_PROJECT_ROOT" in script.read_text(encoding="utf-8")
    assert "OpenBiliClaw" in fake_winreg.values
    assert str(script) in fake_winreg.values["OpenBiliClaw"]
    assert manager.is_registered() is True

    script.unlink()
    assert manager.is_registered() is False


def test_windows_run_unregister_cleans_registry_and_pyw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.runtime.autostart.windows import WindowsRunManager

    fake_winreg = _FakeWinreg()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = Config()
    cfg.data_dir = str(tmp_path / "data")
    manager = WindowsRunManager(winreg_module=fake_winreg)

    manager.register(cfg)
    manager.unregister()
    manager.unregister()

    assert "OpenBiliClaw" not in fake_winreg.values
    assert not (tmp_path / "data" / "autostart" / "openbiliclaw-autostart.pyw").exists()
    assert manager.is_registered() is False
