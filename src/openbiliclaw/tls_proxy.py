#!/usr/bin/env python3
"""Optional TLS reverse proxy for LAN and self-managed deployments.

The proxy terminates TLS, forwards HTTP/1.1 and WebSocket traffic to the local
OpenBiliClaw API, and performs the minimum scheme adaptation needed by the
backend's same-origin checks. It is deliberately disabled by default and is
not a general-purpose, Internet-facing reverse proxy.

When certificate generation is explicitly enabled, the proxy creates a local
CA and a server certificate for localhost plus the configured DNS/IP SANs. An
existing certificate is never overwritten. If configured SANs change, startup
fails with a re-signing instruction instead of silently serving a mismatched
certificate.
"""

from __future__ import annotations

import datetime
import ipaddress
import os
import posixpath
import select
import socket
import ssl
import tempfile
from contextlib import suppress
from http.client import HTTPConnection, HTTPException, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

if TYPE_CHECKING:
    from collections.abc import Iterable

# Module state is populated before a server is made reachable. A single proxy
# listener is supported per process, which matches both the CLI and container
# entry points.
_HOST: str = "0.0.0.0"
_PORT: int = 8443
_BACKEND_HOST: str = "127.0.0.1"
_BACKEND_PORT: int = 8420
_CERT_DIR: str = "/certs"
_CERT_FILE: str = "/certs/srv.crt"
_KEY_FILE: str = "/certs/srv.key"
_CRL_FILE: str = "/certs/ca.crl"
_CA_FILE: str = "/certs/ca.crt"
_AUTO_GEN: bool = False
_SAN_NAMES: list[str] = []

_EXTENSION_SCHEMES = frozenset({"chrome-extension", "moz-extension"})
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_PRIVATE_KEY_PATHS = frozenset({"/ca.key", "/srv.key", "/certs/ca.key", "/certs/srv.key"})


class _TlsProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _TlsProxyServerV6(_TlsProxyServer):
    address_family = socket.AF_INET6


def _atomic_create_file(path: str, data: bytes, mode: int) -> None:
    """Durably create *path* without ever replacing an existing file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=directory)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # A hard link is atomic and fails if the destination appeared while the
        # certificate was being generated. Unlike os.replace(), it can never
        # overwrite a user-supplied key or certificate.
        os.link(temporary, path)
    finally:
        with suppress(OSError):
            os.close(fd)
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def _normalize_san_name(value: str) -> str:
    name = value.strip()
    if not name or any(ord(char) < 33 for char in name):
        raise ValueError(f"invalid TLS SAN name: {value!r}")
    if any(char in name for char in "/\\@#"):
        raise ValueError(f"invalid TLS SAN name: {value!r}")
    try:
        return str(ipaddress.ip_address(name))
    except ValueError:
        if ":" in name:
            raise ValueError(f"invalid TLS SAN name: {value!r}") from None
        try:
            ascii_name = name.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError(f"invalid TLS SAN name: {value!r}") from exc
        labels = ascii_name.split(".")
        if not ascii_name or len(ascii_name) > 253:
            raise ValueError(f"invalid TLS SAN name: {value!r}") from None
        for label in labels:
            if (
                not label
                or len(label) > 63
                or not label[0].isalnum()
                or not label[-1].isalnum()
                or any(not (char.isalnum() or char == "-") for char in label)
            ):
                raise ValueError(f"invalid TLS SAN name: {value!r}") from None
        return ascii_name


def _normalize_san_names(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value.strip():
            continue
        normalized = _normalize_san_name(value)
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _parse_san_names(value: str) -> list[str]:
    """Parse the standalone container's comma-separated ``SAN_NAMES`` value."""
    return _normalize_san_names(item for item in value.split(",") if item.strip())


