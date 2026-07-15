"""Tests for the shared LLM service facade."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

import openbiliclaw.llm.base as llm_base
from openbiliclaw.llm.base import (
    LLMFallbackError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    classify_llm_failure_kind,
    classify_llm_unavailability,
    describe_llm_failure,
)
from openbiliclaw.llm.service import (
    LLMProviderExecutionError,
    LLMResponseContentError,
    LLMService,
    PrioritySemaphore,
    is_llm_rate_limit_error,
)
from openbiliclaw.memory.manager import MemoryManager

if TYPE_CHECKING:
    from pathlib import Path


class FakeRegistry:
    """Minimal fake registry for service tests."""

    def __init__(
        self,
        response: LLMResponse | None = None,
        error: Exception | None = None,
        *,
        chat_capable: set[str] | None = None,
        default_provider: str = "openai",
        provider_error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.provider_error = provider_error
        self.chat_capable = {name.lower() for name in (chat_capable or {"openai"})}
        self.default_provider = default_provider
        self.calls: list[list[dict[str, object]]] = []
        self.provider_calls: list[dict[str, object]] = []
        self.json_modes: list[bool] = []

    async def complete(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.json_modes.append(json_mode)
        if self.error is not None:
            raise self.error
        return self.response or LLMResponse(content="", provider="openai")

    async def complete_provider(
        self,
        provider_name: str,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.provider_calls.append(
            {
                "provider_name": provider_name,
                "messages": messages,
                "json_mode": json_mode,
                "model": model,
                "reasoning_effort": reasoning_effort,
            }
        )
        if self.provider_error is not None:
            raise self.provider_error
        return self.response or LLMResponse(content="ok", provider=provider_name)

    def is_chat_capable(self, name: str) -> bool:
        return name.strip().lower() in self.chat_capable


class FakeMemoryManager:
    def __init__(self, core_prompt: str) -> None:
        self.core_prompt = core_prompt

    def render_core_memory_prompt(self) -> str:
        return self.core_prompt


def _safe_llm_failure_message(exc: BaseException) -> str:
    helper = getattr(llm_base, "safe_llm_failure_message", None)
    assert helper is not None, "safe_llm_failure_message() is not implemented"
    return str(helper(exc))


def test_is_llm_rate_limit_error_detects_wrapped_provider_backoff() -> None:
    try:
        try:
            raise LLMRateLimitError("429 Too Many Requests")
        except LLMRateLimitError as err:
            raise LLMProviderExecutionError("All providers failed") from err
    except LLMProviderExecutionError as wrapped:
        assert is_llm_rate_limit_error(wrapped)

    assert is_llm_rate_limit_error(
        LLMProviderExecutionError("Provider gemini is cooling down after 429")
    )
    assert is_llm_rate_limit_error(
        LLMProviderExecutionError("Provider deepseek failed: HTTP 402: Insufficient Balance")
    )
    assert not is_llm_rate_limit_error(ValueError("Expected scored JSON array"))


def test_classify_llm_unavailability_rate_limited_through_fallback_chain() -> None:
    with pytest.raises(LLMFallbackError) as exc_info:
        try:
            try:
                raise RuntimeError("openai RateLimitError: HTTP 429")
            except RuntimeError as base_err:
                raise LLMRateLimitError("rate limit; cooling down") from base_err
        except LLMRateLimitError as rl_err:
            raise LLMFallbackError(
                "All providers failed (deepseek, openai). Last error: rate limit"
            ) from rl_err
    assert classify_llm_unavailability(exc_info.value) == "rate_limited"


def test_classify_llm_unavailability_no_provider_message() -> None:
    assert (
        classify_llm_unavailability(
            LLMFallbackError("No provider was available to process the request.")
        )
        == "no_provider"
    )
    # Wrapped in the service-layer execution error the way production does.
    try:
        try:
            raise LLMFallbackError("No provider was available to process the request.")
        except LLMFallbackError as inner:
            raise LLMProviderExecutionError(str(inner)) from inner
    except LLMProviderExecutionError as wrapped:
        assert classify_llm_unavailability(wrapped) == "no_provider"


def test_classify_llm_unavailability_returns_none_for_unrelated_error() -> None:
    assert classify_llm_unavailability(ValueError("Expected scored JSON array")) is None


def test_classify_llm_unavailability_model_not_found_through_chain() -> None:
    # Real shape from the guided-init log: ollama 404 for a model never pulled,
    # wrapped up through the preference-analysis path. Loops must treat this as
    # an expected, calmly-logged deferral — not an "Unexpected error" traceback.
    try:
        try:
            raise LLMProviderError(
                "ollama request failed: HTTP 404: "
                '{"message": "model \'llama3\' not found, try pulling it first", '
                '"type": "not_found_error"}'
            )
        except LLMProviderError as inner:
            raise LLMProviderExecutionError(str(inner)) from inner
    except LLMProviderExecutionError as wrapped:
        assert classify_llm_unavailability(wrapped) == "model_not_found"


def test_classify_llm_unavailability_rate_limit_wins_over_no_provider() -> None:
    with pytest.raises(LLMRateLimitError) as exc_info:
        try:
            raise LLMFallbackError("No provider was available to process the request.")
        except LLMFallbackError as np_err:
            raise LLMRateLimitError("rate limit hit") from np_err
    assert classify_llm_unavailability(exc_info.value) == "rate_limited"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMProviderError("HTTP 401 unauthorized: invalid api key"), "auth_failed"),
        (
            LLMProviderError(
                "ollama request failed: HTTP 404: "
                '{"code": null, "message": "model \'llama3\' not found, try pulling '
                'it first", "param": null, "type": "not_found_error"}'
            ),
            "model_not_found",
        ),
        (
            LLMProviderError("The model `gpt-x` does not exist or you do not have access to it."),
            "model_not_found",
        ),
        (LLMProviderError("connection-a request failed: HTTP 404"), "model_not_found"),
        (LLMProviderError("connection-a request failed: HTTP 403"), "auth_failed"),
        (ConnectionError("connection reset by peer"), "connection"),
        (OSError("network is unreachable"), "connection"),
        (
            LLMProviderError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
            "connection",
        ),
        (LLMProviderError("openai_compatible request failed: Connection error."), "connection"),
        (LLMTimeoutError("request timed out"), "timeout"),
        (LLMProviderExecutionError("upstream returned HTTP 503"), "server_error"),
        (LLMResponseError("empty completion"), "invalid_response"),
        (ValueError("unrelated local failure"), None),
    ],
)
def test_classify_llm_failure_kind(error: BaseException, expected: str | None) -> None:
    assert classify_llm_failure_kind(error) == expected


def test_classify_llm_failure_kind_walks_wrapped_chain() -> None:
    try:
        try:
            raise LLMTimeoutError("provider timeout")
        except LLMTimeoutError as inner:
            raise LLMFallbackError("all providers failed") from inner
    except LLMFallbackError as wrapped:
        assert classify_llm_failure_kind(wrapped) == "timeout"


def test_classify_llm_failure_kind_preserves_auth_precedence_over_wrapper_text() -> None:
    try:
        raise LLMProviderError("HTTP 401 unauthorized: invalid api key")
    except LLMProviderError as inner:
        wrapped = LLMProviderExecutionError("connection reset while reporting failure")
        wrapped.__cause__ = inner
    assert classify_llm_failure_kind(wrapped) == "auth_failed"


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing local file"),
        PermissionError("local disk denied"),
        OSError("disk full"),
    ],
)
def test_classify_llm_failure_kind_does_not_treat_local_os_errors_as_connection(
    error: OSError,
) -> None:
    assert classify_llm_failure_kind(error) is None


def test_describe_llm_failure_content_moderation_500() -> None:
    # A Chinese compat gateway returns a compliance refusal *as a 500*; the
    # cause chain carries the 法律法规 text. Guided init must show "switch model"
    # advice, not the raw traceback fragment.
    try:
        try:
            raise RuntimeError(
                "Error code: 500 - 非常抱歉，根据相关法律法规，"
                "我们无法提供关于以下内容的答案 (code 10013)"
            )
        except RuntimeError as upstream:
            raise LLMProviderError("openai_compatible request failed") from upstream
    except LLMProviderError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "内容合规" in reason


def test_describe_llm_failure_model_not_found() -> None:
    reason = describe_llm_failure(
        LLMProviderError(
            "ollama request failed: HTTP 404: "
            '{"message": "model \'llama3\' not found, try pulling it first", '
            '"type": "not_found_error"}'
        )
    )
    assert reason is not None
    assert "404" in reason
    assert "ollama pull" in reason.lower() or "拉取" in reason


def test_describe_llm_failure_no_provider() -> None:
    reason = describe_llm_failure(
        LLMFallbackError("No provider was available to process the request.")
    )
    assert reason is not None
    assert "没有可用的 AI 服务" in reason


def test_describe_llm_failure_rate_limit_wins_over_no_provider() -> None:
    try:
        try:
            raise LLMFallbackError("No provider was available to process the request.")
        except LLMFallbackError as np_err:
            raise LLMRateLimitError("rate limit hit") from np_err
    except LLMRateLimitError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "限流" in reason


def test_describe_llm_failure_authentication_chain() -> None:
    try:
        try:
            raise RuntimeError("HTTP 401 unauthorized: invalid api key")
        except RuntimeError as upstream:
            raise LLMProviderError("authentication failed") from upstream
    except LLMProviderError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "鉴权失败" in reason
    assert "API key" in reason


def test_describe_llm_failure_insufficient_quota() -> None:
    reason = describe_llm_failure(LLMProviderError("upstream error: insufficient_quota"))
    assert reason is not None
    assert "额度用尽或被限流" in reason


def test_describe_llm_failure_http_429() -> None:
    reason = describe_llm_failure(LLMProviderError("upstream returned HTTP 429"))
    assert reason is not None
    assert "额度用尽或被限流" in reason


def test_describe_llm_failure_timeout_and_empty_response() -> None:
    assert "超时" in (describe_llm_failure(LLMTimeoutError("request timed out")) or "")
    assert "超时" in (describe_llm_failure(TimeoutError("socket stalled")) or "")
    assert "空响应" in (describe_llm_failure(LLMResponseError("empty completion")) or "")
    assert "空响应" in (
        describe_llm_failure(LLMResponseContentError("LLM returned an empty response")) or ""
    )


def test_describe_llm_failure_ssl_certificate_chain() -> None:
    # Issue #113: a local proxy / antivirus MITMs HTTPS, so the openai_compatible
    # provider's request dies with an SSL cert-verify failure. httpx raises
    # ConnectError, the SDK wraps it as a connection error, and analyze_events
    # surfaces it as "All providers failed". Guided init must show the proxy /
    # cert hint, not a generic "稍后重试".
    try:
        try:
            raise RuntimeError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                "unable to get local issuer certificate (_ssl.c:1010)"
            )
        except RuntimeError as ssl_err:
            raise LLMProviderError(
                "openai_compatible request failed: Connection error."
            ) from ssl_err
    except LLMProviderError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "SSL" in reason
    assert "代理" in reason


def test_describe_llm_failure_connection_error() -> None:
    # OpenAI SDK's APIConnectionError stringifies as "Connection error." with no
    # SSL / errno detail; still must map to the network-failure copy.
    reason = describe_llm_failure(
        LLMProviderError("openai_compatible request failed: Connection error.")
    )
    assert reason is not None
    assert "无法连接" in reason


def test_describe_llm_failure_ssl_wins_over_no_provider() -> None:
    # SSL cause is more actionable than the coarse "all providers failed" wrapper.
    try:
        try:
            raise RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        except RuntimeError as ssl_err:
            raise LLMFallbackError("No provider was available to process the request.") from ssl_err
    except LLMFallbackError as exc:
        reason = describe_llm_failure(exc)
    assert reason is not None
    assert "SSL" in reason


@pytest.mark.parametrize(
    ("exc", "expected_fragment"),
    [
        (LLMProviderError("上游内容审查拒绝了请求"), "内容合规"),
        (LLMProviderError("HTTP 401 unauthorized: invalid api key"), "鉴权失败"),
        (LLMRateLimitError("HTTP 429 insufficient_quota"), "额度用尽或被限流"),
        (LLMTimeoutError("provider request timed out"), "响应超时"),
        (TimeoutError("socket stalled"), "响应超时"),
        (LLMFallbackError("No provider was available"), "没有可用的 AI 服务"),
        (LLMResponseError("empty completion"), "空响应"),
        (LLMResponseContentError("LLM returned an empty response"), "空响应"),
        (
            RuntimeError("secret-upstream-detail"),
            "AI 服务暂时不可用；请稍后重试，或检查设置中的模型与网络。",
        ),
    ],
)
def test_safe_llm_failure_message_classifies_without_leaking_unknown_detail(
    exc: BaseException,
    expected_fragment: str,
) -> None:
    message = _safe_llm_failure_message(exc)

    assert expected_fragment in message
    assert "secret-upstream-detail" not in message


def test_safe_llm_failure_message_never_returns_raw_unknown_detail() -> None:
    message = _safe_llm_failure_message(RuntimeError("secret-upstream-detail"))

    assert message == "AI 服务暂时不可用；请稍后重试，或检查设置中的模型与网络。"
    assert "secret-upstream-detail" not in message


def test_describe_llm_failure_returns_none_for_unrelated_error() -> None:
    # A non-LLM failure (e.g. bad history data) must not be mislabeled as an
    # LLM outage — callers fall back to their own generic message.
    assert describe_llm_failure(ValueError("history parse failed")) is None


def test_classify_llm_unavailability_is_cycle_safe() -> None:
    first = ValueError("boom a")
    second = ValueError("boom b")
    first.__cause__ = second
    second.__cause__ = first  # deliberate cycle
    assert classify_llm_unavailability(first) is None


@pytest.mark.asyncio
async def test_llm_service_calls_registry_with_memory_context(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.get_layer("soul").update("personality_portrait", "喜欢深度叙事和结构化表达")
    registry = FakeRegistry(LLMResponse(content="当然，我们继续聊。", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    response = await service.complete_socratic_dialogue(
        user_message="我最近特别喜欢看长视频。",
        history=[{"role": "user", "content": "我喜欢能讲透的内容"}],
    )

    assert response.content == "当然，我们继续聊。"
    assert len(registry.calls) == 1
    assert registry.calls[0][0]["role"] == "system"
    assert "结构化表达" in registry.calls[0][0]["content"]
    assert "老B友" in registry.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_llm_service_injects_empty_memory_placeholder(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="我们可以慢慢聊。", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    await service.complete_socratic_dialogue(
        user_message="我最近想看点新东西。",
        history=[],
    )

    assert "尚未建立完整画像" in registry.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_llm_service_raises_on_empty_response_content(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    with pytest.raises(LLMResponseContentError):
        await service.complete_socratic_dialogue(
            user_message="我想聊聊为什么我总在熬夜看视频。",
            history=[],
        )


@pytest.mark.asyncio
async def test_llm_service_wraps_provider_failures(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(error=LLMProviderError("provider down"))
    service = LLMService(registry=registry, memory=memory)

    with pytest.raises(LLMProviderExecutionError):
        await service.complete_socratic_dialogue(
            user_message="我最近总在重复看同一类视频。",
            history=[],
        )


@pytest.mark.asyncio
async def test_complete_with_core_memory_injects_core_memory() -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="你是内容评估助手。",
        user_input="请评估这个视频。",
    )

    assert "## 用户画像" in registry.calls[0][0]["content"]
    assert "你是内容评估助手。" in registry.calls[0][0]["content"]
    assert registry.calls[0][1]["content"] == "请评估这个视频。"


@pytest.mark.asyncio
async def test_complete_with_core_memory_can_skip_core_memory_for_cacheable_eval() -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="你是内容评估助手。",
        user_input="请评估这个视频。",
        caller="discovery.evaluate_batch",
        inject_core_memory=False,
    )

    system_content = str(registry.calls[0][0]["content"])
    assert "你是内容评估助手。" in system_content
    assert "## 用户画像" not in system_content
    assert registry.calls[0][1]["content"] == "请评估这个视频。"


@pytest.mark.asyncio
async def test_complete_with_core_memory_does_not_normalize_nonstructured_json_text() -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="输出 JSON。",
        user_input="普通对话请求。",
    )

    assert registry.json_modes == [False]
    assert registry.calls[0][0]["content"] == (
        "输出 JSON。\n\n以下是当前用户的 core memory，请作为理解背景：\n\n## 用户画像\nportrait"
    )
    assert registry.calls[0][1]["content"] == "普通对话请求。"


@pytest.mark.asyncio
async def test_complete_structured_task_enables_json_mode() -> None:
    registry = FakeRegistry(LLMResponse(content='{"ok": true}', provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_structured_task(
        system_instruction="输出 JSON。",
        user_input="请返回结构化结果。",
    )

    assert registry.calls
    assert registry.json_modes == [True]
    assert registry.calls[0][0]["content"] == (
        "输出 json。\n\n以下是当前用户的 core memory，请作为理解背景：\n\n## 用户画像\nportrait"
    )


@pytest.mark.asyncio
async def test_structured_task_adds_json_contract_when_absent() -> None:
    registry = FakeRegistry(LLMResponse(content='{"ok": true}', provider="openai"))
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),
    )  # type: ignore[arg-type]

    await service.complete_structured_task(
        system_instruction="请返回结构化结果。",
        user_input="请求。",
    )

    assert registry.calls[0][0]["content"] == "请返回结构化结果。\n\njson"


@pytest.mark.asyncio
async def test_complete_multimodal_structured_task_sends_text_and_images() -> None:
    registry = FakeRegistry(LLMResponse(content='[{"score": 0.8}]', provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_multimodal_structured_task(
        system_instruction="输出 JSON。",
        user_input="请评估候选。",
        image_inputs=[
            {
                "content_id": "yt-demo",
                "data_url": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                "mime_type": "image/jpeg",
            }
        ],
        caller="discovery.evaluate_batch",
    )

    assert registry.json_modes == [True]
    assert registry.calls[0][0]["content"] == (
        "输出 json。\n\n以下是当前用户的 core memory，请作为理解背景：\n\n## 用户画像\nportrait"
    )
    user_message = registry.calls[0][1]
    assert user_message["role"] == "user"
    assert isinstance(user_message["content"], list)
    parts = user_message["content"]
    assert parts[0] == {"type": "text", "text": "请评估候选。"}
    assert parts[1] == {
        "type": "text",
        "text": (
            "Cover image cover:yt-demo maps to the content_batch item whose "
            "cover_image_ref is cover:yt-demo."
        ),
    }
    assert parts[2] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="},
    }


def test_resolve_priority_longest_prefix_wins() -> None:
    """write_expression beats the catch-all default; soul-level prefix matches."""
    assert LLMService._resolve_priority("recommendation.write_expression") == 1
    assert LLMService._resolve_priority("discovery.evaluate_batch") == 1
    assert (
        LLMService._resolve_priority("recommendation.background_score")
        == LLMService._DEFAULT_PRIORITY
    )
    assert LLMService._resolve_priority("soul.preference") == 2
    assert LLMService._resolve_priority("xhs.classify") == 2
    assert LLMService._resolve_priority("unrelated.tag") == LLMService._DEFAULT_PRIORITY
    assert LLMService._resolve_priority("") == LLMService._DEFAULT_PRIORITY


@pytest.mark.asyncio
async def test_all_former_module_callers_use_the_same_global_route() -> None:
    registry = FakeRegistry(
        LLMResponse(content="ok", provider="openai"),
        chat_capable={"openai", "claude", "deepseek"},
    )
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(
        registry=registry,
        memory=memory,  # type: ignore[arg-type]
    )

    callers = (
        "soul.profile_builder",
        "discovery.search.query",
        "recommendation.write_expression",
        "recommendation.evaluate_batch",
        "sources.xhs.classify",
        "eval.batch",
    )
    for caller in callers:
        await service.complete_with_core_memory(
            system_instruction="A",
            user_input=caller,
            caller=caller,
        )

    assert registry.provider_calls == []
    assert len(registry.calls) == len(callers)
    assert [call[-1]["content"] for call in registry.calls] == list(callers)


@pytest.mark.asyncio
async def test_normal_structured_multimodal_and_tool_paths_share_global_complete() -> None:
    registry = FakeRegistry(
        LLMResponse(content='{"reply": "ok"}', provider="openai"),
        chat_capable={"openai", "claude"},
    )
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),  # type: ignore[arg-type]
    )

    await service.complete_with_core_memory(
        system_instruction="A",
        user_input="normal",
        caller="soul.preference",
    )
    await service.complete_structured_task(
        system_instruction="Return JSON.",
        user_input="structured",
        caller="discovery.evaluate_batch",
    )
    await service.complete_multimodal_structured_task(
        system_instruction="Return JSON.",
        user_input="multimodal",
        image_inputs=[],
        caller="recommendation.evaluate_batch",
    )
    await service.complete_with_tools(
        system_instruction="A",
        user_input="tools",
        tools=[],
        caller="recommendation.write_expression",
    )

    assert len(registry.calls) == 4
    assert registry.provider_calls == []


def test_supports_image_input_uses_global_primary_connection_not_caller_override() -> None:
    registry = FakeRegistry(default_provider="custom-id")
    registry.connections = (
        SimpleNamespace(
            id="custom-id",
            type="openai_compatible",
            preset="custom",
            model="gpt-4o-mini",
        ),
    )
    service = LLMService(
        registry=registry,
        memory=FakeMemoryManager(core_prompt=""),  # type: ignore[arg-type]
    )

    assert service.supports_image_input("soul.profile_builder")
    assert service.supports_image_input("discovery.evaluate_batch")


@pytest.mark.asyncio
async def test_priority_semaphore_orders_waiters_by_priority() -> None:
    """When multiple coroutines queue while the slot is held, lower-number priorities run first."""
    sem = PrioritySemaphore(capacity=1)
    log: list[str] = []
    blocker_release = asyncio.Event()

    async def blocker() -> None:
        async with sem.slot(priority=1):
            log.append("blocker.start")
            await blocker_release.wait()
            log.append("blocker.end")

    async def worker(name: str, priority: int) -> None:
        async with sem.slot(priority=priority):
            log.append(name)

    blocker_task = asyncio.create_task(blocker())
    # Give the blocker time to acquire the slot before the contenders queue up.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    low = asyncio.create_task(worker("low", priority=3))
    medium = asyncio.create_task(worker("medium", priority=2))
    high = asyncio.create_task(worker("high", priority=1))
    # Let all three workers reach the queue.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    blocker_release.set()
    await asyncio.gather(blocker_task, low, medium, high)

    assert log[0] == "blocker.start"
    assert log[1] == "blocker.end"
    # Highest priority (lowest number) should be served first after the blocker frees the slot.
    assert log[2:] == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_complete_with_core_memory_defaults_to_three_concurrent_calls() -> None:
    """The shared LLM gate should allow three requests by default and queue the fourth."""
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    in_flight = 0
    peak = 0
    release = asyncio.Event()

    class TrackingRegistry:
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            json_mode: bool = False,
            reasoning_effort: str | None = None,
        ) -> LLMResponse:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await release.wait()
            finally:
                in_flight -= 1
            return LLMResponse(content="ok", provider="openai")

    service = LLMService(registry=TrackingRegistry(), memory=memory)  # type: ignore[arg-type]

    tasks = [
        asyncio.create_task(
            service.complete_with_core_memory(
                system_instruction=str(index),
                user_input=str(index),
                caller="recommendation.write_expression",
            )
        )
        for index in range(4)
    ]

    try:
        for _ in range(5):
            await asyncio.sleep(0)
        observed = in_flight
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert observed == 3
    assert peak == 3
