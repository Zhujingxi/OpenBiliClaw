"""Shared helpers for supervising a local Ollama daemon."""

from __future__ import annotations

import ipaddress
import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

import httpx
from rich.console import Console

from openbiliclaw.llm.registry import _ollama_is_chat_capable

if TYPE_CHECKING:
    from openbiliclaw.config import Config

_DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"

console = Console()


def _embedding_wants_ollama(config: Config) -> bool:
    embedding = config.llm.embedding
    return (
        str(embedding.provider).strip().lower() == "ollama"
        or str(embedding.fallback_provider).strip().lower() == "ollama"
    )


def ollama_required(config: Config) -> bool:
    """Return whether chat or embedding routing may call Ollama."""
    return _ollama_is_chat_capable(config) or _embedding_wants_ollama(config)


def _strip_openai_v1_suffix(url: str) -> str:
    text = url.strip().rstrip("/")
    if not text:
        return _DEFAULT_OLLAMA_ENDPOINT
    parsed = urlparse(text)
    path = parsed.path.rstrip("/")
    if path == "/v1":
        path = ""
    elif path.endswith("/v1"):
        path = path[: -len("/v1")]
    return urlunparse((parsed.scheme, parsed.netloc, path.rstrip("/"), "", "", "")).rstrip("/")


def effective_ollama_endpoint(config: Config) -> str:
    """Return the daemon root endpoint used for Ollama health probes.

    Chat and embedding providers use OpenAI-compatible ``/v1`` URLs in config, but
    Ollama's health API lives at daemon root ``/api/version``.
    """
    if _ollama_is_chat_capable(config):
        base_url = config.llm.ollama.base_url.strip() or f"{_DEFAULT_OLLAMA_ENDPOINT}/v1"
    elif _embedding_wants_ollama(config):
        base_url = (
            config.llm.embedding.base_url.strip()
            or config.llm.ollama.base_url.strip()
            or f"{_DEFAULT_OLLAMA_ENDPOINT}/v1"
        )
    else:
        base_url = config.llm.ollama.base_url.strip() or f"{_DEFAULT_OLLAMA_ENDPOINT}/v1"
    return _strip_openai_v1_suffix(base_url)


def is_loopback(url: str) -> bool:
    """Return whether a URL points at the local machine."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ollama_is_running(host: str = _DEFAULT_OLLAMA_ENDPOINT) -> bool:
    """Probe Ollama's HTTP API; return True only on a healthy 200 response."""
    try:
        # trust_env=False — a localhost Ollama probe must not be hijacked by
        # HTTP_PROXY env (e.g. 127.0.0.1:7897 VPN client).
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            response = client.get(f"{host.rstrip('/')}/api/version")
            return response.status_code == 200
    except Exception:
        return False


def _ollama_start_serve_background() -> bool:
    """Start ``ollama serve`` detached, waiting up to 15s for health."""
    import shutil
    import subprocess
    import time

    if _ollama_is_running():
        return True

    ollama = shutil.which("ollama")
    if ollama is None:
        return False

    try:
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            subprocess.Popen(
                [ollama, "serve"],
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                [ollama, "serve"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
    except Exception as exc:
        console.print(f"[red]启动 ollama serve 失败: {exc}[/red]")
        return False

    for _ in range(30):
        if _ollama_is_running():
            return True
        time.sleep(0.5)
    return False