def _parse_authority(authority: str | None, *, default_port: int) -> tuple[str, int] | None:
    """Parse an HTTP authority into a normalized host/port pair."""
    if authority is None:
        return None
    text = authority.strip()
    if not text or any(char.isspace() or ord(char) < 32 for char in text):
        return None
    if any(char in text for char in "/\\?#,"):
        return None
    try:
        parsed = urlsplit(f"//{text}")
        port = parsed.port
    except ValueError:
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = parsed.hostname.rstrip(".").casefold()
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    effective_port = default_port if port is None else port
    if not 1 <= effective_port <= 65535:
        return None
    return host, effective_port


def _extension_origin_allowed(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() in _EXTENSION_SCHEMES
        and parsed.netloc
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and port is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and parsed.netloc == parsed.hostname
    )


def _origin_allowed(origin: str | None, host_header: str | None = None) -> bool:
    """Allow absent Origin, valid extension origins, or the exact TLS Web origin."""
    if not origin:
        return True
    text = origin.strip()
    if not text or text.casefold() == "null":
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    if parsed.scheme.casefold() in _EXTENSION_SCHEMES:
        return _extension_origin_allowed(text)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return False
    origin_authority = _parse_authority(parsed.netloc, default_port=443)
    request_authority = _parse_authority(host_header, default_port=443)
    return origin_authority is not None and origin_authority == request_authority


def _rewrite_origin(origin: str, host_header: str | None = None) -> str:
    """Adapt an already-validated HTTPS Web origin for the plain HTTP backend."""
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return origin
    if parsed.scheme.casefold() != "https":
        return origin
    if host_header is not None and _parse_authority(host_header, default_port=443) is not None:
        return f"http://{host_header.strip()}"
    return parsed._replace(scheme="http").geturl()


def _build_san_entries(extra_names: list[str]) -> list[x509.GeneralName]:
    """Build SANs: localhost/127.0.0.1 plus validated configured names."""
    entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    for name in _normalize_san_names(extra_names):
        if name in ("localhost", "127.0.0.1"):
            continue
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            entries.append(x509.DNSName(name))
    return entries


def _certificate_sans(path: str) -> set[str]:
    try:
        with open(path, "rb") as handle:
            cert = x509.load_pem_x509_certificate(handle.read())
        extension = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except (OSError, ValueError, x509.ExtensionNotFound) as exc:
        raise RuntimeError(f"cannot inspect existing TLS certificate SANs: {path}: {exc}") from exc
    values = {
        name.rstrip(".").casefold() for name in extension.value.get_values_for_type(x509.DNSName)
    }
    values.update(str(value) for value in extension.value.get_values_for_type(x509.IPAddress))
    return values


def _validate_existing_certificate_sans() -> None:
    if not _SAN_NAMES:
        return
    present = _certificate_sans(_CERT_FILE)
    missing = [name for name in _SAN_NAMES if name.casefold() not in present]
    if not missing:
        return
    joined = ", ".join(missing)
    raise RuntimeError(
        "existing TLS certificate does not cover configured SAN(s): "
        f"{joined}. The proxy will not overwrite an existing certificate. "
        "Stop the proxy and replace/re-sign srv.crt and srv.key; for an "
        "auto-generated bundle, move the old certificate bundle out of cert_dir "
        "and restart to generate a new one."
    )


