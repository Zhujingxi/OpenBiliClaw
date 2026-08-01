"""Tests for the optional TLS reverse proxy."""

from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import tomllib
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import typer
from cryptography import x509
from typer.testing import CliRunner

from openbiliclaw import tls_proxy as _tls_proxy_mod
from openbiliclaw.cli import _start_tls_proxy_if_enabled, app
from openbiliclaw.config import (
    ConfigError,
    TlsProxyConfig,
    _build_tls_proxy,
    load_config,
    normalize_tls_cert_dir,
    normalize_tls_proxy_port,
    normalize_tls_san_names,
    save_config,
)
from openbiliclaw.tls_proxy import (
    ProxyHandler,
    _build_san_entries,
    _ensure_certs,
    _origin_allowed,
    _parse_san_names,
    _rewrite_origin,
    backend_connect_host,
    create_tls_proxy_server,
)


def _cert_globals(tmp_path: Path, *, sans: list[str], auto: bool = True) -> dict[str, Any]:
    return {
        "_CERT_DIR": str(tmp_path),
        "_CERT_FILE": str(tmp_path / "srv.crt"),
        "_KEY_FILE": str(tmp_path / "srv.key"),
        "_CA_FILE": str(tmp_path / "ca.crt"),
        "_CRL_FILE": str(tmp_path / "ca.crl"),
        "_AUTO_GEN": auto,
        "_SAN_NAMES": sans,
        "_PORT": 8443,
    }


