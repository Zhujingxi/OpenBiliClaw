"""Process runtime settings lifecycle shared by non-web composition roots."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

from openbiliclaw.logging_setup import (
    apply_owned_handler_levels,
    restore_owned_handler_levels,
    snapshot_owned_handler_levels,
)
from openbiliclaw.network import (
    outbound_proxy_mode,
    outbound_proxy_url,
    set_outbound_proxy,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from openbiliclaw.features.system.domain import UserSettings

_CA_ENVIRONMENT_VARIABLES = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


def _restore_ca_environment(snapshot: dict[str, str]) -> None:
    for name in _CA_ENVIRONMENT_VARIABLES:
        if name in snapshot:
            os.environ[name] = snapshot[name]
        else:
            os.environ.pop(name, None)


@contextmanager
def applied_runtime_settings(settings: UserSettings) -> Iterator[None]:
    """Apply mutable process policy and restore prior state on every exit path."""

    previous_mode = outbound_proxy_mode()
    previous_url = outbound_proxy_url()
    previous_handler_levels = snapshot_owned_handler_levels()
    previous_ca_environment = {
        name: os.environ[name] for name in _CA_ENVIRONMENT_VARIABLES if name in os.environ
    }
    try:
        set_outbound_proxy(settings.network.proxy_url, mode=settings.network.mode)
        apply_owned_handler_levels(
            console_level=settings.logging.console_level,
            file_level=settings.logging.file_level,
        )
        yield
    finally:
        set_outbound_proxy(previous_url or "", mode=previous_mode)
        _restore_ca_environment(previous_ca_environment)
        restore_owned_handler_levels(previous_handler_levels)


__all__ = ["applied_runtime_settings"]
