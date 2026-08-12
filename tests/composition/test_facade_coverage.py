from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.composition.build import BuildOptions, build_application
from openbiliclaw.composition.facade import _ActionExecutor, _Availability, _ContentVerifier
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.registry import ContentProviderRegistry
from openbiliclaw.core.config import AppSettings


@pytest.mark.asyncio
async def test_facade_delegates_every_transport_operation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    facade = app.services.facade
    assert facade is not None
    dynamic = cast("Any", facade)
    delegated = {
        "_source_status": "source-status",
        "_source_form": type("FormResult", (), {"form": "form"})(),
        "_sources": "sources",
        "_connect": "connected",
        "_disconnect": "disconnected",
        "_recommendations": "recommendations",
        "_record_observations": "observed",
        "_profile": "profile",
        "_search": "search",
        "_details": "details",
        "_refresh": "refresh",
        "_feedback": "feedback",
        "_profile_edit": "edit",
        "_propose": "proposal",
        "_confirm": "confirmation",
    }
    for name, result in delegated.items():
        setattr(dynamic, name, AsyncMock(return_value=result))

    assert await dynamic.source_status("demo", None) == "source-status"
    assert await dynamic.source_form("demo", "builtin.manual") == "form"
    assert await dynamic.list_sources(None, 10) == "sources"
    assert await dynamic.connect_source(cast("Any", object())) == "connected"
    assert await dynamic.disconnect_source(cast("Any", object())) == "disconnected"
    assert await dynamic.get_recommendations(5) == "recommendations"
    assert await dynamic.record_observations(cast("Any", object())) == "observed"
    assert await dynamic.show_profile("default") == "profile"
    assert await dynamic.search_content("demo", "query", 5) == "search"
    assert (
        await dynamic.get_content_details(
            '{"provider_id":{"value":"demo"},"content_kind":{"value":"video"},"provider_content_id":"1","canonical_url":"https://example.com/1"}'
        )
        == "details"
    )
    assert await dynamic.refresh_recommendations(cast("Any", object())) == "refresh"
    assert await dynamic.record_feedback(cast("Any", object())) == "feedback"
    assert await dynamic.edit_profile(cast("Any", object())) == "edit"
    assert await dynamic.propose_action(cast("Any", object())) == "proposal"
    assert await dynamic.confirm_action(cast("Any", object())) == "confirmation"

    assistant = type(
        "Assistant",
        (),
        {
            "turn": AsyncMock(return_value="turn"),
            "conversation": AsyncMock(return_value="conversation"),
            "messages": AsyncMock(return_value=("message",)),
        },
    )()
    dynamic._assistant = assistant
    assert await dynamic.assistant_turn(cast("Any", object()), "device") == "turn"
    assert await dynamic.conversation("conv", "device") == "conversation"
    assert await dynamic.conversation_messages("conv", "device", 10) == ("message",)


@pytest.mark.asyncio
async def test_facade_action_helpers_are_bounded() -> None:
    await _Availability().refresh("demo")
    registry = ContentProviderRegistry()
    verifier = _ContentVerifier(registry)
    ref = ContentRef(
        provider_id=ProviderId(value="demo"),
        content_kind=ContentKind(value="video"),
        provider_content_id="1",
        canonical_url="https://example.com/1",
    )
    assert not await verifier.available(ref, cast("Any", object()))
    executor = _ActionExecutor(registry)
    with pytest.raises(TypeError, match="invalid"):
        await executor.execute(cast("Any", object()), cast("Any", object()))


@pytest.mark.asyncio
async def test_facade_optional_operations_fail_closed_and_assistant_is_set_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    facade = app.services.facade
    assert facade is not None
    dynamic = cast("Any", facade)
    for attribute in ("_refresh", "_feedback", "_profile_edit", "_propose", "_confirm"):
        setattr(dynamic, attribute, None)
    operations = (
        dynamic.refresh_recommendations(cast("Any", object())),
        dynamic.record_feedback(cast("Any", object())),
        dynamic.edit_profile(cast("Any", object())),
        dynamic.propose_action(cast("Any", object())),
        dynamic.confirm_action(cast("Any", object())),
    )
    for operation in operations:
        with pytest.raises(Exception, match="not configured"):
            await operation
    assistant = cast("Any", object())
    dynamic.set_assistant(assistant)
    with pytest.raises(RuntimeError, match="already"):
        dynamic.set_assistant(assistant)
