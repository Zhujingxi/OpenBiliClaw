"""Tests for the process-level outbound-proxy single source of truth."""

from collections.abc import Iterator

import pytest

from openbiliclaw import network


@pytest.fixture(autouse=True)
def _reset_outbound_proxy() -> Iterator[None]:
    """Isolate global proxy state between tests (both directions)."""
    network.reset_outbound_proxy_for_tests()
    yield
    network.reset_outbound_proxy_for_tests()


def test_default_is_none_and_empty_kwargs() -> None:
    assert network.outbound_proxy_url() is None
    assert network.outbound_httpx_kwargs() == {}


def test_set_outbound_proxy_updates_url_and_kwargs() -> None:
    network.set_outbound_proxy("socks5://127.0.0.1:1080")
    assert network.outbound_proxy_url() == "socks5://127.0.0.1:1080"
    assert network.outbound_httpx_kwargs() == {"proxy": "socks5://127.0.0.1:1080"}


def test_set_empty_string_resets_to_none() -> None:
    network.set_outbound_proxy("socks5://127.0.0.1:1080")
    network.set_outbound_proxy("")
    assert network.outbound_proxy_url() is None
    assert network.outbound_httpx_kwargs() == {}


def test_set_whitespace_only_resets_to_none() -> None:
    network.set_outbound_proxy("http://127.0.0.1:7890")
    network.set_outbound_proxy("   ")
    assert network.outbound_proxy_url() is None
