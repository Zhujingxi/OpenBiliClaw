"""Anthropic-native PydanticAI construction."""

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> AnthropicModel:
    provider = AnthropicProvider(base_url=config.endpoint, api_key=api_key)
    return AnthropicModel(
        config.model_name, provider=provider, settings=config.options.to_settings()
    )
