"""DeepSeek-native PydanticAI construction."""

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> OpenAIChatModel:
    provider = (
        DeepSeekProvider(openai_client=AsyncOpenAI(base_url=config.endpoint, api_key=api_key))
        if config.endpoint is not None
        else DeepSeekProvider(api_key=api_key)
    )
    return OpenAIChatModel(
        config.model_name, provider=provider, settings=config.options.to_settings()
    )