@contextmanager
def _serving(server: ThreadingHTTPServer) -> Any:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _unverified_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class _BackendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls: list[dict[str, Any]] = []
    websocket_payloads: list[bytes] = []

    def _record(self, body: bytes = b"") -> dict[str, Any]:
        record = {
            "method": self.command,
            "path": self.path,
            "host": self.headers.get("Host"),
            "origin": self.headers.get("Origin"),
            "body": body.decode("utf-8", errors="replace"),
        }
        self.calls.append(record)
        return record

    def _send(
        self,
        body: bytes,
        *,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.send_response(200)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/ws":
            self._websocket()
            return
        record = self._record()
        if self.path == "/cookies":
            self._send(
                b"cookies",
                headers=(
                    ("Set-Cookie", "obc_session=abc; HttpOnly; Path=/; SameSite=lax"),
                    ("Set-Cookie", "secondary=xyz; Path=/"),
                ),
            )
            return
        if self.path == "/empty":
            self._send(b"")
            return
        self._send(json.dumps(record, sort_keys=True).encode())

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        record = self._record(body)
        self._send(json.dumps(record, sort_keys=True).encode())

    def do_HEAD(self) -> None:  # noqa: N802
        self._record()
        self.send_response(200)
        self.send_header("Content-Length", "7")
        self.end_headers()

    def _websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(
            hashlib.sha1(  # noqa: S324 - mandated by RFC 6455 handshake
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        ).decode()
        self.send_response(101)
        self.send_header("Connection", "Upgrade")
        self.send_header("Upgrade", "websocket")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        header = self.rfile.read(2)
        if len(header) != 2:
            return
        length = header[1] & 0x7F
        assert header[1] & 0x80
        mask = self.rfile.read(4)
        payload = bytes(
            value ^ mask[index % 4] for index, value in enumerate(self.rfile.read(length))
        )
        self.websocket_payloads.append(payload)
        self.connection.sendall(bytes((0x81, len(payload))) + payload)
        self.close_connection = True

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def proxy_stack(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    _BackendHandler.calls.clear()
    _BackendHandler.websocket_payloads.clear()
    backend = ThreadingHTTPServer(("127.0.0.1", 0), _BackendHandler)
    cert_dir = tmp_path_factory.mktemp("tls-integration-certs")
    proxy = create_tls_proxy_server(
        host="127.0.0.1",
        port=_free_port(),
        backend_host="127.0.0.1",
        backend_port=backend.server_port,
        cert_dir=str(cert_dir),
        auto_gen_certs=True,
        san_names=["127.0.0.1"],
    )
    with _serving(backend), _serving(proxy):
        yield {
            "backend": backend,
            "proxy": proxy,
            "port": proxy.server_port,
            "cert_dir": cert_dir,
        }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _https_request(
    stack: dict[str, Any],
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[http.client.HTTPResponse, bytes]:
    conn = http.client.HTTPSConnection(
        "127.0.0.1",
        stack["port"],
        context=_unverified_tls_context(),
        timeout=5,
    )
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


class TestOriginAllowed:
    def test_absent_origin_is_allowed_for_non_browser_clients(self) -> None:
        assert _origin_allowed(None, "example.test:8443")
        assert _origin_allowed("", "example.test:8443")

    @pytest.mark.parametrize(
        "origin",
        ["chrome-extension://abcdefghijklmnop", "moz-extension://a1b2-c3d4"],
    )
    def test_valid_extension_origins_are_allowed(self, origin: str) -> None:
        assert _origin_allowed(origin, "example.test:8443")

    @pytest.mark.parametrize(
        "origin",
        [
            "chrome-extension://",
            "chrome-extension://id/path",
            "chrome-extension://id@evil",
            "moz-extension://id?query=1",
            "chrome-extension:https://id",
        ],
    )
    def test_malformed_extension_origins_are_rejected(self, origin: str) -> None:
        assert not _origin_allowed(origin, "example.test:8443")

    @pytest.mark.parametrize(
        ("origin", "host"),
        [
            ("https://example.test:8443", "example.test:8443"),
            ("https://192.168.1.8:8443", "192.168.1.8:8443"),
            ("https://example.test", "example.test"),
            ("https://example.test:443", "example.test"),
            ("https://example.test", "example.test:443"),
            ("https://[2001:db8::1]:8443", "[2001:db8::1]:8443"),
            ("https://[2001:db8::1]", "[2001:0db8:0:0::1]:443"),
        ],
    )
    def test_exact_web_origin_matches_normalized_host(self, origin: str, host: str) -> None:
        assert _origin_allowed(origin, host)

    @pytest.mark.parametrize(
        ("origin", "host"),
        [
            ("https://evil.com:8443", "example.test:8443"),
            ("https://example.test:9443", "example.test:8443"),
            ("http://example.test:8443", "example.test:8443"),
            ("https://example.test:8443/path", "example.test:8443"),
            ("https://user@example.test:8443", "example.test:8443"),
            ("null", "example.test:8443"),
            ("https://example.test:8443", "example.test:bad"),
            ("https://[2001:db8::1]:8443", "2001:db8::1:8443"),
            ("https://example.test:8443", "evil.test:8443, example.test:8443"),
        ],
    )
    def test_foreign_or_malformed_origin_host_is_rejected(self, origin: str, host: str) -> None:
        assert not _origin_allowed(origin, host)

    def test_origin_rewrite_uses_request_host_authority(self) -> None:
        assert _rewrite_origin("https://example.test:443", "example.test") == "http://example.test"
        assert _rewrite_origin("moz-extension://id", "example.test") == "moz-extension://id"


class TestCertificateHandling:
    def test_san_entries_include_local_and_remote_names(self) -> None:
        entries = _build_san_entries(["192.168.1.20", "Host.LAN", "2001:db8::1"])
        dns_names = [entry.value for entry in entries if isinstance(entry, x509.DNSName)]
        ip_names = [str(entry.value) for entry in entries if isinstance(entry, x509.IPAddress)]
        assert dns_names == ["localhost", "host.lan"]
        assert ip_names == ["127.0.0.1", "192.168.1.20", "2001:db8::1"]

    def test_empty_san_entry_is_ignored(self) -> None:
        assert len(_build_san_entries([""])) == 2

    def test_generation_is_complete_and_uses_safe_key_permissions(self, tmp_path: Path) -> None:
        with patch.multiple(
            _tls_proxy_mod,
            **_cert_globals(tmp_path, sans=["10.0.0.5", "bili.server.lan"]),
        ):
            _ensure_certs()

        expected = {"srv.crt", "srv.key", "ca.crt", "ca.key", "ca.crl"}
        assert expected <= {path.name for path in tmp_path.iterdir()}
        assert (tmp_path / "srv.key").stat().st_mode & 0o777 == 0o600
        assert (tmp_path / "ca.key").stat().st_mode & 0o777 == 0o600
        cert = x509.load_pem_x509_certificate((tmp_path / "srv.crt").read_bytes())
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert "bili.server.lan" in sans.get_values_for_type(x509.DNSName)
        assert ipaddress.ip_address("10.0.0.5") in sans.get_values_for_type(x509.IPAddress)

    def test_partial_pair_fails_without_overwriting(self, tmp_path: Path) -> None:
        cert = tmp_path / "srv.crt"
        cert.write_text("user certificate", encoding="utf-8")
        with (
            patch.multiple(_tls_proxy_mod, **_cert_globals(tmp_path, sans=[])),
            pytest.raises(RuntimeError, match="incomplete TLS certificate pair"),
        ):
            _ensure_certs()
        assert cert.read_text(encoding="utf-8") == "user certificate"
        assert not (tmp_path / "srv.key").exists()

    def test_missing_pair_requires_explicit_generation(self, tmp_path: Path) -> None:
        with (
            patch.multiple(
                _tls_proxy_mod,
                **_cert_globals(tmp_path, sans=[], auto=False),
            ),
            pytest.raises(RuntimeError, match="explicitly enable certificate generation"),
        ):
            _ensure_certs()

    def test_partial_ca_artifact_fails_with_diagnostic(self, tmp_path: Path) -> None:
        (tmp_path / "ca.crt").write_text("partial", encoding="utf-8")
        with (
            patch.multiple(_tls_proxy_mod, **_cert_globals(tmp_path, sans=[])),
            pytest.raises(RuntimeError, match="partial generation artifacts"),
        ):
            _ensure_certs()

    def test_changed_san_fails_loudly_and_preserves_existing_cert(self, tmp_path: Path) -> None:
        with patch.multiple(
            _tls_proxy_mod,
            **_cert_globals(tmp_path, sans=["old.lan"]),
        ):
            _ensure_certs()
        original = (tmp_path / "srv.crt").read_bytes()

        with (
            patch.multiple(
                _tls_proxy_mod,
                **_cert_globals(tmp_path, sans=["new.lan"]),
            ),
            pytest.raises(RuntimeError, match=r"does not cover configured SAN.*new\.lan"),
        ):
            _ensure_certs()
        assert (tmp_path / "srv.crt").read_bytes() == original

    def test_existing_cert_with_matching_san_is_reused(self, tmp_path: Path) -> None:
        values = _cert_globals(tmp_path, sans=["same.lan"])
        with patch.multiple(_tls_proxy_mod, **values):
            _ensure_certs()
            before = (tmp_path / "srv.crt").read_bytes()
            _ensure_certs()
        assert (tmp_path / "srv.crt").read_bytes() == before


class TestTlsProxyConfig:
    def test_defaults_disabled(self) -> None:
        config = TlsProxyConfig()
        assert not config.enabled
        assert config.port == 8443
        assert config.cert_dir == ""
        assert config.san_names == []

    def test_explicit_environment_contract_overrides_toml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENBILICLAW_TLS_PROXY_ENABLED", "true")
        monkeypatch.setenv("OPENBILICLAW_TLS_PROXY_PORT", "9443")
        monkeypatch.setenv("OPENBILICLAW_TLS_PROXY_CERT_DIR", " ./private-certs ")
        monkeypatch.setenv("OPENBILICLAW_TLS_SAN_NAMES", "10.0.0.8, Host.LAN,10.0.0.8")
        config = _build_tls_proxy(
            {
                "tls_proxy": {
                    "enabled": False,
                    "port": 8443,
                    "cert_dir": "ignored",
                    "san_names": ["ignored.lan"],
                }
            }
        )
        assert config == TlsProxyConfig(
            enabled=True,
            port=9443,
            cert_dir="private-certs",
            san_names=["10.0.0.8", "host.lan"],
        )

    def test_standalone_san_env_parser_accepts_ipv4_ipv6_and_dns(self) -> None:
        assert _parse_san_names(" 10.0.0.1,host.lan,2001:db8::1 ") == [
            "10.0.0.1",
            "host.lan",
            "2001:db8::1",
        ]

    @pytest.mark.parametrize("value", [0, 65536, True, "abc", 1.5])
    def test_invalid_port_is_rejected(self, value: object) -> None:
        with pytest.raises(ConfigError, match="1..65535"):
            normalize_tls_proxy_port(value)

    @pytest.mark.parametrize("value", ["maybe", 2, [], None])
    def test_invalid_enabled_value_is_rejected(self, value: object) -> None:
        with pytest.raises(ConfigError, match="enabled"):
            _build_tls_proxy({"tls_proxy": {"enabled": value}})

    @pytest.mark.parametrize("value", ["bad name", "https://host", "host:8443", "*.lan"])
    def test_invalid_san_is_rejected(self, value: str) -> None:
        with pytest.raises(ConfigError, match="san_names"):
            normalize_tls_san_names([value])

    def test_cert_dir_rejects_control_characters(self) -> None:
        with pytest.raises(ConfigError, match="控制字符"):
            normalize_tls_cert_dir("certs\nsecret")

    def test_non_string_san_list_item_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="字符串列表"):
            normalize_tls_san_names(["host.lan", 123])

    def test_save_load_round_trip_preserves_tls_and_other_tables(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(
            """
[general]
language = "en"
data_dir = "runtime-data"

[api]
host = "127.0.0.1"
port = 9123

[tls_proxy]
enabled = true
port = 9443
cert_dir = "private/certs"
san_names = ["10.0.0.9", "Host.LAN"]

[saved_sync]
auto_sync_enabled = true

[storage]
db_path = "state.db"
""".strip(),
            encoding="utf-8",
        )
        config = load_config(path)
        save_config(config, path)

        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        assert raw["tls_proxy"] == {
            "enabled": True,
            "port": 9443,
            "cert_dir": "private/certs",
            "san_names": ["10.0.0.9", "host.lan"],
        }
        assert raw["general"]["language"] == "en"
        assert raw["api"]["host"] == "127.0.0.1"
        assert raw["api"]["port"] == 9123
        assert raw["saved_sync"]["auto_sync_enabled"] is True
        assert raw["storage"]["db_path"] == "state.db"
        assert load_config(path).tls_proxy == config.tls_proxy

    def test_env_overrides_are_never_baked_into_base_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "config.toml"
        path.write_text(
            "[tls_proxy]\n"
            "enabled = false\n"
            "port = 8443\n"
            'cert_dir = "base/certs"\n'
            'san_names = ["base.lan"]\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENBILICLAW_TLS_PROXY_ENABLED", "true")
        monkeypatch.setenv("OPENBILICLAW_TLS_PROXY_PORT", "9443")
        monkeypatch.setenv("OPENBILICLAW_TLS_PROXY_CERT_DIR", "env/certs")
        monkeypatch.setenv("OPENBILICLAW_TLS_SAN_NAMES", "10.0.0.8,env.lan")

        merged = load_config(path)
        assert merged.tls_proxy == TlsProxyConfig(
            enabled=True,
            port=9443,
            cert_dir="env/certs",
            san_names=["10.0.0.8", "env.lan"],
        )
        merged.language = "en"  # unrelated whole-file save
        save_config(merged, path)

        persisted = tomllib.loads(path.read_text(encoding="utf-8"))["tls_proxy"]
        assert persisted == {
            "enabled": False,
            "port": 8443,
            "cert_dir": "base/certs",
            "san_names": ["base.lan"],
        }
        for name in (
            "OPENBILICLAW_TLS_PROXY_ENABLED",
            "OPENBILICLAW_TLS_PROXY_PORT",
            "OPENBILICLAW_TLS_PROXY_CERT_DIR",
            "OPENBILICLAW_TLS_SAN_NAMES",
        ):
            monkeypatch.delenv(name)
        assert load_config(path).tls_proxy == TlsProxyConfig(
            enabled=False,
            port=8443,
            cert_dir="base/certs",
            san_names=["base.lan"],
        )

    def test_config_local_overrides_are_not_baked_into_default_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        for name in (
            "OPENBILICLAW_TLS_PROXY_ENABLED",
            "OPENBILICLAW_TLS_PROXY_PORT",
            "OPENBILICLAW_TLS_PROXY_CERT_DIR",
            "OPENBILICLAW_TLS_SAN_NAMES",
        ):
            monkeypatch.delenv(name, raising=False)
        base_path = tmp_path / "config.toml"
        base_path.write_text(
            "[tls_proxy]\n"
            "enabled = false\n"
            "port = 8443\n"
            'cert_dir = "base/certs"\n'
            'san_names = ["base.lan"]\n',
            encoding="utf-8",
        )
        local_path = tmp_path / "config.local.toml"
        local_path.write_text(
            "[tls_proxy]\n"
            "enabled = true\n"
            "port = 9443\n"
            'cert_dir = "local/certs"\n'
            'san_names = ["10.0.0.9", "local.lan"]\n',
            encoding="utf-8",
        )

        merged = load_config()
        assert merged.tls_proxy.port == 9443
        assert merged.tls_proxy.san_names == ["10.0.0.9", "local.lan"]
        merged.language = "en"
        save_config(merged)

        persisted = tomllib.loads(base_path.read_text(encoding="utf-8"))["tls_proxy"]
        assert persisted == {
            "enabled": False,
            "port": 8443,
            "cert_dir": "base/certs",
            "san_names": ["base.lan"],
        }
        local_path.unlink()
        assert load_config().tls_proxy == TlsProxyConfig(
            enabled=False,
            port=8443,
            cert_dir="base/certs",
            san_names=["base.lan"],
        )

    def test_explicit_config_path_ignores_project_root_config_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
        (project_root / "config.local.toml").write_text(
            "[tls_proxy]\n"
            "enabled = false\n"
            "port = 7443\n"
            'cert_dir = "local/certs"\n'
            'san_names = ["local.lan"]\n',
            encoding="utf-8",
        )
        explicit = tmp_path / "elsewhere" / "config.toml"
        explicit.parent.mkdir()
        explicit.write_text(
            "[tls_proxy]\n"
            "enabled = true\n"
            "port = 9443\n"
            'cert_dir = "explicit/certs"\n'
            'san_names = ["explicit.lan"]\n',
            encoding="utf-8",
        )

        loaded = load_config(explicit)
        assert loaded.tls_proxy == TlsProxyConfig(
            enabled=True,
            port=9443,
            cert_dir="explicit/certs",
            san_names=["explicit.lan"],
        )
        save_config(loaded, explicit)
        assert tomllib.loads(explicit.read_text(encoding="utf-8"))["tls_proxy"] == {
            "enabled": True,
            "port": 9443,
            "cert_dir": "explicit/certs",
            "san_names": ["explicit.lan"],
        }

    def test_explicit_default_path_does_not_consult_local_for_tls_or_auth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        base_path = tmp_path / "config.toml"
        base_path.write_text(
            "[api.auth]\n"
            "trust_loopback = true\n\n"
            "[tls_proxy]\n"
            "enabled = false\n"
            "port = 8443\n"
            'cert_dir = "base/certs"\n'
            'san_names = ["base.lan"]\n',
            encoding="utf-8",
        )
        (tmp_path / "config.local.toml").write_text(
            "[api.auth]\n"
            "trust_loopback = false\n\n"
            "[tls_proxy]\n"
            "enabled = true\n"
            "port = 9443\n"
            'cert_dir = "local/certs"\n'
            'san_names = ["local.lan"]\n',
            encoding="utf-8",
        )

        # Passing the default path explicitly follows explicit-load semantics:
        # config.local.toml was not merged and must not gate the matching save.
        loaded = load_config(base_path)
        assert loaded.api.auth.trust_loopback is True
        assert loaded.tls_proxy.enabled is False
        loaded.api.auth.trust_loopback = False
        loaded.tls_proxy = TlsProxyConfig(
            enabled=True,
            port=10443,
            cert_dir="explicit/certs",
            san_names=["explicit.lan"],
        )
        save_config(loaded, base_path)

        persisted = tomllib.loads(base_path.read_text(encoding="utf-8"))
        assert persisted["api"]["auth"]["trust_loopback"] is False
        assert persisted["tls_proxy"] == {
            "enabled": True,
            "port": 10443,
            "cert_dir": "explicit/certs",
            "san_names": ["explicit.lan"],
        }

    def test_cli_enable_disable_persists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        for name in (
            "OPENBILICLAW_TLS_PROXY_ENABLED",
            "OPENBILICLAW_TLS_PROXY_PORT",
            "OPENBILICLAW_TLS_PROXY_CERT_DIR",
            "OPENBILICLAW_TLS_SAN_NAMES",
        ):
            monkeypatch.delenv(name, raising=False)
        runner = CliRunner()

        enabled = runner.invoke(app, ["tls-proxy", "enable", "--san", "192.168.1.50"])
        assert enabled.exit_code == 0, enabled.output
        loaded = load_config(tmp_path / "config.toml")
        assert loaded.tls_proxy.enabled
        assert loaded.tls_proxy.san_names == ["192.168.1.50"]

        disabled = runner.invoke(app, ["tls-proxy", "disable"])
        assert disabled.exit_code == 0, disabled.output
        loaded = load_config(tmp_path / "config.toml")
        assert not loaded.tls_proxy.enabled
        assert loaded.tls_proxy.san_names == ["192.168.1.50"]

    def test_cli_refuses_false_success_when_enabled_is_env_managed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setenv("OPENBILICLAW_TLS_PROXY_ENABLED", "true")
        result = CliRunner().invoke(app, ["tls-proxy", "disable"])
        assert result.exit_code == 1
        assert "OPENBILICLAW_TLS_PROXY_ENABLED" in result.output
        assert not (tmp_path / "config.toml").exists()


class TestStartup:
    @pytest.mark.parametrize(
        ("bind", "connect"),
        [
            ("0.0.0.0", "127.0.0.1"),
            ("::", "::1"),
            ("[::]", "::1"),
            ("192.168.1.5", "192.168.1.5"),
        ],
    )
    def test_backend_connect_host(self, bind: str, connect: str) -> None:
        assert backend_connect_host(bind) == connect

    def test_dockerfile_exports_build_arg_certificate_directory(self) -> None:
        dockerfile = (
            Path(__file__).resolve().parents[1] / "docker/openbiliclaw-tls-proxy.Dockerfile"
        ).read_text(encoding="utf-8")
        assert "ENV CERT_DIR=${CERT_DIR} \\\n    CERT_FILE=${CERT_DIR}/srv.crt" in dockerfile

    def test_websocket_relay_closes_http_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = object.__new__(ProxyHandler)
        handler.close_connection = False
        marker = object()
        relayed: list[object] = []
        monkeypatch.setattr(
            ProxyHandler,
            "_relay_ws",
            lambda _self, connection: relayed.append(connection),
        )

        handler._relay_ws_and_close(marker)  # type: ignore[arg-type]

        assert relayed == [marker]
        assert handler.close_connection is True

    def test_library_server_ignores_standalone_container_path_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CERT_FILE", str(tmp_path / "unrelated.crt"))
        server = create_tls_proxy_server(
            host="127.0.0.1",
            port=_free_port(),
            cert_dir=str(tmp_path),
            auto_gen_certs=True,
        )
        try:
            assert str(tmp_path / "srv.crt") == _tls_proxy_mod._CERT_FILE
        finally:
            server.server_close()

    def test_standalone_environment_is_translated_explicitly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}
        monkeypatch.setenv("LISTEN_HOST", "127.0.0.1")
        monkeypatch.setenv("LISTEN_PORT", "9443")
        monkeypatch.setenv("BACKEND_HOST", "api.internal")
        monkeypatch.setenv("BACKEND_PORT", "9123")
        monkeypatch.setenv("CERT_DIR", str(tmp_path))
        monkeypatch.setenv("CERT_FILE", "/mounted/server.pem")
        monkeypatch.setenv("KEY_FILE", "/mounted/server.key")
        monkeypatch.setenv("CA_CERT_FILE", "/mounted/ca.pem")
        monkeypatch.setenv("CRL_FILE", "/mounted/ca.crl")
        monkeypatch.setenv("AUTO_GEN_CERTS", "false")
        monkeypatch.setenv("SAN_NAMES", "10.0.0.8, Host.LAN")
        monkeypatch.setattr(
            _tls_proxy_mod,
            "start_tls_proxy",
            lambda **kwargs: captured.update(kwargs),
        )

        _tls_proxy_mod._run_from_environment()

        assert captured == {
            "host": "127.0.0.1",
            "port": 9443,
            "backend_host": "api.internal",
            "backend_port": 9123,
            "cert_dir": str(tmp_path),
            "cert_file": "/mounted/server.pem",
            "key_file": "/mounted/server.key",
            "ca_file": "/mounted/ca.pem",
            "crl_file": "/mounted/ca.crl",
            "auto_gen_certs": False,
            "san_names": ["10.0.0.8", "host.lan"],
        }

    def test_standalone_invalid_boolean_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTO_GEN_CERTS", "tru")
        with pytest.raises(ValueError, match="AUTO_GEN_CERTS must be true or false"):
            _tls_proxy_mod._run_from_environment()

    def test_prepare_failure_occurs_before_thread_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        config = load_config(tmp_path / "missing.toml")
        config.tls_proxy.enabled = True
        config.tls_proxy.san_names = ["localhost"]
        save_config(config)
        thread_started = False

        class _UnexpectedThread:
            def __init__(self, **_kwargs: Any) -> None:
                nonlocal thread_started
                thread_started = True

            def start(self) -> None:
                pass

        monkeypatch.setattr(threading, "Thread", _UnexpectedThread)
        monkeypatch.setattr(
            _tls_proxy_mod,
            "create_tls_proxy_server",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("address already in use")),
        )
        with pytest.raises(typer.Exit) as error:
            _start_tls_proxy_if_enabled("0.0.0.0", 8420)
        assert error.value.exit_code == 1
        assert not thread_started


class TestHttpsProxyIntegration:
    def test_tls_health_endpoint_is_certificate_bundle_independent(
        self, proxy_stack: dict[str, Any]
    ) -> None:
        response, data = _https_request(proxy_stack, "GET", "/healthz")
        assert response.status == 200
        assert data == b"ok"

    def test_tls_health_head_has_no_body(self, proxy_stack: dict[str, Any]) -> None:
        response, data = _https_request(proxy_stack, "HEAD", "/healthz")
        assert response.status == 200
        assert response.getheader("Content-Length") == "2"
        assert data == b""

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    def test_tls_health_rejects_other_methods_and_closes_connection(
        self, proxy_stack: dict[str, Any], method: str
    ) -> None:
        before = len(_BackendHandler.calls)
        response, data = _https_request(proxy_stack, method, "/healthz", body=b"unread body")
        assert response.status == 405
        assert response.getheader("Allow") == "GET, HEAD"
        assert response.getheader("Connection") == "close"
        assert data == b"method not allowed"
        assert len(_BackendHandler.calls) == before

    def test_real_https_get_rewrites_only_the_web_scheme(self, proxy_stack: dict[str, Any]) -> None:
        port = proxy_stack["port"]
        response, data = _https_request(
            proxy_stack,
            "GET",
            "/echo?value=1",
            headers={"Origin": f"https://127.0.0.1:{port}"},
        )
        record = json.loads(data)
        assert response.status == 200
        assert record["path"] == "/echo?value=1"
        assert record["host"] == f"127.0.0.1:{port}"
        assert record["origin"] == f"http://127.0.0.1:{port}"

    def test_real_https_post_preserves_body(self, proxy_stack: dict[str, Any]) -> None:
        port = proxy_stack["port"]
        response, data = _https_request(
            proxy_stack,
            "POST",
            "/echo",
            body=b'{"hello":"world"}',
            headers={
                "Content-Type": "application/json",
                "Origin": f"https://127.0.0.1:{port}",
            },
        )
        assert response.status == 200
        assert json.loads(data)["body"] == '{"hello":"world"}'

    def test_real_https_head_has_no_body_and_preserves_length(
        self, proxy_stack: dict[str, Any]
    ) -> None:
        response, data = _https_request(proxy_stack, "HEAD", "/head")
        assert response.status == 200
        assert response.getheader("Content-Length") == "7"
        assert data == b""

    def test_empty_response_has_explicit_zero_length(self, proxy_stack: dict[str, Any]) -> None:
        response, data = _https_request(proxy_stack, "GET", "/empty")
        assert response.status == 200
        assert response.getheader("Content-Length") == "0"
        assert data == b""

    def test_evil_same_port_origin_is_rejected(self, proxy_stack: dict[str, Any]) -> None:
        response, data = _https_request(
            proxy_stack,
            "GET",
            "/echo",
            headers={"Origin": f"https://evil.com:{proxy_stack['port']}"},
        )
        assert response.status == 403
        assert data == b"origin not allowed"

    def test_moz_extension_origin_is_forwarded_unchanged(self, proxy_stack: dict[str, Any]) -> None:
        response, data = _https_request(
            proxy_stack,
            "GET",
            "/echo",
            headers={"Origin": "moz-extension://a1b2-c3d4"},
        )
        assert response.status == 200
        assert json.loads(data)["origin"] == "moz-extension://a1b2-c3d4"

    def test_duplicate_set_cookie_headers_are_preserved_and_secure(
        self, proxy_stack: dict[str, Any]
    ) -> None:
        response, _ = _https_request(proxy_stack, "GET", "/cookies")
        cookies = [value for name, value in response.getheaders() if name.lower() == "set-cookie"]
        assert len(cookies) == 2
        assert cookies[0].startswith("obc_session=")
        assert all("secure" in cookie.lower().split(";")[-1] for cookie in cookies)

    @pytest.mark.parametrize("path", ["/ca.key", "/certs/ca.key", "/%63a.key"])
    def test_ca_private_key_is_never_forwarded_or_served(
        self, proxy_stack: dict[str, Any], path: str
    ) -> None:
        before = len(_BackendHandler.calls)
        response, _ = _https_request(proxy_stack, "GET", path)
        assert response.status == 404
        assert len(_BackendHandler.calls) == before

    def test_ca_endpoint_cannot_leak_a_misconfigured_private_key(
        self, proxy_stack: dict[str, Any]
    ) -> None:
        private_key = str(proxy_stack["cert_dir"] / "ca.key")
        with patch.object(_tls_proxy_mod, "_CA_FILE", private_key):
            response, data = _https_request(proxy_stack, "GET", "/ca.crt")
        assert response.status == 404
        assert b"PRIVATE KEY" not in data

    def test_websocket_real_handshake_and_bidirectional_relay(
        self, proxy_stack: dict[str, Any]
    ) -> None:
        port = proxy_stack["port"]
        key = base64.b64encode(b"0123456789abcdef").decode()
        expected_accept = base64.b64encode(
            hashlib.sha1(  # noqa: S324 - mandated by RFC 6455 handshake
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        ).decode()
        context = _unverified_tls_context()
        with (
            socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
            context.wrap_socket(raw, server_hostname="127.0.0.1") as client,
        ):
            client.sendall(
                (
                    "GET /ws HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: keep-alive, Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    f"Origin: https://127.0.0.1:{port}\r\n"
                    "\r\n"
                ).encode()
            )
            handshake = _recv_until(client, b"\r\n\r\n")
            assert handshake.startswith(b"HTTP/1.1 101")
            assert f"Sec-WebSocket-Accept: {expected_accept}".encode() in handshake

            payload = b"hello through proxy"
            mask = b"\x01\x02\x03\x04"
            masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            client.sendall(bytes((0x81, 0x80 | len(payload))) + mask + masked)
            header = _recv_exact(client, 2)
            assert header == bytes((0x81, len(payload)))
            assert _recv_exact(client, len(payload)) == payload

        assert _BackendHandler.websocket_payloads[-1] == payload


def _recv_until(sock: ssl.SSLSocket, marker: bytes) -> bytes:
    data = b""
    while marker not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _recv_exact(sock: ssl.SSLSocket, length: int) -> bytes:
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise AssertionError("socket closed before complete WebSocket frame")
        data += chunk
    return data