def _ensure_certs() -> None:
    """Validate an existing cert/key pair or explicitly generate a new bundle."""
    cert_exists = os.path.isfile(_CERT_FILE)
    key_exists = os.path.isfile(_KEY_FILE)
    if cert_exists != key_exists:
        missing = _KEY_FILE if cert_exists else _CERT_FILE
        raise RuntimeError(
            "incomplete TLS certificate pair; refusing to generate over a partial bundle. "
            f"Missing: {missing}. Supply the missing file or move the partial bundle aside."
        )
    if cert_exists and key_exists:
        _validate_existing_certificate_sans()
        return
    if not _AUTO_GEN:
        raise RuntimeError(
            "no TLS server certificate found. Expected both "
            f"{_CERT_FILE} and {_KEY_FILE}. Supply them or explicitly enable "
            "certificate generation."
        )

    partial_artifacts = [
        path
        for path in (_CA_FILE, os.path.join(_CERT_DIR, "ca.key"), _CRL_FILE)
        if os.path.exists(path)
    ]
    if partial_artifacts:
        raise RuntimeError(
            "TLS certificate directory contains partial generation artifacts but no complete "
            "srv.crt/srv.key pair; refusing to overwrite: "
            + ", ".join(partial_artifacts)
            + ". Move the partial bundle aside or supply a complete certificate pair."
        )

    os.makedirs(_CERT_DIR, mode=0o700, exist_ok=True)
    now = datetime.datetime.now(datetime.UTC)
    crl_dp_url = f"https://localhost:{_PORT}/ca.crl"

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "OpenBiliClaw Local CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_cn = _SAN_NAMES[0] if _SAN_NAMES else "localhost"
    distribution_points = x509.CRLDistributionPoints(
        [
            x509.DistributionPoint(
                full_name=[x509.UniformResourceIdentifier(crl_dp_url)],
                relative_name=None,
                reasons=None,
                crl_issuer=None,
            )
        ]
    )
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, server_cn)]))
        .issuer_name(ca_subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(_build_san_entries(_SAN_NAMES)), False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .add_extension(distribution_points, False)
        .sign(ca_key, hashes.SHA256())
    )
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_subject)
        .last_update(now)
        .next_update(now + datetime.timedelta(days=3650))
        .add_extension(x509.CRLNumber(1000), False)
        .sign(ca_key, hashes.SHA256())
    )

    # Create every destination with O_EXCL-like semantics. Server cert/key are
    # written last; a crash can leave a partial pair, but the next startup will
    # detect it and fail loudly instead of mistaking it for success.
    generated = (
        (_CA_FILE, ca_cert.public_bytes(serialization.Encoding.PEM), 0o644),
        (
            os.path.join(_CERT_DIR, "ca.key"),
            ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ),
            0o600,
        ),
        (_CRL_FILE, crl.public_bytes(serialization.Encoding.PEM), 0o644),
        (
            _KEY_FILE,
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ),
            0o600,
        ),
        (_CERT_FILE, server_cert.public_bytes(serialization.Encoding.PEM), 0o644),
    )
    for path, data, mode in generated:
        _atomic_create_file(path, data, mode)


def _connection_tokens(headers: Iterable[tuple[str, str]]) -> set[str]:
    tokens: set[str] = set()
    for name, value in headers:
        if name.casefold() == "connection":
            tokens.update(token.strip().casefold() for token in value.split(",") if token.strip())
    return tokens


def _secure_set_cookie(value: str) -> str:
    attributes = [part.strip().casefold() for part in value.split(";")[1:]]
    return value if "secure" in attributes else f"{value}; Secure"


