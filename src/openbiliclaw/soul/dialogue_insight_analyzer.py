"""Structured extraction of dialogue-derived insight candidates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    format_parse_failure,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import build_dialogue_insight_prompt
from openbiliclaw.llm.service import LLMServiceError

logger = logging.getLogger(__name__)


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
    ) -> LLMResponse: ...


class DialogueInsightAnalysisError(Exception):
    """Raised when dialogue insight extraction fails or returns invalid data."""


@dataclass
class DialogueInsightAnalyzer:
    """Extract structured insight candidates from chat turns."""

    registry: SupportsCoreMemoryTask

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError(
                "DialogueInsightAnalyzer requires a service with complete_structured_task()."
            )

    async def extract(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
    ) -> list[dict[str, object]]:
        """Extract candidate insights from a single chat exchange."""
        messages = build_dialogue_insight_prompt(
            user_message=user_message,
            assistant_reply=assistant_reply,
            core_memory=core_memory,
        )
        try:
            response = await self.registry.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
                caller="soul.dialogue_insight",
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise DialogueInsightAnalysisError(str(exc)) from exc

        return self._parse_response(response.content)

    def _parse_response(self, content: str) -> list[dict[str, object]]:
        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="dialogue insight analysis"),
            )
            raise DialogueInsightAnalysisError(
                f"LLM returned invalid JSON for dialogue insight analysis "
                f"(raw_len={len(content.strip())})"
            )
        if not isinstance(parsed, dict):
            raise DialogueInsightAnalysisError("Dialogue insight response must be a JSON object.")
        raw_candidates = parsed.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise DialogueInsightAnalysisError("Dialogue insight candidates must be a list.")
        normalized: list[dict[str, object]] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            content_text = str(item.get("content", "")).strip()
            if not content_text:
                continue
            normalized.append(
                {
                    "kind": str(item.get("kind", "")).strip() or "state",
                    "content": content_text,
                    "confidence": self._clamp_confidence(item.get("confidence", 0.0)),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
            )
        return normalized

    @staticmethod
    def _clamp_confidence(raw_value: object) -> float:
        if isinstance(raw_value, bool | int | float):
            value = float(raw_value)
        elif isinstance(raw_value, str):
            try:
                value = float(raw_value)
            except ValueError:
                value = 0.0
        else:
            value = 0.0
        return max(0.0, min(1.0, round(value, 4)))
