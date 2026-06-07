"""Tests for the desktop entry point's data-location + migration logic.

``packaging/entry.py`` is not part of the importable package, so load it by
path (mirroring ``test_packaging_build.py``). The risky behaviour here is moving
user data out of the install directory on upgrade — cover it directly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_entry_module():
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / "packaging" / "entry.py"
    spec = importlib.util.spec_from_file_location("openbiliclaw_packaging_entry", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = _load_entry_module()


# --------------------------------------------------------------------------- #
# _user_data_root_for — per-OS conventional location, independent of install dir
#
# Use the pure resolver with injected params: monkeypatching the real os.name to
# "nt" would make pathlib try (and fail) to build a WindowsPath on POSIX CI.
# Path comparisons stay internally consistent because both sides construct paths
# the same way on the host.
# --------------------------------------------------------------------------- #


def test_user_data_root_windows_uses_localappdata() -> None:
    root = entry._user_data_root_for(
        "nt",
        "win32",
        Path(r"C:\Users\tester"),
        {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
    )

    assert root == Path(r"C:\Users\tester\AppData\Local") / "OpenBiliClaw"


def test_user_data_root_windows_falls_back_when_localappdata_missing() -> None:
    root = entry._user_data_root_for("nt", "win32", Path(r"C:\Users\tester"), {})

    assert root == Path(r"C:\Users\tester") / "AppData" / "Local" / "OpenBiliClaw"


def test_user_data_root_macos_uses_application_support() -> None:
    root = entry._user_data_root_for("posix", "darwin", Path("/Users/tester"), {})

    assert root == Path("/Users/tester/Library/Application Support/OpenBiliClaw")


def test_user_data_root_linux_prefers_xdg() -> None:
    root = entry._user_data_root_for(
        "posix", "linux", Path("/home/tester"), {"XDG_DATA_HOME": "/home/tester/.local/share"}
    )

    assert root == Path("/home/tester/.local/share/OpenBiliClaw")


def test_user_data_root_linux_falls_back_without_xdg() -> None:
    root = entry._user_data_root_for("posix", "linux", Path("/home/tester"), {})

    assert root == Path("/home/tester/.local/share/OpenBiliClaw")


def test_user_data_root_delegates_to_pure_resolver() -> None:
    # The thin wrapper just feeds real os/sys/home/environ to the pure resolver.
    assert entry._user_data_root() == entry._user_data_root_for(
        entry.os.name, entry.sys.platform, Path.home(), entry.os.environ
    )


# --------------------------------------------------------------------------- #
# _resolve_runtime_paths — onedir keeps data out of the install dir
# --------------------------------------------------------------------------- #


def test_resolve_runtime_paths_dev_fallback_uses_repo_root(monkeypatch) -> None:
    monkeypatch.delenv("OPENBILICLAW_PROJECT_ROOT", raising=False)
    project_root, bundled = entry._resolve_runtime_paths()

    repo_root = Path(entry.__file__).resolve().parent.parent
    assert project_root == repo_root
    assert bundled == repo_root


def test_resolve_runtime_paths_onedir_splits_data_from_install_dir(
    monkeypatch, tmp_path: Path
) -> None:
    # Simulate a frozen onedir launch. We can't force os.name="nt" on POSIX CI
    # (breaks pathlib), so assert the *split* property — data root is separate
    # from the install dir — using whatever _user_data_root() the host returns.
    monkeypatch.delenv("OPENBILICLAW_PROJECT_ROOT", raising=False)
    install_dir = tmp_path / "Programs" / "OpenBiliClaw"
    install_dir.mkdir(parents=True)
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "executable", str(install_dir / "OpenBiliClaw"))

    project_root, bundled = entry._resolve_runtime_paths()

    # User data lives in the per-user root, NOT next to the executable.
    assert bundled == install_dir
    assert project_root == entry._user_data_root()
    assert project_root != bundled


def test_resolve_runtime_paths_honors_project_root_override(monkeypatch, tmp_path: Path) -> None:
    # An explicit OPENBILICLAW_PROJECT_ROOT relocates user data (portable installs
    # / isolated tests) while bundled resources still resolve from the package.
    override = tmp_path / "custom-data-root"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(override))

    project_root, _bundled = entry._resolve_runtime_paths()

    assert project_root == override


# --------------------------------------------------------------------------- #
# _migrate_legacy_install_dir_data — relocate old in-install-dir data
# --------------------------------------------------------------------------- #


def _seed_legacy_install(install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "config.toml").write_text("language = 'zh'\n", encoding="utf-8")
    (install_dir / "data").mkdir()
    (install_dir / "data" / "openbiliclaw.db").write_bytes(b"SQLite format 3\x00payload")
    (install_dir / "logs").mkdir()
    (install_dir / "logs" / "openbiliclaw.log").write_text("hello\n", encoding="utf-8")


def test_migrate_moves_config_data_and_logs(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)
    original_db = (install_dir / "data" / "openbiliclaw.db").read_bytes()

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    # Moved into the new root with contents intact...
    assert (project_root / "config.toml").read_text(encoding="utf-8") == "language = 'zh'\n"
    assert (project_root / "data" / "openbiliclaw.db").read_bytes() == original_db
    assert (project_root / "logs" / "openbiliclaw.log").exists()
    # ...and gone from the install dir (so upgrades/uninstall can't touch them).
    assert not (install_dir / "config.toml").exists()
    assert not (install_dir / "data").exists()
    assert not (install_dir / "logs").exists()


def test_migrate_skips_when_new_root_already_has_config(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)
    project_root.mkdir()
    (project_root / "config.toml").write_text("language = 'en'\n", encoding="utf-8")

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    # Existing config preserved; nothing clobbered or pulled from the install dir.
    assert (project_root / "config.toml").read_text(encoding="utf-8") == "language = 'en'\n"
    assert not (project_root / "data").exists()
    assert (install_dir / "config.toml").exists()  # left untouched


def test_migrate_skips_when_new_root_already_has_database(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)
    (project_root / "data").mkdir(parents=True)
    (project_root / "data" / "openbiliclaw.db").write_bytes(b"existing")

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    assert (project_root / "data" / "openbiliclaw.db").read_bytes() == b"existing"
    assert not (project_root / "config.toml").exists()


def test_migrate_does_not_clobber_partial_destination(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)
    # A logs/ already exists in the new root (but no config/db → migration runs).
    (project_root / "logs").mkdir(parents=True)
    (project_root / "logs" / "keep.log").write_text("keep\n", encoding="utf-8")

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    # config + data migrated; the pre-existing logs/ is left as-is (not overwritten).
    assert (project_root / "config.toml").exists()
    assert (project_root / "data" / "openbiliclaw.db").exists()
    assert (project_root / "logs" / "keep.log").read_text(encoding="utf-8") == "keep\n"
    assert (install_dir / "logs").exists()  # not moved (destination existed)


def test_migrate_noop_when_install_dir_equals_project_root(tmp_path: Path) -> None:
    install_dir = tmp_path / "same"
    _seed_legacy_install(install_dir)

    entry._migrate_legacy_install_dir_data(install_dir, install_dir)

    # Dev / same-dir layout: everything stays put, no nesting.
    assert (install_dir / "config.toml").exists()
    assert (install_dir / "data" / "openbiliclaw.db").exists()
    assert not (install_dir / "data" / "data").exists()


def test_migrate_noop_when_nothing_legacy(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    project_root = tmp_path / "userdata"

    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    # Fresh install: no legacy data, new root not even created by migration.
    assert not project_root.exists()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)

    entry._migrate_legacy_install_dir_data(install_dir, project_root)
    db_after_first = (project_root / "data" / "openbiliclaw.db").read_bytes()
    # Second run (now the install dir is empty) must be a clean no-op.
    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    assert (project_root / "data" / "openbiliclaw.db").read_bytes() == db_after_first


def test_migrate_survives_unmovable_entry(tmp_path: Path, monkeypatch) -> None:
    install_dir = tmp_path / "install"
    project_root = tmp_path / "userdata"
    _seed_legacy_install(install_dir)

    real_move = entry.shutil.move

    def _flaky_move(src: str, dst: str):
        if src.endswith("logs"):
            raise OSError("simulated lock")
        return real_move(src, dst)

    monkeypatch.setattr(entry.shutil, "move", _flaky_move)

    # Must not raise — a failed move degrades to leaving that entry behind.
    entry._migrate_legacy_install_dir_data(install_dir, project_root)

    assert (project_root / "config.toml").exists()
    assert (project_root / "data" / "openbiliclaw.db").exists()
    assert (install_dir / "logs").exists()  # the one that failed to move


# --------------------------------------------------------------------------- #
# System-tray desktop mode gating (Windows-only, frozen-only)
# --------------------------------------------------------------------------- #


def test_should_use_tray_false_when_not_frozen() -> None:
    # The test process isn't frozen → tray mode is never selected (dev keeps its
    # foreground/console server).
    assert entry._should_use_tray() is False


def test_should_use_tray_false_on_unsupported_platform(monkeypatch) -> None:
    # Frozen but neither Windows nor macOS (e.g. Linux) → no tray, regardless of
    # whether pystray is importable. Short-circuits before the import check.
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.os, "name", "posix")
    monkeypatch.setattr(entry.sys, "platform", "linux")

    assert entry._should_use_tray() is False


def test_redirect_output_to_logfile_noop_when_not_frozen(tmp_path: Path) -> None:
    # Dev (not frozen) keeps its real stdout/stderr — the redirect is a no-op and
    # must not create a log file.
    assert entry._redirect_output_to_logfile(tmp_path) is None
    assert not (tmp_path / "logs" / "desktop.log").exists()


def test_close_splash_noop_without_pyi_splash() -> None:
    # Dev / non-splash builds have no ``pyi_splash`` module — closing the splash
    # must be a silent no-op, never raise.
    entry._close_splash()  # must not raise


def test_notify_starting_noop_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not frozen (tests) → no OS notification subprocess is spawned regardless of
    # platform, so the test suite never pops a real notification.
    calls: list[object] = []
    monkeypatch.setattr(entry.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    entry._notify_starting()
    assert calls == []


def test_notify_starting_noop_on_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even frozen, only macOS uses the notification path (Windows has the splash).
    calls: list[object] = []
    monkeypatch.setattr(entry.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "platform", "win32")
    entry._notify_starting()
    assert calls == []


def test_notify_starting_fires_on_frozen_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(entry.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "platform", "darwin")
    entry._notify_starting()
    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[0] == "osascript"
    assert any("OpenBiliClaw" in str(part) for part in argv)


def test_redirect_output_writes_utf8_bom_on_fresh_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh desktop.log gets a UTF-8 BOM so Windows zh-CN viewers detect the
    # encoding instead of guessing GBK and rendering Chinese as mojibake.
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    real_out, real_err = entry.sys.stdout, entry.sys.stderr
    try:
        log_path = entry._redirect_output_to_logfile(tmp_path)
        stream = entry.sys.stdout  # the redirect installed this as stdout
    finally:
        entry.sys.stdout, entry.sys.stderr = real_out, real_err
    assert log_path is not None
    stream.close()
    assert log_path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_redirect_output_no_extra_bom_when_appending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Re-opening an existing (non-empty) log must NOT inject a BOM mid-file.
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    log = tmp_path / "logs" / "desktop.log"
    log.parent.mkdir(parents=True)
    log.write_text("existing line\n", encoding="utf-8")
    real_out, real_err = entry.sys.stdout, entry.sys.stderr
    try:
        entry._redirect_output_to_logfile(tmp_path)
        stream = entry.sys.stdout
    finally:
        entry.sys.stdout, entry.sys.stderr = real_out, real_err
    stream.close()
    data = log.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"existing line" in data


# --------------------------------------------------------------------------- #
# Single-instance lock (one running instance per data dir)
# --------------------------------------------------------------------------- #


def test_single_instance_lock_blocks_second_acquire(tmp_path: Path) -> None:
    # First launch wins the lock; a second launch on the same data dir is "busy"
    # (the OS treats the two open handles as independent even in one process), and
    # the slot frees once the first handle closes (== the owning process exits).
    status1, handle1 = entry._try_single_instance_lock(tmp_path)
    assert status1 == "acquired"
    assert handle1 is not None

    status2, handle2 = entry._try_single_instance_lock(tmp_path)
    assert status2 == "busy"
    assert handle2 is None

    handle1.close()  # owning instance exits → lock released

    status3, handle3 = entry._try_single_instance_lock(tmp_path)
    assert status3 == "acquired"
    handle3.close()


def test_single_instance_lock_separate_dirs_both_acquire(tmp_path: Path) -> None:
    # Different data dirs (portable installs / OPENBILICLAW_PROJECT_ROOT) may run
    # side by side — the lock is per data dir.
    root_a = tmp_path / "a"
    root_a.mkdir()
    root_b = tmp_path / "b"
    root_b.mkdir()

    status_a, handle_a = entry._try_single_instance_lock(root_a)
    status_b, handle_b = entry._try_single_instance_lock(root_b)

    assert status_a == "acquired"
    assert status_b == "acquired"
    handle_a.close()
    handle_b.close()


if __name__ == "__main__":  # pragma: no cover - convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
