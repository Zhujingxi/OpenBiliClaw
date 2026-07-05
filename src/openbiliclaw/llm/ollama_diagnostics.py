"""Ollama embedding diagnostics + self-repair (v0.3.155+).

Answers "为什么向量模型不可用" with a *classified* cause instead of a bare
retry-and-fail, and can re-pull the embedding model in place. Extracted
from field logs (2026-07-05): a user's ``bge-m3`` returned HTTP 500 for
an hour while the UI only offered a dead「重试」button — nothing said
whether Ollama was down, the model was missing, or the model was broken.

Pure functions over ``base_url`` + ``model`` so both the provider-backed
path (EmbeddingService is built) and the config-only path (registry
returned ``None``) can use them, and tests can inject an
``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Diagnosis codes surfaced to the UI (init-status prerequisites and the
# repair endpoint). Keep in sync with the popup's EMBEDDING_CHECK_TEXT.
DIAG_OK = "ok"
DIAG_NOT_RUNNING = "not_running"
DIAG_MODEL_MISSING = "model_missing"
DIAG_MODEL_BROKEN = "model_broken"
DIAG_ERROR = "error"

_TAGS_TIMEOUT_SECONDS = 5.0
# One embed probe on an installed model: absorbs the cold-load from disk
# (same rationale as OllamaProvider's embed timeout, but a diagnosis can
# afford to be a bit less patient than production traffic).
_PROBE_TIMEOUT_SECONDS = 60.0
# /api/pull streams NDJSON progress lines; between-chunk gaps are bounded
# by network stalls, not model size, so a per-read timeout is the right
# shape (a 568MB pull can legitimately take many minutes overall).
_PULL_READ_TIMEOUT_SECONDS = 120.0


def native_root(base_url: str) -> str:
    """Strip the OpenAI-compat ``/v1`` suffix to reach Ollama's native API root."""
    return base_url.rstrip("/").rsplit("/v1", 1)[0]


def _model_names_match(installed: str, wanted: str) -> bool:
    """``bge-m3:latest`` (tags) matches ``bge-m3`` (config), and vice versa."""
    return installed.split(":", 1)[0] == wanted.split(":", 1)[0]


def _error_snippet(response: httpx.Response) -> str:
    """Ollama puts the useful part in ``{"error": ...}`` — surface it."""
    try:
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])[:200]
    except Exception:
        pass
    return response.text[:200]


async def diagnose_ollama_embedding(
    base_url: str,
    model: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, str]:
    """Classify why the Ollama embedding path is (or isn't) working.

    Returns ``(code, detail)``; ``detail`` is a short human-readable
    Chinese hint ("" when ok). Codes: ``ok`` / ``not_running`` /
    ``model_missing`` / ``model_broken`` / ``error``.

    Order matters: /api/tags first (definitive "is it running / is the
    model installed"), then one real embed probe — a model can be listed
    yet fail to load (incomplete download, OOM), which is exactly the
    500-forever case this exists to name.
    """
    root = native_root(base_url)
    # trust_env=False for the same reason as OllamaProvider.embed: user
    # proxies must not hijack localhost calls.
    client_kwargs: dict[str, Any] = {"trust_env": False}
    if transport is not None:
        client_kwargs["transport"] = transport

    async with httpx.AsyncClient(**client_kwargs) as client:
        try:
            tags = await client.get(f"{root}/api/tags", timeout=_TAGS_TIMEOUT_SECONDS)
        except Exception as exc:
            return (
                DIAG_NOT_RUNNING,
                f"Ollama 服务无法连接（{root}）：{type(exc).__name__}。"
                "请启动 Ollama（或运行 `ollama serve`）；"
                "还没安装的话，去 ollama.com/download 下载，"
                "或在终端运行 `openbiliclaw setup-embedding` 一键装好。",
            )
        if tags.status_code != 200:
            return (
                DIAG_ERROR,
                f"Ollama 响应异常（GET /api/tags -> {tags.status_code}）：{_error_snippet(tags)}",
            )
        try:
            models = tags.json().get("models") or []
            installed = [str(m.get("name") or "") for m in models if isinstance(m, dict)]
        except Exception:
            installed = []
        if not any(_model_names_match(name, model) for name in installed):
            return (
                DIAG_MODEL_MISSING,
                f"Ollama 已在运行，但没有安装 {model} 模型。"
                f"可一键修复自动拉取，或手动运行 `ollama pull {model}`。",
            )

        try:
            probe = await client.post(
                f"{root}/api/embeddings",
                json={"model": model, "prompt": "ping"},
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            return (
                DIAG_MODEL_BROKEN,
                f"{model} 已安装但调用失败（{type(exc).__name__}）。"
                f"建议 `ollama pull {model}` 重新拉取，或重启 Ollama。",
            )
        if probe.status_code != 200:
            return (
                DIAG_MODEL_BROKEN,
                f"{model} 已安装但调用返回 HTTP {probe.status_code}"
                f"（{_error_snippet(probe)}）。可能下载不完整或内存不足："
                f"可一键修复重新拉取，或重启 Ollama 后重试。",
            )
        try:
            vec = probe.json().get("embedding")
        except Exception:
            vec = None
        if not isinstance(vec, list) or not vec:
            return (
                DIAG_MODEL_BROKEN,
                f"{model} 返回了空向量。建议 `ollama pull {model}` 重新拉取。",
            )
        return (DIAG_OK, "")


async def pull_ollama_model(
    base_url: str,
    model: str,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bool, str]:
    """(Re-)pull ``model`` via Ollama's native ``/api/pull``, streaming progress.

    ``on_progress(status, completed, total)`` fires per NDJSON line
    (total may be 0 while Ollama resolves the manifest). Re-pulling an
    installed-but-corrupt model is safe: Ollama re-verifies layer digests
    and re-downloads what's broken.

    Returns ``(ok, error_detail)``.
    """
    root = native_root(base_url)
    client_kwargs: dict[str, Any] = {"trust_env": False}
    if transport is not None:
        client_kwargs["transport"] = transport
    timeout = httpx.Timeout(_PULL_READ_TIMEOUT_SECONDS, connect=10.0)

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:  # noqa: SIM117
            async with client.stream(
                "POST",
                f"{root}/api/pull",
                json={"name": model, "stream": True},
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    return (False, f"HTTP {response.status_code}: {_error_snippet(response)}")
                succeeded = False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        return (False, str(event["error"])[:200])
                    status = str(event.get("status") or "")
                    if on_progress is not None:
                        on_progress(
                            status,
                            int(event.get("completed") or 0),
                            int(event.get("total") or 0),
                        )
                    if status == "success":
                        succeeded = True
                if succeeded:
                    return (True, "")
                return (False, "拉取流结束但未收到 success 状态")
    except Exception as exc:
        logger.warning("Ollama pull %s failed", model, exc_info=True)
        return (False, f"{type(exc).__name__}: {exc}")
