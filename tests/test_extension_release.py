from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from scripts.extension_release import (
    archive_name,
    build_extension_tree,
    normalize_version,
    verify_manifest_assets,
)


def test_release_version_and_archive_names() -> None:
    assert normalize_version("extension-v1.2.3") == "v1.2.3"
    assert normalize_version("1.2.3") == "v1.2.3"
    assert archive_name("1.2.3", firefox=False) == "openbiliclaw-extension-v1.2.3.zip"
    assert archive_name("1.2.3", firefox=True) == "openbiliclaw-extension-v1.2.3-firefox.zip"


def test_build_extension_tree_copies_generated_assets_and_packages(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    generated = root / "frontend/apps/extension/dist"
    generated.joinpath("background").mkdir(parents=True)
    generated.joinpath("popup").mkdir()
    generated.joinpath("content").mkdir()
    generated.joinpath("background/background.js").write_text("generated")
    generated.joinpath("content/content.js").write_text("generated")
    generated.joinpath("popup/popup.js").write_text("generated")
    generated.joinpath("src/popup").mkdir(parents=True)
    generated.joinpath("src/popup/index.html").write_text("<main></main>")
    extension = root / "extension"
    extension.joinpath("icons").mkdir(parents=True)
    extension.joinpath("icons/icon16.png").write_bytes(b"png")
    manifest = {
        "version": "1.2.3",
        "background": {"service_worker": "background/background.js"},
        "content_scripts": [{"matches": ["http://localhost/*"], "js": ["content/content.js"]}],
        "side_panel": {"default_path": "popup/index.html"},
        "icons": {"16": "icons/icon16.png"},
    }
    extension.joinpath("manifest.json").write_text(json.dumps(manifest))
    output = build_extension_tree(root, firefox=False)
    assert verify_manifest_assets(output) == ()
    archive = root / archive_name("1.2.3", firefox=False)
    with zipfile.ZipFile(archive) as bundle:
        assert "manifest.json" in bundle.namelist()
        assert bundle.read("background/background.js") == b"generated"


def test_manifest_asset_verification_rejects_missing_or_traversal(tmp_path: Path) -> None:
    tmp_path.joinpath("manifest.json").write_text(
        json.dumps({"background": {"service_worker": "../secret.js"}})
    )
    with pytest.raises(ValueError, match="unsafe asset"):
        verify_manifest_assets(tmp_path)


def test_pyinstaller_uses_generated_frontend_artifact_not_deleted_web_tree() -> None:
    spec = (Path(__file__).parents[1] / "packaging/openbiliclaw.spec").read_text()
    assert 'frontend" / "apps" / "web" / "dist' in spec
    assert 'src" / "openbiliclaw" / "web' not in spec
