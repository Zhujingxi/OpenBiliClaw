"""Cached prerequisite probes for guided init (gui-init spec §3, plan C1).

These feed the ``prerequisites`` block of ``GET /api/init-status``. All probes
are TTL-cached + single-flighted so a polling UI never hammers the chat
provider or Bilibili (validate_cookie alone is a ~30s round-trip). Bound to a
RuntimeContext and read ``ctx.llm_registry`` / ``ctx.config`` lazily.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from openbiliclaw.bilibili.auth import AuthManager

logger = logging.getLogger(__name__)

_CHAT_READY_TTL = 30.0
_CHAT_PROBE_TIMEOUT = 8.0
_BILI_OK_TTL = 60.0
_BILI_FAIL_TTL = 10.0
_BILI_PROBE_TIMEOUT = 12.0

_PLATFORM_SOURCE_FIELDS = ("bilibili", "xiaohongshu", "douyin", "youtube")


class InitPrereqs:
    """TTL-cached prerequisite probes bound to a RuntimeContext."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._chat_value = False
        self._chat_at = float("-inf")
        self._chat_lock = asyncio.Lock()
        self._bili_value = "checking"
        self._bili_at = float("-inf")
        self._bili_lock = asyncio.Lock()

    async def chat_ready(self) -> bool:
        """Whether the default chat provider can *currently* complete.

        Registry-built is necessary-not-sufficient (a configured Ollama whose
        model was never pulled 404s at call time), so this does a real
        ``health_check`` (tiny completion) — cached, single-flighted, and
        optimistic on a cold-load timeout (matches embedding readiness).
        """
        registry = getattr(self._ctx, "llm_registry", None)
        if registry is None:
            return False
        if time.monotonic() - self._chat_at < _CHAT_READY_TTL:
            return self._chat_value
        async with self._chat_lock:
            if time.monotonic() - self._chat_at < _CHAT_READY_TTL:
                return self._chat_value
            try:
                provider = registry.get()  # default chat provider
                ready = bool(
                    await asyncio.wait_for(provider.health_check(), timeout=_CHAT_PROBE_TIMEOUT)
                )
            except TimeoutError:
                # Cold model load, not a hard failure — be optimistic and let
                # the init pipeline surface a real error if it's truly down.
                logger.debug("Chat readiness probe timed out; optimistic ready")
                ready = True
            except Exception:
                logger.debug("Chat readiness probe errored", exc_info=True)
                ready = False
            self._chat_value = ready
            self._chat_at = time.monotonic()
            return ready

    async def bilibili_check(self) -> str:
        """``ok`` / ``failed`` / ``checking`` for the configured B站 cookie.

        Real validation (validate_cookie hits B站 nav) but TTL-cached so polls
        don't repeat the ~30s round-trip: success cached 60s, failure 10s.
        """
        cfg = getattr(self._ctx, "config", None)
        cookie = ""
        if cfg is not None:
            cookie = str(getattr(getattr(cfg, "bilibili", None), "cookie", "") or "").strip()
        if cfg is None or not cookie:
            return "failed"

        ttl = _BILI_OK_TTL if self._bili_value == "ok" else _BILI_FAIL_TTL
        if self._bili_value != "checking" and time.monotonic() - self._bili_at < ttl:
            return self._bili_value

        async with self._bili_lock:
            ttl = _BILI_OK_TTL if self._bili_value == "ok" else _BILI_FAIL_TTL
            if self._bili_value != "checking" and time.monotonic() - self._bili_at < ttl:
                return self._bili_value
            try:
                manager = AuthManager(data_dir=cfg.data_path)
                status = await asyncio.wait_for(
                    manager.validate_cookie(cookie), timeout=_BILI_PROBE_TIMEOUT
                )
                self._bili_value = "ok" if status.authenticated else "failed"
            except Exception:
                logger.debug("Bilibili cookie probe errored/timed out", exc_info=True)
                self._bili_value = "failed"
            self._bili_at = time.monotonic()
            return self._bili_value

    def enabled_platforms(self) -> list[str]:
        """Platform source families currently enabled in config."""
        sources = getattr(getattr(self._ctx, "config", None), "sources", None)
        if sources is None:
            return []
        return [
            name
            for name in _PLATFORM_SOURCE_FIELDS
            if getattr(getattr(sources, name, None), "enabled", False)
        ]
