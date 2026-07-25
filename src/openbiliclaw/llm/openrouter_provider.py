"""OpenRouter provider built on the OpenAI-compatible client."""

from __future__ import annotations

from typing import Any

from .base import DEFAULT_REASONING_EFFORT
from .openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter provider with optional attribution headers."""

    # OpenRouter routes most chat models, but its embeddings coverage is
    # spotty per-route — better to fall back to ollama / gemini by default
    # than to surprise users with mid-pipeline 404s. Users who want
    # OpenRouter embedding can set ``[llm.embedding] provider="openrouter"``
    # with an explicit ``<vendor>/<model>`` (e.g.
    # ``google/gemini-embedding-2-preview``); that dedicated path lives in
    # ``registry._build_dedicated_embedding_provider`` and does not
    # consult this flag.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-4o-mini",
        base_url: str = "https://openrouter.ai/api/v1",
        http_referer: str = "",
        x_title: str = "",
        timeout: float = 300.0,
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="openrouter",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
            reasoning_effort=reasoning_effort,
        )
        self._http_referer = http_referer
        self._x_title = x_title

    def _extra_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._http_referer.strip():
            headers["HTTP-Referer"] = self._http_referer
        if self._x_title.strip():
            headers["X-Title"] = self._x_title
        return headers

    def _extra_body(self, *, reasoning_effort: str | None = None) -> dict[str, Any]:
        """Use OpenRouter's cross-provider reasoning normalization."""

        effort = self._reasoning_effort if reasoning_effort is None else reasoning_effort.strip()
        if not effort:
            # OpenRouter exposes whether reasoning is mandatory per model, but
            # that metadata is not available in this stateless adapter.  Omitting
            # the field is safer than sending ``none`` to a mandatory model.
            return {}
        normalized = effort.lower()
        if normalized not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            normalized = DEFAULT_REASONING_EFFORT
        return {"reasoning": {"effort": normalized}}
