"""Ollama LLM provider via OpenAI-compatible API."""

from __future__ import annotations

from .openai_provider import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    """Ollama provider using the local OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str = "ollama",
        model: str = "llama3",
        base_url: str = "http://localhost:11434/v1",
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="ollama",
        )
