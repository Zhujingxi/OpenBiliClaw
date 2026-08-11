"""Concrete technical adapters for the target architecture."""

from .credentials.vault import CredentialVault
from .events.publisher import EventPublisher, EventSubscription
from .files import BoundedFiles
from .http.clients import HttpClientFactory
from .http.policy import HttpPolicy, RetryPolicy
from .sqlite.database import ConnectionPolicy, SqliteDatabase, SqliteSession
from .sqlite.schema import Migration, SchemaMigrator, backup_before_destructive_migration
from .telemetry import TelemetryRecord, TelemetrySink

__all__ = [
    "BoundedFiles",
    "ConnectionPolicy",
    "CredentialVault",
    "EventPublisher",
    "EventSubscription",
    "HttpClientFactory",
    "HttpPolicy",
    "Migration",
    "RetryPolicy",
    "SchemaMigrator",
    "SqliteDatabase",
    "SqliteSession",
    "TelemetryRecord",
    "TelemetrySink",
    "backup_before_destructive_migration",
]
