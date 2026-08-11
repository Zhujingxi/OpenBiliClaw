"""Ollama construction through PydanticAI's native OpenAI-compatible provider."""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> OpenAIChatModel:
    endpoint = config.endpoint or "http://127.0.0.1:11434/v1"
    return OpenAIChatModel(
        config.model_name,
        provider=OpenAIProvider(base_url=endpoint, api_key=api_key or "ollama"),
        settings=config.options.to_settings(),
    )
