"""Post-commit observation notifications."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ObservationsCommitted:
    """Committed IDs only; payloads remain in the repository."""

    observation_ids: tuple[str, ...]
