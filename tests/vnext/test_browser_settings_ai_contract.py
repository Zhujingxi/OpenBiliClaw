"""Task 21b contracts for complete product settings and safe AI navigation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models.test import TestModel

from openbiliclaw.api.dependencies import _apply_runtime_settings
from openbiliclaw.api.routers.settings import UserSettingsPatch
from openbiliclaw.features.system.domain import UserSettings
from openbiliclaw.features.system.service import SettingsService
from openbiliclaw.infrastructure.ai.health import AIHealthService
from openbiliclaw.infrastructure.ai.runner import TaskRunner
from openbiliclaw.infrastructure.ai.spec import CachePolicy, TaskLane, TaskSpec
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.network import reset_outbound_proxy_for_tests

if TYPE_CHECKING:
    from pathlib import Path


def _service(tmp_path: Path) -> SettingsService:
    url = f"sqlite:///{tmp_path / 'complete-settings.db'}"
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=url))
    UserSettings()  # force schema construction before the test owns the engine lifecycle
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine.dispose()
    return SettingsService(lambda: UnitOfWork(session_factory))


def test_complete_settings_are_nested_strict_and_secret_free(tmp_path: Path) -> None:
    service = _service(tmp_path)
    settings = service.get()

    assert set(settings.sources.enabled) == {
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
    }
    assert settings.schedules.source_sync_interval_minutes == 30
    assert settings.feed.low_watermark == 20
    assert settings.profile.minimum_evidence_confidence == 0
    assert set(settings.tasks) == {
        "profile_delta",
        "keyword_generation",
        "candidate_assessment",
        "candidate_batch_assessment",
        "chat_response",
        "recommendation_explanation",
    }
    assert settings.network.mode == "direct"
    assert settings.logging.console_level == "INFO"
    assert settings.access_control.extension_session_ttl_hours == 24
    assert settings.jobs.retention_days == 30

    serialized = settings.model_dump_json().casefold()
    for forbidden in ("api_key", "access_token", "password_hash", "master_key", "base_url"):
        assert forbidden not in serialized


def test_flat_settings_are_absent_from_public_schemas_and_rejected() -> None:
    flat_fields = {
        "source_enabled",
        "source_weights",
        "source_sync_interval_minutes",
        "feed_low_watermark",
        "feed_high_watermark",
    }
    assert flat_fields.isdisjoint(UserSettings.model_json_schema()["properties"])
    assert flat_fields.isdisjoint(UserSettingsPatch.model_json_schema()["properties"])
    with pytest.raises(ValidationError):
        UserSettings.model_validate({"feed_low_watermark": 5})
    with pytest.raises(ValidationError):
        UserSettingsPatch.model_validate({"feed_low_watermark": 5})


def test_runtime_logging_changes_only_openbiliclaw_owned_handlers() -> None:
    class OwnedConsoleHandler(logging.StreamHandler[Any]):
        _openbiliclaw_sink = "console"

    root = logging.getLogger()
    original_root_level = root.level
    host_handler = logging.StreamHandler()
    host_handler.setLevel(logging.ERROR)
    owned_handler = OwnedConsoleHandler()
    owned_handler.setLevel(logging.WARNING)
    root.addHandler(host_handler)
    root.addHandler(owned_handler)
    defaults = UserSettings()
    settings = defaults.model_copy(
        update={"logging": defaults.logging.model_copy(update={"console_level": "DEBUG"})}
    )

    try:
        _apply_runtime_settings(settings)
        assert host_handler.level == logging.ERROR
        assert owned_handler.level == logging.DEBUG
        assert root.level == original_root_level
    finally:
        root.removeHandler(host_handler)
        root.removeHandler(owned_handler)
        root.setLevel(original_root_level)
        reset_outbound_proxy_for_tests()


def test_alembic_upgrade_does_not_disable_existing_named_loggers(tmp_path: Path) -> None:
    application_logger = logging.getLogger("openbiliclaw.features.system")
    original_disabled = application_logger.disabled
    original_level = application_logger.level
    application_logger.disabled = False
    application_logger.setLevel(logging.NOTSET)

    try:
        _service(tmp_path)
        assert application_logger.disabled is False
        assert application_logger.level == logging.NOTSET
    finally:
        application_logger.disabled = original_disabled
        application_logger.setLevel(original_level)


def test_nested_settings_patch_recursively_merges_and_rejects_read_only(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    original = service.get()

    updated = service.update(
        {
            "sources": {"enabled": {"bilibili": True}, "weights": {"youtube": 2.5}},
            "feed": {"min_score": 0.7},
            "tasks": {"chat_response": {"timeout_seconds": 75}},
            "access_control": {"session_ttl_hours": 72},
            "jobs": {"retention_days": 14},
        }
    )

    assert updated.sources.enabled["bilibili"] is True
    assert updated.sources.enabled["youtube"] is False
    assert updated.sources.weights["youtube"] == 2.5
    assert updated.sources.weights["bilibili"] == 1
    assert updated.feed.min_score == 0.7
    assert updated.feed.min_novelty == original.feed.min_novelty
    assert updated.tasks["chat_response"].timeout_seconds == 75
    assert (
        updated.tasks["chat_response"].total_tokens_limit
        == original.tasks["chat_response"].total_tokens_limit
    )
    assert updated.access_control.session_ttl_hours == 72
    assert updated.jobs.retention_days == 14
    assert service.get() == updated

    for patch in (
        {"onboarding_complete": True},
        {"access_control": {"installer_bearer_configured": True}},
        {"access_control": {"password_configured": True}},
        {"jobs": {"worker_concurrency": 2}},
        {"logging": {"directory": "/tmp/redirect"}},
    ):
        with pytest.raises(ValueError, match="read-only|workflow-owned"):
            service.update(patch)


@pytest.mark.parametrize(
    "patch",
    [
        {"feed": {"low_watermark": 100, "high_watermark": 20}},
        {"profile": {"minimum_evidence_confidence": 1.1}},
        {"tasks": {"chat_response": {"model_alias": "provider/model"}}},
        {"tasks": {"chat_response": {"semantic_retry_limit": 11}}},
        {"network": {"mode": "custom", "proxy_url": "http://user:secret@proxy.test"}},
        {"access_control": {"session_ttl_hours": 8761}},
        {"access_control": {"extension_session_ttl_hours": 0}},
        {"jobs": {"retention_days": 0}},
        {"logging": {"console_level": "VERBOSE"}},
    ],
)
def test_complete_settings_bounds_fail_atomically(tmp_path: Path, patch: dict[str, object]) -> None:
    service = _service(tmp_path)
    original = service.get()

    with pytest.raises((ValidationError, ValueError)):
        service.update(patch)

    assert service.get() == original


@pytest.mark.asyncio
async def test_ai_health_exposes_only_explicit_safe_external_admin_url() -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.params["model"])
        return httpx.Response(200, json={"healthy_count": 1, "unhealthy_count": 0})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://internal-litellm.test"
    ) as client:
        result = await AIHealthService(
            base_url="http://internal-litellm.test",
            api_key="internal-key",
            public_admin_url="https://models.example/admin/ui",
            client=client,
        ).check_aliases()

    assert result.admin_url == "https://models.example/admin/ui"
    assert requested == ["obc-interactive", "obc-analysis", "obc-embedding"]
    payload = result.model_dump_json()
    assert "internal-litellm" not in payload
    assert "internal-key" not in payload


@pytest.mark.parametrize(
    "url",
    [
        "ftp://models.example/ui",
        "https://user:secret@models.example/ui",
        "https://models.example/ui?token=secret",
        "//models.example/ui",
    ],
)
def test_ai_health_rejects_unsafe_public_admin_urls(url: str) -> None:
    with pytest.raises(ValueError):
        AIHealthService(base_url="http://internal.test", api_key="key", public_admin_url=url)


def test_ai_health_does_not_infer_public_admin_url_from_internal_base() -> None:
    service = AIHealthService(base_url="http://litellm:4000", api_key="key")
    assert service.public_admin_url is None


@pytest.mark.asyncio
async def test_task_runner_consumes_authoritative_alias_and_limits() -> None:
    class Input(BaseModel):
        value: str

    class Output(BaseModel):
        value: str

    configured = UserSettings.model_validate(
        {
            "tasks": {
                "chat_response": {
                    "model_alias": "obc-analysis",
                    "semantic_retry_limit": 0,
                    "timeout_seconds": 12,
                    "request_limit": 1,
                    "total_tokens_limit": 1234,
                }
            }
        }
    )

    class Settings:
        def get(self) -> UserSettings:
            return configured

    class Recorder:
        started: list[tuple[str, str]] = []

        def start(self, *, task_name: str, model_alias: str) -> Any:
            from uuid import uuid4

            self.started.append((task_name, model_alias))
            return uuid4()

        def succeed(self, run_id: Any, *, usage: dict[str, int]) -> None:
            pass

        def fail(self, run_id: Any, *, error_kind: str) -> None:
            pass

    resolved: list[str] = []
    model = TestModel(custom_output_args={"value": "ok"})

    def resolver(alias: str) -> TestModel:
        resolved.append(alias)
        return model

    recorder = Recorder()
    runner = TaskRunner(model_resolver=resolver, recorder=recorder, settings=Settings())
    output = await runner.run(
        TaskSpec(
            name="chat_response",
            input_type=Input,
            output_type=Output,
            agent=Agent(output_type=Output),
            model_alias="obc-interactive",
            semantic_retry_limit=2,
            timeout_seconds=45,
            usage_limits=UsageLimits(request_limit=3, total_tokens_limit=8000),
            cache_policy=CachePolicy.BYPASS,
            lane=TaskLane.INTERACTIVE,
        ),
        {"value": "input"},
    )

    assert output == Output(value="ok")
    assert resolved == ["obc-analysis"]
    assert recorder.started == [("chat_response", "obc-analysis")]
