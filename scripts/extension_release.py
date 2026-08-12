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
    archive = root / archive_name(manifest["version"], firefox=firefox)
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
    side_panel = raw.get("side_panel")
    if isinstance(side_panel, dict) and isinstance(side_panel.get("default_path"), str):
        assets.add(side_panel["default_path"])
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
    url: str, *, method: str = "GET", body: bytes | None = None, amo: bool = False
) -> object:
    """Make a bounded release API request without logging credentials/bodies."""
    headers = {"accept": "application/json"}
    if amo:
        headers["authorization"] = f"JWT {_amo_jwt()}"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 -- fixed release API URL is supplied by operator
            if response.length is not None and response.length > 1_000_000:
                raise RuntimeError("release API response too large")
            return json.loads(response.read(1_000_001))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"release API request failed ({exc.code})") from None


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


def chrome_upload(archive: Path, *, publish: bool) -> object:
    """Upload one generated archive with OAuth credentials from the environment."""
    if not archive.is_file() or archive.stat().st_size > 100_000_000:
        raise ValueError("extension archive is missing or too large")
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
    item_id = _required_env("CHROME_WEBSTORE_EXTENSION_ID")
    request = urllib.request.Request(
        f"https://www.googleapis.com/upload/chromewebstore/v1.1/items/{item_id}",
        data=archive.read_bytes(),
        method="PUT",
        headers={"authorization": f"Bearer {token['access_token']}", "x-goog-api-version": "2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 -- fixed CWS API
            result: object = json.loads(response.read(1_000_001))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Chrome Web Store upload failed ({exc.code})") from None
    if publish:
        request_json(
            f"https://www.googleapis.com/chromewebstore/v1.1/items/{item_id}/publish",
            method="POST",
            body=b"",
        )
    return result


def amo_sign(archive: Path) -> object:
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
            return json.loads(response.read(1_000_001))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"AMO signing request failed ({exc.code})") from None


def main() -> None:
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
    parser.add_argument("--version")
    parser.add_argument("--listing", type=Path)
    parser.add_argument("--mode", choices=("probe", "apply"), default="probe")
    args, _unknown = parser.parse_known_args()
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
            digest = hashlib.sha256((ROOT / "docs/privacy.md").read_bytes()).hexdigest()
            print(json.dumps({"privacy_sha256": digest}))
        else:
            print(json.dumps(request_json(endpoint, amo=True), sort_keys=True))
    elif args.command == "amo-sign":
        version = args.version or json.loads(_manifest(True).read_text())["version"]
        archive = args.zip or ROOT / archive_name(version, firefox=True)
        print(json.dumps(amo_sign(archive), sort_keys=True))
    elif args.command == "chrome-upload":
        if args.zip is None:
            raise SystemExit("--zip is required")
        print(json.dumps(chrome_upload(args.zip, publish=args.publish), sort_keys=True))
    else:
        if args.listing is None or not args.listing.is_file():
            raise SystemExit("--listing is required")
        # The public APIs do not reliably expose listing fields. Probe returns a
        # safe digest; apply refuses rather than guessing private Dashboard RPCs.
        summary = {
            "listing_sha256": hashlib.sha256(args.listing.read_bytes()).hexdigest(),
            "mode": args.mode,
        }
        if args.mode == "apply":
            raise SystemExit("Chrome Web Store listing writes require the Developer Dashboard")
        print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
