"""User profile data models.

Defines the structured representation of user understanding at each layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class InterestTag:
    """A weighted interest tag with time decay."""

    name: str
    category: str  # Top-level category (e.g., "科技", "游戏")
    weight: float = 1.0  # 0.0 - 1.0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    source: str = ""  # How this tag was inferred


@dataclass
class StylePreference:
    """Content style preferences."""

    preferred_duration: str = ""  # "short" | "medium" | "long"
    preferred_pace: str = ""  # "fast" | "moderate" | "slow"
    quality_sensitivity: float = 0.5  # How much production quality matters
    humor_preference: float = 0.5  # Preference for humorous content
    depth_preference: float = 0.5  # Preference for in-depth analysis


@dataclass
class ContextMode:
    """Contextual usage patterns."""

    weekday_patterns: str = ""  # Description of weekday usage
    weekend_patterns: str = ""  # Description of weekend usage
    time_of_day_patterns: str = ""  # Morning vs night preferences
    session_type: str = ""  # "browsing" | "deep_dive" | "background"


@dataclass
class PreferenceLayer:
    """Preference Layer — structured preferences extracted from behavior."""

    interests: list[InterestTag] = field(default_factory=list)
    style: StylePreference = field(default_factory=StylePreference)
    context: ContextMode = field(default_factory=ContextMode)
    exploration_openness: float = 0.5  # How open to new domains (0-1)
    disliked_topics: list[str] = field(default_factory=list)
    favorite_up_users: list[str] = field(default_factory=list)


@dataclass
class AwarenessNote:
    """A single awareness observation."""

    date: str = ""
    observation: str = ""  # What was observed
    trend: str = ""  # What trend this suggests
    emotion_guess: str = ""  # Guessed emotional state


@dataclass
class InsightHypothesis:
    """An insight or hypothesis about the user."""

    hypothesis: str = ""  # The insight itself
    evidence: list[str] = field(default_factory=list)  # Supporting observations
    confidence: float = 0.5  # 0.0 - 1.0
    validated: bool = False  # Has this been confirmed?
    created_at: str = ""


@dataclass
class SoulProfile:
    """Soul Layer — the deepest understanding of who the user is.

    This is the natural language personality portrait that the agent
    maintains, written as if by a close friend who truly understands
    this person.
    """

    # Soul layer — the personality portrait
    personality_portrait: str = ""  # Long-form natural language description
    core_traits: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    life_stage: str = ""  # Current life stage/situation
    deep_needs: list[str] = field(default_factory=list)  # Unmet psychological needs

    # Embedded preference summary (for LLM context)
    preferences: PreferenceLayer = field(default_factory=PreferenceLayer)

    # Recent awareness notes
    recent_awareness: list[AwarenessNote] = field(default_factory=list)

    # Active insights / hypotheses
    active_insights: list[InsightHypothesis] = field(default_factory=list)

    # Metadata
    created_at: str = ""
    updated_at: str = ""
    version: int = 0

    def to_llm_context(self) -> str:
        """Generate a natural language summary for LLM context.

        Returns a rich description that can be injected into LLM prompts
        to give the agent full understanding of the user.
        """
        parts = []

        if self.personality_portrait:
            parts.append(f"## 用户画像\n{self.personality_portrait}")

        if self.core_traits:
            parts.append(f"## 核心特质\n{', '.join(self.core_traits)}")

        if self.deep_needs:
            parts.append(f"## 深层需求\n{', '.join(self.deep_needs)}")

        if self.active_insights:
            insights_text = "\n".join(
                f"- {i.hypothesis} (置信度: {i.confidence:.0%})"
                for i in self.active_insights
            )
            parts.append(f"## 当前洞察\n{insights_text}")

        if self.recent_awareness:
            notes = "\n".join(
                f"- [{n.date}] {n.observation}" for n in self.recent_awareness[:5]
            )
            parts.append(f"## 近期观察\n{notes}")

        return "\n\n".join(parts) if parts else "（尚未建立用户画像）"

    def to_dict(self) -> dict[str, object]:
        """Serialize the soul profile into JSON-friendly dictionaries."""
        return {
            "personality_portrait": self.personality_portrait,
            "core_traits": self.core_traits,
            "values": self.values,
            "life_stage": self.life_stage,
            "deep_needs": self.deep_needs,
            "preferences": preference_layer_to_dict(self.preferences),
            "recent_awareness": [awareness_note_to_dict(note) for note in self.recent_awareness],
            "active_insights": [insight_hypothesis_to_dict(item) for item in self.active_insights],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, raw_data: dict[str, object]) -> SoulProfile:
        """Build a SoulProfile from persisted JSON data."""
        return cls(
            personality_portrait=str(raw_data.get("personality_portrait", "")),
            core_traits=_as_str_list(raw_data.get("core_traits")),
            values=_as_str_list(raw_data.get("values")),
            life_stage=str(raw_data.get("life_stage", "")),
            deep_needs=_as_str_list(raw_data.get("deep_needs")),
            preferences=preference_layer_from_dict(raw_data.get("preferences")),
            recent_awareness=[
                awareness_note_from_dict(item)
                for item in _as_list(raw_data.get("recent_awareness"))
                if isinstance(item, dict)
            ],
            active_insights=[
                insight_hypothesis_from_dict(item)
                for item in _as_list(raw_data.get("active_insights"))
                if isinstance(item, dict)
            ],
            created_at=str(raw_data.get("created_at", "")),
            updated_at=str(raw_data.get("updated_at", "")),
            version=_as_int(raw_data.get("version", 0)),
        )


def preference_layer_to_dict(layer: PreferenceLayer) -> dict[str, object]:
    """Serialize a preference layer into JSON-friendly dictionaries."""
    return {
        "interests": [interest_tag_to_dict(item) for item in layer.interests],
        "style": style_preference_to_dict(layer.style),
        "context": context_mode_to_dict(layer.context),
        "exploration_openness": layer.exploration_openness,
        "disliked_topics": layer.disliked_topics,
        "favorite_up_users": layer.favorite_up_users,
    }


def preference_layer_from_dict(raw_value: object) -> PreferenceLayer:
    """Build a preference layer from persisted JSON data."""
    data = raw_value if isinstance(raw_value, dict) else {}
    return PreferenceLayer(
        interests=[
            interest_tag_from_dict(item)
            for item in _as_list(data.get("interests"))
            if isinstance(item, dict)
        ],
        style=style_preference_from_dict(data.get("style")),
        context=context_mode_from_dict(data.get("context")),
        exploration_openness=_as_float(data.get("exploration_openness", 0.5), 0.5),
        disliked_topics=_as_str_list(data.get("disliked_topics")),
        favorite_up_users=_as_str_list(data.get("favorite_up_users")),
    )


def interest_tag_to_dict(tag: InterestTag) -> dict[str, object]:
    """Serialize an interest tag."""
    return {
        "name": tag.name,
        "category": tag.category,
        "weight": tag.weight,
        "first_seen": tag.first_seen.isoformat() if tag.first_seen else "",
        "last_seen": tag.last_seen.isoformat() if tag.last_seen else "",
        "source": tag.source,
    }


def interest_tag_from_dict(raw_data: dict[str, object]) -> InterestTag:
    """Build an interest tag from persisted JSON data."""
    return InterestTag(
        name=str(raw_data.get("name", "")),
        category=str(raw_data.get("category", "")),
        weight=_as_float(raw_data.get("weight", 1.0), 1.0),
        source=str(raw_data.get("source", "")),
    )


def style_preference_to_dict(style: StylePreference) -> dict[str, object]:
    return {
        "preferred_duration": style.preferred_duration,
        "preferred_pace": style.preferred_pace,
        "quality_sensitivity": style.quality_sensitivity,
        "humor_preference": style.humor_preference,
        "depth_preference": style.depth_preference,
    }


def style_preference_from_dict(raw_value: object) -> StylePreference:
    data = raw_value if isinstance(raw_value, dict) else {}
    return StylePreference(
        preferred_duration=str(data.get("preferred_duration", "")),
        preferred_pace=str(data.get("preferred_pace", "")),
        quality_sensitivity=_as_float(data.get("quality_sensitivity", 0.5), 0.5),
        humor_preference=_as_float(data.get("humor_preference", 0.5), 0.5),
        depth_preference=_as_float(data.get("depth_preference", 0.5), 0.5),
    )


def context_mode_to_dict(context: ContextMode) -> dict[str, object]:
    return {
        "weekday_patterns": context.weekday_patterns,
        "weekend_patterns": context.weekend_patterns,
        "time_of_day_patterns": context.time_of_day_patterns,
        "session_type": context.session_type,
    }


def context_mode_from_dict(raw_value: object) -> ContextMode:
    data = raw_value if isinstance(raw_value, dict) else {}
    return ContextMode(
        weekday_patterns=str(data.get("weekday_patterns", "")),
        weekend_patterns=str(data.get("weekend_patterns", "")),
        time_of_day_patterns=str(data.get("time_of_day_patterns", "")),
        session_type=str(data.get("session_type", "")),
    )


def awareness_note_to_dict(note: AwarenessNote) -> dict[str, object]:
    return {
        "date": note.date,
        "observation": note.observation,
        "trend": note.trend,
        "emotion_guess": note.emotion_guess,
    }


def awareness_note_from_dict(raw_data: dict[str, object]) -> AwarenessNote:
    return AwarenessNote(
        date=str(raw_data.get("date", "")),
        observation=str(raw_data.get("observation", "")),
        trend=str(raw_data.get("trend", "")),
        emotion_guess=str(raw_data.get("emotion_guess", "")),
    )


def insight_hypothesis_to_dict(item: InsightHypothesis) -> dict[str, object]:
    return {
        "hypothesis": item.hypothesis,
        "evidence": item.evidence,
        "confidence": item.confidence,
        "validated": item.validated,
        "created_at": item.created_at,
    }


def insight_hypothesis_from_dict(raw_data: dict[str, object]) -> InsightHypothesis:
    return InsightHypothesis(
        hypothesis=str(raw_data.get("hypothesis", "")),
        evidence=_as_str_list(raw_data.get("evidence")),
        confidence=_as_float(raw_data.get("confidence", 0.5), 0.5),
        validated=bool(raw_data.get("validated", False)),
        created_at=str(raw_data.get("created_at", "")),
    )


def _as_list(raw_value: object) -> list[object]:
    return raw_value if isinstance(raw_value, list) else []


def _as_str_list(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    return [str(item) for item in raw_value]


def _as_float(raw_value: object, default: float) -> float:
    if isinstance(raw_value, bool):
        return float(raw_value)
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if isinstance(raw_value, str):
        try:
            return float(raw_value)
        except ValueError:
            return default
    return default


def _as_int(raw_value: object) -> int:
    if isinstance(raw_value, bool):
        return int(raw_value)
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, float):
        return int(raw_value)
    if isinstance(raw_value, str):
        try:
            return int(raw_value)
        except ValueError:
            return 0
    return 0
