from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.runtime_context import RuntimeContext, RuntimeModelBundle
from openbiliclaw.config import (
    Config,
    LoggingConfig,
    load_config,
    save_config,
)
from openbiliclaw.config_write import coordinated_config_write
from openbiliclaw.llm import connection_factory
from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.concurrency import LLMConcurrencyGate
from openbiliclaw.logging_setup import configure_logging
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingRouteConfig,
    ModelConfig,
    compute_model_revision,
)

if TYPE_CHECKING:
    from pathlib import Path


def _valid_config(api_key: str = "sk-valid-openai-key") -> Config:
    return Config(
        models=ModelConfig(
            chat=ChatRouteConfig(
                connections=(
                    ChatConnection(
                        id="openai-main",
                        name="OpenAI",
                        type="openai_compatible",
                        preset="openai",
                        model="gpt-4o-mini",
                        base_url="https://api.openai.com/v1",
                        credential=CredentialConfig(source="inline", value=api_key),
                        api_mode="chat_completions",
                    ),
                )
            )
        )
    )


def _make_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: Config) -> TestClient:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(cfg, tmp_path / "config.toml")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app)


def _model_put_payload(revision: str, models: ModelConfig) -> dict[str, object]:
    def credential(connection: ChatConnection) -> dict[str, str]:
        return {"action": "keep", "value": ""}

    return {
        "revision": revision,
        "models": {
            "schema_version": models.schema_version,
            "chat": {
                "connections": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "type": item.type,
                        "model": item.model,
                        "preset": item.preset,
                        "base_url": item.base_url,
                        "credential": credential(item),
                        "api_mode": item.api_mode,
                        "reasoning_effort": item.reasoning_effort,
                        "http_referer": item.http_referer,
                        "x_title": item.x_title,
                        "num_ctx": item.num_ctx,
                    }
                    for item in models.chat.connections
                ],
                "concurrency": models.chat.concurrency,
                "timeout_seconds": models.chat.timeout_seconds,
            },
            "embedding": {
                "enabled": models.embedding.enabled,
                "settings": {
                    "model": models.embedding.settings.model,
                    "output_dimensionality": models.embedding.settings.output_dimensionality,
                    "similarity_threshold": models.embedding.settings.similarity_threshold,
                    "multimodal_enabled": models.embedding.settings.multimodal_enabled,
                },
                "providers": [],
            },
        },
        "migration_resolutions": {},
    }


def test_put_config_rejects_unknown_reset_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    client = _make_client(monkeypatch, tmp_path, _valid_config())
    before = config_path.read_bytes()

    response = client.put("/api/config", json={"reset_fields": ["storage.db_path"]})

    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["error"] == "unknown_reset_fields"
    assert config_path.read_bytes() == before
    assert not (tmp_path / "config.toml.bak").exists()


def test_put_config_success_saves_snapshot_then_hot_reloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    client = _make_client(monkeypatch, tmp_path, _valid_config())
    before = config_path.read_bytes()

    response = client.put("/api/config", json={"language": "en-US"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["reloaded"] is True
    assert body["rollback_applied"] is False
    assert body["restart_required"] is False
    assert load_config(config_path).language == "en-US"
    assert (tmp_path / "config.toml.bak").read_bytes() == before


def test_put_config_rolls_back_when_hot_reload_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    client = _make_client(monkeypatch, tmp_path, _valid_config())
    before = config_path.read_bytes()

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)

    response = client.put("/api/config", json={"language": "en-US"})

    assert response.status_code == 200
    body = response.json()
    assert body["reloaded"] is False
    assert body["rollback_applied"] is True
    assert "simulated" in body["message"]
    assert config_path.read_bytes() == before
    assert (tmp_path / "config.toml.bak").read_bytes() == before


def test_put_config_hot_reload_failure_file_log_keeps_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    configure_logging(
        Config(
            logging=LoggingConfig(
                level="INFO",
                file_level="DEBUG",
                directory=str(log_dir),
                filename="app.log",
                max_file_size_mb=0,
                backup_count=1,
            )
        )
    )
    client = _make_client(monkeypatch, tmp_path, _valid_config())

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated hot reload crash")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)

    response = client.put("/api/config", json={"language": "en-US"})

    assert response.status_code == 200
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            handler.flush()
    text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "Config hot-reload failed" in text
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: simulated hot reload crash" in text


def test_put_config_returns_500_when_rollback_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    client = _make_client(monkeypatch, tmp_path, _valid_config())
    before = config_path.read_bytes()

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated")

    def fail_restore(*_args: object, **_kwargs: object) -> None:
        raise OSError("restore denied")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)
    monkeypatch.setattr(
        "openbiliclaw.api.app._restore_config_snapshot",
        fail_restore,
        raising=False,
    )

    response = client.put("/api/config", json={"language": "en-US"})

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "config_persistence_corrupted"
    assert "config.toml.bak" in body["manual_recovery"]
    assert config_path.read_bytes() != before


