"""OpenAI-native PydanticAI construction."""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> OpenAIChatModel:
    provider = OpenAIProvider(base_url=config.endpoint, api_key=api_key)
    return OpenAIChatModel(
        config.model_name, provider=provider, settings=config.options.to_settings()
    )
