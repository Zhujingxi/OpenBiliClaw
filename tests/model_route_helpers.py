"""Small native model-route fixtures shared by API tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
)

if TYPE_CHECKING:
    from openbiliclaw.config import Config


def use_native_ollama(config: Config, *, model: str = "llama3") -> None:
    """Give a mutable ``Config`` fixture one credential-free Chat route."""
    config.models = replace(
        config.models,
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="ollama-main",
                    name="Ollama",
                    type="ollama",
                    model=model,
                    base_url="http://127.0.0.1:11434/v1",
                ),
            ),
            concurrency=config.models.chat.concurrency,
            timeout_seconds=config.models.chat.timeout_seconds,
        ),
    )


def use_native_openai(
    config: Config,
    *,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> None:
    """Give a mutable ``Config`` fixture one inline-key OpenAI route."""
    config.models = replace(
        config.models,
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="openai-main",
                    name="OpenAI",
                    type="openai_compatible",
                    preset="openai",
                    model=model,
                    base_url="https://api.openai.com/v1",
                    credential=CredentialConfig(source="inline", value=api_key),
                    api_mode="chat_completions",
                ),
            ),
            concurrency=config.models.chat.concurrency,
            timeout_seconds=config.models.chat.timeout_seconds,
        ),
    )
