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


def test_default_is_direct_and_ignores_environment_proxy() -> None:
    assert network.outbound_proxy_mode() == "direct"
    assert network.outbound_proxy_url() is None
    assert network.outbound_httpx_kwargs() == {"trust_env": False}


def test_set_outbound_proxy_updates_url_and_kwargs() -> None:
    network.set_outbound_proxy("socks5://127.0.0.1:1080")
    assert network.outbound_proxy_mode() == "custom"
    assert network.outbound_proxy_url() == "socks5://127.0.0.1:1080"
    assert network.outbound_httpx_kwargs() == {
        "proxy": "socks5://127.0.0.1:1080",
        "trust_env": False,
    }


def test_set_empty_string_resets_to_none() -> None:
    network.set_outbound_proxy("socks5://127.0.0.1:1080")
    network.set_outbound_proxy("")
    assert network.outbound_proxy_mode() == "direct"
    assert network.outbound_proxy_url() is None
    assert network.outbound_httpx_kwargs() == {"trust_env": False}


def test_set_whitespace_only_resets_to_none() -> None:
    network.set_outbound_proxy("http://127.0.0.1:7890")
    network.set_outbound_proxy("   ")
    assert network.outbound_proxy_url() is None


def test_system_mode_inherits_environment_without_explicit_proxy() -> None:
    network.set_outbound_proxy("", mode="system")

    assert network.outbound_proxy_mode() == "system"
    assert network.outbound_proxy_url() is None
    assert network.outbound_httpx_kwargs() == {"trust_env": True}
    assert network.outbound_requests_proxies() is None
    assert network.outbound_ytdlp_proxy() is None


def test_direct_mode_disables_environment_for_all_supported_http_stacks() -> None:
    network.set_outbound_proxy("", mode="direct")

    assert network.outbound_requests_proxies() == {"http": "", "https": ""}
    assert network.outbound_ytdlp_proxy() == ""


def test_custom_mode_exposes_explicit_proxy_for_all_supported_http_stacks() -> None:
    url = "http://127.0.0.1:7897"
    network.set_outbound_proxy(url, mode="custom")

    assert network.outbound_requests_proxies() == {"http": url, "https": url}
    assert network.outbound_ytdlp_proxy() == url


def test_custom_mode_requires_a_proxy_url() -> None:
    with pytest.raises(ValueError, match="custom"):
        network.set_outbound_proxy("", mode="custom")


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="mode"):
        network.set_outbound_proxy("", mode="auto")
