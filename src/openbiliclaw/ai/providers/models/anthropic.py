"""Anthropic-native PydanticAI construction."""

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> AnthropicModel:
    # Anthropic's SDK appends /v1/messages; models.dev API values already end in /v1.
    base_url = config.endpoint.removesuffix("/v1") if config.endpoint else None
    provider = AnthropicProvider(base_url=base_url, api_key=api_key)
    return AnthropicModel(
        config.model_name, provider=provider, settings=config.options.to_settings()
    )
