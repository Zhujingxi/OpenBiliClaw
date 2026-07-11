from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .identity import canonical_source_platform
from .models import (
    NativeSaveAction,
    NativeSaveCapability,
    NativeSaveResult,
    NativeSaveRoute,
    SavedItemInput,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


class UnsupportedNativeSaveError(ValueError):
    """Raised when no registered capability can satisfy a save intent."""


class NativeSaveAdapter(Protocol):
    """Platform adapter used by the native-save router and sync service."""

    @property
    def capability(self) -> NativeSaveCapability: ...

    def target_label(self, action: NativeSaveAction) -> str: ...

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult: ...


class NativeSaveRouter:
    """Resolve platform-neutral save intents through registered capabilities."""

    def __init__(self, adapters: Iterable[NativeSaveAdapter] | None = None) -> None:
        self._adapters: dict[str, NativeSaveAdapter] = {}
        for adapter in adapters or ():
            self.register(adapter)

    def register(self, adapter: NativeSaveAdapter) -> None:
        """Register or replace the adapter for its canonical platform name."""
        platform = canonical_source_platform(adapter.capability.platform)
        if not platform:
            raise UnsupportedNativeSaveError("unsupported platform: platform is required")
        self._adapters[platform] = adapter

    def route(
        self,
        platform: str,
        requested_action: NativeSaveAction,
    ) -> tuple[NativeSaveAdapter, NativeSaveRoute]:
        """Resolve an intent to the native action and truthful target label."""
        normalized_platform = canonical_source_platform(platform)
        adapter = self._adapters.get(normalized_platform)
        if adapter is None:
            raise UnsupportedNativeSaveError(
                f"unsupported platform: {normalized_platform or platform}"
            )

        capability = adapter.capability
        if requested_action == "favorite":
            if not capability.supports_favorite:
                raise UnsupportedNativeSaveError(
                    f"unsupported favorite action: {normalized_platform}"
                )
            resolved_action: NativeSaveAction = "favorite"
        elif requested_action == "watch_later":
            if capability.supports_watch_later:
                resolved_action = "watch_later"
            elif capability.supports_favorite:
                resolved_action = "favorite"
            else:
                raise UnsupportedNativeSaveError(
                    f"unsupported watch-later action: {normalized_platform}"
                )
        else:  # pragma: no cover - protected by the NativeSaveAction type
            raise UnsupportedNativeSaveError(
                f"unsupported native save action: {requested_action}"
            )

        return adapter, NativeSaveRoute(
            requested_action=requested_action,
            resolved_action=resolved_action,
            resolved_target=adapter.target_label(resolved_action),
        )
