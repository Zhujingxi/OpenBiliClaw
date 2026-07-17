"""Process runtime settings lifecycle shared by non-web composition roots."""

from __future__ import annotations

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


@contextmanager
def applied_runtime_settings(settings: UserSettings) -> Iterator[None]:
    """Apply mutable process policy and restore prior state on every exit path."""

    previous_mode = outbound_proxy_mode()
    previous_url = outbound_proxy_url()
    previous_handler_levels = snapshot_owned_handler_levels()
    set_outbound_proxy(settings.network.proxy_url, mode=settings.network.mode)
    apply_owned_handler_levels(
        console_level=settings.logging.console_level,
        file_level=settings.logging.file_level,
    )
    try:
        yield
    finally:
        set_outbound_proxy(previous_url or "", mode=previous_mode)
        restore_owned_handler_levels(previous_handler_levels)


__all__ = ["applied_runtime_settings"]
