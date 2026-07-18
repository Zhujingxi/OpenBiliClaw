"""LLM package — multi-model provider support."""

from .base import (
    LLMFallbackError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    classify_llm_failure_kind,
    classify_llm_unavailability,
)
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProtocolProvider, OpenAIProvider
from .registry import (
    RegistryBuildError,
    build_ordered_chat_route,
    build_ordered_embedding_service,
)
from .service import (
    LLMProviderExecutionError,
    LLMResponseContentError,
    LLMService,
    LLMServiceError,
    is_llm_rate_limit_error,
)

__all__ = [
    "ClaudeProvider",
    "GeminiProvider",
    "LLMFallbackError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenAIProtocolProvider",
    "RegistryBuildError",
    "LLMProviderExecutionError",
    "LLMService",
    "LLMServiceError",
    "LLMResponseContentError",
    "build_ordered_chat_route",
    "build_ordered_embedding_service",
    "classify_llm_unavailability",
    "classify_llm_failure_kind",
    "is_llm_rate_limit_error",
]
