"""Socket helpers for serving the local API over IPv4 and IPv6."""

from __future__ import annotations

import logging
import socket
from contextlib import suppress

logger = logging.getLogger(__name__)


def create_wildcard_listener_sockets(host: str, port: int) -> list[socket.socket] | None:
    """Create separate IPv4 and IPv6 listeners for the default wildcard host.

    Uvicorn creates one address-family socket from ``host``.  Binding to
    ``0.0.0.0`` therefore excludes IPv6, while relying on an ``::`` socket to
    accept IPv4-mapped connections is platform-dependent.  Separate sockets,
    with ``IPV6_V6ONLY`` enabled on the IPv6 listener, provide predictable
    dual-stack behaviour.  IPv4 remains available when the OS has no IPv6.
    """
    if host != "0.0.0.0":
        return None

    listeners: list[socket.socket] = []
    ipv4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        ipv4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ipv4.bind(("0.0.0.0", port))
        ipv4.listen(socket.SOMAXCONN)
        ipv4.setblocking(False)
        listeners.append(ipv4)
        listener_port = int(ipv4.getsockname()[1])

        if not socket.has_ipv6:
            return listeners

        ipv6: socket.socket | None = None
        try:
            ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            ipv6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            ipv6.bind(("::", listener_port))
            ipv6.listen(socket.SOMAXCONN)
            ipv6.setblocking(False)
            listeners.append(ipv6)
        except OSError as exc:
            if ipv6 is not None:
                ipv6.close()
            logger.warning("IPv6 API listener unavailable on [::]:%d: %s", listener_port, exc)
        return listeners
    except BaseException:
        for listener in listeners:
            with suppress(OSError):
                listener.close()
        if not listeners:
            with suppress(OSError):
                ipv4.close()
        raise


def close_listener_sockets(listeners: list[socket.socket] | None) -> None:
    """Close listener sockets created by :func:`create_wildcard_listener_sockets`."""
    for listener in listeners or []:
        with suppress(OSError):
            listener.close()
