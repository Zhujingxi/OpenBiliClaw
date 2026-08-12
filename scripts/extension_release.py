#!/usr/bin/env python3
"""Build and package the generated Vue extension; optional store upload helpers.

Executable extension release tooling lives in Python. Vite is invoked only to
produce browser-executable generated JavaScript under ignored ``dist/`` paths.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
GENERATED: Final = ROOT / "frontend" / "apps" / "extension" / "dist"
EXTENSION: Final = ROOT / "extension"
ARTIFACTS: Final = ROOT / "artifacts" / "extension"


def normalize_version(value: str) -> str:
    """Normalize component tags and plain versions to ``vX.Y.Z``."""
    if "-v" in value:
        value = value.rsplit("-v", 1)[1]
    return value if value.startswith("v") else f"v{value}"


def archive_name(version: str, *, firefox: bool) -> str:
    suffix = "-firefox" if firefox else ""
    return f"openbiliclaw-extension-{normalize_version(version)}{suffix}.zip"


def _manifest(firefox: bool) -> Path:
    return EXTENSION / ("manifest.firefox.json" if firefox else "manifest.json")


def build_extension_tree(root: Path = ROOT, *, firefox: bool) -> Path:
    """Copy generated Vite output and declarative browser metadata into artifacts."""
    generated = root / "frontend/apps/extension/dist"
    extension = root / "extension"
    output = root / "artifacts/extension" / ("firefox" if firefox else "chrome")
    if not generated.is_dir():
        raise FileNotFoundError(
            "frontend extension build is missing; run npm --prefix frontend run build"
        )
    manifest_source = extension / ("manifest.firefox.json" if firefox else "manifest.json")
    manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("extension manifest must be an object")
    if not isinstance(manifest.get("version"), str) and firefox:
        chrome_manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
        if isinstance(chrome_manifest, dict) and isinstance(chrome_manifest.get("version"), str):
            manifest["version"] = chrome_manifest["version"]
    if not isinstance(manifest.get("version"), str):
        raise ValueError("extension manifest requires a string version")
    shutil.rmtree(output, ignore_errors=True)
    shutil.copytree(generated, output)
    # Vite retains the source-relative popup HTML path; browser metadata uses a stable path.
    popup_source = output / "src/popup/index.html"
    popup_target = output / "popup/index.html"
    popup_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(popup_source, popup_target)
    shutil.rmtree(output / "src")
    shutil.copytree(extension / "icons", output / "icons")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    missing = verify_manifest_assets(output)
    if missing:
        raise FileNotFoundError("missing manifest assets: " + ", ".join(missing))
    archive = root / "artifacts/extension" / archive_name(manifest["version"], firefox=firefox)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(output).as_posix())
    return output


def verify_manifest_assets(root: Path) -> tuple[str, ...]:
    """Return missing manifest-referenced assets and reject path traversal."""
    raw = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest must be an object")
    assets: set[str] = set()
    background = raw.get("background")
    if isinstance(background, dict) and isinstance(background.get("service_worker"), str):
        assets.add(background["service_worker"])
    for section, field in (
        ("side_panel", "default_path"),
        ("action", "default_popup"),
        ("sidebar_action", "default_panel"),
    ):
        descriptor = raw.get(section)
        if isinstance(descriptor, dict) and isinstance(descriptor.get(field), str):
            assets.add(descriptor[field])
    icons = raw.get("icons")
    if isinstance(icons, dict):
        assets.update(value for value in icons.values() if isinstance(value, str))
    scripts = raw.get("content_scripts")
    if isinstance(scripts, list):
        for item in scripts:
            if isinstance(item, dict) and isinstance(item.get("js"), list):
                assets.update(value for value in item["js"] if isinstance(value, str))
    for asset in assets:
        path = Path(asset)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe asset path: {asset}")
    return tuple(sorted(asset for asset in assets if not (root / asset).is_file()))


def _amo_jwt() -> str:
    issuer = os.environ.get("AMO_JWT_ISSUER")
    secret = os.environ.get("AMO_JWT_SECRET")
    if not issuer or not secret:
        raise RuntimeError("AMO_JWT_ISSUER and AMO_JWT_SECRET are required")

    def encode(value: object) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
            .rstrip(b"=")
            .decode()
        )

    now = int(time.time())
    unsigned = f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode({'iss': issuer, 'jti': str(uuid.uuid4()), 'iat': now, 'exp': now + 60})}"
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode(), unsigned.encode(), hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return f"{unsigned}.{signature}"


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    amo: bool = False,
    headers: dict[str, str] | None = None,
) -> object:
    """Make a bounded release API request without logging credentials/bodies."""
    request_headers = {"accept": "application/json", **(headers or {})}
    if amo:
        request_headers["authorization"] = f"JWT {_amo_jwt()}"
    request = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 -- fixed release API URL is supplied by operator
            if response.length is not None and response.length > 1_000_000:
                raise RuntimeError("release API response too large")
            return json.loads(response.read(1_000_001))
    except urllib.error.HTTPError as exc:
        detail = exc.read(64_000).decode("utf-8", errors="replace")
        raise RuntimeError(f"release API request failed ({exc.code}): {detail}") from None


def parse_listing_metadata(path: Path) -> dict[str, str]:
    """Extract canonical listing copy from the documented fenced sections."""
    markdown = path.read_text(encoding="utf-8")

    def section(heading: str) -> str:
        marker = f"## {heading}"
        start = markdown.find(marker)
        if start < 0:
            raise ValueError(f"missing {heading} heading")
        fenced = markdown.find("```", start + len(marker))
        content = markdown.find("\n", fenced) + 1
        end = markdown.find("\n```", content)
        if fenced < 0 or content == 0 or end < 0:
            raise ValueError(f"missing {heading} fenced block")
        return markdown[content:end].strip()

    return {
        "summary": section("Short Description"),
        "description": section("Detailed Description"),
    }


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = f"----openbiliclaw-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            ]
        )
    for name, (filename, payload) in files.items():
        parts.extend(
            [
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: application/zip\r\n\r\n'.encode(),
                payload,
                b"\r\n",
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


@dataclass(frozen=True)
class ChromeUploadOptions:
    publish: bool = False
    staged: bool = False
    replace_pending: bool = False
    poll_interval_seconds: int = 5
    wait_timeout_seconds: int = 120


def chrome_upload(
    archive: Path,
    options: ChromeUploadOptions,
    *,
    request: Callable[[str, object | None], object] | None = None,
) -> object:
    """Upload, optionally replace a pending review, and publish an archive."""
    if not archive.is_file() or archive.stat().st_size > 100_000_000:
        raise ValueError("extension archive is missing or too large")
    if request is not None:
        try:
            result = request("upload", archive)
        except RuntimeError as exc:
            if not options.replace_pending or "NOT_UPDATEABLE" not in str(exc):
                raise
            request("cancel", None)
            result = request("upload", archive)
        if options.publish:
            request(
                "publish",
                {"publishType": "STAGED_PUBLISH" if options.staged else "DEFAULT_PUBLISH"},
            )
        return result
    token_body = urllib.parse.urlencode(
        {
            "client_id": _required_env("CHROME_WEBSTORE_CLIENT_ID"),
            "client_secret": _required_env("CHROME_WEBSTORE_CLIENT_SECRET"),
            "refresh_token": _required_env("CHROME_WEBSTORE_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        }
    ).encode()
    token = request_json("https://oauth2.googleapis.com/token", method="POST", body=token_body)
    if not isinstance(token, dict) or not isinstance(token.get("access_token"), str):
        raise RuntimeError("OAuth response omitted access_token")
    publisher_id = _required_env("CHROME_WEBSTORE_PUBLISHER_ID")
    item_id = _required_env("CHROME_WEBSTORE_EXTENSION_ID")
    name = f"publishers/{urllib.parse.quote(publisher_id)}/items/{urllib.parse.quote(item_id)}"
    authorization = {"authorization": f"Bearer {token['access_token']}"}

    def cws(operation: str, body: object | None = None) -> object:
        if operation == "upload":
            return request_json(
                f"https://chromewebstore.googleapis.com/upload/v2/{name}:upload",
                method="POST",
                body=archive.read_bytes(),
                headers={**authorization, "content-type": "application/zip"},
            )
        endpoint = {
            "cancel": "cancelSubmission",
            "status": "fetchStatus",
            "publish": "publish",
        }[operation]
        encoded = None if body is None else json.dumps(body).encode()
        return request_json(
            f"https://chromewebstore.googleapis.com/v2/{name}:{endpoint}",
            method="GET" if operation == "status" else "POST",
            body=encoded,
            headers={**authorization, "content-type": "application/json"},
        )

    try:
        result = cws("upload")
    except RuntimeError as exc:
        if not options.replace_pending or "NOT_UPDATEABLE" not in str(exc):
            raise
        cws("cancel")
        result = cws("upload")
    state = result.get("uploadState") if isinstance(result, dict) else None
    deadline = time.monotonic() + options.wait_timeout_seconds
    while state == "IN_PROGRESS" and time.monotonic() < deadline:
        time.sleep(options.poll_interval_seconds)
        result = cws("status")
        state = result.get("uploadState") if isinstance(result, dict) else None
    if state != "SUCCEEDED":
        raise RuntimeError(f"Chrome Web Store upload did not succeed: {state or 'unknown'}")
    if options.publish:
        cws(
            "publish",
            {"publishType": "STAGED_PUBLISH" if options.staged else "DEFAULT_PUBLISH"},
        )
    return result


def save_signed_xpi(payload: bytes, target: Path) -> None:
    """Persist a signed AMO package after validating its ZIP signature."""
    if len(payload) < 4 or payload[:2] != b"PK":
        raise RuntimeError("AMO signed file is not a zip archive")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def verify_amo_status(version: str, page: object) -> dict[str, object]:
    """Require the requested AMO version to exist on the listed channel."""
    if not isinstance(page, dict) or not isinstance(page.get("results"), list):
        raise RuntimeError("AMO version response omitted results")
    match = next(
        (
            item
            for item in page["results"]
            if isinstance(item, dict) and item.get("version") == version
        ),
        None,
    )
    if not isinstance(match, dict) or match.get("channel") != "listed":
        raise RuntimeError(f"AMO version {version} was not found on expected listed channel")
    return match


def amo_sign(archive: Path, *, output: Path | None = None) -> object:
    """Submit a generated Firefox archive for unlisted AMO signing."""
    manifest = json.loads(_manifest(True).read_text(encoding="utf-8"))
    gecko = manifest.get("browser_specific_settings", {}).get("gecko", {}).get("id")
    if not isinstance(gecko, str) or not archive.is_file():
        raise ValueError("Firefox Gecko ID or archive is missing")
    body, content_type = _multipart(
        {"channel": "unlisted"}, {"upload": (archive.name, archive.read_bytes())}
    )
    request = urllib.request.Request(
        f"https://addons.mozilla.org/api/v5/addons/addon/{urllib.parse.quote(gecko, safe='')}/versions/",
        data=body,
        method="POST",
        headers={"authorization": f"JWT {_amo_jwt()}", "content-type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 -- fixed AMO API
            result: object = json.loads(response.read(1_000_001))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"AMO signing request failed ({exc.code})") from None
    if output is not None:
        if not isinstance(result, dict) or not isinstance(result.get("files"), list):
            raise RuntimeError("AMO signing response omitted signed files")
        files = result["files"]
        signed_url = next(
            (
                item.get("download_url")
                for item in files
                if isinstance(item, dict) and isinstance(item.get("download_url"), str)
            ),
            None,
        )
        poll_url = result.get("url")
        deadline = time.monotonic() + 300
        while signed_url is None and isinstance(poll_url, str) and time.monotonic() < deadline:
            time.sleep(5)
            result = request_json(poll_url, amo=True)
            files = result.get("files", []) if isinstance(result, dict) else []
            signed_url = next(
                (
                    item.get("download_url")
                    for item in files
                    if isinstance(item, dict) and isinstance(item.get("download_url"), str)
                ),
                None,
            )
        if signed_url is None:
            raise RuntimeError("AMO signing did not expose a signed download_url")
        with urllib.request.urlopen(signed_url, timeout=120) as response:  # noqa: S310 -- AMO response URL
            save_signed_xpi(response.read(100_000_001), output)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "build",
            "package",
            "verify",
            "amo-status",
            "amo-privacy",
            "amo-sign",
            "chrome-upload",
            "chrome-metadata",
        ),
    )
    parser.add_argument("--firefox", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--replace-pending", action="store_true")
    parser.add_argument("--poll-interval-seconds", type=int, default=5)
    parser.add_argument("--wait-timeout-seconds", type=int, default=120)
    parser.add_argument("--version")
    parser.add_argument("--listing", type=Path)
    parser.add_argument("--mode", choices=("probe", "apply"), default="probe")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.command in {"build", "package"} and not args.no_build:
        subprocess.run(
            [
                "npm",
                "--prefix",
                "frontend",
                "run",
                "build",
                "--workspace",
                "@openbiliclaw/extension",
            ],
            cwd=ROOT,
            check=True,
        )
    if args.command in {"build", "package"}:
        output = build_extension_tree(firefox=args.firefox)
        print(output)
    elif args.command == "verify":
        missing = verify_manifest_assets(ARTIFACTS / ("firefox" if args.firefox else "chrome"))
        if missing:
            raise SystemExit("missing assets: " + ", ".join(missing))
    elif args.command in {"amo-status", "amo-privacy"}:
        manifest = json.loads(_manifest(True).read_text(encoding="utf-8"))
        gecko = manifest.get("browser_specific_settings", {}).get("gecko", {}).get("id")
        if not isinstance(gecko, str):
            raise SystemExit("Firefox manifest lacks Gecko ID")
        endpoint = (
            f"https://addons.mozilla.org/api/v5/addons/addon/{urllib.parse.quote(gecko, safe='')}/"
        )
        if args.command == "amo-privacy":
            addon = request_json(endpoint, amo=True)
            addon_id = addon.get("id") if isinstance(addon, dict) else None
            if not isinstance(addon_id, int) or addon_id <= 0:
                raise SystemExit("AMO add-on detail omitted numeric id")
            privacy = (ROOT / "docs/privacy.md").read_text(encoding="utf-8")
            policy_url = f"https://addons.mozilla.org/api/v5/addons/addon/{addon_id}/eula_policy/"
            request_json(
                policy_url,
                method="PATCH",
                body=json.dumps({"privacy_policy": {"zh-CN": privacy}}).encode(),
                amo=True,
                headers={"content-type": "application/json"},
            )
            saved = request_json(policy_url, amo=True)
            saved_policy = saved.get("privacy_policy") if isinstance(saved, dict) else None
            if not isinstance(saved_policy, dict) or saved_policy.get("zh-CN") != privacy:
                raise SystemExit("AMO privacy policy read-back mismatch")
            digest = hashlib.sha256(privacy.encode()).hexdigest()
            print(json.dumps({"privacy_sha256": digest}))
        else:
            version = args.version or json.loads(_manifest(False).read_text())["version"]
            page = request_json(
                endpoint + "versions/?filter=all_with_unlisted&page_size=50", amo=True
            )
            print(json.dumps(verify_amo_status(version, page), sort_keys=True))
    elif args.command == "amo-sign":
        version = args.version or json.loads(_manifest(False).read_text())["version"]
        archive = args.zip or ARTIFACTS / archive_name(version, firefox=True)
        output = ARTIFACTS / f"openbiliclaw-extension-{normalize_version(version)}-firefox.xpi"
        print(json.dumps(amo_sign(archive, output=output), sort_keys=True))
    elif args.command == "chrome-upload":
        if args.zip is None:
            raise SystemExit("--zip is required")
        print(
            json.dumps(
                chrome_upload(
                    args.zip,
                    ChromeUploadOptions(
                        publish=args.publish or args.staged,
                        staged=args.staged,
                        replace_pending=args.replace_pending,
                        poll_interval_seconds=args.poll_interval_seconds,
                        wait_timeout_seconds=args.wait_timeout_seconds,
                    ),
                ),
                sort_keys=True,
            )
        )
    else:
        if args.listing is None or not args.listing.is_file():
            raise SystemExit("--listing is required")
        listing = parse_listing_metadata(args.listing)
        token_body = urllib.parse.urlencode(
            {
                "client_id": _required_env("CHROME_WEBSTORE_CLIENT_ID"),
                "client_secret": _required_env("CHROME_WEBSTORE_CLIENT_SECRET"),
                "refresh_token": _required_env("CHROME_WEBSTORE_REFRESH_TOKEN"),
                "grant_type": "refresh_token",
            }
        ).encode()
        token = request_json("https://oauth2.googleapis.com/token", method="POST", body=token_body)
        if not isinstance(token, dict) or not isinstance(token.get("access_token"), str):
            raise SystemExit("OAuth response omitted access_token")
        item_id = _required_env("CHROME_WEBSTORE_EXTENSION_ID")
        url = f"https://www.googleapis.com/chromewebstore/v1.1/items/{item_id}"
        headers = {"authorization": f"Bearer {token['access_token']}"}
        draft = request_json(url + "?projection=DRAFT", headers=headers)
        if not isinstance(draft, dict):
            raise SystemExit("Chrome listing probe omitted draft")
        result: object = draft
        if args.mode == "apply":
            payload = {
                key: value
                for key, value in draft.items()
                if key in {"title", "category", "defaultLocale", "homepageUrl", "supportUrl"}
                and isinstance(value, str)
            }
            payload.update(listing)
            result = request_json(
                url,
                method="PUT",
                body=json.dumps(payload).encode(),
                headers={**headers, "content-type": "application/json"},
            )
            readback = request_json(url + "?projection=DRAFT", headers=headers)
            if not isinstance(readback, dict) or any(
                readback.get(key) != value for key, value in listing.items()
            ):
                raise SystemExit("Chrome listing metadata read-back mismatch")
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