def _private_key_path(path: str) -> bool:
    decoded = path
    for _ in range(3):
        unquoted = unquote(decoded)
        if unquoted == decoded:
            break
        decoded = unquoted
    normalized = posixpath.normpath("/" + decoded.replace("\\", "/").lstrip("/")).casefold()
    return normalized in _PRIVATE_KEY_PATHS


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "openbiliclaw-tls-proxy/1.1"

    def _reply(
        self,
        code: int,
        body: bytes = b"",
        headers: Iterable[tuple[str, str]] = (),
        *,
        content_length: int | None = None,
    ) -> None:
        output_headers = list(headers)
        has_length = any(name.casefold() == "content-length" for name, _ in output_headers)
        self.send_response(code)
        for name, value in output_headers:
            self.send_header(name, value)
        if not has_length and not (100 <= code < 200 or code in (204, 304)):
            length = len(body) if content_length is None else content_length
            self.send_header("Content-Length", str(length))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _request_body(self) -> tuple[bytes | None, bool]:
        if self.headers.get_all("Transfer-Encoding"):
            self._reply(
                501,
                b"chunked request bodies are not supported",
                (("Content-Type", "text/plain; charset=utf-8"),),
            )
            return None, False
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            self._reply(400, b"invalid Content-Length")
            return None, False
        if not lengths:
            return None, True
        try:
            length = int(lengths[0])
        except ValueError:
            self._reply(400, b"invalid Content-Length")
            return None, False
        if length < 0:
            self._reply(400, b"invalid Content-Length")
            return None, False
        return self.rfile.read(length), True

    def _forward_request(
        self,
        conn: HTTPConnection,
        *,
        body: bytes | None,
        host_header: str,
        forwarded_origin: str | None,
        websocket: bool,
    ) -> None:
        raw_headers = list(self.headers.raw_items())
        connection_tokens = _connection_tokens(raw_headers)
        stripped = (
            set(_HOP_BY_HOP_HEADERS)
            | connection_tokens
            | {
                "host",
                "origin",
                "content-length",
            }
        )
        conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
        for name, value in raw_headers:
            if name.casefold() not in stripped:
                conn.putheader(name, value)
        conn.putheader("Host", host_header)
        if forwarded_origin is not None:
            conn.putheader("Origin", forwarded_origin)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        if websocket:
            conn.putheader("Connection", "Upgrade")
            conn.putheader("Upgrade", "websocket")
        conn.endheaders(body)

    def _response_headers(
        self,
        response: HTTPResponse,
        *,
        websocket: bool,
        preserve_content_length: bool,
    ) -> list[tuple[str, str]]:
        raw_headers = response.getheaders()
        connection_tokens = _connection_tokens(raw_headers)
        stripped = set(_HOP_BY_HOP_HEADERS) | connection_tokens
        if not preserve_content_length:
            stripped.add("content-length")
        result: list[tuple[str, str]] = []
        for name, value in raw_headers:
            lowered = name.casefold()
            if lowered in stripped:
                continue
            if lowered == "set-cookie":
                value = _secure_set_cookie(value)
            result.append((name, value))
        if websocket:
            result.extend((("Connection", "Upgrade"), ("Upgrade", "websocket")))
        return result

    def _relay_ws(self, conn: HTTPConnection) -> None:
        """Relay bytes bidirectionally after a successful WebSocket upgrade."""
        client = self.connection
        backend = conn.sock
        if backend is None:
            return
        try:
            while True:
                readable, _, _ = select.select([client, backend], [], [], 60)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = backend if source is client else client
                    destination.sendall(data)
        except (OSError, ConnectionError):
            return

    def _relay_ws_and_close(self, conn: HTTPConnection) -> None:
        """Relay an upgraded connection and never resume HTTP keep-alive parsing."""
        try:
            self._relay_ws(conn)
        finally:
            self.close_connection = True

    def _serve_ca_certificate(self) -> None:
        if self.command not in ("GET", "HEAD"):
            self._reply(405, b"method not allowed", (("Allow", "GET, HEAD"),))
            return
        try:
            with open(_CA_FILE, "rb") as handle:
                certificate = x509.load_pem_x509_certificate(handle.read())
        except (OSError, ValueError):
            self._reply(404, b"not found")
            return
        # Re-serialize the parsed public certificate. Even a misconfigured file
        # containing a cert followed by private-key PEM blocks can never leak
        # the trailing secret bytes through this endpoint.
        data = certificate.public_bytes(serialization.Encoding.PEM)
        self._reply(200, data, (("Content-Type", "application/x-x509-ca-cert"),))

    def _serve_crl(self) -> None:
        if self.command not in ("GET", "HEAD"):
            self._reply(405, b"method not allowed", (("Allow", "GET, HEAD"),))
            return
        try:
            with open(_CRL_FILE, "rb") as handle:
                crl = x509.load_pem_x509_crl(handle.read())
        except (OSError, ValueError):
            self._reply(404, b"not found")
            return
        data = crl.public_bytes(serialization.Encoding.PEM)
        self._reply(200, data, (("Content-Type", "application/pkix-crl"),))

    def _handle(self) -> None:
        request_path = urlsplit(self.path).path
        if _private_key_path(request_path):
            self._reply(404, b"not found")
            return
        if request_path == "/healthz":
            if self.command not in ("GET", "HEAD"):
                # Do not leave an unread POST body in the HTTP/1.1 input buffer.
                # Closing makes the method rejection unambiguous and prevents the
                # body bytes from being parsed as a subsequent request line.
                self.close_connection = True
                self._reply(
                    405,
                    b"method not allowed",
                    (("Allow", "GET, HEAD"), ("Connection", "close")),
                )
                return
            self._reply(200, b"ok", (("Content-Type", "text/plain; charset=utf-8"),))
            return
        if request_path == "/ca.crt":
            self._serve_ca_certificate()
            return
        if request_path == "/ca.crl":
            self._serve_crl()
            return

        host_values = self.headers.get_all("Host", [])
        if len(host_values) != 1 or _parse_authority(host_values[0], default_port=443) is None:
            self._reply(400, b"invalid Host")
            return
        host_header = host_values[0]
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1:
            self._reply(400, b"invalid Origin")
            return
        origin = origins[0] if origins else None
        if not _origin_allowed(origin, host_header):
            self._reply(
                403,
                b"origin not allowed",
                (("Content-Type", "text/plain; charset=utf-8"),),
            )
            return

        body, valid_body = self._request_body()
        if not valid_body:
            return
        websocket = self.headers.get("Upgrade", "").casefold() == "websocket" and "upgrade" in {
            token.strip().casefold()
            for value in self.headers.get_all("Connection", [])
            for token in value.split(",")
        }
        forwarded_origin = _rewrite_origin(origin, host_header) if origin else None

        conn: HTTPConnection | None = None
        try:
            conn = HTTPConnection(_BACKEND_HOST, _BACKEND_PORT, timeout=60)
            self._forward_request(
                conn,
                body=body,
                host_header=host_header,
                forwarded_origin=forwarded_origin,
                websocket=websocket,
            )
            response = conn.getresponse()
            if websocket and response.status == 101:
                self._reply(
                    101,
                    headers=self._response_headers(
                        response,
                        websocket=True,
                        preserve_content_length=False,
                    ),
                )
                self._relay_ws_and_close(conn)
                return

            data = response.read()
            preserve_length = self.command == "HEAD"
            headers = self._response_headers(
                response,
                websocket=False,
                preserve_content_length=preserve_length,
            )
            declared_length: int | None = None
            if preserve_length:
                raw_length = response.getheader("Content-Length")
                if raw_length is not None:
                    try:
                        declared_length = int(raw_length)
                    except ValueError:
                        declared_length = None
            self._reply(response.status, data, headers, content_length=declared_length)
        except (OSError, HTTPException) as exc:
            self._reply(
                502,
                f"backend unavailable: {exc}".encode("utf-8", errors="replace"),
                (("Content-Type", "text/plain; charset=utf-8"),),
            )
        finally:
            if conn is not None:
                conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _handle  # noqa: N815

    def log_message(self, *args: Any) -> None:
        """Silence the standard library's per-request stderr logger."""


