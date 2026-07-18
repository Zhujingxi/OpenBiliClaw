"""Typed AI infrastructure routed exclusively through the LiteLLM proxy."""

from openbiliclaw.infrastructure.ai.embedding import (
    EmbeddingBatch,
    EmbeddingNamespace,
    EmbeddingService,
    EmbeddingSettings,
)
from openbiliclaw.infrastructure.ai.health import AIHealthResult, AIHealthService, AliasHealth
from openbiliclaw.infrastructure.ai.runner import (
    AIRunRecorder,
    LiteLLMModelResolver,
    TaskRunner,
)
from openbiliclaw.infrastructure.ai.spec import CachePolicy, TaskLane, TaskSpec

__all__ = [
    "AIHealthResult",
    "AIHealthService",
    "AIRunRecorder",
    "AliasHealth",
    "CachePolicy",
    "EmbeddingBatch",
    "EmbeddingNamespace",
    "EmbeddingService",
    "EmbeddingSettings",
    "LiteLLMModelResolver",
    "TaskLane",
    "TaskRunner",
    "TaskSpec",
]