@pytest.mark.asyncio
async def test_put_config_serializes_concurrent_saves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    first_cfg = _valid_config()
    save_config(first_cfg, config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first, second = await asyncio.gather(
            client.put("/api/config", json={"language": "en-US"}),
            client.put("/api/config", json={"language": "zh-TW"}),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert load_config(config_path).language == "zh-TW"
    assert load_config(tmp_path / "config.toml.bak").language == "en-US"


@pytest.mark.asyncio
async def test_put_config_waits_on_the_canonical_path_write_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        async with coordinated_config_write(config_path):
            request_task = asyncio.create_task(
                client.put("/api/config", json={"language": "en-US"})
            )
            await asyncio.sleep(0.05)
            assert request_task.done() is False
        response = await request_task

    assert response.status_code == 200
    assert load_config(config_path).language == "en-US"


@pytest.mark.asyncio
async def test_put_config_rebases_models_saved_while_waiting_for_write_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    initial = _valid_config()
    save_config(initial, config_path, models_authoritative=True)

    async def no_background_restart(self: RuntimeContext, *_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(RuntimeContext, "restart_background_tasks", no_background_restart)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    entered = asyncio.Event()
    release = asyncio.Event()

    @asynccontextmanager
    async def delayed_ordinary_write(path: Path):
        entered.set()
        await release.wait()
        async with coordinated_config_write(path):
            yield

    monkeypatch.setattr(
        "openbiliclaw.api.app.coordinated_config_write",
        delayed_ordinary_write,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        ordinary = asyncio.create_task(client.put("/api/config", json={"language": "en-US"}))
        await asyncio.wait_for(entered.wait(), timeout=1)
        current = (await client.get("/api/model-config")).json()
        new_connection = replace(initial.models.chat.connections[0], model="gpt-4.1-new")
        new_models = replace(
            initial.models,
            chat=replace(initial.models.chat, connections=(new_connection,)),
        )
        new_revision = compute_model_revision(new_models)
        model_response = await client.put(
            "/api/model-config",
            json=_model_put_payload(current["revision"], new_models),
        )
        release.set()
        ordinary_response = await ordinary

    assert model_response.status_code == 200, model_response.text
    assert ordinary_response.status_code == 200, ordinary_response.text
    persisted = load_config(config_path)
    assert persisted.language == "en-US"
    assert persisted.models == new_models
    runtime = app.state.runtime_context.current_model_candidate
    assert runtime is not None
    assert runtime.revision == new_revision
    assert runtime.models == new_models


@pytest.mark.asyncio
async def test_put_config_rebase_keeps_effective_local_model_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    initial = _valid_config()
    initial.models = replace(
        initial.models,
        chat=replace(initial.models.chat, concurrency=4),
    )
    save_config(initial, config_path, models_authoritative=True)
    (tmp_path / "config.local.toml").write_text(
        "[models.chat]\nconcurrency = 9\n",
        encoding="utf-8",
    )
    assert load_config().models.chat.concurrency == 9

    async def no_background_restart(self: RuntimeContext, *_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(RuntimeContext, "restart_background_tasks", no_background_restart)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put("/api/config", json={"language": "en-US"})

    assert response.status_code == 200, response.text
    assert response.json()["config"]["llm"]["concurrency"] == 9
    runtime = app.state.runtime_context.current_model_candidate
    assert runtime is not None
    assert runtime.models.chat.concurrency == 9
    effective = load_config()
    assert "models.chat.concurrency" in effective.model_meta.override_paths
    assert effective.models.chat.concurrency == 9


async def test_runtime_model_candidate_swap_is_staged_and_identity_preserving() -> None:
    context = RuntimeContext()
    models = ModelConfig()
    gate = LLMConcurrencyGate(models.chat.concurrency)

    def bundle(revision: str) -> RuntimeModelBundle:
        return RuntimeModelBundle(
            revision=revision,
            models=models,
            chat_route=object(),
            llm_service=SimpleNamespace(concurrency_gate=gate),
        )

    first = bundle("first")
    second = bundle("second")

    initial = await context.swap_model_candidate(first)
    held_by_in_flight_request = context.current_model_candidate
    previous = await context.swap_model_candidate(second)

    assert initial is None
    assert previous is first
    assert held_by_in_flight_request is first
    assert context.current_model_candidate is second
    await context.restore_model_candidate(previous)
    assert context.current_model_candidate is first


async def test_runtime_model_candidate_build_is_side_effect_free_until_swap() -> None:
    context = RuntimeContext()
    models = ModelConfig(
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="local",
                    name="Local",
                    type="ollama",
                    model="local-model",
                    base_url="http://127.0.0.1:11434/v1",
                ),
            ),
            timeout_seconds=30,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=False,
            settings=EmbeddingModelSettings(model="embedding-model"),
        ),
    )
    revision = compute_model_revision(models)

    candidate = await context.build_model_candidate(models, revision)

    assert context.current_model_candidate is None
    assert candidate.revision == revision
    assert candidate.models is models
    await context.swap_model_candidate(candidate)
    assert context.current_model_candidate is candidate


async def test_runtime_exact_probe_returns_safe_result_for_expected_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RuntimeContext()
    draft = ChatConnection(
        id="local",
        name="Local",
        type="ollama",
        model="local-model",
        base_url="http://127.0.0.1:11434/v1",
    )

    def fail_adapter(_draft: object, _options: object) -> object:
        raise LLMProviderError("test-secret-provider-detail")

    monkeypatch.setattr(connection_factory, "build_chat_adapter", fail_adapter)

    result = await context.probe_model_draft(draft)

    assert result.ok is False
    assert result.error_code == "probe_failed"
    assert result.message == "The exact model draft probe failed."
    assert "test-secret-provider-detail" not in repr(result)


@pytest.mark.parametrize("configured_effort", ["high", "max"])
async def test_runtime_exact_deepseek_probe_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
    configured_effort: str,
) -> None:
    context = RuntimeContext()
    draft = ChatConnection(
        id="deepseek-main",
        name="DeepSeek",
        type="openai_compatible",
        preset="deepseek",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        credential=CredentialConfig(source="inline", value="test-key"),
        api_mode="chat_completions",
        reasoning_effort=configured_effort,
    )
    calls: list[dict[str, object]] = []

    class ProbeAdapter:
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            json_mode: bool = False,
            reasoning_effort: str | None = None,
            model: str | None = None,
        ) -> LLMResponse:
            calls.append(
                {
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "json_mode": json_mode,
                    "reasoning_effort": reasoning_effort,
                    "model": model,
                }
            )
            return LLMResponse(content="OK", provider="deepseek", model=draft.model)

    monkeypatch.setattr(
        connection_factory,
        "build_chat_adapter",
        lambda _draft, _options: ProbeAdapter(),
    )

    result = await context.probe_model_draft(draft)

    assert result.ok is True
    assert calls == [
        {
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "temperature": 0.7,
            "max_tokens": 8,
            "json_mode": False,
            "reasoning_effort": "",
            "model": None,
        }
    ]
    assert draft.reasoning_effort == configured_effort


async def test_runtime_exact_probe_propagates_programming_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RuntimeContext()
    draft = ChatConnection(
        id="local",
        name="Local",
        type="ollama",
        model="local-model",
        base_url="http://127.0.0.1:11434/v1",
    )

    def crash_adapter(_draft: object, _options: object) -> object:
        raise RuntimeError("programming error")

    monkeypatch.setattr(connection_factory, "build_chat_adapter", crash_adapter)

    with pytest.raises(RuntimeError, match="programming error"):
        await context.probe_model_draft(draft)
