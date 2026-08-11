"""Structured telemetry with mandatory secret redaction."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

TelemetryValue = str | int | float | bool | None
_SENSITIVE_FRAGMENTS = ("secret", "token", "password", "api_key", "cookie", "authorization")


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    """One already-redacted telemetry record."""

    kind: str
    name: str
    fields: tuple[tuple[str, TelemetryValue], ...]
    recorded_at: str


class TelemetrySink:
    """Collect structured metrics/traces only after redaction."""

    def __init__(self, *, secret_values: tuple[str, ...] = (), max_records: int = 10_000) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self._secrets = tuple(value for value in secret_values if value)
        self._records: deque[TelemetryRecord] = deque(maxlen=max_records)

    @property
    def records(self) -> tuple[TelemetryRecord, ...]:
        return tuple(self._records)

    def metric(self, name: str, fields: Mapping[str, TelemetryValue]) -> TelemetryRecord:
        """Record a metric with redacted fields."""

        return self._append("metric", name, fields)

    @contextmanager
    def trace(self, name: str, fields: Mapping[str, TelemetryValue]) -> Iterator[TelemetryRecord]:
        """Record trace start and a content-free outcome."""

        started = self._append("trace_start", name, fields)
        try:
            yield started
        except BaseException as exc:
            self._append("trace_end", name, {"outcome": "error", "error_type": type(exc).__name__})
            raise
        else:
            self._append("trace_end", name, {"outcome": "ok"})

    def _append(
        self, kind: str, name: str, fields: Mapping[str, TelemetryValue]
    ) -> TelemetryRecord:
        redacted = tuple(sorted((key, self._redact(key, value)) for key, value in fields.items()))
        safe_name = name
        for secret in self._secrets:
            safe_name = safe_name.replace(secret, "<redacted>")
        record = TelemetryRecord(kind, safe_name, redacted, datetime.now(UTC).isoformat())
        self._records.append(record)
        return record

    def _redact(self, key: str, value: TelemetryValue) -> TelemetryValue:
        if any(fragment in key.lower() for fragment in _SENSITIVE_FRAGMENTS):
            return "<redacted>"
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "<redacted>")
        return value
