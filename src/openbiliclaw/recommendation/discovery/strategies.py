"""Configured discovery strategy kinds dispatched by the deterministic planner."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field

from openbiliclaw.content.integration.identity import ContentRef, ProviderId  # noqa: TC001
from openbiliclaw.core._pydantic import StrictBaseModel


class StrategyKind(StrEnum):
    SEARCH = "search"
    TRENDING = "trending"
    RELATED_CHAIN = "related_chain"
    EXPLORATION = "exploration"
    DIRECT_PROVIDER = "direct_provider"


class StrategyConfig(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    kind: StrategyKind
    provider_id: ProviderId
    quota: int = Field(ge=1, le=50)
    query: str | None = Field(default=None, min_length=1, max_length=120)
    seed_ref: ContentRef | None = None


def strategy_query(config: StrategyConfig, generated_query: str) -> str:
    """Resolve the provider-neutral query used by each proven strategy kind."""
    match config.kind:
        case StrategyKind.SEARCH | StrategyKind.EXPLORATION:
            return config.query or generated_query
        case StrategyKind.TRENDING:
            return config.query or "trending"
        case StrategyKind.RELATED_CHAIN:
            if config.seed_ref is None:
                raise ValueError("related-chain strategy requires a seed reference")
            return config.query or config.seed_ref.provider_content_id
        case StrategyKind.DIRECT_PROVIDER:
            if config.query is None:
                raise ValueError("direct-provider strategy requires a configured query")
            return config.query
