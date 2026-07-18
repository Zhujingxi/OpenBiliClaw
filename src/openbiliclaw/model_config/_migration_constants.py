"""Shared deterministic constants for legacy model migration."""

CHAT_PROVIDER_ORDER = (
    "openai",
    "claude",
    "gemini",
    "deepseek",
    "ollama",
    "openrouter",
    "openai_compatible",
)
EMBEDDING_PROVIDERS = frozenset(
    {"openai", "gemini", "ollama", "openai_compatible", "openrouter", "dashscope"}
)
MODULE_NAMES = ("soul", "discovery", "recommendation", "evaluation")
CHAT_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "claude": "claude-sonnet-4-20250514",
    "gemini": "gemini-2.5-flash",
    "deepseek": "deepseek-v4-flash",
    "ollama": "llama3",
    "openrouter": "openai/gpt-4o-mini",
    "openai_compatible": "gpt-4o-mini",
}
EMBEDDING_DEFAULT_MODELS = {
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-001",
    "ollama": "bge-m3",
    "openai_compatible": "text-embedding-3-small",
    "openrouter": "",
    "dashscope": "qwen3-vl-embedding",
}
PROVIDER_LABELS = {
    "openai": "OpenAI",
    "claude": "Anthropic",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "ollama": "Ollama",
    "openrouter": "OpenRouter",
    "openai_compatible": "OpenAI-compatible",
    "dashscope": "DashScope",
}
PROVIDER_FIELDS = {
    "openai": frozenset({"api_key", "model", "base_url", "auth_mode", "api_flavor"}),
    "claude": frozenset({"api_key", "model", "base_url"}),
    "gemini": frozenset({"api_key", "model", "base_url"}),
    "deepseek": frozenset({"api_key", "model", "base_url", "reasoning_effort"}),
    "ollama": frozenset({"api_key", "model", "base_url", "num_ctx"}),
    "openrouter": frozenset({"api_key", "model", "base_url", "http_referer", "x_title"}),
    "openai_compatible": frozenset({"api_key", "model", "base_url", "api_flavor"}),
}
EMBEDDING_FIELDS = frozenset(
    {
        "provider",
        "model",
        "api_key",
        "base_url",
        "output_dimensionality",
        "similarity_threshold",
        "fallback_enabled",
        "fallback_provider",
        "multimodal_enabled",
    }
)
KNOWN_LLM_FIELDS = frozenset(
    {
        "default_provider",
        "concurrency",
        "timeout",
        "fallback_provider",
        "embedding",
        *CHAT_PROVIDER_ORDER,
        *MODULE_NAMES,
    }
)
OFFICIAL_PATHS = frozenset({"", "/v1"})

__all__ = [
    "CHAT_DEFAULT_MODELS",
    "CHAT_PROVIDER_ORDER",
    "EMBEDDING_DEFAULT_MODELS",
    "EMBEDDING_FIELDS",
    "EMBEDDING_PROVIDERS",
    "KNOWN_LLM_FIELDS",
    "MODULE_NAMES",
    "OFFICIAL_PATHS",
    "PROVIDER_FIELDS",
    "PROVIDER_LABELS",
]