def backend_connect_host(api_host: str) -> str:
    """Map wildcard API bind addresses to a connectable loopback address."""
    host = api_host.strip()
    if host in ("", "0.0.0.0", "*"):
        return "127.0.0.1"
    if host in ("::", "[::]"):
        return "::1"
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def create_tls_proxy_server(
    host: str = "0.0.0.0",
    port: int = 8443,
    backend_host: str = "127.0.0.1",
    backend_port: int = 8420,
    cert_dir: str = "",
    cert_file: str = "",
    key_file: str = "",
    crl_file: str = "",
    ca_file: str = "",
    auto_gen_certs: bool = False,
    san_names: list[str] | None = None,
) -> ThreadingHTTPServer:
    """Synchronously validate certificates, load TLS, bind, and return a server."""
    global _HOST, _PORT, _BACKEND_HOST, _BACKEND_PORT
    global _CERT_DIR, _CERT_FILE, _KEY_FILE, _CRL_FILE, _CA_FILE, _AUTO_GEN, _SAN_NAMES

    if isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("TLS listen port must be in 1..65535")
    if isinstance(backend_port, bool) or not 1 <= backend_port <= 65535:
        raise ValueError("backend port must be in 1..65535")
    normalized_sans = _normalize_san_names(san_names or [])
    cert_directory = cert_dir or os.environ.get("CERT_DIR", "/certs")

    _HOST = host
    _PORT = port
    _BACKEND_HOST = backend_connect_host(backend_host)
    _BACKEND_PORT = backend_port
    _CERT_DIR = cert_directory
    # Keep this library API deterministic. The standalone container translates
    # its short environment variables in ``_run_from_environment`` below;
    # callers such as the CLI must not be affected by an unrelated CERT_FILE
    # variable in their shell.
    _CERT_FILE = cert_file or os.path.join(cert_directory, "srv.crt")
    _KEY_FILE = key_file or os.path.join(cert_directory, "srv.key")
    _CRL_FILE = crl_file or os.path.join(cert_directory, "ca.crl")
    _CA_FILE = ca_file or os.path.join(cert_directory, "ca.crt")
    _AUTO_GEN = auto_gen_certs
    _SAN_NAMES = normalized_sans

    _ensure_certs()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=_CERT_FILE, keyfile=_KEY_FILE)

    server_type: type[_TlsProxyServer] = _TlsProxyServerV6 if ":" in host else _TlsProxyServer
    server = server_type((host, port), ProxyHandler, bind_and_activate=False)
    try:
        server.server_bind()
        server.server_activate()
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except Exception:
        server.server_close()
        raise
    return server


