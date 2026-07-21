"""Shared public-URL sanitization at domain contract boundaries."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_QUERY_SUFFIXES = (
    "apikey",
    "apikeys",
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "passwords",
    "secret",
    "secrets",
    "session",
    "sessionid",
    "sessions",
    "signature",
    "signatures",
    "token",
    "tokens",
)
_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "accesskey",
        "accesstoken",
        "auth",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "refreshtoken",
        "session",
        "sessionid",
        "signature",
        "token",
        "xsecsource",
        "xsectoken",
    }
)
_PUBLIC_URL_PREFIX = re.compile(r"^https?://", re.IGNORECASE)


def sanitize_public_url(value: object) -> object:
    """Remove credentials from an HTTP URL before it crosses a persistence boundary."""

    if value is None:
        return None
    raw = str(value).strip()
    if _PUBLIC_URL_PREFIX.match(raw) is None:
        return value
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return ""
        # Accessing port validates malformed/non-numeric ports before anything is persisted.
        _ = parsed.port
    except ValueError:
        return ""

    safe_netloc = parsed.netloc.rsplit("@", 1)[-1]
    safe_query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_sensitive_query_key(key)
        ],
        doseq=True,
    )
    safe_fragment = parsed.fragment
    if "=" in safe_fragment:
        fragment_query = safe_fragment.removeprefix("?")
        safe_fragment = urlencode(
            [
                (key, item)
                for key, item in parse_qsl(fragment_query, keep_blank_values=True)
                if not _is_sensitive_query_key(key)
            ],
            doseq=True,
        )
    return urlunsplit((parsed.scheme, safe_netloc, parsed.path, safe_query, safe_fragment))


def _is_sensitive_query_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _SENSITIVE_QUERY_NAMES or normalized.endswith(_SENSITIVE_QUERY_SUFFIXES)


__all__ = ["sanitize_public_url"]
