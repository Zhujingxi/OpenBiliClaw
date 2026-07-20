"""Typed narrow dependency bundles for API routers.

Each router module declares a small dataclass with exactly the callables it
needs. ``create_app()`` in ``api/app.py`` constructs these bundles from the
existing closures and injects them into the router factory. This avoids a
broad ``ApiServices`` service-locator while routers are still being
extracted — no router may reach through to ``deps.services.<any-engine>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True)
class SystemRouteDeps:
    """Narrow dependencies for the liveness / QR-info system router.

    ``get_lan_ip`` returns the current cached LAN IP (or ``None`` when no
    usable IPv4 interface is detected). Callers may invoke it on every
    request — the underlying implementation applies a TTL cache, so the
    callable is cheap.
    """

    get_lan_ip: Callable[[], str | None]


@dataclass(frozen=True)
class HealthRouteDeps:
    """Narrow dependencies for the health / init-status router.

    Every field is a closure or state getter bundled by ``create_app()``.
    No broad ``ApiServices`` container — only the closures the two handlers
    actually reference: LAN-IP lookup, embedding-ready probe, profile-ready
    probe, degraded state, and init-status request state.
    """

    get_lan_ip: Callable[[], str | None]
    health_profile_ready: Callable[[], bool | None]
    health_embedding_ready: Callable[..., Awaitable[bool]]
    embedding_required_for_init: Callable[[], bool]
    diagnose_embedding: Callable[..., Awaitable[tuple[str, str]]]
    embedding_pull_progress_view: Callable[[], dict[str, object]]
    progress_int: Callable[[object], int]
    degraded_issues_payload: Callable[[], list[dict[str, str]]]
    get_auth_gate: Callable[[], Any]
    get_init_coordinator: Callable[[], Any]
    get_init_prereqs: Callable[[], Any]
    get_account_sync_service: Callable[[], Any]
    degraded: Callable[[], bool]
    degraded_reason: Callable[[], str]


__all__ = ["SystemRouteDeps", "HealthRouteDeps"]
