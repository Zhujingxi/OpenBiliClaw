"""Structured initial soul-profile generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.prompts import build_soul_profile_prompt
from openbiliclaw.llm.service import LLMServiceError

from .profile import SoulProfile
from .tone import build_tone_profile


class SupportsCoreMemoryTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...


class SoulProfileBuildError(Exception):
    """Raised when soul-profile generation fails or returns invalid data."""


@dataclass
class ProfileBuilder:
    """Generate an initial soul profile from history and preference context."""

    registry: SupportsCoreMemoryTask

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError("ProfileBuilder requires a service with complete_structured_task().")

    async def build(
        self,
        *,
        history: list[dict[str, Any]],
        preference: dict[str, Any],
        awareness_notes: list[dict[str, Any]],
        active_insights: list[dict[str, Any]],
    ) -> SoulProfile:
        messages = build_soul_profile_prompt(
            history_summary=self._summarize_history(history),
            preference_summary=preference,
            recent_awareness=awareness_notes,
            active_insights=active_insights,
            tone_profile=build_tone_profile(
                profile=None,
                preference_summary=preference,
                recent_feedback=[],
            ),
        )
        try:
            response = await self.registry.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise SoulProfileBuildError(str(exc)) from exc
        payload = self._parse_response(response.content)
        profile = SoulProfile(
            personality_portrait=str(payload.get("personality_portrait", "")),
            core_traits=self._as_str_list(payload.get("core_traits")),
            cognitive_style=self._as_str_list(payload.get("cognitive_style")),
            motivational_drivers=self._as_str_list(payload.get("motivational_drivers")),
            current_phase=str(payload.get("current_phase", "")),
            values=self._as_str_list(payload.get("values")),
            life_stage=str(payload.get("life_stage", "")),
            deep_needs=self._as_str_list(payload.get("deep_needs")),
        )
        # Attach raw MBTI data so OnionProfile.from_legacy() can pick it up
        profile._raw_mbti = payload.get("mbti")  # type: ignore[attr-defined]
        return profile

    def _parse_response(self, content: str) -> dict[str, object]:
        text = content.strip()
        if not text:
            raise SoulProfileBuildError("LLM returned an empty soul profile.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SoulProfileBuildError("LLM returned invalid JSON for soul profile.") from exc
        if not isinstance(parsed, dict):
            raise SoulProfileBuildError("LLM soul profile response must be a JSON object.")
        self._validate_payload(parsed)
        return parsed

    def _validate_payload(self, payload: dict[str, object]) -> None:
        required_fields = (
            "personality_portrait",
            "core_traits",
            "cognitive_style",
            "motivational_drivers",
            "current_phase",
            "values",
            "life_stage",
            "deep_needs",
        )
        missing = [field for field in required_fields if field not in payload]
        if missing:
            missing_text = ", ".join(missing)
            raise SoulProfileBuildError(
                f"LLM soul profile response is missing fields: {missing_text}"
            )

        portrait = str(payload.get("personality_portrait", "")).strip()
        if len(portrait) < 200:
            raise SoulProfileBuildError(
                "LLM soul profile portrait must be at least 200 characters long."
            )

        if not str(payload.get("current_phase", "")).strip():
            raise SoulProfileBuildError("LLM soul profile field 'current_phase' must be non-empty.")

        list_fields = (
            "core_traits",
            "cognitive_style",
            "motivational_drivers",
            "values",
            "deep_needs",
        )
        for field in list_fields:
            if not isinstance(payload.get(field), list):
                raise SoulProfileBuildError(f"LLM soul profile field '{field}' must be a list.")

    @staticmethod
    def _summarize_history(history: list[dict[str, Any]]) -> dict[str, object]:
        titles = [str(item.get("title", "")).strip() for item in history if item.get("title")]
        # Extract authors from multiple possible field names
        authors: list[str] = []
        for item in history:
            author = (
                item.get("author_name")
                or item.get("author")
                or item.get("up_name")
                or (item.get("metadata") or {}).get("author", "")
                or (item.get("metadata") or {}).get("up_name", "")
            )
            if author and str(author).strip():
                authors.append(str(author).strip())
        # Deduplicate while preserving order for frequency ranking
        from collections import Counter
        author_counts = Counter(authors)
        top_authors = [name for name, _ in author_counts.most_common(15)]

        # Time-based grouping: split into recent vs older if timestamps exist
        recent_titles: list[str] = []
        older_titles: list[str] = []
        for item in history:
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            view_at = item.get("view_at") or item.get("timestamp") or 0
            # Items in top 30% by position are "recent" (history is usually sorted newest first)
            # This is a heuristic when timestamps aren't available
        cutoff = max(1, len(history) * 3 // 10)
        for i, item in enumerate(history):
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            if i < cutoff:
                recent_titles.append(title)
            else:
                older_titles.append(title)

        summary: dict[str, object] = {
            "count": len(history),
            "titles": titles[:20],
            "authors": top_authors,
        }
        if recent_titles:
            summary["recent_titles"] = recent_titles[:10]
            summary["recent_hint"] = f"最近观看的 {len(recent_titles)} 个视频（前30%）代表当前活跃兴趣"
        if older_titles:
            summary["older_titles"] = older_titles[:10]
        return summary

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]
