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
    generated.joinpath("popup").mkdir(parents=True)
    generated.joinpath("popup/popup.js").write_text("generated")
    generated.joinpath("src/popup").mkdir(parents=True)
    generated.joinpath("src/popup/index.html").write_text("<main></main>")
    extension = root / "extension"
    extension.joinpath("icons").mkdir(parents=True)
    extension.joinpath("icons/icon16.png").write_bytes(b"png")
    extension.joinpath("_locales/en").mkdir(parents=True)
    extension.joinpath("_locales/en/messages.json").write_text("{}")
    manifest = {
        "version": "1.2.3",
        "side_panel": {"default_path": "popup/index.html"},
        "icons": {"16": "icons/icon16.png"},
    }
    extension.joinpath("manifest.json").write_text(json.dumps(manifest))
    output = build_extension_tree(root, firefox=False)
    assert verify_manifest_assets(output) == ()
    archive = root / "artifacts/extension" / archive_name("1.2.3", firefox=False)
    with zipfile.ZipFile(archive) as bundle:
        assert "manifest.json" in bundle.namelist()
        assert bundle.read("popup/popup.js") == b"generated"
        assert "_locales/en/messages.json" in bundle.namelist()


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


def test_release_parser_rejects_unknown_arguments() -> None:
    from scripts.extension_release import parse_args

    with pytest.raises(SystemExit):
        parse_args(["package", "--typo"])


def test_chrome_upload_replaces_pending_and_stages(tmp_path: Path) -> None:
    from scripts.extension_release import ChromeUploadOptions, chrome_upload

    archive = tmp_path / "extension.zip"
    archive.write_bytes(b"PK\x03\x04")
    calls: list[tuple[str, object | None]] = []
    upload_attempts = 0

    def request(operation: str, body: object | None = None) -> object:
        nonlocal upload_attempts
        calls.append((operation, body))
        if operation == "upload":
            upload_attempts += 1
            if upload_attempts == 1:
                raise RuntimeError("NOT_UPDATEABLE")
        return {"uploadState": "SUCCEEDED"}

    chrome_upload(
        archive,
        ChromeUploadOptions(publish=True, staged=True, replace_pending=True),
        request=request,
    )
    assert ("cancel", None) in calls
    assert ("publish", {"publishType": "STAGED_PUBLISH"}) in calls


def test_amo_status_requires_requested_listed_version() -> None:
    from scripts.extension_release import verify_amo_status

    result = verify_amo_status(
        "1.2.3",
        {"results": [{"version": "1.2.3", "channel": "listed", "file": {"status": "public"}}]},
    )
    assert result["version"] == "1.2.3"
    with pytest.raises(RuntimeError, match="expected listed"):
        verify_amo_status("1.2.3", {"results": [{"version": "1.2.3", "channel": "unlisted"}]})


def test_signed_xpi_download_validates_zip(tmp_path: Path) -> None:
    from scripts.extension_release import save_signed_xpi

    target = tmp_path / "signed.xpi"
    save_signed_xpi(b"PK\x03\x04signed", target)
    assert target.read_bytes().startswith(b"PK")
    with pytest.raises(RuntimeError, match="not a zip"):
        save_signed_xpi(b"html", target)


def test_composed_host_serves_generated_frontend_not_deleted_web_tree() -> None:
    source = (Path(__file__).parents[1] / "src/openbiliclaw/hosts/api/app.py").read_text()
    assert "frontend/apps/web/dist" in source
    assert "src/openbiliclaw/web" not in source


def test_listing_metadata_parser_extracts_canonical_copy(tmp_path: Path) -> None:
    from scripts.extension_release import parse_listing_metadata

    listing = tmp_path / "listing.md"
    listing.write_text(
        "## Short Description\n```text\nShort local copy\n```\n"
        "## Detailed Description\n```text\nLong local backend copy\n```\n"
    )
    assert parse_listing_metadata(listing) == {
        "summary": "Short local copy",
        "description": "Long local backend copy",
    }


def test_amo_sign_defaults_to_base_manifest_version(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.extension_release as release

    captured: list[tuple[Path, Path | None]] = []

    def fake_sign(archive: Path, *, output: Path | None = None) -> dict[str, bool]:
        captured.append((archive, output))
        return {"ok": True}

    monkeypatch.setattr(release, "amo_sign", fake_sign)
    monkeypatch.setattr("sys.argv", ["extension_release.py", "amo-sign", "--no-build"])

    release.main()

    assert captured == [
        (
            release.ARTIFACTS / "openbiliclaw-extension-v0.3.201-firefox.zip",
            release.ARTIFACTS / "openbiliclaw-extension-v0.3.201-firefox.xpi",
        )
    ]


def test_amo_privacy_patches_and_verifies_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.extension_release as release

    calls: list[tuple[str, str, bytes | None]] = []
    privacy = (release.ROOT / "docs/privacy.md").read_text(encoding="utf-8")

    def request(
        url: str, *, method: str = "GET", body: bytes | None = None, **_kwargs: object
    ) -> object:
        calls.append((url, method, body))
        if url.endswith("eula_policy/"):
            return {} if method == "PATCH" else {"privacy_policy": {"zh-CN": privacy}}
        return {"id": 42}

    monkeypatch.setattr(release, "request_json", request)
    monkeypatch.setattr("sys.argv", ["extension_release.py", "amo-privacy"])
    release.main()

    assert any(url.endswith("/42/eula_policy/") and method == "PATCH" for url, method, _ in calls)


def test_chrome_metadata_apply_writes_and_verifies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import scripts.extension_release as release

    listing = tmp_path / "listing.md"
    listing.write_text(
        "## Short Description\n```text\nShort local copy\n```\n"
        "## Detailed Description\n```text\nLong local backend copy\n```\n"
    )
    draft: dict[str, object] = {"title": "OpenBiliClaw", "defaultLocale": "zh-CN"}
    writes: list[dict[str, object]] = []

    def request(
        url: str, *, method: str = "GET", body: bytes | None = None, **_kwargs: object
    ) -> object:
        if "oauth2.googleapis.com" in url:
            return {"access_token": "token"}
        if method == "PUT":
            assert body is not None
            payload = json.loads(body)
            assert isinstance(payload, dict)
            writes.append(payload)
            draft.update(payload)
        return dict(draft)

    for name in (
        "CHROME_WEBSTORE_CLIENT_ID",
        "CHROME_WEBSTORE_CLIENT_SECRET",
        "CHROME_WEBSTORE_REFRESH_TOKEN",
        "CHROME_WEBSTORE_EXTENSION_ID",
    ):
        monkeypatch.setenv(name, "value")
    monkeypatch.setattr(release, "request_json", request)
    monkeypatch.setattr(
        "sys.argv",
        ["extension_release.py", "chrome-metadata", "--listing", str(listing), "--mode", "apply"],
    )
    release.main()

    assert writes[0]["summary"] == "Short local copy"
    assert writes[0]["description"] == "Long local backend copy"


def test_docker_static_root_matches_runtime_resolution() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    assert "OPENBILICLAW_FRONTEND_DIR=/app/frontend" in dockerfile
    assert "COPY --from=frontend-build /build/frontend/apps/web/dist ./frontend" in dockerfile
