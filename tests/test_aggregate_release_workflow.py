"""Regression checks for the user-facing aggregate GitHub release."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def read_text(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def project_version() -> str:
    pyproject = tomllib.loads(read_text("pyproject.toml"))
    return str(pyproject["project"]["version"])


def test_aggregate_release_helper_updates_latest_release() -> None:
    script = read_text(".github/scripts/sync-aggregate-release.sh")

    assert "openbiliclaw-v" in script
    assert "pyproject.toml" in script
    assert "backend-v" in script
    assert "extension-v" in script
    assert "desktop-v" in script
    assert "gh release create" in script
    assert "gh release edit" in script
    assert "gh release upload" in script
    assert "--latest" in script
    assert "--clobber" in script


def test_aggregate_release_helper_prunes_stale_package_assets() -> None:
    script = read_text(".github/scripts/sync-aggregate-release.sh")

    assert "prune_existing_package_assets" in script
    assert "delete_existing_package_asset" in script
    assert "gh release delete-asset" in script
    assert 'grep -qi "not found"' in script
    assert "openbiliclaw-extension-v*.zip" in script
    assert "OpenBiliClaw-macos-v*.dmg" in script
    assert "OpenBiliClaw-windows-*-Setup.exe" in script


def test_aggregate_release_helper_only_lists_signed_firefox_xpi_when_asset_exists() -> None:
    script = read_text(".github/scripts/sync-aggregate-release.sh")

    expected_xpi_name_assignment = (
        'firefox_xpi_asset_name="openbiliclaw-extension-v${extension_version}-firefox.xpi"'
    )
    assert expected_xpi_name_assignment in script
    assert 'asset_name_seen "$firefox_xpi_asset_name"' in script
    # Fallback wording must state the XPI is absent (v0.3.153 readable copy).
    assert "no signed XPI in this release" in script
    unconditional_xpi_assignment = (
        'firefox_signed_asset_line="use '
        '\\`openbiliclaw-extension-v${extension_version}-firefox.xpi\\`"'
    )
    assert unconditional_xpi_assignment not in script


def test_aggregate_release_helper_does_not_backfill_previous_channel_assets(
    tmp_path: Path,
) -> None:
    version = project_version()
    stale_version = "0.0.1"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.log"
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail

            printf '%s\\n' "$*" >> "$GH_LOG"

            if [ "$1" = "release" ] && [ "$2" = "list" ]; then
              printf 'extension-v%s\\n' "$STALE_VERSION"
              printf 'desktop-v%s\\n' "$STALE_VERSION"
              exit 0
            fi

            if [ "$1" = "release" ] && [ "$2" = "view" ]; then
              tag="$3"
              case "$tag" in
                openbiliclaw-v*)
                  exit 0
                  ;;
                extension-v"$PROJECT_VERSION"|desktop-v"$PROJECT_VERSION")
                  exit 1
                  ;;
                *)
                  exit 0
                  ;;
              esac
            fi

            if [ "$1" = "release" ] && [ "$2" = "download" ]; then
              tag="$3"
              target_dir=""
              pattern=""
              while [ "$#" -gt 0 ]; do
                case "$1" in
                  --dir)
                    target_dir="$2"
                    shift 2
                    ;;
                  --pattern)
                    pattern="$2"
                    shift 2
                    ;;
                  *)
                    shift
                    ;;
                esac
              done
              mkdir -p "$target_dir"
              case "$tag:$pattern" in
                extension-v"$STALE_VERSION":openbiliclaw-extension-v*.zip)
                  touch "$target_dir/openbiliclaw-extension-v$STALE_VERSION.zip"
                  exit 0
                  ;;
                desktop-v"$STALE_VERSION":*.dmg)
                  touch "$target_dir/OpenBiliClaw-macos-v$STALE_VERSION-arm64.dmg"
                  exit 0
                  ;;
                desktop-v"$STALE_VERSION":*.exe)
                  touch "$target_dir/OpenBiliClaw-windows-$STALE_VERSION-Setup.exe"
                  exit 0
                  ;;
                *)
                  exit 1
                  ;;
              esac
            fi

            if [ "$1" = "release" ] && [ "$2" = "edit" ]; then
              exit 0
            fi

            if [ "$1" = "release" ] && [ "$2" = "upload" ]; then
              exit 0
            fi

            echo "unexpected gh command: $*" >&2
            exit 1
            """
        ),
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "CHANNEL": "backend",
            "GH_LOG": str(gh_log),
            "GITHUB_REPOSITORY": "whiteguo233/OpenBiliClaw",
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "PROJECT_VERSION": version,
            "RELEASE_TAG": f"backend-v{version}",
            "STALE_VERSION": stale_version,
        }
    )

    result = subprocess.run(
        ["bash", ".github/scripts/sync-aggregate-release.sh"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = gh_log.read_text(encoding="utf-8")
    assert f"release download extension-v{stale_version}" not in commands
    assert f"release download desktop-v{stale_version}" not in commands
    assert f"openbiliclaw-extension-v{stale_version}.zip" not in commands


def test_aggregate_release_python_picker_discovers_future_versioned_binary(
    tmp_path: Path,
) -> None:
    """``pick_python`` must accept any future ``python3.N`` interpreter.

    Regression for the review finding that the picker hard-coded
    ``python3.13 / 3.12 / 3.11`` and exited 1 on systems whose only suitable
    interpreter was ``python3.14``. This test installs fake ``python3`` (old)
    and fake ``python3.10`` (too old), ``python3.12`` (suitable),
    ``python3.14`` (suitable, newest) and a stray ``python3.13-config``
    (must never be picked) into an isolated PATH and verifies the script
    selects ``python3.14`` deterministically — independent of directory
    listing order. This is executable coverage, not a static string match.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Hermetic toolchain: the picker needs bash/awk/sort from PATH, but we
    # must not leak host ``python3.N`` interpreters into the test. Symlink
    # only the required tools into a dedicated directory and use that as the
    # sole non-fake PATH entry.
    toolchain = tmp_path / "toolchain"
    toolchain.mkdir()
    for tool in ("bash", "awk", "sort"):
        tool_path = shutil.which(tool)
        assert tool_path is not None, f"{tool} not found on host PATH"
        (toolchain / tool).symlink_to(tool_path)

    # Old unversioned python3 (simulates macOS system python3 = 3.9).
    fake_old_python3 = bin_dir / "python3"
    fake_old_python3.write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    fake_old_python3.chmod(0o755)

    # python3.10 — too old, must be skipped.
    fake_310 = bin_dir / "python3.10"
    fake_310.write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    fake_310.chmod(0o755)

    # python3.12 — suitable but not the newest on PATH.
    fake_312 = bin_dir / "python3.12"
    fake_312.write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    fake_312.chmod(0o755)

    # python3.14 — suitable and the newest; must be picked.
    fake_314 = bin_dir / "python3.14"
    fake_314.write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    fake_314.chmod(0o755)

    # Stray non-interpreter sharing the python3.N prefix; must never be
    # picked even though its version "passes" the check.
    stray = bin_dir / "python3.13-config"
    stray.write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    stray.chmod(0o755)

    # Minimal gh stub — the script invokes `gh release ...` after picking
    # Python; exiting 1 here stops the run right after pick_python emits.
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    # Run only pick_python() in isolation, sourced from the real script, so
    # we don't depend on gh / network / real pyproject.toml.
    driver = tmp_path / "driver.sh"
    driver.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            eval "$(awk '/^pick_python\\(\\) \\{/,/^}$/' \\
                .github/scripts/sync-aggregate-release.sh)"
            pick_python
            """
        ),
        encoding="utf-8",
    )
    driver.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{toolchain}"

    result = subprocess.run(
        ["bash", str(driver)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, f"picker exited {result.returncode}; stderr={result.stderr!r}"
    # Deterministic: the highest suitable versioned binary wins regardless
    # of PATH order or stray ``python3.*-config`` siblings.
    assert result.stdout.strip() == "python3.14", f"expected python3.14, got {result.stdout!r}"

    # Sanity: when the unversioned python3 is already 3.11+, it wins over
    # any versioned binary — preserves the fast-path for modern systems.
    bin2 = tmp_path / "bin2"
    bin2.mkdir()
    modern_python3 = bin2 / "python3"
    modern_python3.write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    modern_python3.chmod(0o755)
    env2 = os.environ.copy()
    env2["PATH"] = f"{bin2}:{bin_dir}:{toolchain}"
    result2 = subprocess.run(
        ["bash", str(driver)],
        cwd=PROJECT_ROOT,
        env=env2,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result2.returncode == 0
    assert result2.stdout.strip() == "python3"

    # Negative path: with only an old python3 and no suitable versioned
    # binary, the picker must exit 1 with the documented error message.
    bin3 = tmp_path / "bin3"
    bin3.mkdir()
    (bin3 / "python3").write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    (bin3 / "python3").chmod(0o755)
    (bin3 / "python3.10").write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    (bin3 / "python3.10").chmod(0o755)
    env3 = os.environ.copy()
    env3["PATH"] = f"{bin3}:{toolchain}"
    result3 = subprocess.run(
        ["bash", str(driver)],
        cwd=PROJECT_ROOT,
        env=env3,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result3.returncode == 1
    assert "Python 3.11+ is required" in result3.stderr


def test_release_channels_sync_assets_to_aggregate_release() -> None:
    extension = read_text(".github/workflows/release-extension.yml")
    desktop = read_text(".github/workflows/release-desktop.yml")
    backend = read_text(".github/workflows/release-backend.yml")

    assert ".github/scripts/sync-aggregate-release.sh" in extension
    assert "CHANNEL: extension" in extension
    assert "release-artifacts/openbiliclaw-extension-v*.zip" in extension

    assert ".github/scripts/sync-aggregate-release.sh" in desktop
    assert "CHANNEL: desktop" in desktop
    assert "release-artifacts/*.dmg release-artifacts/*.exe" in desktop

    assert ".github/scripts/sync-aggregate-release.sh" in backend
    assert "contents: write" in backend
    assert "CHANNEL: backend" in backend


def test_user_docs_explain_aggregate_release_entrypoint() -> None:
    docs = {
        "README.md": read_text("README.md"),
        "README_EN.md": read_text("README_EN.md"),
        "docs/index.md": read_text("docs/index.md"),
        "docs/modules/extension.md": read_text("docs/modules/extension.md"),
        "docs/modules/runtime.md": read_text("docs/modules/runtime.md"),
    }

    for relative_path, content in docs.items():
        assert "openbiliclaw-v*" in content, f"{relative_path} must mention the aggregate tag"

    assert "聚合" in docs["README.md"]
    assert "aggregate" in docs["README_EN.md"].lower()
    assert "Latest Release" in docs["docs/index.md"]
