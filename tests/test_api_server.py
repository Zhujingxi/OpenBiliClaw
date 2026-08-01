from __future__ import annotations

import socket
from typing import Any

from openbiliclaw.runtime import api_server


class _FakeSocket:
    def __init__(self, family: int, *, ipv6_bind_error: bool = False) -> None:
        self.family = family
        self.ipv6_bind_error = ipv6_bind_error
        self.bound: tuple[Any, ...] | None = None
        self.closed = False

    def setsockopt(self, *args: object) -> None:
        pass

    def bind(self, address: tuple[Any, ...]) -> None:
        if self.family == socket.AF_INET6 and self.ipv6_bind_error:
            raise OSError("IPv6 disabled")
        self.bound = address

    def listen(self, backlog: int) -> None:
        pass

    def setblocking(self, enabled: bool) -> None:
        pass

    def getsockname(self) -> tuple[str, int]:
        return ("0.0.0.0", 18420)

    def close(self) -> None:
        self.closed = True


def test_default_wildcard_creates_separate_ipv4_and_ipv6_listeners(monkeypatch) -> None:
    created: list[_FakeSocket] = []

    def _socket(family: int, kind: int) -> _FakeSocket:
        item = _FakeSocket(family)
        created.append(item)
        return item

    monkeypatch.setattr(api_server.socket, "has_ipv6", True)
    monkeypatch.setattr(api_server.socket, "socket", _socket)

    listeners = api_server.create_wildcard_listener_sockets("0.0.0.0", 18420)

    assert listeners == created
    assert [item.family for item in created] == [socket.AF_INET, socket.AF_INET6]
    assert created[0].bound == ("0.0.0.0", 18420)
    assert created[1].bound == ("::", 18420)


def test_ipv6_listener_failure_keeps_ipv4_available(monkeypatch) -> None:
    created: list[_FakeSocket] = []

    def _socket(family: int, kind: int) -> _FakeSocket:
        item = _FakeSocket(family, ipv6_bind_error=True)
        created.append(item)
        return item

    monkeypatch.setattr(api_server.socket, "has_ipv6", True)
    monkeypatch.setattr(api_server.socket, "socket", _socket)

    listeners = api_server.create_wildcard_listener_sockets("0.0.0.0", 18420)

    assert listeners == [created[0]]
    assert created[1].closed is True


def test_non_ipv4_wildcard_uses_uvicorn_normal_binding() -> None:
    assert api_server.create_wildcard_listener_sockets("127.0.0.1", 8420) is None
    assert api_server.create_wildcard_listener_sockets("::", 8420) is None
