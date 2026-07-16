"""Tests for the process-level outbound-proxy single source of truth."""

import os
from collections.abc import Iterator
from pathlib import Path

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


def test_system_mode_drops_missing_ca_paths_without_dropping_proxy_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale SSL_CERT_FILE must not crash every trust_env=True client.

    Regression for issue #113's Windows log: httpx builds its SSL context at
    client construction time and raises FileNotFoundError before any request
    when a CA environment variable points at a deleted file. The invalid CA
    override should fall back to the normal verified trust store while proxy
    variables remain available in ``system`` mode.
    """
    import httpx

    missing = f"{tmp_path}/missing-ca.pem"
    monkeypatch.setenv("SSL_CERT_FILE", missing)
    monkeypatch.setenv("SSL_CERT_DIR", f"{tmp_path}/missing-certs")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", missing)
    monkeypatch.setenv("CURL_CA_BUNDLE", missing)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")

    network.set_outbound_proxy("", mode="system")

    assert "SSL_CERT_FILE" not in os.environ
    assert "SSL_CERT_DIR" not in os.environ
    assert "REQUESTS_CA_BUNDLE" not in os.environ
    assert "CURL_CA_BUNDLE" not in os.environ
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    # Construction itself used to raise FileNotFoundError here. TLS
    # verification remains enabled because no ``verify=False`` is supplied.
    with httpx.Client(**network.outbound_httpx_kwargs()):
        pass


def test_system_mode_preserves_existing_custom_ca_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ca_file = tmp_path / "custom-ca.pem"
    ca_dir = tmp_path / "custom-ca-dir"
    ca_file.write_text("placeholder", encoding="utf-8")
    ca_dir.mkdir()
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    monkeypatch.setenv("SSL_CERT_DIR", str(ca_dir))

    network.set_outbound_proxy("", mode="system")

    assert os.environ["SSL_CERT_FILE"] == str(ca_file)
    assert os.environ["SSL_CERT_DIR"] == str(ca_dir)


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
