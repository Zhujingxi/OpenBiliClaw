"""Awareness-layer generation from recent behavior."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    extract_llm_json_list,
    format_parse_failure,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import (
    build_awareness_prompt,
    build_awareness_with_confusions_prompt,
)
from openbiliclaw.llm.service import LLMServiceError
from openbiliclaw.llm.task_options import without_core_memory_kwargs

from .profile import AwarenessNote

logger = logging.getLogger(__name__)

_AWARENESS_WRAPPED_ARRAY_KEYS = (
    "results",
    "items",
    "notes",
    "awareness_notes",
    "awareness",
    "data",
    "output",
    "list",
    "array",
    # MiMo / reasoning-model variants seen in the wild (v0.3.x resilience pass).
    "observations",
    "recent_observations",
    "latest",
    "latest_observations",
)

# The full schema of a single awareness note, used by `_looks_like_single_note`
# below. The runtime check only requires `observation` (the only field whose
# absence makes the note worthless); the other keys are recovered with sensible
# defaults in `_build_note`.
_NOTE_SHAPE_KEYS = frozenset({"date", "observation", "trend", "emotion_guess"})


class SupportsCoreMemoryTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        inject_core_memory: bool = True,
    ) -> LLMResponse: ...


class AwarenessGenerationError(Exception):
    """Raised when awareness generation fails or returns invalid data."""


@dataclass
class AwarenessAnalyzer:
    """Generate structured recent-awareness notes from events."""

    registry: SupportsCoreMemoryTask

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError("AwarenessAnalyzer requires a service with complete_structured_task().")

    async def analyze(
        self,
        *,
        events: list[dict[str, object]],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        max_tokens: int = DEFAULT_STRUCTURED_MAX_TOKENS,
        source_event_ids: list[int] | None = None,
    ) -> list[AwarenessNote]:
        """Generate awareness notes.

        ``source_event_ids`` (Phase 0 evidence chain) is the id subset of
        events consumed this round; when provided, it is attached to every
        produced note as approximate provenance (the LLM does not attribute
        observations to specific events, so the whole batch rides along).
        When ``None`` (non-cursor callers), ids are derived from any ``id``
        fields on ``events`` — the awareness prompt itself is unchanged
        either way, so the ``analyze()`` render path stays byte-identical.
        """
        messages = build_awareness_prompt(
            events=events,
            preference_summary=preference,
            soul_profile=soul_profile,
        )
        try:
            complete_structured = self.registry.complete_structured_task
            response = await complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=max_tokens,
                caller="soul.awareness",
                **without_core_memory_kwargs(complete_structured),
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise AwarenessGenerationError(str(exc)) from exc
        payload = self._parse_response(response.content)
        evidence_ids = (
            list(source_event_ids) if source_event_ids is not None else self._event_ids_from(events)
        )
        return [self._build_note(item, evidence_ids) for item in payload if isinstance(item, dict)]

    @staticmethod
    def _event_ids_from(events: list[dict[str, object]]) -> list[int]:
        ids: list[int] = []
        for event in events:
            raw = event.get("id")
            if isinstance(raw, bool) or not isinstance(raw, int | str | float):
                continue
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        return ids

    async def analyze_with_confusions(
        self,
        *,
        events: list[dict[str, object]],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        max_tokens: int = DEFAULT_STRUCTURED_MAX_TOKENS,
        source_event_ids: list[int] | None = None,
    ) -> tuple[list[AwarenessNote], list[dict[str, object]]]:
        """Generate awareness notes AND confusion candidates (Phase 2).

        Uses the independent :func:`build_awareness_with_confusions_prompt`
        builder — the ``analyze()`` render path is left byte-identical. Returns
        ``(notes, confusion_candidates)``; ``notes`` are built exactly like
        ``analyze()`` (same ``_build_note`` + evidence attribution), and
        confusion candidates are whitelist-validated dicts (bad rows dropped).
        """
        messages = build_awareness_with_confusions_prompt(
            events=events,
            preference_summary=preference,
            soul_profile=soul_profile,
        )
        try:
            complete_structured = self.registry.complete_structured_task
            response = await complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=max_tokens,
                caller="soul.awareness_confusions",
                **without_core_memory_kwargs(complete_structured),
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise AwarenessGenerationError(str(exc)) from exc
        notes_payload, confusions_payload = self._parse_with_confusions(response.content)
        evidence_ids = (
            list(source_event_ids) if source_event_ids is not None else self._event_ids_from(events)
        )
        notes = [
            self._build_note(item, evidence_ids) for item in notes_payload if isinstance(item, dict)
        ]
        return notes, confusions_payload

    def _parse_with_confusions(
        self,
        content: str,
    ) -> tuple[list[object], list[dict[str, object]]]:
        """Parse the {"notes", "confusions"} object; tolerate legacy shapes.

        A bare array (or a wrapped-array under a known notes key) is treated as
        notes-only with no confusions — so a model that ignores the confusion
        contract still yields valid notes (parse-failure = drop confusions).
        """
        if not content.strip():
            return [], []
        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="awareness+confusions generation"),
            )
            raise AwarenessGenerationError(
                f"LLM returned invalid JSON for awareness+confusions generation "
                f"(raw_len={len(content.strip())})"
            )
        if isinstance(parsed, dict) and ("notes" in parsed or "confusions" in parsed):
            raw_notes = parsed.get("notes", [])
            notes = self._coerce_note_list(raw_notes) or []
            confusions = self._validate_confusions(parsed.get("confusions"))
            return notes, confusions
        # Legacy: array or wrapped-array of notes, no confusions.
        notes = self._coerce_note_list(parsed) or []
        return notes, []

    @staticmethod
    def _validate_confusions(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        result: list[dict[str, object]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            observation = str(item.get("observation", "")).strip()
            if not observation:
                logger.warning("confusion candidate dropped: empty observation")
                continue
            evidence = [
                str(ref).strip()
                for ref in (item.get("evidence_refs") or [])
                if isinstance(item.get("evidence_refs"), list) and str(ref).strip()
            ]
            result.append(
                {
                    "topic": str(item.get("topic", "")).strip(),
                    "observation": observation,
                    "interpretation": str(item.get("interpretation", "")).strip(),
                    "interpretation_confidence": _clamp_unit(
                        item.get("interpretation_confidence", 0.0)
                    ),
                    "evidence_refs": evidence,
                }
            )
        return result

    def merge_notes(
        self,
        existing: list[AwarenessNote],
        incoming: list[AwarenessNote],
    ) -> list[AwarenessNote]:
        """Merge awareness notes while deduplicating same-day observations."""
        merged = list(existing)
        seen = {(note.date, self._normalize_text(note.observation)) for note in existing}
        for note in incoming:
            key = (note.date, self._normalize_text(note.observation))
            if key in seen:
                continue
            merged.append(note)
            seen.add(key)
        return merged

    def _parse_response(self, content: str) -> list[object]:
        if not content.strip():
            return []
        helper_payload = extract_llm_json_list(
            content,
            wrapper_keys=_AWARENESS_WRAPPED_ARRAY_KEYS,
            allow_singleton=True,
            item_predicate=lambda item: "observation" in item,
        )
        if helper_payload is not None:
            return list(helper_payload)

        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="awareness generation"),
            )
            raise AwarenessGenerationError(
                f"LLM returned invalid JSON for awareness generation "
                f"(raw_len={len(content.strip())})"
            )
        payload = self._coerce_note_list(parsed)
        if payload is None:
            raise AwarenessGenerationError("LLM awareness response must be a JSON array.")
        return payload

    @staticmethod
    def _looks_like_single_note(value: object) -> bool:
        # Only `observation` is load-bearing — `date`, `trend`, `emotion_guess`
        # are recovered with defaults by `_build_note`. Reasoning models that
        # return a bare singular note dict (no array wrapper) are still
        # recoverable as long as `observation` is present.
        return isinstance(value, dict) and "observation" in value

    @staticmethod
    def _coerce_note_list(value: object) -> list[object] | None:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            for key in _AWARENESS_WRAPPED_ARRAY_KEYS:
                nested = value.get(key)
                if isinstance(nested, list):
                    return list(nested)
                if AwarenessAnalyzer._looks_like_single_note(nested):
                    return [nested]
            if AwarenessAnalyzer._looks_like_single_note(value):
                return [value]
        return None

    @staticmethod
    def _build_note(
        raw_item: dict[str, object],
        source_event_ids: list[int],
    ) -> AwarenessNote:
        return AwarenessNote(
            date=str(raw_item.get("date", "")).strip(),
            observation=str(raw_item.get("observation", "")).strip(),
            trend=str(raw_item.get("trend", "")).strip(),
            emotion_guess=str(raw_item.get("emotion_guess", "")).strip(),
            note_id=uuid4().hex[:12],
            source_event_ids=list(source_event_ids),
            # Attribution is per-round, not per-note: the LLM does not map
            # observations to specific events, so the whole consumed batch is
            # attached to every note and flagged approximate.
            source_event_ids_approximate=bool(source_event_ids),
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(value.split())


def _clamp_unit(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, round(number, 4)))
