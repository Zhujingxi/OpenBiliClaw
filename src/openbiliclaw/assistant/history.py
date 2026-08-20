"""Safe Assistant transcript reconstruction and full-window selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from .models import (
    AssistantClarification,
    AssistantMessage,
    AssistantOutput,
    AssistantRecommendationPresentation,
    ContextMeter,
    ConversationRole,
)

if TYPE_CHECKING:
    from .models import ConversationMessage

_OUTPUT_ADAPTER: TypeAdapter[AssistantOutput] = TypeAdapter(AssistantOutput)


@dataclass(frozen=True, slots=True)
class HistorySelection:
    """Safe complete turns selected for one model request."""

    messages: tuple[ModelMessage, ...]
    meter: ContextMeter


def estimate_tokens(text: str) -> int:
    """Conservatively approximate tokens from UTF-8 bytes."""

    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def render_assistant_output(output: AssistantOutput) -> str:
    """Project validated structured output to user-visible text without opaque IDs."""

    if isinstance(output, AssistantMessage):
        return output.text
    if isinstance(output, AssistantRecommendationPresentation):
        return output.intro
    if isinstance(output, AssistantClarification):
        return "\n".join((output.question, *output.choices))
    return f"{output.action.effect} (expires {output.action.expires_at.isoformat()})"


def _assistant_text(message: ConversationMessage) -> str:
    try:
        output = _OUTPUT_ADAPTER.validate_json(message.content)
    except ValidationError:
        # Older persisted Assistant messages may predate the structured output contract.
        visible = message.content
    else:
        visible = render_assistant_output(output)
    if message.tool_calls:
        summaries = "; ".join(
            f"{item.tool_name.replace('_', ' ')}: {item.outcome} — {item.safe_summary}"
            for item in message.tool_calls
        )
        visible = f"{visible}\nTool activity: {summaries}"
    return visible


def _complete_turns(
    messages: tuple[ConversationMessage, ...],
) -> tuple[tuple[ModelMessage, ModelMessage], ...]:
    turns: list[tuple[ModelMessage, ModelMessage]] = []
    pending_user: ConversationMessage | None = None
    for message in messages:
        if message.role is ConversationRole.USER:
            pending_user = message
        elif message.role is ConversationRole.ASSISTANT and pending_user is not None:
            turns.append(
                (
                    ModelRequest(parts=[UserPromptPart(content=pending_user.content)]),
                    ModelResponse(parts=[TextPart(content=_assistant_text(message))]),
                )
            )
            pending_user = None
    return tuple(turns)


def select_history(
    messages: tuple[ConversationMessage, ...],
    *,
    context_window_tokens: int,
    base_input_tokens: int,
    input_tokens_limit: int,
    output_reserve_ratio: float = 0.2,
) -> HistorySelection:
    """Keep newest complete turns within the available input window; never summarize."""

    if context_window_tokens < 1 or input_tokens_limit < 1 or base_input_tokens < 0:
        raise ValueError("context window, input limit, and base estimate must be valid")
    if not 0 < output_reserve_ratio < 1:
        raise ValueError("output reserve ratio must be between zero and one")
    input_capacity = min(
        input_tokens_limit, int(context_window_tokens * (1 - output_reserve_ratio))
    )
    turns = _complete_turns(messages)
    selected: list[tuple[ModelMessage, ModelMessage]] = []
    used = base_input_tokens
    for turn in reversed(turns):
        turn_tokens = 8 + (len(ModelMessagesTypeAdapter.dump_json(list(turn))) + 3) // 4
        if used + turn_tokens > input_capacity:
            break
        selected.append(turn)
        used += turn_tokens
    selected.reverse()
    return HistorySelection(
        messages=tuple(message for turn in selected for message in turn),
        meter=ContextMeter(
            estimated_input_tokens=used,
            context_window_tokens=context_window_tokens,
            approximate_usage_percent=min(100, round(used * 100 / context_window_tokens)),
            excluded_oldest_turns=len(turns) - len(selected),
        ),
    )
