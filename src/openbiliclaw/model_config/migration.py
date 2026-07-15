"""Public facade for deterministic, read-only legacy model migration."""

from ._migration_inspection import (
    legacy_connection_id,
    slugify_id,
    unique_id,
)
from ._migration_mapping import migrate_legacy_llm
from ._migration_resolution import apply_migration_resolutions
from ._migration_types import (
    LegacyMigrationResult,
    MigrationAction,
    MigrationIssue,
    MigrationReport,
    MigrationResolution,
    MigrationResolutionError,
)

__all__ = [
    "LegacyMigrationResult",
    "MigrationAction",
    "MigrationIssue",
    "MigrationReport",
    "MigrationResolution",
    "MigrationResolutionError",
    "apply_migration_resolutions",
    "legacy_connection_id",
    "migrate_legacy_llm",
    "slugify_id",
    "unique_id",
]
