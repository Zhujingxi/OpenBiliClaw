from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path


def _load_build_module():
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / "packaging" / "build.py"
    spec = importlib.util.spec_from_file_location("openbiliclaw_packaging_build", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_module = _load_build_module()


def test_make_archive_name_includes_platform_and_version() -> None:
    assert build_module.make_archive_name("v0.1.1", "macos") == "OpenBiliClaw-macos-v0.1.1.zip"


def test_make_archive_name_strips_backend_release_channel_prefix() -> None:
    assert (
        build_module.make_archive_name("backend-v0.1.3", "windows")
        == "OpenBiliClaw-windows-v0.1.3.zip"
    )


def test_make_bundle_version_strips_backend_release_channel_prefix() -> None:
    assert build_module.make_bundle_version("backend-v0.1.3") == "0.1.3"


def test_build_pyinstaller_install_command_falls_back_to_uv_when_pip_missing() -> None:
    assert build_module.build_pyinstaller_install_command(
        pip_available=False,
        uv_executable="/usr/local/bin/uv",
    ) == ["/usr/local/bin/uv", "pip", "install", "pyinstaller"]


def test_find_packaged_root_prefers_app_bundle_on_macos(tmp_path: Path) -> None:
    app_bundle = tmp_path / "OpenBiliClaw.app"
    app_bundle.mkdir()
    package_dir = tmp_path / "OpenBiliClaw"
    package_dir.mkdir()

    resolved = build_module.find_packaged_root(tmp_path, platform_name="Darwin")

    assert resolved == app_bundle


def test_create_archive_writes_zip_with_packaged_root_contents(tmp_path: Path) -> None:
    packaged_root = tmp_path / "OpenBiliClaw"
    packaged_root.mkdir()
    (packaged_root / "config.example.toml").write_text("language = 'zh'\n", encoding="utf-8")

    archive_path = build_module.create_archive(
        packaged_root=packaged_root,
        output_dir=tmp_path / "release",
        version="v0.1.1",
        target="windows",
    )

    assert archive_path.name == "OpenBiliClaw-windows-v0.1.1.zip"
    assert archive_path.exists()

    with zipfile.ZipFile(archive_path) as archive:
        assert "OpenBiliClaw/config.example.toml" in archive.namelist()
