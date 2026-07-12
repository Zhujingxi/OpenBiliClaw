"""Process-level single source of truth for the overseas-outbound proxy.

``[network].proxy`` (see :class:`openbiliclaw.config.NetworkConfig`) governs
whether overseas clients — the LLM SDKs, YouTube, the GitHub updater, and Codex
OAuth — route through a proxy. Those construction points do not all hold a
``Config`` reference, so instead of threading the value through every call site
we mirror the resolved value here once. ``set_outbound_proxy`` is invoked at the
three config-application points (``create_app``, CLI entry, ``update_config``
hot reload); every consumer reads :func:`outbound_httpx_kwargs` /
:func:`outbound_proxy_url`.

CN-direct clients (bilibili / douyin / ollama / CN-CDN image cache) MUST NOT
read this module — that isolation is pinned by
``tests/test_network_proxy_isolation.py``.
"""

from __future__ import annotations

from typing import Any

_outbound_proxy: str | None = None


def set_outbound_proxy(url: str) -> None:
    """Set the process-wide overseas proxy. Empty / whitespace disables it."""
    global _outbound_proxy
    _outbound_proxy = url.strip() or None


def outbound_proxy_url() -> str | None:
    """Return the active proxy URL, or ``None`` when disabled."""
    return _outbound_proxy


def outbound_httpx_kwargs() -> dict[str, Any]:
    """Return ``{"proxy": url}`` when set, else ``{}``.

    Spreading ``{}`` at a client construction point leaves it byte-equivalent
    to the pre-feature call (invariant: empty-value zero drift).
    """
    if _outbound_proxy is None:
        return {}
    return {"proxy": _outbound_proxy}


def reset_outbound_proxy_for_tests() -> None:
    """Reset global state. Test-only; not part of the runtime contract."""
    global _outbound_proxy
    _outbound_proxy = None
