"""OpenRouter construction through PydanticAI's native provider."""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> OpenAIChatModel:
    if config.endpoint is not None:
        # pydantic-ai's OpenRouterProvider has no base_url parameter; silently
        # dropping a configured endpoint would give distinct instance IDs that
        # route identically. Fail loudly instead.
        raise ValueError("endpoint override is not supported for openrouter")
    provider = OpenRouterProvider(api_key=api_key)
    return OpenAIChatModel(
        config.model_name, provider=provider, settings=config.options.to_settings()
    )
