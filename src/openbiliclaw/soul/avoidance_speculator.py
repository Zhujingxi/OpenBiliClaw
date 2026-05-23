"""Speculative avoidance lifecycle for proactive dislike-boundary exploration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.soul.speculator import (
    _build_event_text,
    _text_matches_keywords,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.soul.profile import OnionProfile

logger = logging.getLogger(__name__)


@dataclass
class SpeculativeAvoidanceSpecific:
    """A narrow avoided content pattern within a speculative avoidance domain."""

    name: str = ""
    confirmation_count: int = 0
    confirming_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "confirmation_count": self.confirmation_count,
            "confirming_events": list(self.confirming_events),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpeculativeAvoidanceSpecific:
        return cls(
            name=str(data.get("name", "")),
            confirmation_count=int(data.get("confirmation_count", 0)),
            confirming_events=list(data.get("confirming_events") or []),
        )


@dataclass
class SpeculativeAvoidance:
    """A speculated avoidance direction awaiting confirmation."""

    domain: str = ""
    reason: str = ""
    source_mode: str = ""
    source_signal: str = ""
    experience_mode: str = ""
    entry_load: str = ""
    confidence: float = 0.4
    weight: float = 0.4
    created_at: str = ""
    ttl_days: int = 3
    confirmation_count: int = 0
    confirmation_threshold: int = 3
    status: str = "active"
    confirming_events: list[str] = field(default_factory=list)
    specifics: list[SpeculativeAvoidanceSpecific] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "reason": self.reason,
            "source_mode": self.source_mode,
            "source_signal": self.source_signal,
            "experience_mode": self.experience_mode,
            "entry_load": self.entry_load,
            "confidence": self.confidence,
            "weight": self.weight,
            "created_at": self.created_at,
            "ttl_days": self.ttl_days,
            "confirmation_count": self.confirmation_count,
            "confirmation_threshold": self.confirmation_threshold,
            "status": self.status,
            "confirming_events": list(self.confirming_events),
            "specifics": [item.to_dict() for item in self.specifics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpeculativeAvoidance:
        return cls(
            domain=str(data.get("domain", "")),
            reason=str(data.get("reason", "")),
            source_mode=str(data.get("source_mode", "")),
            source_signal=str(data.get("source_signal", "")),
            experience_mode=str(data.get("experience_mode", "")),
            entry_load=str(data.get("entry_load", "")),
            confidence=float(data.get("confidence", 0.4)),
            weight=float(data.get("weight", 0.4)),
            created_at=str(data.get("created_at", "")),
            ttl_days=int(data.get("ttl_days", 3)),
            confirmation_count=int(data.get("confirmation_count", 0)),
            confirmation_threshold=int(data.get("confirmation_threshold", 3)),
            status=str(data.get("status", "active")),
            confirming_events=list(data.get("confirming_events") or []),
            specifics=[
                SpeculativeAvoidanceSpecific.from_dict(item)
                for item in data.get("specifics", [])
                if isinstance(item, dict)
            ],
        )


@dataclass
class AvoidanceCooldownEntry:
    """A denied or expired avoidance candidate suppressed until cooldown ends."""

    domain: str = ""
    source_mode: str = ""
    rejected_at: str = ""
    cooldown_until: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "source_mode": self.source_mode,
            "rejected_at": self.rejected_at,
            "cooldown_until": self.cooldown_until,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvoidanceCooldownEntry:
        return cls(
            domain=str(data.get("domain", "")),
            source_mode=str(data.get("source_mode", "")),
            rejected_at=str(data.get("rejected_at", "")),
            cooldown_until=str(data.get("cooldown_until", "")),
        )


@dataclass
class AvoidanceState:
    """Container for all speculative avoidance lifecycle state."""

    active: list[SpeculativeAvoidance] = field(default_factory=list)
    cooldown: list[AvoidanceCooldownEntry] = field(default_factory=list)
    last_generation_at: str = ""
    total_promoted: int = 0
    total_rejected: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": [item.to_dict() for item in self.active],
            "cooldown": [item.to_dict() for item in self.cooldown],
            "last_generation_at": self.last_generation_at,
            "total_promoted": self.total_promoted,
            "total_rejected": self.total_rejected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvoidanceState:
        return cls(
            active=[
                SpeculativeAvoidance.from_dict(item)
                for item in data.get("active", [])
                if isinstance(item, dict)
            ],
            cooldown=[
                AvoidanceCooldownEntry.from_dict(item)
                for item in data.get("cooldown", [])
                if isinstance(item, dict)
            ],
            last_generation_at=str(data.get("last_generation_at", "")),
            total_promoted=int(data.get("total_promoted", 0)),
            total_rejected=int(data.get("total_rejected", 0)),
        )


@dataclass
class AvoidanceTickResult:
    """Summary of one avoidance speculator tick."""

    generated: list[SpeculativeAvoidance] = field(default_factory=list)
    promoted: list[SpeculativeAvoidance] = field(default_factory=list)
    rejected: list[SpeculativeAvoidance] = field(default_factory=list)
    observed_matches: int = 0


def load_avoidance_state(data_dir: Path) -> AvoidanceState:
    """Load avoidance state from disk."""
    path = data_dir / "memory" / "avoidance_state.json"
    if not path.exists():
        return AvoidanceState()
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            return AvoidanceState.from_dict(data)
    except (json.JSONDecodeError, OSError):
        logger.debug("Failed to load avoidance state", exc_info=True)
    return AvoidanceState()


def save_avoidance_state(data_dir: Path, state: AvoidanceState) -> None:
    """Persist avoidance state to disk."""
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    with open(memory_dir / "avoidance_state.json", "w", encoding="utf-8") as file:
        json.dump(state.to_dict(), file, ensure_ascii=False, indent=2)


def promote_ready_avoidances(state: AvoidanceState) -> tuple[list[SpeculativeAvoidance], AvoidanceState]:
    """Extract avoidance candidates that are ready for external writeback."""
    promoted: list[SpeculativeAvoidance] = []
    remaining: list[SpeculativeAvoidance] = []
    for item in state.active:
        ready = (
            item.status == "active"
            and item.confirmation_count >= item.confirmation_threshold
        ) or item.status == "confirmed"
        if ready:
            item.status = "promoted"
            promoted.append(item)
            state.total_promoted += 1
        else:
            remaining.append(item)
    state.active = remaining
    return promoted, state


def expire_stale_avoidances(
    state: AvoidanceState,
    now: datetime,
    cooldown_days: int = 7,
) -> tuple[list[SpeculativeAvoidance], AvoidanceState]:
    """Expire stale active avoidance candidates and add cooldown entries."""
    rejected: list[SpeculativeAvoidance] = []
    remaining: list[SpeculativeAvoidance] = []
    for item in state.active:
        if item.status != "active":
            remaining.append(item)
            continue
        try:
            created = datetime.fromisoformat(item.created_at)
        except (TypeError, ValueError):
            remaining.append(item)
            continue
        if now > created + timedelta(days=item.ttl_days):
            item.status = "rejected"
            rejected.append(item)
            state.total_rejected += 1
            state.cooldown.append(
                AvoidanceCooldownEntry(
                    domain=item.domain,
                    source_mode=item.source_mode,
                    rejected_at=now.isoformat(),
                    cooldown_until=(now + timedelta(days=cooldown_days)).isoformat(),
                )
            )
        else:
            remaining.append(item)
    state.active = remaining

    valid_cooldown: list[AvoidanceCooldownEntry] = []
    for item in state.cooldown:
        try:
            cooldown_until = datetime.fromisoformat(item.cooldown_until)
        except (TypeError, ValueError):
            continue
        if now <= cooldown_until:
            valid_cooldown.append(item)
    state.cooldown = valid_cooldown
    return rejected, state


def _is_explicit_negative_event(event: dict[str, Any]) -> bool:
    metadata = event.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    feedback_type = str(metadata.get("feedback_type", "")).strip().lower()
    reaction = str(metadata.get("reaction", "")).strip().lower()
    event_type = str(event.get("event_type", "")).strip().lower()
    return feedback_type == "dislike" or reaction == "thumbs_down" or event_type == "dislike"


def _event_matches_avoidance(event: dict[str, Any], item: SpeculativeAvoidance) -> bool:
    event_text = _build_event_text(event)
    if _text_matches_keywords(event_text, item.domain):
        return True
    return any(_text_matches_keywords(event_text, specific.name) for specific in item.specifics)


def observe_avoidance_events(
    events: list[dict[str, Any]],
    state: AvoidanceState,
) -> tuple[AvoidanceState, int]:
    """Observe explicit negative evidence against active avoidance candidates."""
    match_count = 0
    for event in events:
        if not isinstance(event, dict) or not _is_explicit_negative_event(event):
            continue
        event_text = _build_event_text(event)
        title_short = str(event.get("title", ""))[:80]
        for item in state.active:
            if item.status != "active" or not _event_matches_avoidance(event, item):
                continue
            item.confirmation_count += 1
            if title_short:
                item.confirming_events.append(title_short)
            for specific in item.specifics:
                if _text_matches_keywords(event_text, specific.name):
                    specific.confirmation_count += 1
                    if title_short:
                        specific.confirming_events.append(title_short)
            match_count += 1
    return state, match_count


class AvoidanceSpeculator:
    """IO boundary for speculative avoidance lifecycle state."""

    def __init__(
        self,
        *,
        llm_service: object | None,
        data_dir: Path,
        generation_interval_minutes: int = 10,
        default_ttl_days: int = 3,
        cooldown_days: int = 7,
        confirmation_threshold: int = 3,
        max_active: int = 5,
    ) -> None:
        self._llm_service = llm_service
        self._data_dir = data_dir
        self._generation_interval_minutes = generation_interval_minutes
        self._default_ttl_days = default_ttl_days
        self._cooldown_days = cooldown_days
        self._confirmation_threshold = confirmation_threshold
        self._max_active = max_active

    def _load_state(self) -> AvoidanceState:
        return load_avoidance_state(self._data_dir)

    def _save_state(self, state: AvoidanceState) -> None:
        save_avoidance_state(self._data_dir, state)

    def get_active_avoidances(self) -> list[SpeculativeAvoidance]:
        state = self._load_state()
        return [item for item in state.active if item.status == "active"]

    def observe(self, events: list[dict[str, Any]]) -> int:
        if not events:
            return 0
        state = self._load_state()
        if not any(item.status == "active" for item in state.active):
            return 0
        state, match_count = observe_avoidance_events(events, state)
        if match_count:
            self._save_state(state)
        return match_count

    async def tick(
        self,
        profile: OnionProfile,
        *,
        feedback_history: object | None = None,
    ) -> AvoidanceTickResult:
        now = datetime.now()
        state = self._load_state()
        result = AvoidanceTickResult()

        rejected, state = expire_stale_avoidances(state, now, self._cooldown_days)
        result.rejected = rejected
        promoted, state = promote_ready_avoidances(state)
        result.promoted = promoted

        self._save_state(state)
        return result

    async def force_tick(
        self,
        profile: OnionProfile,
        *,
        feedback_history: object | None = None,
    ) -> AvoidanceTickResult:
        return await self.tick(profile, feedback_history=feedback_history)
