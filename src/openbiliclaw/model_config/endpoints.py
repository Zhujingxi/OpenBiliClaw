"""Secret-safe validation shared by model configuration and runtime factories."""

from __future__ import annotations

import ipaddress
import unicodedata
from urllib.parse import urlsplit


class InvalidModelEndpointError(ValueError):
    """An endpoint failed policy validation; the raw value is never retained."""

    def __init__(self) -> None:
        super().__init__("model endpoint is invalid")


def validated_native_base_url(value: str) -> str:
    """Return an empty or safe HTTP(S) base URL without normalizing its bytes.

    Every populated native model endpoint uses this one policy before it can
    enter a public snapshot, persistence transaction, credential callback,
    proxy callback, or SDK constructor.  Rejected input is deliberately absent
    from the exception and its chain.
    """
    if value == "":
        return ""
    invalid_text = (
        value != value.strip()
        or "\\" in value
        or "?" in value
        or "#" in value
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in value
        )
    )
    if invalid_text:
        raise InvalidModelEndpointError()

    parsed = None
    hostname = None
    port = None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        parsed = None
        hostname = None
        port = None
    invalid_structure = (
        parsed is None
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or (port is not None and port <= 0)
        or (hostname is not None and not _valid_endpoint_hostname(hostname))
    )
    if invalid_structure:
        raise InvalidModelEndpointError() from None
    return value


def _valid_endpoint_hostname(hostname: str) -> bool:
    if hostname.endswith(".."):
        return False
    normalized = hostname[:-1] if hostname.endswith(".") else hostname
    if not normalized:
        return False
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        try:
            ascii_name = normalized.encode("idna").decode("ascii")
        except UnicodeError:
            return False
        if len(ascii_name) > 253:
            return False
        labels = ascii_name.split(".")
        return all(
            0 < len(label) <= 63
            and not label.startswith("-")
            and not label.endswith("-")
            and all(
                character.isascii() and (character.isalnum() or character == "-")
                for character in label
            )
            for label in labels
        )
    return True


__all__ = ["InvalidModelEndpointError", "validated_native_base_url"]
