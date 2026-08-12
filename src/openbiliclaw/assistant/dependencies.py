"""Secret-free, projection-only Assistant dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openbiliclaw.ai.runtime.history import audit_text, sanitize_untrusted_text
from openbiliclaw.understanding.projections import DialogueProfile

if TYPE_CHECKING:
    from .models import ConversationScope
    from .tools import ApplicationFacade


@dataclass(frozen=True, slots=True)
class ConversationMetadata:
    conversation_id: str
    scope: ConversationScope

    def __post_init__(self) -> None:
        if not self.conversation_id.startswith("conv_"):
            raise ValueError("invalid conversation identity")


@dataclass(frozen=True, slots=True)
class AssistantDependencies:
    """The agent receives workflows and a bounded DialogueProfile only."""

    application: ApplicationFacade
    profile: DialogueProfile
    locale: str
    conversation: ConversationMetadata

    def __post_init__(self) -> None:
        if not self.locale or len(self.locale) > 32:
            raise ValueError("invalid locale")
        audit_text(self.locale)
        # Profile labels are untrusted model-visible data: audit for secrets and
        # scrub instruction-override phrases before the model can see them.
        sanitized = DialogueProfile(
            version=self.profile.version,
            preference_summary=tuple(
                sanitize_untrusted_text(v) for v in self.profile.preference_summary
            ),
            insights=tuple(sanitize_untrusted_text(v) for v in self.profile.insights),
        )
        for value in (*sanitized.preference_summary, *sanitized.insights):
            audit_text(value)
        object.__setattr__(self, "profile", sanitized)

    def __repr__(self) -> str:
        return (
            f"AssistantDependencies(application={type(self.application).__name__}, "
            f"profile=DialogueProfile(version={self.profile.version}), "
            f"locale={self.locale!r}, conversation={self.conversation!r})"
        )
