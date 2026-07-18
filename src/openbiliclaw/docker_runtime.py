"""Docker runtime helpers for optional host proxy bootstrap."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tomllib
from dataclasses import replace
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


def bootstrap_runtime_root(
    *,
    runtime_root: Path,
    template_path: Path,
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Create the isolated runtime root with config/data/logs when missing.

    When ``OPENBILICLAW_SEED_OLLAMA_DEFAULTS`` is set in ``env`` (the
    Docker compose file ships it on by default), the freshly-created
    config gets a native ``ollama-docker`` embedding provider plus shared
    model settings for the bundled sidecar. Existing ordered remote providers
    and the Chat route remain intact.

    An existing ``config.toml`` is never overwritten — users who already
    set up their own embedding stack keep their choices.
    """
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "data").mkdir(parents=True, exist_ok=True)
    (runtime_root / "logs").mkdir(parents=True, exist_ok=True)

    config_path = runtime_root / "config.toml"
    if config_path.exists() or not template_path.exists():
        return

    shutil.copyfile(template_path, config_path)

    resolved_env = env if env is not None else os.environ
    if str(resolved_env.get("OPENBILICLAW_SEED_OLLAMA_DEFAULTS", "")).strip():
        ollama_base = (
            resolved_env.get("OPENBILICLAW_OLLAMA_BASE_URL", "").strip() or "http://ollama:11434/v1"
        )
        embedding_model = resolved_env.get("OPENBILICLAW_EMBEDDING_MODEL", "").strip() or "bge-m3"
        _seed_ollama_defaults(config_path, ollama_base, embedding_model)


def _seed_ollama_defaults(
    config_path: Path,
    ollama_base_url: str,
    embedding_model: str,
) -> None:
    """Seed one typed native provider without disturbing existing routes."""
    from openbiliclaw.config import render_model_config_document
    from openbiliclaw.model_config import (
        EmbeddingProviderConfig,
        parse_model_config,
    )

    original = config_path.read_bytes()
    raw = tomllib.loads(original.decode("utf-8"))
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict):
        raise ValueError("Docker config template requires a native [models] table")
    models = parse_model_config(models_raw)
    chat_ids = {item.id for item in models.chat.connections}
    existing_owned = next(
        (
            item
            for item in models.embedding.providers
            if item.id == "ollama-docker" and item.type == "ollama" and item.id not in chat_ids
        ),
        None,
    )
    provider_id = "ollama-docker"
    if existing_owned is None:
        used_ids = chat_ids | {item.id for item in models.embedding.providers}
        suffix = 2
        while provider_id in used_ids:
            provider_id = f"ollama-docker-{suffix}"
            suffix += 1
    provider = EmbeddingProviderConfig(
        id=provider_id,
        name="Docker Ollama",
        type="ollama",
        base_url=ollama_base_url,
    )
    retained = tuple(
        item
        for item in models.embedding.providers
        if existing_owned is None or item.id != existing_owned.id
    )
    if len(retained) >= 10:
        return
    updated = replace(
        models,
        embedding=replace(
            models.embedding,
            enabled=True,
            settings=replace(models.embedding.settings, model=embedding_model),
            providers=(provider, *retained),
        ),
    )
    config_path.write_bytes(render_model_config_document(original, updated))


def can_connect(host: str, port: int, timeout: float) -> bool:
    """Return whether a TCP endpoint is reachable.

    A missing/unreachable endpoint is the common case (most users run the
    container without a host-side proxy on port 7897), so it MUST return
    ``False`` rather than raise. ``socket.create_connection`` raises an
    ``OSError`` subclass on connection refused, timeout, or DNS failure;
    letting that propagate would crash the runtime bootstrapper before it
    ever execs ``serve-api``, exiting the container on startup.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


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


def is_running_in_container(env: MutableMapping[str, str] | None = None) -> bool:
    """Return whether this process is running inside a container runtime.

    The host-proxy auto-detection below is only safe inside a container,
    where ``host.docker.internal`` really does point to the host and is
    the only route to the internet.  On a native macOS developer
    machine Docker Desktop still resolves that name — so without this
    gate the bootstrapper routes every outbound request through the
    host's Clash proxy, which breaks Bilibili calls (and anything else
    that doesn't tolerate Clash's routing).
    """
    resolved_env = env if env is not None else os.environ
    if str(resolved_env.get("OPENBILICLAW_IN_CONTAINER", "")).strip():
        return True
    # Docker writes /.dockerenv; Podman writes /run/.containerenv.
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def bootstrap_runtime_environment(
    env: MutableMapping[str, str],
    *,
    can_connect: Callable[[str, int, float], bool] = can_connect,
    in_container: Callable[[MutableMapping[str, str]], bool] = is_running_in_container,
) -> None:
    """Bootstrap the isolated runtime root and optional proxy env in-place."""
    runtime_root = Path(env.get("OPENBILICLAW_PROJECT_ROOT", _DEFAULT_RUNTIME_ROOT))
    template_path = Path(env.get("OPENBILICLAW_CONFIG_TEMPLATE", _DEFAULT_TEMPLATE_PATH))
    bootstrap_runtime_root(
        runtime_root=runtime_root,
        template_path=template_path,
        env=env,
    )
    env.setdefault("OPENBILICLAW_PROJECT_ROOT", str(runtime_root))

    # Proxy auto-detection is ONLY safe inside container runtimes, and is
    # STRICTLY optional: a misconfigured OPENBILICLAW_PROXY_* value or an
    # unexpected probe failure must never stop serve-api from launching.
    # We already guard the common "no proxy on the port" case in
    # can_connect; this broad guard additionally covers a malformed
    # OPENBILICLAW_PROXY_PORT / _TIMEOUT (int()/float() would otherwise
    # raise and crash the container on startup, same symptom as before).
    if not in_container(env):
        return

    try:
        proxy_host = env.get("OPENBILICLAW_PROXY_HOST", _DEFAULT_PROXY_HOST).strip() or (
            _DEFAULT_PROXY_HOST
        )
        proxy_port = int(env.get("OPENBILICLAW_PROXY_PORT", "").strip() or _DEFAULT_PROXY_PORT)
        timeout = float(env.get("OPENBILICLAW_PROXY_TIMEOUT", "").strip() or _DEFAULT_PROXY_TIMEOUT)
        env.update(
            resolve_optional_proxy_env(
                dict(env),
                can_connect=can_connect,
                proxy_host=proxy_host,
                proxy_port=proxy_port,
                timeout=timeout,
            )
        )
        # Container proxy variables are intentional runtime configuration.
        # Opt into system inheritance unless the user chose a mode explicitly.
        if any(str(env.get(key, "")).strip() for key in _PROXY_KEYS):
            env.setdefault("OPENBILICLAW_NETWORK_MODE", "system")
    except Exception as exc:  # noqa: BLE001 - optional step must not block startup
        print(
            f"[docker_runtime] optional host-proxy bootstrap skipped: {exc!r}",
            file=sys.stderr,
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
