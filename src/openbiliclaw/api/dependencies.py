"""Typed narrow dependency bundles for API routers.

Each router module declares a small dataclass with exactly the callables it
needs. ``create_app()`` in ``api/app.py`` constructs these bundles from the
existing closures and injects them into the router factory. This avoids a
broad ``ApiServices`` service-locator while routers are still being
extracted — no router may reach through to ``deps.services.<any-engine>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class SystemRouteDeps:
    """Narrow dependencies for the liveness / QR-info system router.

    ``get_lan_ip`` returns the current cached LAN IP (or ``None`` when no
    usable IPv4 interface is detected). Callers may invoke it on every
    request — the underlying implementation applies a TTL cache, so the
    callable is cheap.
    """

    get_lan_ip: Callable[[], str | None]


__all__ = ["SystemRouteDeps"]
