"""OpenRouter construction through PydanticAI's native provider."""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from openbiliclaw.ai.providers.verification import UnsupportedCapabilityError

from .config import ModelInstanceConfig


def build(config: ModelInstanceConfig, api_key: str) -> OpenAIChatModel:
    if config.endpoint is not None:
        raise UnsupportedCapabilityError("endpoint override is not supported for openrouter")
    provider = OpenRouterProvider(api_key=api_key)
    return OpenAIChatModel(
        config.model_name, provider=provider, settings=config.options.to_settings()
    )
