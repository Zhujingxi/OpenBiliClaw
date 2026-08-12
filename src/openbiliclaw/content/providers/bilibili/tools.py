"""Native PydanticAI tools for implemented Bilibili capabilities only."""

from pydantic_ai import Tool

from openbiliclaw.access.models import AccessHandle
from openbiliclaw.content.integration.manifest import CapabilityKind
from openbiliclaw.content.integration.tools import ToolBudget, build_provider_tools

from .capabilities import BilibiliProvider
from .manifest import BILIBILI_MANIFEST


def build_bilibili_tools(
    provider: BilibiliProvider,
    access: AccessHandle,
    *,
    enabled: frozenset[CapabilityKind],
    budget: ToolBudget,
) -> tuple[Tool[None], ...]:
    """Expose integration-generated tools over the same direct capabilities."""

    # Tool's concrete generic type is intentionally hidden from provider callers;
    # PydanticAI accepts the tuple directly at the Agent boundary.
    return tuple(
        build_provider_tools(
            BILIBILI_MANIFEST,
            provider,
            access,
            enabled=enabled,
            budget=budget,
        )
    )
