"""Explicit composition-time registry for the seven retained source connectors."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.features.sources.domain import SourceConnector, SourceManifest


class SourceRegistry:
    """Immutable registry; sources cannot be discovered or injected dynamically."""

    def __init__(self, connectors: tuple[SourceConnector, ...]) -> None:
        by_id = {connector.manifest.source_id: connector for connector in connectors}
        if len(by_id) != len(connectors):
            raise ValueError("source registry contains duplicate source IDs")
        self._connectors: Mapping[str, SourceConnector] = MappingProxyType(by_id)

    @property
    def source_ids(self) -> tuple[str, ...]:
        """Return canonical IDs in composition order."""

        return tuple(self._connectors)

    @property
    def manifests(self) -> Mapping[str, SourceManifest]:
        """Return immutable capability declarations keyed by canonical ID."""

        return MappingProxyType(
            {source_id: connector.manifest for source_id, connector in self._connectors.items()}
        )

    def get(self, source_id: str) -> SourceConnector:
        """Resolve a canonical source ID or reject it explicitly."""

        try:
            return self._connectors[source_id]
        except KeyError as exc:
            raise LookupError(f"unknown source: {source_id}") from exc


def build_source_registry(
    *,
    bilibili: SourceConnector,
    xiaohongshu: SourceConnector,
    douyin: SourceConnector,
    youtube: SourceConnector,
    twitter: SourceConnector,
    zhihu: SourceConnector,
    reddit: SourceConnector,
) -> SourceRegistry:
    """Register exactly seven explicitly constructed connectors at composition time."""

    named = (
        ("bilibili", bilibili),
        ("xiaohongshu", xiaohongshu),
        ("douyin", douyin),
        ("youtube", youtube),
        ("twitter", twitter),
        ("zhihu", zhihu),
        ("reddit", reddit),
    )
    for expected_id, connector in named:
        if connector.manifest.source_id != expected_id:
            raise ValueError(f"registry slot {expected_id} received {connector.manifest.source_id}")
    return SourceRegistry(tuple(connector for _, connector in named))
