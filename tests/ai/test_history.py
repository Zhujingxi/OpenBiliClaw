from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from openbiliclaw.ai.runtime.history import (
    ContextProjection,
    HistoryPolicy,
    MessageAuditError,
    ToolResultTooLargeError,
    audit_model_messages,
    audit_text,
    audit_tool_result,
    history_size,
    trim_history,
)


def _turn(text: str) -> tuple[ModelRequest, ModelResponse]:
    return (ModelRequest(parts=[UserPromptPart(text)]), ModelResponse(parts=[TextPart(text)]))


def test_projection_is_bounded_at_construction() -> None:
    assert ContextProjection("profile", "short", max_bytes=5).text == "short"
    with pytest.raises(ValueError, match="projection"):
        ContextProjection("profile", "too long", max_bytes=2)


def test_history_size_and_trim_keep_newest_complete_turns() -> None:
    old = _turn("old")
    new = _turn("new")
    limit = history_size(new)
    assert trim_history(old + new, HistoryPolicy(max_bytes=limit, max_tool_result_bytes=100)) == new
    assert trim_history(new, HistoryPolicy(max_bytes=limit - 1, max_tool_result_bytes=100)) == ()


def test_oversized_tool_result_is_rejected() -> None:
    message = ModelRequest(parts=[ToolReturnPart("search", "12345", tool_call_id="1")])
    with pytest.raises(ToolResultTooLargeError):
        trim_history((message,), HistoryPolicy(max_bytes=1_000, max_tool_result_bytes=4))


@pytest.mark.parametrize(
    "canary",
    [
        "vault:secret-id",
        "Authorization: Bearer abc",
        "api_key=abc",
        "cookie=session",
        "cred_0123456789abcdef0123456789abcdef",
    ],
)
def test_secret_canaries_never_pass_message_audit(canary: str) -> None:
    message = ModelRequest(parts=[UserPromptPart(canary)])
    with pytest.raises(MessageAuditError):
        audit_model_messages((message,))
    with pytest.raises(MessageAuditError):
        audit_text(canary)


def test_normal_messages_pass_audit() -> None:
    audit_model_messages(_turn("normal discussion"))


def test_structured_tool_secrets_are_rejected() -> None:
    with pytest.raises(MessageAuditError):
        audit_tool_result("website", {"cookie": "session-secret"}, 1_000)


def test_history_policy_and_projection_require_positive_limits() -> None:
    with pytest.raises(ValueError):
        HistoryPolicy(0, 1)
    with pytest.raises(ValueError):
        ContextProjection("", "ok", 1)
