"""Orthogonal source-auth contract and its legacy compatibility layer.

See ``docs/plans/2026-07-18-source-auth-contract-spec.md`` for the design and
``legacy.py`` for why the pre-existing ``state`` field is carried through rather
than derived.
"""

from __future__ import annotations

from openbiliclaw.api.source_auth.contract import (
    Credential,
    CredentialOrigin,
    SourceAuthContract,
    Verification,
    VerifyMethod,
)
from openbiliclaw.api.source_auth.legacy import check_legacy_consistency

__all__ = [
    "Credential",
    "CredentialOrigin",
    "SourceAuthContract",
    "Verification",
    "VerifyMethod",
    "check_legacy_consistency",
]
