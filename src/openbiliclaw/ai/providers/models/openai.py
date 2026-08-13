"""OpenAI-protocol construction with PydanticAI-native provider inference."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from pydantic_ai.providers import Provider

    from .config import ModelInstanceConfig


def _provider(config: ModelInstanceConfig, api_key: str) -> Provider[AsyncOpenAI]:
    """Use native OpenAI/DeepSeek registry classes; otherwise use generic OpenAI."""

    try:
        provider_class = infer_provider_class(config.provider)
    except ValueError:
        return OpenAIProvider(base_url=config.endpoint, api_key=api_key)
    if issubclass(provider_class, DeepSeekProvider):
        return (
            DeepSeekProvider(openai_client=AsyncOpenAI(base_url=config.endpoint, api_key=api_key))
            if config.endpoint is not None
            else DeepSeekProvider(api_key=api_key)
        )
    if issubclass(provider_class, OpenAIProvider):
        return provider_class(base_url=config.endpoint, api_key=api_key)
    return OpenAIProvider(base_url=config.endpoint, api_key=api_key)


def build(config: ModelInstanceConfig, api_key: str) -> OpenAIChatModel:
    settings = OpenAIChatModelSettings()
    if config.options.temperature is not None:
        settings["temperature"] = config.options.temperature
    if config.options.max_tokens is not None:
        settings["max_tokens"] = config.options.max_tokens
    if config.options.top_p is not None:
        settings["top_p"] = config.options.top_p
    if config.options.disable_thinking:
        settings["extra_body"] = {"thinking": {"type": "disabled"}}
    return OpenAIChatModel(
        config.model_name, provider=_provider(config, api_key), settings=settings
    )
