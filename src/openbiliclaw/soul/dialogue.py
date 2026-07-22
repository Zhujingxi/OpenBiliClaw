"""Socratic dialogue module.

Handles deep, probing conversations with the user to better understand them.
The dialogue style is inspired by the Socratic method:
- Ask "why" to uncover motivations
- Propose hypotheses and test them
- Confirm understanding before adjusting
- Adapt dynamically based on responses
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import tzinfo

    from openbiliclaw.llm.service import LLMService, ModuleOverride, SupportsComplete
    from openbiliclaw.soul.engine import SoulEngine

logger = logging.getLogger(__name__)

# Cap the dialogue history folded into each prompt. Calibration (2026-07-17,
# first-round — revisit after a provider swap): one exchange ≈ 2 short messages
# ≈ 80 tokens; 20 exchanges ≈ 1.6k tokens of history, which keeps the socratic
# prompt bounded without losing the near-term thread. Below the window the
# prompt bytes are unchanged (baseline test), so provider prompt cache still
# fires for short sessions.
DIALOGUE_WINDOW_TURNS = 20


def _default_turn_timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def format_dialogue_turn_timestamp(
    timestamp: str,
    *,
    local_timezone: tzinfo,
) -> str:
    """Render a recorded turn timestamp without consulting the current clock.

    SQLite ``CURRENT_TIMESTAMP`` values are unmarked UTC. In-memory turns are
    recorded with an explicit local offset. Both pass through this single,
    injectable conversion point before becoming stable prompt bytes.
    """
    normalized = timestamp.strip().replace("Z", "+00:00")
    if not normalized:
        return ""
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        logger.warning("Ignoring invalid dialogue turn timestamp %r", timestamp)
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone(local_timezone)
    return f"[{local:%m-%d %H:%M}]"


@dataclass
class DialogueTurn:
    """A single turn in a dialogue."""

    role: str  # "user" | "agent"
    content: str
    timestamp: str = field(default_factory=_default_turn_timestamp)
    extracted_insights: list[str] | None = None


class SocraticDialogue:
    """Manages Socratic-style dialogue with the user.

    The dialogue module doesn't just record what the user says — it actively
    probes deeper to understand motivations, validate hypotheses, and refine
    the agent's understanding of who the user really is.

    Dialogue strategies:
    1. 追问 Why — Don't stop at preferences, dig into motivations
    2. 提出假设 — Actively hypothesize based on current understanding
    3. 确认验证 — Use recommendations to test hypotheses
    4. 动态调整 — Refine the soul profile based on dialogue
    """

    def __init__(
        self,
        llm: SupportsComplete | None,
        soul_engine: SoulEngine,
        llm_service: LLMService | None = None,
        session: str = "cli",
        tools: list[dict[str, Any]] | None = None,
        tool_dispatcher: Any | None = None,
        module_overrides: Mapping[str, ModuleOverride] | None = None,
        learn_queue: Any | None = None,
        database: Any | None = None,
        local_timezone: tzinfo | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._llm = llm
        self._soul_engine = soul_engine
        self._llm_service = llm_service
        self._session = session
        self._history: list[DialogueTurn] = []
        default_timezone = datetime.now().astimezone().tzinfo
        self._local_timezone = local_timezone or default_timezone or UTC
        self._now_provider = now_provider or (lambda: datetime.now().astimezone())
        # Phase 1 durable-history regurgitation: after a restart the in-process
        # history is empty, but the durable popup ``chat_turns`` table holds the
        # completed exchanges. Lazily reload them once so a popup session keeps
        # its thread across restarts. Only popup + scope='chat' + completed rows
        # qualify (CLI has no DB; probe/confusion scopes carry prefixed context).
        self._database = database
        self._history_loaded = False
        self._respond_lock = asyncio.Lock()
        self._tools = tools or []
        self._tool_dispatcher = tool_dispatcher
        self._module_overrides = dict(module_overrides) if module_overrides is not None else None
        # Phase 1: when provided, background learning is serialized through this
        # single-worker queue instead of a per-turn ``asyncio.create_task``. CLI
        # / OpenClaw sessions inject no queue and keep the detached-task path
        # (process-internal, not durable — no popup regurgitation).
        self._learn_queue = learn_queue

    async def respond(
        self,
        user_message: str,
        *,
        scope: str = "chat",
        turn_id: str = "",
        session: str = "",
    ) -> str:
        """Generate a Socratic response to a user message.

        The response should:
        - Acknowledge what the user said
        - Probe deeper when appropriate ("为什么？")
        - Propose hypotheses ("我猜你可能...")
        - Confirm understanding ("所以你的意思是...")
        - Feel natural and warm, like a friend talking

        Args:
            user_message: The user's message.
            scope: Chat scope threaded to ``learn_from_dialogue`` — only
                unanchored ``"chat"`` runs inventory settles. Probe settlement
                stays in its durable side effect; confusion settlement belongs
                exclusively to the serialized dialogue-anchor processor.
            turn_id: Durable chat-turn id (idempotency observation key).
            session: UI ownership label for this request. The cognitive history
                remains shared across sessions.

        Returns:
            Agent's response.
        """
        async with self._respond_lock:
            self._ensure_history_loaded()
            history_length = len(self._history)
            turn_timestamp = self._local_now().isoformat()
            self._history.append(
                DialogueTurn(role="user", content=user_message, timestamp=turn_timestamp)
            )

            try:
                service = self._llm_service or self._build_service()
                prompt_user_message = self._user_prompt_with_current_time(user_message)

                # If tools are configured, try tool-calling path first
                if self._tools and self._tool_dispatcher:
                    reply = await self._respond_with_tools(service, prompt_user_message)
                else:
                    response = await service.complete_socratic_dialogue(
                        user_message=prompt_user_message,
                        history=self._history_to_messages(),
                        caller="soul.dialogue",
                    )
                    reply = response.content
            except BaseException:
                del self._history[history_length:]
                logger.exception("Failed to generate Socratic dialogue response.")
                raise

            self._history.append(
                DialogueTurn(
                    role="agent",
                    content=reply,
                    timestamp=self._local_now().isoformat(),
                )
            )
            learn_fn = getattr(self._soul_engine, "learn_from_dialogue", None)
            if callable(learn_fn):
                payload = {
                    "user_message": user_message,
                    "assistant_reply": reply,
                    "session": session.strip() or self._session,
                    "scope": scope,
                    "turn_id": turn_id,
                }
                if self._learn_queue is not None:
                    # Serialized path: append to the single-worker queue so
                    # adjacent turns can't interleave read/merge/write.
                    await self._learn_queue.submit(payload)
                else:

                    async def _background_learn() -> None:
                        try:
                            # This chain was initiated by an interactive user
                            # turn. It must keep running even when background
                            # admission is parked because canonical inventory is
                            # empty; otherwise the user's explicit correction
                            # cannot repair the very recommendation state that
                            # triggered it. The bypass only skips background
                            # admission — every provider call still respects the
                            # runtime-wide total concurrency gate.
                            from openbiliclaw.llm.service import _background_admission_bypass

                            with _background_admission_bypass():
                                await learn_fn(**payload)
                        except Exception:
                            logger.exception("Failed to learn from dialogue turn.")

                    asyncio.create_task(_background_learn())
            return reply

    async def _respond_with_tools(self, service: Any, user_message: str) -> str:
        """Attempt a tool-calling response, falling back to normal dialogue.

        The flow:
        1. Ask LLM with tool definitions — it may return a tool_call or text.
        2. If tool_call: execute via dispatcher, feed result back, get final reply.
        3. If text: return as-is.
        """
        from openbiliclaw.llm.prompts import build_socratic_dialogue_prompt

        core_memory = ""
        build_block = getattr(service, "_build_core_memory_block", None)
        if callable(build_block):
            core_memory = build_block()
        tone_profile = None
        build_tone = getattr(service, "_build_dialogue_tone_profile", None)
        if callable(build_tone):
            tone_profile = build_tone()
        prompt_messages = build_socratic_dialogue_prompt(
            user_message=user_message,
            history=self._history_to_messages(),
            core_memory_text=core_memory,
            tone_profile=tone_profile,
        )
        system = prompt_messages[0]["content"] if prompt_messages else ""

        response = await service.complete_with_tools(
            system_instruction=system,
            user_input=user_message,
            tools=self._tools,
            history=self._history_to_messages(),
            caller="soul.dialogue.tools",
            bypass_semaphore=True,
        )

        # If the LLM returned a tool call, execute and continue
        if response.tool_calls:
            tool_call = response.tool_calls[0]
            logger.info("Dialogue tool call: %s", tool_call.get("name"))
            if self._tool_dispatcher is None:
                return str(response.content)
            tool_result = self._tool_dispatcher.dispatch(tool_call)

            # Feed tool result back to get a natural reply
            followup = await service.complete_socratic_dialogue(
                user_message=f"[工具执行结果] {tool_result}",
                history=self._history_to_messages()
                + [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": f"（调用了工具 {tool_call.get('name')}）"},
                ],
                caller="soul.dialogue.tool_followup",
            )
            return str(followup.content)

        return str(response.content)

    async def extract_insights(self, turns: list[DialogueTurn]) -> list[dict[str, Any]]:
        """Extract insights about the user from dialogue turns.

        Args:
            turns: Recent dialogue turns to analyze.

        Returns:
            List of extracted insight dicts.
        """
        # TODO: Use LLM to identify preference signals, motivations,
        #       personality traits from the conversation
        return []

    @property
    def history(self) -> list[DialogueTurn]:
        """The dialogue history."""
        return self._history.copy()

    def clear_history(self) -> None:
        """Clear the dialogue history."""
        self._history.clear()

    def _ensure_history_loaded(self) -> None:
        """Regurgitate the one durable cognition history across UI sessions."""
        if self._history_loaded:
            return
        self._history_loaded = True
        if self._session == "cli" or self._database is None or self._history:
            return
        try:
            history_lister = getattr(self._database, "list_dialogue_history", None)
            if callable(history_lister):
                rows = history_lister(
                    scopes=("chat", "hypothesis", "confusion"),
                    limit=DIALOGUE_WINDOW_TURNS,
                )
            else:
                lister = getattr(self._database, "list_chat_turns", None)
                if not callable(lister):
                    return
                rows = lister(session="popup", scope="chat", limit=DIALOGUE_WINDOW_TURNS)
        except Exception:
            logger.debug("Failed to regurgitate durable chat history", exc_info=True)
            return
        for row in rows:
            if str(row.get("status", "")) != "completed":
                continue
            scope = str(row.get("scope", "chat")).strip()
            payload = row.get("payload", {})
            if scope == "hypothesis" and isinstance(payload, dict):
                title = str(payload.get("title", "") or row.get("subject_title", "")).strip()
                if title:
                    self._history.append(
                        DialogueTurn(
                            role="agent",
                            content=title,
                            timestamp=str(row.get("created_at", "") or ""),
                        )
                    )
                continue
            message = str(row.get("message", "")).strip()
            reply = str(row.get("reply", "")).strip()
            if not message or not reply:
                continue
            timestamp = str(row.get("created_at", "") or "")
            self._history.append(DialogueTurn(role="user", content=message, timestamp=timestamp))
            self._history.append(DialogueTurn(role="agent", content=reply, timestamp=timestamp))

    def _history_to_messages(self) -> list[dict[str, str]]:
        """Convert prior dialogue turns to chat messages for the LLM.

        Truncated to the last ``DIALOGUE_WINDOW_TURNS`` exchanges (each ≈ a
        user+agent pair) so the prompt stays bounded. Sessions at or below the
        window are unaffected — the returned bytes match the pre-window
        baseline, keeping provider prompt cache warm for short chats.
        """
        prior = self._history[:-1]
        window_messages = DIALOGUE_WINDOW_TURNS * 2
        if len(prior) > window_messages:
            prior = prior[-window_messages:]
        messages: list[dict[str, str]] = []
        for turn in prior:
            prefix = format_dialogue_turn_timestamp(
                turn.timestamp,
                local_timezone=self._local_timezone,
            )
            content = f"{prefix} {turn.content}" if prefix else turn.content
            messages.append(
                {
                    "role": "assistant" if turn.role == "agent" else turn.role,
                    "content": content,
                }
            )
        return messages

    def _local_now(self) -> datetime:
        current = self._now_provider()
        if current.tzinfo is None:
            return current.replace(tzinfo=self._local_timezone)
        return current.astimezone(self._local_timezone)

    def _user_prompt_with_current_time(self, user_message: str) -> str:
        current = self._local_now()
        raw_offset = current.strftime("%z")
        offset = f"{raw_offset[:3]}:{raw_offset[3:]}" if len(raw_offset) == 5 else raw_offset
        return f"{user_message}\n\n当前时间:{current:%Y-%m-%d %H:%M} {offset}".rstrip()

    def _build_service(self) -> LLMService:
        """Create the shared LLM service when one is not injected."""
        from openbiliclaw.llm.service import LLMService

        shared_service = getattr(self._soul_engine, "_llm_service", None)
        if shared_service is not None:
            return cast("LLMService", shared_service)
        memory = getattr(self._soul_engine, "_memory", None)
        if self._llm is None or memory is None:
            raise RuntimeError("Dialogue service is not configured.")
        module_overrides = self._module_overrides
        if module_overrides is None:
            module_overrides = getattr(self._soul_engine, "_module_overrides", {})
        return LLMService(
            registry=self._llm,
            memory=memory,
            module_overrides=module_overrides or {},
            concurrency=int(getattr(self._soul_engine, "_llm_concurrency", 4)),
            concurrency_gate=getattr(self._soul_engine, "_llm_concurrency_gate", None),
        )
