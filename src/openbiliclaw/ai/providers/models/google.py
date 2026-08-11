"""Google-native PydanticAI construction."""

from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> GoogleModel:
    provider = GoogleProvider(base_url=config.endpoint, api_key=api_key)
    return GoogleModel(config.model_name, provider=provider, settings=config.options.to_settings())
