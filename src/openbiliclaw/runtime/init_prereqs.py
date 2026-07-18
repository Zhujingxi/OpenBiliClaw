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

# Strict readiness: a prereq is "ok" only when a REAL probe request succeeds.
# Success caches longer; a failure/timeout caches briefly so a service that
# just came up (or finished a cold model load) greens within seconds rather
# than staying red for the full success-TTL. Timeout is generous enough to
# cover a cold Ollama load but still fails (not optimistically passes) if the
# service never answers.
#
# The chat probe is a real (billable) completion, so its success TTL is
# generous: a green checkmark going stale for a few minutes is harmless,
# while a 30s TTL meant an open polling page burned a provider request
# every 30s — users spotted the recurring 5-in/10-out lines on their
# DeepSeek bill.
_CHAT_OK_TTL = 300.0
_CHAT_FAIL_TTL = 8.0
_CHAT_PROBE_TIMEOUT = 15.0
_BILI_OK_TTL = 60.0
_BILI_FAIL_TTL = 10.0
_BILI_PROBE_TIMEOUT = 12.0

# A cookie can be perfectly valid while the *probe request* dies in transit.
# The client already bypasses env/system proxies (BilibiliAPIClient
# trust_env=False — proxy exits trip B站 risk control; field report 2026-07),
# so a transport failure here means direct connectivity itself is broken:
# genuine network outage, a TUN/global-mode proxy intercepting at the network
# layer, or a misconfigured [bilibili].proxy override.
_BILI_NETWORK_HINT = (
    "检测已绕过系统代理直连 B站 仍失败：请检查本机网络；"
    "TUN / 全局模式代理请为 bilibili.com 添加直连分流规则；"
    "如果你的网络必须走代理才能访问 B站，可在 config.toml 的 [bilibili] proxy 单独指定。"
)

_PLATFORM_SOURCE_FIELDS = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
)


class InitPrereqs:
    """TTL-cached prerequisite probes bound to a RuntimeContext."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._chat_value = False
        self._chat_at = float("-inf")
        self._chat_lock = asyncio.Lock()
        self._bili_value = "checking"
        self._bili_detail = ""
        self._bili_at = float("-inf")
        self._bili_lock = asyncio.Lock()

    def peek_chat(self) -> bool:
        """Last cached chat probe result, without firing a new probe.

        Used by already-initialized status reads where the checklist is
        informational only — a live (billable) probe is not justified.
        """
        return self._chat_value

    def peek_bilibili(self) -> str:
        """Last cached Bilibili probe result, without firing a new probe."""
        return self._bili_value

    def peek_bilibili_detail(self) -> str:
        """Why the last Bilibili probe failed ("" when it succeeded)."""
        return self._bili_detail

    async def chat_ready(self) -> bool:
        """Whether chat completions can *currently* be served.

        Registry-built is necessary-not-sufficient (a configured Ollama whose
        model was never pulled 404s at call time), so this does a real
        ``health_check`` (tiny completion) — cached, single-flighted, and
        strict on timeout (matches embedding readiness).

        Ordered route connections are probed in configured priority order.
        Every runtime chat call uses that same fallback chain, so a healthy
        fallback means chat genuinely works and init must not be blocked just
        because an earlier connection is down.
        """
        registry = getattr(self._ctx, "llm_registry", None)
        if registry is None:
            return False
        ttl = _CHAT_OK_TTL if self._chat_value else _CHAT_FAIL_TTL
        if time.monotonic() - self._chat_at < ttl:
            return self._chat_value
        async with self._chat_lock:
            ttl = _CHAT_OK_TTL if self._chat_value else _CHAT_FAIL_TTL
            if time.monotonic() - self._chat_at < ttl:
                return self._chat_value
            route_connections = getattr(registry, "connections", ())
            ready = await self._probe_ordered_chat_route(tuple(route_connections))
            self._chat_value = ready
            self._chat_at = time.monotonic()
            return ready

    async def _probe_ordered_chat_route(self, connections: tuple[Any, ...]) -> bool:
        """Probe bound adapters in the same priority order as runtime calls."""
        for position, connection in enumerate(connections):
            provider = getattr(connection, "adapter", None)
            if provider is None or not await self._probe_chat_provider(provider):
                continue
            if position:
                logger.info(
                    "Chat readiness: connection %s answered after %d earlier route failure(s).",
                    str(getattr(connection, "id", "") or position),
                    position,
                )
            return True
        return False

    async def _probe_chat_provider(self, provider: Any) -> bool:
        """One strict, bounded health_check; False on timeout or any error."""
        try:
            return bool(
                await asyncio.wait_for(provider.health_check(), timeout=_CHAT_PROBE_TIMEOUT)
            )
        except TimeoutError:
            # Strict: the prereq must confirm a REAL request succeeded. A
            # timeout means we could NOT confirm the provider answers within
            # a (generous, cold-load-tolerant) window → report not-ready so
            # the checklist never greenlights an unverified chat service.
            logger.debug("Chat readiness probe timed out; reporting not ready")
            return False
        except Exception:
            logger.debug("Chat readiness probe errored", exc_info=True)
            return False

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
            self._bili_detail = "后端还没有收到 B站 Cookie。"
            return "failed"

        ttl = _BILI_OK_TTL if self._bili_value == "ok" else _BILI_FAIL_TTL
        if self._bili_value != "checking" and time.monotonic() - self._bili_at < ttl:
            return self._bili_value

        async with self._bili_lock:
            ttl = _BILI_OK_TTL if self._bili_value == "ok" else _BILI_FAIL_TTL
            if self._bili_value != "checking" and time.monotonic() - self._bili_at < ttl:
                return self._bili_value
            proxy = str(getattr(getattr(cfg, "bilibili", None), "proxy", "") or "").strip()
            # The hint must match the actual transport: default is a direct
            # connection (client bypasses env/system proxies), but an explicit
            # [bilibili].proxy override means the failure is on THAT proxy.
            network_hint = (
                f"当前经 config.toml [bilibili] proxy（{proxy}）检测 B站 失败："
                "请确认该代理可达且能访问 B站，或清空该配置改回直连。"
                if proxy
                else _BILI_NETWORK_HINT
            )
            try:
                manager = AuthManager(data_dir=cfg.data_path, proxy=proxy or None)
                status = await asyncio.wait_for(
                    manager.validate_cookie(cookie), timeout=_BILI_PROBE_TIMEOUT
                )
                if status.authenticated:
                    self._bili_value, self._bili_detail = "ok", ""
                else:
                    self._bili_value = "failed"
                    message = str(getattr(status, "message", "") or "").strip()
                    if getattr(status, "network_error", False):
                        self._bili_detail = f"检测请求失败（{message}）。{network_hint}"
                    else:
                        self._bili_detail = message or "当前 Cookie 未登录或已失效。"
            except TimeoutError:
                logger.debug("Bilibili cookie probe timed out", exc_info=True)
                self._bili_value = "failed"
                self._bili_detail = f"检测超时，B站 接口未在时限内响应。{network_hint}"
            except Exception as exc:
                logger.debug("Bilibili cookie probe errored", exc_info=True)
                self._bili_value = "failed"
                self._bili_detail = f"检测请求失败（{exc}）。{network_hint}"
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
