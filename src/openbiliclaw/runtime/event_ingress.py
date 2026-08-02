"""Durable, idempotent ingress for canonical behavioral events."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from openbiliclaw.memory.manager import SUPPORTED_EVENT_TYPES

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventIngressItemReceipt:
    """Outcome for one input position in an ingress batch."""

    index: int
    event_type: str
    event_id: int = 0
    inserted: bool = False
    duplicate: bool = False
    error: str = ""


@dataclass(frozen=True)
class EventIngressReceipt:
    """Durable acceptance summary returned to compatibility adapters."""

    items: tuple[EventIngressItemReceipt, ...] = field(default_factory=tuple)
    accepted: int = 0
    inserted: int = 0
    duplicates: int = 0
    rejected: int = 0


class EventIngressService:
    """Validate canonical events, commit them atomically, then wake one owner.

    The wake is only a hint. SQLite rows and producer-owned ``ingest_key``
    values are the source of truth, so a crash before/after wake loses no fact.
    """

    def __init__(
        self,
        memory_manager: Any,
        *,
        memory_manager_resolver: Callable[[], Any] | None = None,
        prepare_owner: Callable[[], Awaitable[object] | object] | None = None,
        wake: Callable[[], object] | None = None,
    ) -> None:
        self._memory_manager = memory_manager
        self._memory_manager_resolver = memory_manager_resolver
        self._prepare_owner = prepare_owner
        self._wake = wake
        self._compat_next_event_id = 1

    async def accept(
        self,
        event: dict[str, Any],
        *,
        producer: str,
    ) -> EventIngressReceipt:
        """Accept one event through the batch transaction path."""
        return await self.accept_batch([event], producer=producer)

    async def accept_batch(
        self,
        events: list[dict[str, Any]],
        *,
        producer: str,
    ) -> EventIngressReceipt:
        """Persist every valid input in one transaction and retain rejections."""
        producer_name = self._validate_producer(producer)
        valid: list[dict[str, Any]] = []
        valid_indexes: list[int] = []
        receipts: dict[int, EventIngressItemReceipt] = {}
        for index, raw_event in enumerate(events):
            try:
                event = self._validated_event(raw_event, producer=producer_name)
            except ValueError as exc:
                event_type = ""
                if isinstance(raw_event, dict):
                    event_type = str(
                        raw_event.get("event_type") or raw_event.get("type") or ""
                    ).strip()
                receipts[index] = EventIngressItemReceipt(
                    index=index,
                    event_type=event_type,
                    error=str(exc),
                )
                continue
            valid.append(event)
            valid_indexes.append(index)

        if valid:
            await self._call_prepare_owner()
            memory_manager = (
                self._memory_manager_resolver()
                if self._memory_manager_resolver is not None
                else self._memory_manager
            )
            persist = getattr(memory_manager, "persist_events_with_receipts", None)
            if callable(persist):
                stored = await persist(valid)
            else:
                # Compatibility for narrow injected/legacy test doubles only;
                # production MemoryManager always exposes the atomic receipt
                # API above.
                propagate_many = getattr(memory_manager, "propagate_events", None)
                propagate_one = getattr(memory_manager, "propagate_event", None)
                if callable(propagate_many):
                    result = propagate_many(valid)
                    if inspect.isawaitable(result):
                        await result
                elif callable(propagate_one):
                    for event in valid:
                        result = propagate_one(event)
                        if inspect.isawaitable(result):
                            await result
                else:
                    raise RuntimeError("memory manager has no durable event persistence API")
                stored = []
                for event in valid:
                    stored.append(
                        SimpleNamespace(
                            event_id=self._compat_next_event_id,
                            event_type=str(event["event_type"]),
                            inserted=True,
                            duplicate=False,
                        )
                    )
                    self._compat_next_event_id += 1
            if len(stored) != len(valid):
                raise RuntimeError("event receipt count did not match accepted input count")
            for index, result in zip(valid_indexes, stored, strict=True):
                receipts[index] = EventIngressItemReceipt(
                    index=index,
                    event_type=str(result.event_type),
                    event_id=int(result.event_id),
                    inserted=bool(result.inserted),
                    duplicate=bool(result.duplicate),
                )
            self._wake_owner()

        ordered = tuple(receipts[index] for index in range(len(events)))
        inserted = sum(1 for item in ordered if item.inserted)
        duplicates = sum(1 for item in ordered if item.duplicate)
        rejected = sum(1 for item in ordered if item.error)
        return EventIngressReceipt(
            items=ordered,
            accepted=inserted + duplicates,
            inserted=inserted,
            duplicates=duplicates,
            rejected=rejected,
        )

    async def _call_prepare_owner(self) -> None:
        prepare = self._prepare_owner
        if prepare is None:
            return
        result = prepare()
        if inspect.isawaitable(result):
            await result

    def _wake_owner(self) -> None:
        wake = self._wake
        if wake is not None:
            try:
                wake()
            except Exception:
                # The durable row/cursor recovery loop is authoritative; a wake
                # is only a latency hint and can never undo a committed fact.
                logger.warning("event ingress owner wake failed after commit", exc_info=True)

    @staticmethod
    def _validate_producer(producer: str) -> str:
        normalized = str(producer or "").strip().lower()
        if not normalized or len(normalized) > 80:
            raise ValueError("producer must be a non-empty bounded name")
        if any(character.isspace() or ord(character) < 32 for character in normalized):
            raise ValueError("producer contains invalid characters")
        return normalized

    @staticmethod
    def _validated_event(raw_event: object, *, producer: str) -> dict[str, Any]:
        if not isinstance(raw_event, dict):
            raise ValueError("event must be an object")
        event = dict(raw_event)
        event_type = str(event.get("event_type") or event.get("type") or "").strip()
        if event_type not in SUPPORTED_EVENT_TYPES:
            raise ValueError(f"Unsupported event type: {event_type or 'unknown'}")
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Event metadata must be an object")
        event["event_type"] = event_type
        event["metadata"] = dict(metadata)
        raw_key = str(event.get("ingest_key", "") or "").strip()
        if raw_key:
            namespaced = f"{producer}:{raw_key}"
            if len(namespaced) > 512:
                raise ValueError("ingest_key exceeds 512 characters")
            event["ingest_key"] = namespaced
        else:
            event["ingest_key"] = ""
        return event
