"""Preference layer analysis built on structured LLM extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.prompts import build_preference_analysis_prompt


class SupportsComplete(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse: ...


class PreferenceAnalysisError(Exception):
    """Raised when preference extraction fails or returns invalid data."""


@dataclass
class PreferenceAnalyzer:
    """Analyze recent events into a structured preference profile."""

    registry: SupportsComplete
    decay_factor_per_week: float = 0.9
    min_interest_weight: float = 0.05

    async def analyze_events(
        self,
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
    ) -> dict[str, object]:
        """Run structured extraction and merge the result with existing preference state."""
        messages = build_preference_analysis_prompt(
            events=events,
            existing_preference=existing_preference,
        )
        try:
            response = await self.registry.complete(messages, json_mode=True)
        except LLMProviderError as exc:
            raise PreferenceAnalysisError(str(exc)) from exc

        raw_preference = self._parse_response(response.content)
        normalized = self._normalize_preference(raw_preference)
        return self.merge_preferences(existing_preference, normalized, now=datetime.now())

    def merge_preferences(
        self,
        existing_preference: dict[str, object],
        new_preference: dict[str, object],
        *,
        now: datetime,
    ) -> dict[str, object]:
        """Merge and decay preference state."""
        existing_interests = self._decay_interests(
            existing_preference.get("interests", []),
            now=now,
        )
        merged_interests: dict[tuple[str, str], dict[str, object]] = {
            (str(item["name"]), str(item["category"])): item for item in existing_interests
        }

        for item in self._as_list(new_preference.get("interests", [])):
            if not isinstance(item, dict):
                continue
            key = (str(item["name"]), str(item["category"]))
            existing = merged_interests.get(key)
            if existing is None:
                merged_interests[key] = {
                    **item,
                    "first_seen": now.isoformat(),
                    "last_seen": now.isoformat(),
                }
                continue
            merged_interests[key] = {
                **existing,
                **item,
                "first_seen": existing.get("first_seen") or now.isoformat(),
                "last_seen": now.isoformat(),
                "weight": self._clamp_weight(
                    max(
                        self._to_float(existing.get("weight", 0.0)),
                        self._to_float(item.get("weight", 0.0)),
                    )
                ),
            }

        favorite_up_users = sorted({
            *self._as_str_list(existing_preference.get("favorite_up_users", [])),
            *self._as_str_list(new_preference.get("favorite_up_users", [])),
        })
        disliked_topics = sorted({
            *self._as_str_list(existing_preference.get("disliked_topics", [])),
            *self._as_str_list(new_preference.get("disliked_topics", [])),
        })

        default_preference = self._default_preference()
        style = self._as_dict(default_preference["style"]).copy()
        style.update(self._as_dict(existing_preference.get("style", {})))
        style.update(self._as_dict(new_preference.get("style", {})))
        context = self._as_dict(default_preference["context"]).copy()
        context.update(self._as_dict(existing_preference.get("context", {})))
        context.update(self._as_dict(new_preference.get("context", {})))

        merged = {
            "interests": sorted(
                merged_interests.values(),
                key=lambda item: self._to_float(item.get("weight", 0.0)),
                reverse=True,
            ),
            "style": style,
            "context": context,
            "exploration_openness": self._clamp_weight(
                self._to_float(
                    new_preference.get(
                        "exploration_openness",
                        existing_preference.get("exploration_openness", 0.5),
                    )
                )
            ),
            "disliked_topics": disliked_topics,
            "favorite_up_users": favorite_up_users,
        }
        return merged

    def _decay_interests(
        self,
        interests: object,
        *,
        now: datetime,
    ) -> list[dict[str, object]]:
        if not isinstance(interests, list):
            return []

        decayed: list[dict[str, object]] = []
        for raw_item in interests:
            if not isinstance(raw_item, dict):
                continue
            item = self._normalize_interest(raw_item)
            last_seen_text = str(item.get("last_seen") or "")
            try:
                last_seen = datetime.fromisoformat(last_seen_text) if last_seen_text else now
            except ValueError:
                last_seen = now
            weeks = max((now - last_seen).days, 0) / 7
            decayed_weight = self._clamp_weight(
                self._to_float(item.get("weight", 0.0))
                * (self.decay_factor_per_week ** weeks)
            )
            if decayed_weight < self.min_interest_weight:
                continue
            item["weight"] = decayed_weight
            decayed.append(item)
        return decayed

    def _parse_response(self, content: str) -> dict[str, object]:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PreferenceAnalysisError(
                "LLM returned invalid JSON for preference analysis."
            ) from exc
        if not isinstance(parsed, dict):
            raise PreferenceAnalysisError("LLM preference response must be a JSON object.")
        return parsed

    def _normalize_preference(self, raw_preference: dict[str, object]) -> dict[str, object]:
        normalized = self._default_preference()
        style = self._as_dict(normalized["style"]).copy()
        style.update(self._as_dict(raw_preference.get("style")))
        context = self._as_dict(normalized["context"]).copy()
        context.update(self._as_dict(raw_preference.get("context")))
        normalized["interests"] = [
            self._normalize_interest(item)
            for item in self._as_list(raw_preference.get("interests", []))
            if isinstance(item, dict)
        ]
        normalized["style"] = style
        normalized["context"] = context
        normalized["exploration_openness"] = self._clamp_weight(
            self._to_float(raw_preference.get("exploration_openness", 0.5))
        )
        normalized["disliked_topics"] = self._as_str_list(
            raw_preference.get("disliked_topics", [])
        )
        normalized["favorite_up_users"] = self._as_str_list(
            raw_preference.get("favorite_up_users", [])
        )
        return normalized

    def _normalize_interest(self, raw_item: dict[str, object]) -> dict[str, object]:
        return {
            "name": str(raw_item.get("name", "")).strip(),
            "category": str(raw_item.get("category", "")).strip(),
            "weight": self._clamp_weight(self._to_float(raw_item.get("weight", 0.0))),
            "first_seen": raw_item.get("first_seen", ""),
            "last_seen": raw_item.get("last_seen", ""),
            "source": str(raw_item.get("source", "")).strip(),
        }

    @staticmethod
    def _as_dict(raw_value: object) -> dict[str, object]:
        return raw_value if isinstance(raw_value, dict) else {}

    @staticmethod
    def _as_list(raw_value: object) -> list[object]:
        return raw_value if isinstance(raw_value, list) else []

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item) for item in raw_value]

    @staticmethod
    def _to_float(raw_value: object) -> float:
        if isinstance(raw_value, bool):
            return float(raw_value)
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            try:
                return float(raw_value)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _clamp_weight(value: float) -> float:
        return max(0.0, min(1.0, round(value, 4)))

    @staticmethod
    def _default_preference() -> dict[str, object]:
        return {
            "interests": [],
            "style": {
                "preferred_duration": "",
                "preferred_pace": "",
                "quality_sensitivity": 0.5,
                "humor_preference": 0.5,
                "depth_preference": 0.5,
            },
            "context": {
                "weekday_patterns": "",
                "weekend_patterns": "",
                "time_of_day_patterns": "",
                "session_type": "",
            },
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }
