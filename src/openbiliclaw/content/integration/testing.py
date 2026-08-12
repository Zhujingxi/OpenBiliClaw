"""Reusable manifest/implementation contract checks for provider packages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import provider_contract_violations

if TYPE_CHECKING:
    from .manifest import ProviderManifest


def validate_provider_contract(
    manifest: ProviderManifest, implementation: object
) -> tuple[str, ...]:
    """Return violations suitable for provider package contract tests."""

    return provider_contract_violations(manifest, implementation)
