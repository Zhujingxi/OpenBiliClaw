"""Process-level single source of truth for overseas network routing.

``[network].mode`` selects ``direct`` (ignore env/system proxies), ``system``
(inherit them), or ``custom`` (use ``[network].proxy`` explicitly). Those
construction points do not all hold a ``Config`` reference, so the resolved
policy is mirrored here once and consumed by every supported HTTP stack.

CN-direct clients (bilibili / douyin / ollama / CN-CDN image cache) MUST NOT
read this module — that isolation is pinned by
``tests/test_network_proxy_isolation.py``.
"""

from __future__ import annotations

from typing import Any, Literal, cast

OutboundProxyMode = Literal["direct", "system", "custom"]
_VALID_MODES = frozenset({"direct", "system", "custom"})
_outbound_proxy: str | None = None
_outbound_mode: OutboundProxyMode = "direct"


def set_outbound_proxy(url: str, *, mode: str | None = None) -> None:
    """Set the process-wide overseas routing policy.

    ``mode=None`` preserves the original helper's call contract: a non-empty
    URL means ``custom`` and an empty URL means ``direct``. Config-aware call
    sites always pass the explicit mode.
    """
    global _outbound_mode, _outbound_proxy
    normalized_url = url.strip()
    resolved_mode = (mode or ("custom" if normalized_url else "direct")).strip().lower()
    if resolved_mode not in _VALID_MODES:
        raise ValueError(f"unsupported outbound proxy mode: {resolved_mode}")
    if resolved_mode == "custom" and not normalized_url:
        raise ValueError("custom outbound proxy mode requires a proxy URL")
    _outbound_mode = cast("OutboundProxyMode", resolved_mode)
    _outbound_proxy = normalized_url if resolved_mode == "custom" else None


def outbound_proxy_mode() -> OutboundProxyMode:
    """Return the active routing mode."""
    return _outbound_mode


def outbound_proxy_url() -> str | None:
    """Return the active proxy URL, or ``None`` when disabled."""
    return _outbound_proxy


def outbound_httpx_kwargs() -> dict[str, Any]:
    """Return explicit kwargs for an ``httpx`` client construction."""
    return httpx_kwargs_for(_outbound_mode, _outbound_proxy or "")


def httpx_kwargs_for(mode: str, proxy: str = "") -> dict[str, Any]:
    """Resolve ``httpx`` kwargs for a routing policy without mutating globals."""
    normalized_mode = mode.strip().lower()
    if normalized_mode not in _VALID_MODES:
        raise ValueError(f"unsupported outbound proxy mode: {normalized_mode}")
    normalized_proxy = proxy.strip()
    if normalized_mode == "system":
        return {"trust_env": True}
    if normalized_mode == "custom":
        if not normalized_proxy:
            raise ValueError("custom outbound proxy mode requires a proxy URL")
        return {"proxy": normalized_proxy, "trust_env": False}
    return {"trust_env": False}


def outbound_trust_env() -> bool:
    """Whether SDK-owned clients should inherit environment proxy settings."""
    return _outbound_mode == "system"


def outbound_requests_proxies() -> dict[str, str] | None:
    """Return a ``requests``/scrapetube proxy mapping for the active mode.

    Empty per-scheme values deliberately override ``requests`` environment
    proxies. ``None`` is reserved for explicit ``system`` inheritance.
    """
    if _outbound_mode == "system":
        return None
    value = _outbound_proxy or ""
    return {"http": value, "https": value}


def outbound_ytdlp_proxy() -> str | None:
    """Return yt-dlp's proxy option (``""`` means force direct)."""
    if _outbound_mode == "system":
        return None
    return _outbound_proxy or ""


def reset_outbound_proxy_for_tests() -> None:
    """Reset global state. Test-only; not part of the runtime contract."""
    global _outbound_mode, _outbound_proxy
    _outbound_mode = "direct"
    _outbound_proxy = None
