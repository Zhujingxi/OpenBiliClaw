"""Shared service facade for prompt assembly and LLM execution."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

from openbiliclaw.soul.profile import SoulProfile, preference_layer_from_dict
from openbiliclaw.soul.tone import ToneProfile, build_tone_profile

from .base import LLMProviderError, LLMRateLimitError
from .concurrency import (
    DEFAULT_TOTAL_LLM_CONCURRENCY,
    LLMConcurrencyGate,
    PrioritySemaphore,
    coerce_total_concurrency,
)
from .prompts import build_socratic_dialogue_prompt

DEFAULT_LLM_CONCURRENCY = DEFAULT_TOTAL_LLM_CONCURRENCY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openbiliclaw.memory.manager import MemoryManager

    from .base import LLMResponse


class SupportsComplete(Protocol):
    """Protocol for the one global ordered route (or its legacy shim)."""

    @property
    def default_provider(self) -> str: ...

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse: ...


class LLMServiceError(Exception):
    """Base exception for service-layer LLM errors."""


class LLMResponseContentError(LLMServiceError):
    """Raised when an LLM call returns empty content."""


class LLMProviderExecutionError(LLMServiceError):
    """Raised when the underlying provider or registry call fails."""


_RATE_LIMIT_ERROR_MARKERS = (
    "rate limit",
    "429",
    "402",
    "cooling down",
    "too many requests",
    "resource exhausted",
    "quota exceeded",
    "payment required",
    "insufficient balance",
    "billing",
    "out of credit",
    "credit exhausted",
    "余额不足",
    "账户余额",
)


def is_llm_rate_limit_error(exc: BaseException) -> bool:
    """Return True when an exception chain represents provider backoff.

    Batch callers use this to avoid exploding one provider-limit event
    into N doomed per-item calls while the registry is already cooling
    down.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, LLMRateLimitError):
            return True
        message = str(current).lower()
        if any(marker in message for marker in _RATE_LIMIT_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _coerce_concurrency(value: object) -> int:
    """Return a positive LLM concurrency value, falling back to the default."""
    return coerce_total_concurrency(value)


def _build_priority_semaphore(capacity: int = DEFAULT_LLM_CONCURRENCY) -> PrioritySemaphore:
    return PrioritySemaphore(capacity=_coerce_concurrency(capacity))


@dataclass
class LLMService:
    """Facade that assembles prompts and delegates calls to the registry."""

    # v0.3.63+: caller-tag → priority map. Lower number wins. Resolved
    # by longest-prefix match against the ``caller`` tag passed to
    # ``complete_with_core_memory``. Untagged or unmatched callers fall
    # through to ``_DEFAULT_PRIORITY``. The intent: when the system is
    # under load, popup-visible work (write_expression, evaluate_batch
    # for the active discovery batch) gets the next LLM slot before
    # cold-path soul/xhs analysis.
    _PRIORITY_MAP: ClassVar[dict[str, int]] = {
        "recommendation.write_expression": 1,
        "discovery.evaluate_batch": 1,
        "soul": 2,
        "xhs": 2,
    }
    _DEFAULT_PRIORITY: ClassVar[int] = 3
    registry: SupportsComplete
    memory: MemoryManager
    # v0.3.26+: optional usage ledger sink. When supplied, every
    # successful LLM response is written to the ``llm_usage`` table so
    # ``openbiliclaw cost`` can report daily spend. Default None
    # preserves prior behaviour for tests / standalone callers that
    # don't care about cost tracking.
    usage_recorder: object | None = None
    concurrency: int = DEFAULT_LLM_CONCURRENCY
    concurrency_gate: LLMConcurrencyGate | None = None

    def __post_init__(self) -> None:
        self.concurrency = _coerce_concurrency(self.concurrency)
        if self.concurrency_gate is None:
            self.concurrency_gate = LLMConcurrencyGate(self.concurrency)

    @asynccontextmanager
    async def _provider_slot(
        self, *, caller: str, bypass_background: bool = False
    ) -> AsyncIterator[None]:
        gate = cast("LLMConcurrencyGate", self.concurrency_gate)
        async with gate.slot(caller=caller, bypass_background=bypass_background):
            yield

    @classmethod
    def _resolve_priority(cls, caller: str) -> int:
        """Longest-prefix match of ``caller`` against ``_PRIORITY_MAP``.

        ``"recommendation.write_expression"`` matches exactly, while
        ``"soul.preference"`` matches the ``"soul"`` prefix. Unknown
        callers (or empty tag) fall through to ``_DEFAULT_PRIORITY``.
        """
        if not caller:
            return cls._DEFAULT_PRIORITY
        best: tuple[int, int] | None = None  # (prefix length, priority)
        for prefix, priority in cls._PRIORITY_MAP.items():
            if caller == prefix or caller.startswith(prefix + "."):
                length = len(prefix)
                if best is None or length > best[0]:
                    best = (length, priority)
        return best[1] if best is not None else cls._DEFAULT_PRIORITY

    @staticmethod
    def _structured_json_contract(system_instruction: str) -> str:
        """Ensure JSON-mode instructions carry a lowercase ``json`` token.

        Some OpenAI-compatible endpoints reject ``response_format=json_object``
        unless a message contains the literal lowercase token. Preserve an
        existing instruction's meaning by normalizing its uppercase ``JSON``
        spelling first; only append the minimal contract token when no such
        spelling exists.
        """

        instruction = system_instruction.strip()
        if "json" in instruction:
            return instruction
        normalized = instruction.replace("JSON", "json")
        if "json" in normalized:
            return normalized
        return f"{normalized}\n\njson" if normalized else "json"

    async def complete_with_core_memory(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        caller: str = "",
        reasoning_effort: str | None = None,
        bypass_semaphore: bool = False,
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        """Execute a task with automatically injected core memory context.

        ``caller`` is an optional free-form tag (e.g. ``"soul.preference"``,
        ``"discovery.eval"``) attached to the usage row so the ``cost``
        report can break spend down by module.

        ``reasoning_effort`` (v0.3.51+) lets a caller force-disable the
        provider's thinking mode for tasks that don't benefit from it
        (structured eval / classify / write-expression). ``None`` keeps
        the provider default; ``""`` explicitly disables for this call.

        ``bypass_semaphore`` (legacy name) skips only background admission;
        every provider call still respects the runtime total gate.

        ``inject_core_memory`` lets hot-path evaluators opt out when
        they already pass a task-specific structured profile in
        ``user_input``. This keeps provider-side prompt-cache prefixes
        stable without changing the information available to the task.
        """
        core_memory_block = ""
        if inject_core_memory and self.memory is not None:
            with suppress(Exception):
                core_memory_block = self.memory.render_core_memory_prompt()
        parts = [system_instruction.strip()]
        if core_memory_block:
            parts.append("以下是当前用户的 core memory，请作为理解背景：")
            parts.append(core_memory_block)
        system_content = "\n\n".join(parts)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        async def _do_llm_call() -> LLMResponse:
            return await self.registry.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=reasoning_effort,
            )

        try:
            async with self._provider_slot(caller=caller, bypass_background=bypass_semaphore):
                response = await _do_llm_call()
        except LLMProviderError as exc:
            raise LLMProviderExecutionError(str(exc)) from exc
        if not response.content.strip():
            raise LLMResponseContentError("LLM returned an empty response.")
        # Best-effort usage ledger write. The recorder swallows its own
        # exceptions so a billing-table hiccup never affects the LLM
        # response that just succeeded.
        recorder = self.usage_recorder
        if recorder is not None:
            record_fn = getattr(recorder, "record", None)
            if callable(record_fn):
                with suppress(Exception):
                    record_fn(response, caller=caller)
        return response

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        """Execute a JSON-mode task with core memory injection.

        ``reasoning_effort`` (v0.3.51+): pass ``""`` to disable the
        provider's thinking mode for this call. Recommended for
        structured tasks (eval / classify / write-expression) that
        don't benefit from chain-of-thought — disabling it on
        DeepSeek-V4 cuts a 30-item batch from ~10 min to ~30s.
        """
        return await self.complete_with_core_memory(
            system_instruction=self._structured_json_contract(system_instruction),
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            caller=caller,
            reasoning_effort=reasoning_effort,
            inject_core_memory=inject_core_memory,
        )

    def supports_image_input(self, caller: str = "discovery.evaluate_batch") -> bool:
        """Best-effort check for OpenAI-compatible vision-capable routes."""
        del caller
        provider_key = self.registry.default_provider.strip().lower()
        model = ""
        route_connections = getattr(self.registry, "connections", ())
        if route_connections:
            primary = route_connections[0]
            provider_key = str(getattr(primary, "type", "") or "").lower()
            model = str(getattr(primary, "model", "") or "")
        if provider_key not in {"openai", "openai_compatible", "openrouter"}:
            return False

        provider_obj: object | None = None
        get_provider = getattr(self.registry, "get", None)
        if not model and callable(get_provider):
            with suppress(Exception):
                provider_obj = get_provider(provider_key)
        if provider_obj is not None:
            model = str(getattr(provider_obj, "_model", "") or "")
        model_lower = model.lower()
        vision_markers = (
            "gpt-4o",
            "gpt-4.1",
            "gpt-5",
            "o3",
            "o4",
            "vision",
            "vl",
            "qwen-vl",
            "pixtral",
            "llava",
            "gemini",
            "claude-3",
            "claude-sonnet-4",
        )
        return any(marker in model_lower for marker in vision_markers)

    async def complete_multimodal_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        image_inputs: list[dict[str, str]],
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        """Execute a JSON-mode task with user text plus image inputs."""
        core_memory_block = ""
        if inject_core_memory and self.memory is not None:
            with suppress(Exception):
                core_memory_block = self.memory.render_core_memory_prompt()
        parts = [self._structured_json_contract(system_instruction)]
        if core_memory_block:
            parts.append("以下是当前用户的 core memory，请作为理解背景：")
            parts.append(core_memory_block)
        system_content = "\n\n".join(parts)

        user_parts: list[dict[str, Any]] = [{"type": "text", "text": user_input}]
        for image in image_inputs:
            content_id = str(image.get("content_id") or "").strip()
            data_url = str(image.get("data_url") or "").strip()
            if not content_id or not data_url:
                continue
            cover_ref = f"cover:{content_id}"
            user_parts.append(
                {
                    "type": "text",
                    "text": (
                        f"Cover image {cover_ref} maps to the content_batch item whose "
                        f"cover_image_ref is {cover_ref}."
                    ),
                }
            )
            user_parts.append({"type": "image_url", "image_url": {"url": data_url}})

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
        if history:
            messages.extend(cast("list[dict[str, Any]]", history))
        messages.append({"role": "user", "content": user_parts})

        async def _do_llm_call() -> LLMResponse:
            return await self.registry.complete(
                cast("Any", messages),
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
                reasoning_effort=reasoning_effort,
            )

        try:
            async with self._provider_slot(caller=caller):
                response = await _do_llm_call()
        except LLMProviderError as exc:
            raise LLMProviderExecutionError(str(exc)) from exc
        if not response.content.strip():
            raise LLMResponseContentError("LLM returned an empty response.")
        recorder = self.usage_recorder
        if recorder is not None:
            record_fn = getattr(recorder, "record", None)
            if callable(record_fn):
                with suppress(Exception):
                    record_fn(response, caller=caller)
        return response

    async def complete_with_tools(
        self,
        *,
        system_instruction: str,
        user_input: str,
        tools: list[dict[str, object]],
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        bypass_semaphore: bool = False,
    ) -> LLMResponse:
        """Execute a completion that may include tool/function calls.

        The LLM is given a set of tool definitions.  If it decides to call
        a tool, the response will have ``tool_calls`` populated.  Otherwise
        ``content`` will contain the text reply.

        This method uses JSON mode under the hood: the tools are serialised
        into the system prompt and the model is asked to return a JSON
        wrapper with either ``reply`` or ``tool_call`` keys.
        """
        tools_desc = "\n".join(f"- {t['name']}: {t.get('description', '')}" for t in tools)
        tool_names = [t["name"] for t in tools]
        augmented_system = (
            system_instruction + "\n\n"
            "<available_tools>\n" + tools_desc + "\n"
            "</available_tools>\n\n"
            "<tool_call_format>\n"
            "如果你需要调用工具，请返回如下 JSON（不要附带任何其他文字）：\n"
            '{"tool_call": {"name": "工具名", "arguments": {参数}}}\n'
            "如果不需要调用工具，正常回复用户即可（不要输出 JSON）。\n"
            "</tool_call_format>"
        )
        response = await self.complete_with_core_memory(
            system_instruction=augmented_system,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
            caller=caller,
            bypass_semaphore=bypass_semaphore,
        )

        # Try to parse tool calls from the response
        import json

        content = (response.content or "").strip()
        if content.startswith("{"):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "tool_call" in parsed:
                    call = parsed["tool_call"]
                    if isinstance(call, dict) and call.get("name") in tool_names:
                        response.tool_calls = [call]
                        response.content = ""
            except (json.JSONDecodeError, TypeError):
                pass  # Not valid JSON — treat as normal text reply

        return response

    async def complete_socratic_dialogue(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
        caller: str = "",
    ) -> LLMResponse:
        """Generate a Socratic dialogue reply using core memory context."""
        tone_profile = self._build_dialogue_tone_profile()
        preference_raw = self.memory.get_layer("preference").data
        source_mix = preference_layer_from_dict(preference_raw).source_platform_mix
        prompt_messages = build_socratic_dialogue_prompt(
            user_message=user_message,
            core_memory_text="",
            tone_profile=tone_profile,
            history=[],
            source_platform_mix=source_mix or None,
        )
        return await self.complete_with_core_memory(
            system_instruction=prompt_messages[0]["content"],
            user_input=user_message,
            history=history,
            caller=caller,
        )

    def _build_dialogue_tone_profile(self) -> ToneProfile:
        """Infer tone profile for dialogue from persisted memory."""
        soul_raw = self.memory.get_layer("soul").data
        preference_raw = self.memory.get_layer("preference").data
        profile = None
        if soul_raw:
            profile = SoulProfile.from_dict(soul_raw)
            profile.preferences = preference_layer_from_dict(preference_raw)
        return build_tone_profile(
            profile=profile,
            preference_summary=self.memory.get_core_memory().get("preference_summary", {}),
            recent_feedback=[],
        )
