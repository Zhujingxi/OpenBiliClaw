"""Process-global progress for managed Ollama embedding setup."""

from __future__ import annotations

import threading
import time
from typing import Final

_VALID_OLLAMA_PHASES: Final = {"starting", "ready", "down"}
_MB: Final = 1024 * 1024

_lock = threading.Lock()
_pull_state: dict[str, object] = {
    "running": False,
    "model": "",
    "status": "",
    "completed": 0,
    "total": 0,
    "done": False,
    "ok": False,
    "error": "",
    "started_monotonic": 0.0,
}
_ollama_phase = "ready"


def _as_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        return 0
    try:
        return int(str(value))
    except ValueError:
        return 0


def _status_text(state: dict[str, object]) -> str:
    model = str(state.get("model") or "向量模型")
    completed = _as_int(state.get("completed"))
    total = _as_int(state.get("total"))
    status = str(state.get("status") or "").strip()
    running = bool(state.get("running"))
    if total > 0:
        cap = 99 if running else 100
        pct = min(cap, max(0, round(completed * 100 / total)))
        done_mb = completed // _MB
        total_mb = total // _MB
        return f"正在下载 {model}：{pct}%（{done_mb}MB/{total_mb}MB）"
    if status:
        return f"正在下载 {model}：{status}"
    if running:
        return f"正在下载 {model}（准备中…）"
    return ""


def mark_pull_running(model: str) -> None:
    """Start or replace the process-global embedding pull progress."""
    with _lock:
        _pull_state.update(
            {
                "running": True,
                "model": model,
                "status": "",
                "completed": 0,
                "total": 0,
                "done": False,
                "ok": False,
                "error": "",
                "started_monotonic": time.monotonic(),
            }
        )


def report_pull(status: str, completed: int, total: int) -> None:
    """Record one streamed Ollama pull progress event."""
    with _lock:
        started = _pull_state.get("started_monotonic")
        if not isinstance(started, int | float) or started <= 0:
            _pull_state["started_monotonic"] = time.monotonic()
        _pull_state.update(
            {
                "status": status,
                "completed": max(0, int(completed)),
                "total": max(0, int(total)),
            }
        )


def mark_pull_done(ok: bool, error: str) -> None:
    """Mark the current embedding pull as terminal."""
    with _lock:
        _pull_state.update(
            {
                "running": False,
                "done": True,
                "ok": bool(ok),
                "error": str(error or ""),
            }
        )


def snapshot() -> dict[str, object]:
    """Return a lock-consistent snapshot of process-global pull progress."""
    with _lock:
        state = dict(_pull_state)
        state["status_text"] = _status_text(state)
        return state


def report_ollama_phase(phase: str) -> None:
    """Record the current managed-Ollama daemon phase."""
    if phase not in _VALID_OLLAMA_PHASES:
        raise ValueError(f"invalid Ollama phase: {phase}")
    global _ollama_phase
    with _lock:
        _ollama_phase = phase


def ollama_phase() -> str:
    """Return the latest managed-Ollama daemon phase."""
    with _lock:
        return _ollama_phase