def start_tls_proxy(
    host: str = "0.0.0.0",
    port: int = 8443,
    backend_host: str = "127.0.0.1",
    backend_port: int = 8420,
    cert_dir: str = "",
    cert_file: str = "",
    key_file: str = "",
    crl_file: str = "",
    ca_file: str = "",
    auto_gen_certs: bool = False,
    san_names: list[str] | None = None,
) -> ThreadingHTTPServer:
    """Prepare and serve the TLS proxy in the calling thread until shutdown."""
    server = create_tls_proxy_server(
        host=host,
        port=port,
        backend_host=backend_host,
        backend_port=backend_port,
        cert_dir=cert_dir,
        cert_file=cert_file,
        key_file=key_file,
        crl_file=crl_file,
        ca_file=ca_file,
        auto_gen_certs=auto_gen_certs,
        san_names=san_names,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return server


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, str(default)).strip()
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= result <= 65535:
        raise ValueError(f"{name} must be in 1..65535")
    return result


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "1" if default else "0").strip().casefold()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false")


def _run_from_environment() -> None:
    """Translate the standalone container environment into explicit arguments."""
    cert_dir = os.environ.get("CERT_DIR", "/certs")
    start_tls_proxy(
        host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        port=_env_int("LISTEN_PORT", 8443),
        backend_host=os.environ.get("BACKEND_HOST", "openbiliclaw-backend"),
        backend_port=_env_int("BACKEND_PORT", 8420),
        cert_dir=cert_dir,
        cert_file=os.environ.get("CERT_FILE", ""),
        key_file=os.environ.get("KEY_FILE", ""),
        ca_file=os.environ.get("CA_CERT_FILE", ""),
        crl_file=os.environ.get("CRL_FILE", ""),
        auto_gen_certs=_env_bool("AUTO_GEN_CERTS", True),
        san_names=_parse_san_names(os.environ.get("SAN_NAMES", "")),
    )


if __name__ == "__main__":
    _run_from_environment()
