"""OpenRouter construction through PydanticAI's native provider."""

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> OpenAIChatModel:
    provider = (
        OpenRouterProvider(openai_client=AsyncOpenAI(base_url=config.endpoint, api_key=api_key))
        if config.endpoint is not None
        else OpenRouterProvider(api_key=api_key)
    )
    return OpenAIChatModel(
        config.model_name, provider=provider, settings=config.options.to_settings()
    )
