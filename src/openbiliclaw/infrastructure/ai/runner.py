"""PydanticAI runner whose only production model endpoint is LiteLLM."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from openai import AsyncOpenAI
from pydantic import TypeAdapter
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from uuid import UUID

    import httpx
    from pydantic import BaseModel
    from pydantic_ai import AgentRunResult
    from pydantic_ai.models import Model

    from openbiliclaw.infrastructure.ai.spec import GenerativeAlias, InputT, OutputT, TaskSpec


class AIRunRecorder(Protocol):
    """Secret-safe persistence port for AI run lifecycle metadata."""

    def start(self, *, task_name: str, model_alias: str) -> UUID: ...

    def succeed(
        self,
        run_id: UUID,
        *,
        output_payload: dict[str, object],
        usage: dict[str, int],
    ) -> None: ...

    def fail(self, run_id: UUID, *, error_kind: str) -> None: ...


class ModelResolver(Protocol):
    """Resolve an application alias to one PydanticAI model."""

    def __call__(self, alias: GenerativeAlias) -> Model: ...


class LiteLLMModelResolver:
    """Resolve stable aliases against one OpenAI-compatible LiteLLM endpoint.

    The OpenAI transport retry count is explicitly zero. LiteLLM is the sole owner
    of network retry, routing, provider fallback, cooldown, rate limits, and cache.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_base = base_url.rstrip("/")
        endpoint = normalized_base if normalized_base.endswith("/v1") else f"{normalized_base}/v1"
        client = AsyncOpenAI(
            base_url=endpoint,
            api_key=api_key,
            max_retries=0,
            http_client=http_client,
        )
        provider = OpenAIProvider(openai_client=client)
        self._client = client
        aliases: tuple[GenerativeAlias, ...] = ("obc-interactive", "obc-analysis")
        self._models: dict[GenerativeAlias, Model] = {
            alias: OpenAIChatModel(alias, provider=provider) for alias in aliases
        }

    def __call__(self, alias: GenerativeAlias) -> Model:
        """Return the cached model for an already-validated stable alias."""

        return self._models[alias]

    async def aclose(self) -> None:
        """Close the owned OpenAI-compatible client during application shutdown."""

        await self._client.close()


class TaskRunner:
    """Validate, execute, and record typed semantic tasks without provider logic."""

    def __init__(self, *, model_resolver: ModelResolver, recorder: AIRunRecorder) -> None:
        self._model_resolver = model_resolver
        self._recorder = recorder

    async def run(
        self,
        spec: TaskSpec[InputT, OutputT],
        raw_input: InputT | dict[str, object],
    ) -> OutputT:
        """Run one typed task with bounded semantic retries and wall-clock time."""

        validated_input = TypeAdapter(spec.input_type).validate_python(raw_input)
        model = self._model_resolver(spec.model_alias)
        run_id = self._recorder.start(task_name=spec.name, model_alias=spec.model_alias)
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                result = await spec.agent.run(
                    validated_input.model_dump_json(),
                    model=model,
                    usage_limits=spec.usage_limits,
                    retries={"output": spec.semantic_retry_limit},
                )
            output = TypeAdapter(spec.output_type).validate_python(result.output)
            self._recorder.succeed(
                run_id,
                output_payload=_object_payload(output),
                usage=_safe_usage(result),
            )
            return output
        except asyncio.CancelledError:
            self._recorder.fail(run_id, error_kind="CancelledError")
            raise
        except Exception as exc:
            self._recorder.fail(run_id, error_kind=type(exc).__name__)
            raise


def _object_payload(output: BaseModel) -> dict[str, object]:
    payload = output.model_dump(mode="json")
    return {str(key): _redact_sensitive_value(str(key), value) for key, value in payload.items()}


def _redact_sensitive_value(key: str, value: object) -> object:
    normalized = key.lower().replace("-", "_")
    sensitive_markers = (
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    )
    if any(marker in normalized for marker in sensitive_markers):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact_sensitive_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_value("", item) for item in value]
    return value


def _safe_usage(result: AgentRunResult[object]) -> dict[str, int]:
    usage = result.usage
    return {
        "requests": usage.requests,
        "tool_calls": usage.tool_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
    }
