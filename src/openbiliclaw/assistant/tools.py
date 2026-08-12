"""Scoped native Assistant tools over the safe Application facade."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Tool

from openbiliclaw.ai.runtime.history import audit_text, sanitize_untrusted_text
from openbiliclaw.core._pydantic import StrictBaseModel


class ApplicationFacade(Protocol):
    async def get_recommendations(self, limit: int) -> object: ...
    async def search_content(self, provider_id: str, text: str, limit: int) -> object: ...
    async def get_content_details(self, reference: str) -> object: ...
    async def record_feedback(self, reference: str, kind: str) -> object: ...
    async def show_profile(self) -> object: ...
    async def edit_profile(self, claim_id: str, operation: str, value: str | None) -> object: ...
    async def list_sources(self) -> object: ...
    async def connect_source(self, provider_id: str) -> object: ...


class AssistantIntent(StrEnum):
    CHAT = "chat"
    RECOMMEND = "recommend"
    SEARCH = "search"
    PROFILE = "profile"
    SOURCES = "sources"
    FEEDBACK = "feedback"


class ToolResultBudget(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_chars: int = Field(default=4000, ge=100, le=16_000)
    max_items: int = Field(default=5, ge=1, le=20)


class ToolAvailability(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connected_providers: frozenset[str] = frozenset()
    enabled_skills: frozenset[str] = frozenset()


def bound_tool_result(value: object, budget: ToolResultBudget) -> str:
    if isinstance(value, BaseModel):
        text = value.model_dump_json()
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = sanitize_untrusted_text(text)
    text = text[: budget.max_chars]
    audit_text(text)
    return text


def build_workflow_tools(
    facade: ApplicationFacade, budget: ToolResultBudget
) -> tuple[Tool[None], ...]:
    async def get_recommendations(limit: int = 5) -> str:
        """Get bounded recommendation previews and opaque IDs."""
        return bound_tool_result(
            await facade.get_recommendations(max(1, min(limit, budget.max_items))), budget
        )

    async def search_content(provider_id: str, text: str, limit: int = 5) -> str:
        """Search one connected provider and return bounded previews."""
        return bound_tool_result(
            await facade.search_content(provider_id, text, min(limit, budget.max_items)), budget
        )

    async def get_content_details(reference: str) -> str:
        """Fetch details on demand for one opaque content reference."""
        return bound_tool_result(await facade.get_content_details(reference), budget)

    async def record_feedback(reference: str, kind: str) -> str:
        """Prepare validated explicit recommendation feedback."""
        return bound_tool_result(await facade.record_feedback(reference, kind), budget)

    async def show_profile() -> str:
        """Show only the bounded dialogue profile projection."""
        return bound_tool_result(await facade.show_profile(), budget)

    async def edit_profile(claim_id: str, operation: str, value: str | None = None) -> str:
        """Prepare a typed profile edit requiring application validation."""
        return bound_tool_result(await facade.edit_profile(claim_id, operation, value), budget)

    async def list_sources() -> str:
        """List bounded safe source status records."""
        return bound_tool_result(await facade.list_sources(), budget)

    async def connect_source(provider_id: str) -> str:
        """Prepare source connection; credentials are never model-visible."""
        return bound_tool_result(await facade.connect_source(provider_id), budget)

    return tuple(
        Tool(function, name=function.__name__)
        for function in (
            get_recommendations,
            search_content,
            get_content_details,
            record_feedback,
            show_profile,
            edit_profile,
            list_sources,
            connect_source,
        )
    )


_INTENT_TOOLS: dict[AssistantIntent, frozenset[str]] = {
    AssistantIntent.CHAT: frozenset(),
    AssistantIntent.RECOMMEND: frozenset({"get_recommendations", "get_content_details"}),
    AssistantIntent.SEARCH: frozenset({"search_content", "get_content_details"}),
    AssistantIntent.PROFILE: frozenset({"show_profile", "edit_profile"}),
    AssistantIntent.SOURCES: frozenset({"list_sources", "connect_source"}),
    AssistantIntent.FEEDBACK: frozenset({"record_feedback"}),
}


def select_tools(
    workflow_tools: tuple[Tool[None], ...],
    *,
    intent: AssistantIntent,
    availability: ToolAvailability,
    provider_tools: tuple[Tool[None], ...] = (),
    skill_tools: tuple[Tool[None], ...] = (),
) -> tuple[Tool[None], ...]:
    """Expose the minimal relevant set; never expose every global tool."""
    names = _INTENT_TOOLS[intent]
    selected = [item for item in workflow_tools if item.name in names]
    if intent is AssistantIntent.SEARCH:
        selected.extend(
            item
            for item in provider_tools
            if any(
                item.name.startswith(f"{provider}_")
                for provider in availability.connected_providers
            )
        )
    selected.extend(item for item in skill_tools if item.name in availability.enabled_skills)
    duplicate_names = [item.name for item in selected]
    if len(duplicate_names) != len(set(duplicate_names)):
        raise ValueError("duplicate selected Assistant tool name")
    return tuple(selected)
