"""Docker runtime helpers for optional host proxy bootstrap."""

from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

_DEFAULT_PROXY_HOST = "host.docker.internal"
_DEFAULT_PROXY_PORT = 7897
_DEFAULT_PROXY_TIMEOUT = 1.0
_DEFAULT_RUNTIME_ROOT = "/app/runtime"
_DEFAULT_TEMPLATE_PATH = "/app/config.example.toml"
_DEFAULT_NO_PROXY_ENTRIES = ("127.0.0.1", "localhost", "host.docker.internal")
_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def bootstrap_runtime_root(*, runtime_root: Path, template_path: Path) -> None:
    """Create the isolated runtime root with config/data/logs when missing."""
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "data").mkdir(parents=True, exist_ok=True)
    (runtime_root / "logs").mkdir(parents=True, exist_ok=True)

    config_path = runtime_root / "config.toml"
    if config_path.exists() or not template_path.exists():
        return

    shutil.copyfile(template_path, config_path)


def can_connect(host: str, port: int, timeout: float) -> bool:
    """Return whether a TCP endpoint is reachable."""
    with socket.create_connection((host, port), timeout=timeout):
        return True


def resolve_optional_proxy_env(
    env: dict[str, str] | os._Environ[str],
    *,
    can_connect: Callable[[str, int, float], bool] = can_connect,
    proxy_host: str = _DEFAULT_PROXY_HOST,
    proxy_port: int = _DEFAULT_PROXY_PORT,
    timeout: float = _DEFAULT_PROXY_TIMEOUT,
) -> dict[str, str]:
    """Return proxy env updates when a host-side Clash proxy is reachable."""
    if any(str(env.get(key, "")).strip() for key in _PROXY_KEYS):
        return {}

    if not can_connect(proxy_host, proxy_port, timeout):
        return {}

    proxy_url = f"http://{proxy_host}:{proxy_port}"
    no_proxy = _merge_no_proxy(env.get("NO_PROXY", "") or env.get("no_proxy", ""))
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": proxy_url,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def _merge_no_proxy(current: str) -> str:
    """Merge required local bypass hosts into no_proxy."""
    entries = [item.strip() for item in current.split(",") if item.strip()]
    for entry in _DEFAULT_NO_PROXY_ENTRIES:
        if entry not in entries:
            entries.append(entry)
    return ",".join(entries)


def bootstrap_runtime_environment(
    env: MutableMapping[str, str],
    *,
    can_connect: Callable[[str, int, float], bool] = can_connect,
) -> None:
    """Bootstrap the isolated runtime root and optional proxy env in-place."""
    runtime_root = Path(env.get("OPENBILICLAW_PROJECT_ROOT", _DEFAULT_RUNTIME_ROOT))
    template_path = Path(env.get("OPENBILICLAW_CONFIG_TEMPLATE", _DEFAULT_TEMPLATE_PATH))
    bootstrap_runtime_root(runtime_root=runtime_root, template_path=template_path)
    env.setdefault("OPENBILICLAW_PROJECT_ROOT", str(runtime_root))

    proxy_host = env.get("OPENBILICLAW_PROXY_HOST", _DEFAULT_PROXY_HOST).strip()
    proxy_port = int(env.get("OPENBILICLAW_PROXY_PORT", str(_DEFAULT_PROXY_PORT)))
    timeout = float(env.get("OPENBILICLAW_PROXY_TIMEOUT", str(_DEFAULT_PROXY_TIMEOUT)))
    env.update(
        resolve_optional_proxy_env(
            dict(env),
            can_connect=can_connect,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            timeout=timeout,
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Bootstrap optional proxy settings, then exec the target command."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("usage: python -m openbiliclaw.docker_runtime <command> [args...]")

    bootstrap_runtime_environment(os.environ)
    os.execvpe(args[0], args, os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
