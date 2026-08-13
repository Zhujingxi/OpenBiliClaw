"""OpenAI-native PydanticAI construction."""

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> OpenAIChatModel:
    provider = OpenAIProvider(base_url=config.endpoint, api_key=api_key)
    settings = OpenAIChatModelSettings()
    if config.options.temperature is not None:
        settings["temperature"] = config.options.temperature
    if config.options.max_tokens is not None:
        settings["max_tokens"] = config.options.max_tokens
    if config.options.top_p is not None:
        settings["top_p"] = config.options.top_p
    if config.options.disable_thinking:
        settings["extra_body"] = {"thinking": {"type": "disabled"}}
    return OpenAIChatModel(config.model_name, provider=provider, settings=settings)
