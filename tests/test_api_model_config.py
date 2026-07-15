"""Dedicated, secret-safe model configuration API contract tests."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    ModelConfig,
    compute_model_revision,
)
from openbiliclaw.model_config.service import ModelConfigProbeResult

if TYPE_CHECKING:
    from pathlib import Path


_INLINE_SECRET = "sk-api-model-config-secret-0123456789"


def _native_models() -> ModelConfig:
    return ModelConfig(
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="primary-openai",
                    name="Primary OpenAI",
                    type="openai_compatible",
                    preset="openai",
                    model="gpt-4.1-mini",
                    base_url="https://api.openai.com/v1",
                    credential=CredentialConfig(source="inline", value=_INLINE_SECRET),
                    api_mode="chat_completions",
                ),
                ChatConnection(
                    id="fallback-openai",
                    name="Fallback gateway",
                    type="openai_compatible",
                    preset="custom",
                    model="gateway-model",
                    base_url="https://fallback.example.test/v1",
                    credential=CredentialConfig(source="env", value="FALLBACK_API_KEY"),
                    api_mode="chat_completions",
                ),
                ChatConnection(
                    id="later-ollama",
                    name="Later local",
                    type="ollama",
                    model="qwen3:8b",
                    base_url="http://127.0.0.1:11434/v1",
                ),
            ),
            concurrency=3,
            timeout_seconds=90,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=True,
            settings=EmbeddingModelSettings(
                model="text-embedding-3-small",
                output_dimensionality=1536,
                similarity_threshold=0.81,
                multimodal_enabled=False,
            ),
            providers=(
                EmbeddingProviderConfig(
                    id="embedding-main",
                    name="Embedding main",
                    type="openai_compatible",
                    preset="openai",
                    base_url="https://api.openai.com/v1",
                    credential=CredentialConfig(source="inline", value="embed-secret-value"),
                ),
                EmbeddingProviderConfig(
                    id="embedding-backup",
                    name="Embedding backup",
                    type="ollama",
                    base_url="http://127.0.0.1:11434/v1",
                ),
            ),
        ),
    )


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    models: ModelConfig | None = None,
) -> tuple[TestClient, Path]:
    config_path = tmp_path / "config.toml"
    config = Config(models=models or _native_models())
    save_config(config, config_path, models_authoritative=True)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FALLBACK_API_KEY", "resolved-test-key")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app), config_path


def _make_production_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, Path]:
    """Compose the real runtime graph while keeping all data under ``tmp_path``."""
    config_path = tmp_path / "config.toml"
    config = Config(data_dir=str(tmp_path / "data"), models=_native_models())
    save_config(config, config_path, models_authoritative=True)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FALLBACK_API_KEY", "resolved-test-key")
    return create_app(), config_path


def _runtime_consumer_identities(context: object) -> tuple[object, ...]:
    return tuple(
        getattr(context, name)
        for name in (
            "model_bundle",
            "llm_service",
            "soul_engine",
            "dialogue",
            "discovery_engine",
            "recommendation_engine",
            "runtime_controller",
            "account_sync_service",
            "auto_update_service",
        )
    )


def _assert_loop_slots_match_registry(
    slots: tuple[object, object, object],
    tracked: tuple[tuple[asyncio.Task[Any], ...], ...],
) -> None:
    """Assert every live app loop slot is the registry's sole owner."""
    for slot_task, tracked_tasks in zip(slots, tracked, strict=True):
        assert isinstance(slot_task, asyncio.Task)
        if slot_task.done():
            assert tracked_tasks == ()
        else:
            assert tracked_tasks == (slot_task,)


def _credential(action: str = "keep", value: str = "") -> dict[str, str]:
    return {"action": action, "value": value}


def _chat_payload(connection: ChatConnection, *, action: str = "keep", value: str = "") -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "type": connection.type,
        "model": connection.model,
        "preset": connection.preset,
        "base_url": connection.base_url,
        "credential": _credential(action, value),
        "api_mode": connection.api_mode,
        "reasoning_effort": connection.reasoning_effort,
        "http_referer": connection.http_referer,
        "x_title": connection.x_title,
        "num_ctx": connection.num_ctx,
    }


def _embedding_payload(
    provider: EmbeddingProviderConfig,
    *,
    action: str = "keep",
    value: str = "",
) -> dict:
    return {
        "id": provider.id,
        "name": provider.name,
        "type": provider.type,
        "preset": provider.preset,
        "base_url": provider.base_url,
        "credential": _credential(action, value),
    }


def _put_payload(revision: str, models: ModelConfig) -> dict:
    return {
        "revision": revision,
        "models": {
            "schema_version": models.schema_version,
            "chat": {
                "connections": [_chat_payload(item) for item in models.chat.connections],
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
                "providers": [_embedding_payload(item) for item in models.embedding.providers],
            },
        },
        "migration_resolutions": {},
    }


def _replace_primary_credential_on_disk(config_path: Path, secret: str) -> str:
    """Simulate a concurrent credential-only writer and return its revision."""
    config = load_config(config_path)
    models = config.models
    primary = replace(
        models.chat.connections[0],
        credential=CredentialConfig(source="inline", value=secret),
    )
    config.models = replace(
        models,
        chat=replace(
            models.chat,
            connections=(primary, *models.chat.connections[1:]),
        ),
    )
    save_config(config, config_path, models_authoritative=True)
    return compute_model_revision(config.models)


class _BlockingProbeGate:
    """Hold a probe before route admission using cross-loop-safe events."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    @asynccontextmanager
    async def slot(self, *, caller: str):
        assert caller == "api.config_probe"
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.001)
        yield


def test_model_api_models_are_strict_and_hide_credential_value_from_repr() -> None:
    from openbiliclaw.api.model_config_models import (
        ChatConnectionIn,
        ChatRouteIn,
        CredentialActionIn,
        ModelConfigPutIn,
    )

    action = CredentialActionIn(action="set", value=_INLINE_SECRET)
    assert _INLINE_SECRET not in repr(action)

    with pytest.raises(ValidationError):
        CredentialActionIn(action="keep", value="", api_key=_INLINE_SECRET)
    with pytest.raises(ValidationError):
        ModelConfigPutIn(revision="revision", models={}, surprise=True)

    connection = ChatConnectionIn.model_validate(
        _chat_payload(_native_models().chat.connections[0])
    )
    assert ChatRouteIn(connections=[connection], concurrency=16, timeout_seconds=10)
    with pytest.raises(ValidationError):
        ChatRouteIn(connections=[connection], concurrency=17, timeout_seconds=10)
    with pytest.raises(ValidationError):
        ChatRouteIn(connections=[connection], concurrency=4, timeout_seconds=9)


def test_get_model_config_preserves_order_and_returns_only_public_credential_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _make_client(monkeypatch, tmp_path)

    response = client.get("/api/model-config")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "native"
    assert body["migration"]["state"] == "none"
    assert body["migration"]["confirmed"] is True
    assert [item["id"] for item in body["models"]["chat"]["connections"]] == [
        "primary-openai",
        "fallback-openai",
        "later-ollama",
    ]
    assert [item["id"] for item in body["models"]["embedding"]["providers"]] == [
        "embedding-main",
        "embedding-backup",
    ]
    inline = body["models"]["chat"]["connections"][0]["credential"]
    env = body["models"]["chat"]["connections"][1]["credential"]
    assert inline == {
        "source": "inline",
        "configured": True,
        "env_name": "",
        "credential_ref": "",
        "oauth_logged_in": False,
    }
    assert env["env_name"] == "FALLBACK_API_KEY"
    assert "value" not in inline
    assert body["models"]["chat"]["connections"][0]["probe"] is None
    assert body["models"]["chat"]["connections"][0]["circuit"]["state"] == "closed"
    serialized = response.text
    assert _INLINE_SECRET not in serialized
    assert "embed-secret-value" not in serialized


def test_connection_type_descriptors_are_grouped_ordered_and_capability_filtered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _make_client(monkeypatch, tmp_path)

    all_types = client.get("/api/model-connection-types").json()
    embedding = client.get("/api/model-connection-types?capability=embedding").json()

    assert [group["category"] for group in all_types["groups"]] == [
        "api_protocol",
        "local_runtime",
        "oauth",
    ]
    assert [item["id"] for item in all_types["connection_types"]] == [
        "openai_compatible",
        "anthropic_compatible",
        "gemini_api",
        "dashscope_api",
        "ollama",
        "codex_oauth",
    ]
    embedding_ids = [item["id"] for item in embedding["connection_types"]]
    assert embedding["capability"] == "embedding"
    assert embedding_ids == ["openai_compatible", "gemini_api", "dashscope_api", "ollama"]
    assert all("embedding" in item["capabilities"] for item in embedding["connection_types"])
    assert (
        "deepseek"
        not in next(
            item for item in embedding["connection_types"] if item["id"] == "openai_compatible"
        )["presets"]
    )
    descriptor_text = json.dumps(all_types)
    for forbidden in ("adapter", "callable", "api_key", _INLINE_SECRET):
        assert forbidden not in descriptor_text


def test_get_model_config_reports_legacy_migration_state_and_issues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = Config()
    config.llm.default_provider = "openai"
    config.llm.openai.api_key = "legacy-secret"
    config.llm.openai.model = "gpt-4o-mini"
    config.llm.deepseek.api_key = "unrouted-legacy-secret"
    config.llm.deepseek.model = "deepseek-chat"
    save_config(config, tmp_path / "config.toml")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    client = TestClient(
        create_app(memory_manager=object(), database=object(), soul_engine=object())
    )

    body = client.get("/api/model-config").json()

    assert body["source"] == "legacy"
    assert body["migration"]["state"] in {"ready", "pending"}
    assert body["migration"]["confirmed"] is False
    issue = next(
        item for item in body["migration"]["issues"] if item["code"] == "unrouted_credential"
    )
    assert issue["provider"] == "deepseek"
    assert issue["allowed_actions"] == [
        "add_to_chat_route",
        "confirm_remove_after_backup",
        "cancel",
    ]
    assert "legacy-secret" not in json.dumps(body)
    assert "unrouted-legacy-secret" not in json.dumps(body)


def test_get_model_config_reports_runtime_circuit_and_latest_exact_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.llm.route import CircuitTable

    async def probe(self: object, draft: object, settings: object = None) -> object:
        return ModelConfigProbeResult(
            ok=False,
            connection_id="primary-openai",
            capability="chat",
            error_code="probe_failed",
            message="The exact model draft probe failed.",
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        probe,
    )
    client, _path = _make_client(monkeypatch, tmp_path)
    ctx = client.app.state.runtime_context
    revision = compute_model_revision(_native_models())
    circuits = CircuitTable()
    circuits.record_failure(
        "primary-openai",
        revision,
        "auth_failed",
        RuntimeError("must-not-appear"),
    )
    old_bundle = ctx.model_bundle
    assert old_bundle is not None
    ctx.model_bundle = replace(
        old_bundle,
        chat_route=SimpleNamespace(circuits=circuits),
    )
    current = client.get("/api/model-config").json()
    response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "chat",
            "revision": current["revision"],
            "connection": _chat_payload(_native_models().chat.connections[0]),
        },
    )

    assert response.status_code == 200
    refreshed = client.get("/api/model-config").json()
    primary = refreshed["models"]["chat"]["connections"][0]
    assert primary["probe"]["error_code"] == "probe_failed"
    assert primary["circuit"]["state"] == "open"
    assert primary["circuit"]["failure_kind"] == "auth_failed"
    assert primary["circuit"]["permanent"] is True
    assert "must-not-appear" not in json.dumps(primary)


def test_codex_oauth_public_state_reports_reference_and_login_without_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    oauth_models = ModelConfig(
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="codex-main",
                    name="Codex",
                    type="codex_oauth",
                    model="gpt-5-codex",
                    credential=CredentialConfig(source="oauth", value="codex"),
                ),
            ),
            timeout_seconds=30,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=False,
            settings=EmbeddingModelSettings(model="bge-m3"),
        ),
    )
    monkeypatch.setattr(
        "openbiliclaw.llm.codex_auth.load_codex_credentials",
        lambda: SimpleNamespace(is_expired=lambda: False, access_token="oauth-secret-token"),
    )
    client, _path = _make_client(monkeypatch, tmp_path, oauth_models)

    credential = client.get("/api/model-config").json()["models"]["chat"]["connections"][0][
        "credential"
    ]

    assert credential == {
        "source": "oauth",
        "configured": True,
        "env_name": "",
        "credential_ref": "codex",
        "oauth_logged_in": True,
    }
    assert "oauth-secret-token" not in json.dumps(credential)


@pytest.mark.parametrize("connection_id", ["primary-openai", "new-codex-route"])
def test_put_codex_oauth_keep_resolves_imported_reference_for_switched_and_new_routes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    connection_id: str,
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.llm.connection_factory.load_codex_access_token",
        lambda: "oauth-secret-token",
    )
    client, config_path = _make_client(monkeypatch, tmp_path)
    before = client.get("/api/model-config").json()
    models = _native_models()
    codex = ChatConnection(
        id=connection_id,
        name="Imported Codex",
        type="codex_oauth",
        model="gpt-5-codex",
        credential=CredentialConfig(source="oauth", value="codex"),
    )
    candidate = replace(
        models,
        chat=replace(models.chat, connections=(codex, *models.chat.connections[1:])),
    )

    response = client.put(
        "/api/model-config",
        json=_put_payload(before["revision"], candidate),
    )

    assert response.status_code == 200, response.text
    persisted = load_config(config_path).models.chat.connections[0]
    assert persisted.id == connection_id
    assert persisted.credential == CredentialConfig(source="oauth", value="codex")
    after = client.get("/api/model-config")
    assert after.status_code == 200
    public = after.json()["models"]["chat"]["connections"][0]["credential"]
    assert public["source"] == "oauth"
    assert public["credential_ref"] == "codex"
    assert "access_token" not in json.dumps(after.json())


def test_put_non_oauth_keep_rejects_a_persisted_oauth_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    oauth_models = ModelConfig(
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="codex-main",
                    name="Codex",
                    type="codex_oauth",
                    model="gpt-5-codex",
                    credential=CredentialConfig(source="oauth", value="codex"),
                ),
            ),
        ),
        embedding=EmbeddingRouteConfig(
            enabled=False,
            settings=EmbeddingModelSettings(model="bge-m3"),
        ),
    )
    client, config_path = _make_client(monkeypatch, tmp_path, oauth_models)
    before = client.get("/api/model-config").json()
    switched = replace(
        oauth_models,
        chat=replace(
            oauth_models.chat,
            connections=(
                ChatConnection(
                    id="codex-main",
                    name="API key route",
                    type="openai_compatible",
                    preset="custom",
                    model="gateway-model",
                    base_url="https://gateway.example.test/v1",
                    credential=CredentialConfig(),
                    api_mode="chat_completions",
                ),
            ),
        ),
    )

    response = client.put(
        "/api/model-config",
        json=_put_payload(before["revision"], switched),
    )

    assert response.status_code == 400
    assert {error["code"] for error in response.json()["errors"]} == {"invalid_oauth_reference"}
    persisted = load_config(config_path).models.chat.connections[0]
    assert persisted.type == "codex_oauth"
    assert persisted.credential == CredentialConfig(source="oauth", value="codex")


def test_put_model_config_keeps_existing_secrets_saves_order_and_emits_one_revision_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, config_path = _make_client(monkeypatch, tmp_path)
    before = client.get("/api/model-config").json()
    models = _native_models()
    reordered = replace(
        models,
        chat=replace(
            models.chat,
            connections=(
                replace(models.chat.connections[1], name="Gateway first"),
                models.chat.connections[0],
                models.chat.connections[2],
            ),
            concurrency=5,
        ),
    )
    payload = _put_payload(before["revision"], reordered)
    ctx = client.app.state.runtime_context
    events: list[dict[str, Any]] = []

    async def record_event(event: dict[str, Any]) -> bool:
        events.append(dict(event))
        return True

    monkeypatch.setattr(ctx.event_hub, "publish", record_event)

    response = client.put("/api/model-config", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["reloaded"] is True
    assert body["revision"] != before["revision"]
    assert [item["id"] for item in body["snapshot"]["models"]["chat"]["connections"]] == [
        "fallback-openai",
        "primary-openai",
        "later-ollama",
    ]
    assert load_config(config_path).models.chat.connections[0].name == "Gateway first"
    persisted = config_path.read_text(encoding="utf-8")
    assert _INLINE_SECRET in persisted
    reload_events = [item for item in events if item.get("type") == "config_reloaded"]
    assert reload_events == [{"type": "config_reloaded", "revision": body["revision"]}]


def test_model_put_restarts_new_graph_tasks_before_emitting_reload_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The API-owned lifecycle restarts task owners before announcing reload."""
    app, _config_path = _make_production_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        context = app.state.runtime_context
        old_consumers = _runtime_consumer_identities(context)
        old_tasks = tuple(
            getattr(app.state, name)
            for name in ("refresh_task", "account_sync_task", "auto_update_task")
        )
        lifecycle: list[str] = []
        restarted: list[tuple[tuple[object, ...], tuple[object, ...]]] = []
        real_restart = context.restart_background_tasks

        async def restart_for_new_graph(app_arg: object, **kwargs: object) -> None:
            assert app_arg is app
            assert _runtime_consumer_identities(context) != old_consumers
            await real_restart(app_arg, **kwargs)
            new_tasks = tuple(
                getattr(app.state, name)
                for name in ("refresh_task", "account_sync_task", "auto_update_task")
            )
            restarted.append((_runtime_consumer_identities(context), new_tasks))
            lifecycle.append("restart")

        async def publish(event: dict[str, Any]) -> bool:
            if event.get("type") == "config_reloaded":
                lifecycle.append("event")
            return True

        monkeypatch.setattr(context, "restart_background_tasks", restart_for_new_graph)
        monkeypatch.setattr(context.event_hub, "publish", publish)
        before = client.get("/api/model-config").json()
        changed = replace(
            _native_models(),
            chat=replace(_native_models().chat, concurrency=4),
        )

        response = client.put(
            "/api/model-config",
            json=_put_payload(before["revision"], changed),
        )

        assert response.status_code == 200, response.text
        assert lifecycle == ["restart", "event"]
        assert len(restarted) == 1
        new_consumers, new_tasks = restarted[0]
        assert new_consumers == _runtime_consumer_identities(context)
        assert all(new is not old for new, old in zip(new_tasks, old_tasks, strict=True))
        assert new_tasks[0] is not None and not new_tasks[0].done()
        assert new_tasks[1] is not None and not new_tasks[1].done()
        assert new_tasks[2] is not None


def test_model_put_replaces_all_task_slots_when_one_old_slot_already_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A completed exceptional child is cleanup data, not a restart failure."""
    app, _config_path = _make_production_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        context = app.state.runtime_context
        assert client.portal is not None

        async def install_failed_refresh_slot() -> tuple[object, object, object]:
            old_refresh = app.state.refresh_task
            old_refresh.cancel()
            await asyncio.gather(old_refresh, return_exceptions=True)

            async def fail_immediately() -> None:
                raise RuntimeError("already failed old refresh loop")

            failed = asyncio.create_task(fail_immediately())
            await asyncio.sleep(0)
            assert failed.done()
            app.state.refresh_task = failed
            return (
                failed,
                app.state.account_sync_task,
                app.state.auto_update_task,
            )

        old_tasks = client.portal.call(install_failed_refresh_slot)
        current = client.get("/api/model-config").json()
        changed = replace(
            _native_models(),
            chat=replace(_native_models().chat, concurrency=4),
        )

        response = client.put(
            "/api/model-config",
            json=_put_payload(current["revision"], changed),
        )

        # Keep the pre-fix exceptional slot from masking the behavioral
        # assertion during TestClient shutdown.
        if app.state.refresh_task is old_tasks[0]:
            app.state.refresh_task = None
        assert response.status_code == 200, response.text
        new_tasks = tuple(
            getattr(app.state, name)
            for name in ("refresh_task", "account_sync_task", "auto_update_task")
        )
        assert all(task is not None for task in new_tasks)
        assert all(new is not old for new, old in zip(new_tasks, old_tasks, strict=True))
        assert new_tasks[0] is not None and not new_tasks[0].done()
        assert new_tasks[1] is not None and not new_tasks[1].done()
        assert context.model_bundle is not None


def test_model_put_cancels_detached_old_graph_work_before_reload_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cutover drains registry-owned old work before announcing the new graph."""
    app, _config_path = _make_production_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        context = app.state.runtime_context
        assert client.portal is not None
        detached_started = threading.Event()
        detached_cancelled = threading.Event()
        event_detached_states: list[bool] = []
        event_task_states: list[tuple[bool, bool, bool]] = []

        async def old_graph_detached_work() -> None:
            detached_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                detached_cancelled.set()
                raise

        async def install_detached_work() -> asyncio.Task[Any]:
            task = context.task_registry.track(
                "old_graph_detached",
                old_graph_detached_work(),
            )
            await asyncio.sleep(0)
            return task

        detached_task = client.portal.call(install_detached_work)
        assert detached_started.wait(timeout=2)

        async def publish(event: dict[str, Any]) -> bool:
            if event.get("type") == "config_reloaded":
                event_detached_states.append(detached_cancelled.is_set())
                tasks = (
                    app.state.refresh_task,
                    app.state.account_sync_task,
                    app.state.auto_update_task,
                )
                event_task_states.append(
                    tuple(task is not None and not task.done() for task in tasks)
                )
            return True

        monkeypatch.setattr(context.event_hub, "publish", publish)
        current = client.get("/api/model-config").json()
        changed = replace(
            _native_models(),
            chat=replace(_native_models().chat, timeout_seconds=124),
        )

        response = client.put(
            "/api/model-config",
            json=_put_payload(current["revision"], changed),
        )
        cancelled_before_cleanup = detached_cancelled.is_set()
        detached_count_before_cleanup = context.task_registry.stats().get(
            "old_graph_detached",
            0,
        )

        async def cleanup_detached_work() -> None:
            if not detached_task.done():
                detached_task.cancel()
            await asyncio.gather(detached_task, return_exceptions=True)

        client.portal.call(cleanup_detached_work)

        assert response.status_code == 200, response.text
        assert cancelled_before_cleanup is True
        assert detached_count_before_cleanup == 0
        assert event_detached_states == [True]
        assert event_task_states == [(True, True, True)]


def test_model_cutover_serializes_with_guided_restart_without_orphaned_loops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A guided-style restart cannot interleave a second complete loop set."""
    from openbiliclaw.api.model_config_routes import _AppModelRuntimeCoordinator

    app, _config_path = _make_production_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        context = app.state.runtime_context
        assert client.portal is not None
        loop_slots = ("refresh_task", "account_sync_task", "auto_update_task")
        loop_names = ("refresh_loop", "account_sync_loop", "auto_update_loop")
        event_slots: list[tuple[object, object, object]] = []

        async def publish(event: dict[str, Any]) -> bool:
            if event.get("type") == "config_reloaded":
                event_slots.append(tuple(getattr(app.state, slot) for slot in loop_slots))
            return True

        monkeypatch.setattr(context.event_hub, "publish", publish)
        monkeypatch.setattr(context, "background_llm_work_allowed", lambda: False)

        async def overlap_restarts() -> tuple[
            tuple[object, object, object],
            tuple[tuple[asyncio.Task[Any], ...], ...],
            int,
            str,
        ]:
            real_cancel_all = context.task_registry.cancel_all
            first_drained = asyncio.Event()
            first_release = asyncio.Event()
            cancel_calls = 0
            guided_restart: asyncio.Task[None] | None = None
            model_cutover: asyncio.Task[object | None] | None = None
            fallback_release: asyncio.Task[None] | None = None

            async def controlled_cancel_all(
                *,
                grace_seconds: float = 1.5,
                exclude: frozenset[str] = frozenset(),
            ) -> int:
                nonlocal cancel_calls
                cancelled = await real_cancel_all(
                    grace_seconds=grace_seconds,
                    exclude=exclude,
                )
                cancel_calls += 1
                if cancel_calls == 1:
                    first_drained.set()
                    await first_release.wait()
                else:
                    # Without lifecycle serialization, the concurrent model
                    # restart reaches a second drain and releases the paused
                    # guided restart after taking the same empty snapshot.
                    first_release.set()
                return cancelled

            async def release_after_scheduler_turns() -> None:
                # With serialization, no second drain can enter until the first
                # restart releases ownership. Keep the harness implementation-
                # independent by eventually releasing the first owner.
                for _ in range(50):
                    if first_release.is_set():
                        return
                    await asyncio.sleep(0)
                first_release.set()

            changed = replace(
                _native_models(),
                chat=replace(_native_models().chat, timeout_seconds=125),
            )
            revision = compute_model_revision(changed)
            candidate = await context.build_model_candidate(changed, revision)
            lifecycle = _AppModelRuntimeCoordinator(
                app,
                context,
                getattr(context.event_hub, "publish", None),
            )
            context.task_registry.cancel_all = controlled_cancel_all
            try:
                guided_restart = asyncio.create_task(context.restart_background_tasks(app))
                await asyncio.wait_for(first_drained.wait(), timeout=5)
                model_cutover = asyncio.create_task(lifecycle.swap_model_candidate(candidate))
                fallback_release = asyncio.create_task(release_after_scheduler_turns())
                await asyncio.gather(guided_restart, model_cutover, fallback_release)

                final_slots = tuple(getattr(app.state, slot) for slot in loop_slots)
                tracked = tuple(
                    tuple(
                        task
                        for task, name in context.task_registry._tasks.items()
                        if name == loop_name and not task.done()
                    )
                    for loop_name in loop_names
                )
                return final_slots, tracked, cancel_calls, revision
            finally:
                first_release.set()
                pending = tuple(
                    task
                    for task in (guided_restart, model_cutover, fallback_release)
                    if task is not None and not task.done()
                )
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                context.task_registry.cancel_all = real_cancel_all

        final_slots, tracked_loop_sets, cancel_calls, revision = client.portal.call(
            overlap_restarts
        )

        assert cancel_calls == 2
        assert len(event_slots) == 1
        assert event_slots[0] == final_slots
        _assert_loop_slots_match_registry(final_slots, tracked_loop_sets)
        assert context.model_bundle is not None
        assert context.model_bundle.revision == revision


def test_model_save_cancelled_while_waiting_for_lifecycle_snapshot_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A save snapshots only after an in-flight lifecycle transition completes."""
    from openbiliclaw.api.model_config_routes import _AppModelRuntimeCoordinator
    from openbiliclaw.model_config.service import (
        CredentialAction,
        ModelConfigSaveRequest,
        ModelConfigService,
    )

    app, config_path = _make_production_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        context = app.state.runtime_context
        assert client.portal is not None
        loop_slots = ("refresh_task", "account_sync_task", "auto_update_task")
        loop_names = ("refresh_loop", "account_sync_loop", "auto_update_loop")
        reload_events: list[dict[str, Any]] = []

        async def publish(event: dict[str, Any]) -> bool:
            if event.get("type") == "config_reloaded":
                reload_events.append(dict(event))
            return True

        monkeypatch.setattr(context.event_hub, "publish", publish)
        monkeypatch.setattr(context, "background_llm_work_allowed", lambda: False)

        async def cancel_waiting_save() -> tuple[
            int,
            bool,
            bool,
            bool,
            bool,
            bool,
            bool,
            tuple[object, object, object],
            tuple[tuple[asyncio.Task[Any], ...], ...],
        ]:
            real_cancel_all = context.task_registry.cancel_all
            first_drained = asyncio.Event()
            first_release = asyncio.Event()
            cancel_calls = 0
            guided_restart: asyncio.Task[None] | None = None
            save_task: asyncio.Task[Any] | None = None
            before_disk = config_path.read_bytes()
            before_state = context.capture_model_runtime_state()
            before_consumers = _runtime_consumer_identities(context)

            async def controlled_cancel_all(
                *,
                grace_seconds: float = 1.5,
                exclude: frozenset[str] = frozenset(),
            ) -> int:
                nonlocal cancel_calls
                cancelled = await real_cancel_all(
                    grace_seconds=grace_seconds,
                    exclude=exclude,
                )
                cancel_calls += 1
                if cancel_calls == 1:
                    first_drained.set()
                    await first_release.wait()
                return cancelled

            lifecycle = _AppModelRuntimeCoordinator(
                app,
                context,
                getattr(context.event_hub, "publish", None),
            )
            restaged = asyncio.Event()
            real_restage = lifecycle.restage_model_candidate

            def record_restage(candidate: object, models: ModelConfig, revision: str) -> object:
                result = real_restage(candidate, models, revision)
                restaged.set()
                return result

            lifecycle.restage_model_candidate = record_restage
            service = ModelConfigService(
                config_path,
                lifecycle,
                precommit_guard=lambda: False,
            )
            snapshot = service.read()
            changed = replace(
                _native_models(),
                chat=replace(_native_models().chat, timeout_seconds=126),
            )
            revision = compute_model_revision(changed)
            actions = {
                item.id: CredentialAction("keep")
                for item in (*changed.chat.connections, *changed.embedding.providers)
            }
            request = ModelConfigSaveRequest(
                revision=snapshot.revision,
                models=changed,
                credential_actions=actions,
            )

            context.task_registry.cancel_all = controlled_cancel_all
            try:
                guided_restart = asyncio.create_task(context.restart_background_tasks(app))
                await asyncio.wait_for(first_drained.wait(), timeout=5)
                save_task = asyncio.create_task(service.save(request))
                await asyncio.wait_for(restaged.wait(), timeout=5)

                # The app coordinator must capture its rollback token under
                # lifecycle ownership before disk replacement or publication.
                # The config writer is already held here; lifecycle code never
                # acquires it, so this is a deliberate one-way lock order.
                for _ in range(100):
                    bundle = context.model_bundle
                    if bundle is not None and bundle.revision == revision:
                        break
                    if save_task.done():
                        break
                    await asyncio.sleep(0)
                candidate_visible_while_waiting = (
                    context.model_bundle is not None and context.model_bundle.revision == revision
                )
                disk_changed_while_waiting = config_path.read_bytes() != before_disk
                calls_before_release = cancel_calls
                done_before_cancel = save_task.done()
                cancel_accepted = save_task.cancel()
                first_release.set()
                await guided_restart
                cancelled_raised = False
                try:
                    await save_task
                except asyncio.CancelledError:
                    cancelled_raised = True

                final_slots = tuple(getattr(app.state, slot) for slot in loop_slots)
                tracked = tuple(
                    tuple(
                        task
                        for task, name in context.task_registry._tasks.items()
                        if name == loop_name and not task.done()
                    )
                    for loop_name in loop_names
                )
                restored = (
                    config_path.read_bytes() == before_disk
                    and _runtime_consumer_identities(context) == before_consumers
                    and context.model_bundle is before_state.model_bundle
                    and context.config is before_state.config
                )
                return (
                    calls_before_release,
                    candidate_visible_while_waiting,
                    disk_changed_while_waiting,
                    done_before_cancel,
                    cancel_accepted,
                    cancelled_raised,
                    restored,
                    final_slots,
                    tracked,
                )
            finally:
                first_release.set()
                pending = tuple(
                    task
                    for task in (guided_restart, save_task)
                    if task is not None and not task.done()
                )
                if pending:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                context.task_registry.cancel_all = real_cancel_all

        (
            calls_before_release,
            candidate_visible_while_waiting,
            disk_changed_while_waiting,
            done_before_cancel,
            cancel_accepted,
            cancelled_raised,
            restored,
            final_slots,
            tracked_loop_sets,
        ) = client.portal.call(cancel_waiting_save)

        assert calls_before_release == 1
        assert candidate_visible_while_waiting is False
        assert disk_changed_while_waiting is False
        assert done_before_cancel is False
        assert cancel_accepted is True
        assert cancelled_raised is True
        assert restored is True
        assert reload_events == []
        _assert_loop_slots_match_registry(final_slots, tracked_loop_sets)


def test_model_save_cancellation_during_task_stop_rolls_back_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Caller cancellation is not mistaken for a cancelled child task."""
    from openbiliclaw.api.model_config_routes import _AppModelRuntimeCoordinator
    from openbiliclaw.model_config.service import (
        CredentialAction,
        ModelConfigSaveRequest,
        ModelConfigService,
    )

    app, config_path = _make_production_app(monkeypatch, tmp_path)
    context = app.state.runtime_context
    reload_events: list[dict[str, Any]] = []

    async def publish(event: dict[str, Any]) -> bool:
        if event.get("type") == "config_reloaded":
            reload_events.append(dict(event))
        return True

    monkeypatch.setattr(context.event_hub, "publish", publish)

    async def run_cancelled_save() -> None:
        async def idle_loop() -> None:
            await asyncio.Future()

        cleanup_tasks: list[asyncio.Task[Any]] = []
        try:
            child_cancelling = asyncio.Event()
            child_release = asyncio.Event()

            async def cancellation_delaying_refresh() -> None:
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    child_cancelling.set()
                    await child_release.wait()
                    raise

            stubborn_refresh = context.task_registry.track(
                "refresh_loop",
                cancellation_delaying_refresh(),
            )
            app.state.refresh_task = stubborn_refresh
            app.state.account_sync_task = context.task_registry.track(
                "account_sync_loop",
                idle_loop(),
            )
            app.state.auto_update_task = context.task_registry.track(
                "auto_update_loop",
                idle_loop(),
            )
            await asyncio.sleep(0)

            before_disk = config_path.read_bytes()
            before_state = context.capture_model_runtime_state()
            before_consumers = _runtime_consumer_identities(context)
            before_app_degraded = (
                app.state.degraded,
                app.state.degraded_reason,
                list(app.state.degraded_issues),
            )
            before_tasks = (
                stubborn_refresh,
                app.state.account_sync_task,
                app.state.auto_update_task,
            )
            lifecycle = _AppModelRuntimeCoordinator(
                app,
                context,
                getattr(context.event_hub, "publish", None),
            )
            service = ModelConfigService(
                config_path,
                lifecycle,
                precommit_guard=lambda: False,
            )
            snapshot = service.read()
            changed = replace(
                _native_models(),
                chat=replace(_native_models().chat, timeout_seconds=122),
            )
            actions = {
                item.id: CredentialAction("keep")
                for item in (
                    *changed.chat.connections,
                    *changed.embedding.providers,
                )
            }
            request = ModelConfigSaveRequest(
                revision=snapshot.revision,
                models=changed,
                credential_actions=actions,
            )

            save_task = asyncio.create_task(service.save(request))
            await asyncio.wait_for(child_cancelling.wait(), timeout=5)
            assert save_task.cancel()
            child_release.set()
            with pytest.raises(asyncio.CancelledError):
                await save_task

            assert config_path.read_bytes() == before_disk
            assert _runtime_consumer_identities(context) == before_consumers
            assert context.model_bundle is before_state.model_bundle
            assert context.config is before_state.config
            assert context.degraded is before_state.degraded
            assert context.degraded_reason == before_state.degraded_reason
            assert context.degraded_issues is before_state.degraded_issues
            assert (
                app.state.degraded,
                app.state.degraded_reason,
                app.state.degraded_issues,
            ) == before_app_degraded
            restored_tasks = (
                app.state.refresh_task,
                app.state.account_sync_task,
                app.state.auto_update_task,
            )
            assert all(task is not None for task in restored_tasks)
            assert all(
                restored is not old
                for restored, old in zip(restored_tasks, before_tasks, strict=True)
            )
            assert restored_tasks[0] is not None and not restored_tasks[0].done()
            assert restored_tasks[1] is not None and not restored_tasks[1].done()
            assert reload_events == []
        finally:
            for slot in ("refresh_task", "account_sync_task", "auto_update_task"):
                task = getattr(app.state, slot, None)
                setattr(app.state, slot, None)
                if isinstance(task, asyncio.Task):
                    task.cancel()
                    cleanup_tasks.append(task)
            if cleanup_tasks:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            await context.task_registry.cancel_all()

    asyncio.run(run_cancelled_save())


def test_model_put_restart_failure_rolls_back_disk_graph_tasks_and_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed new-task activation restores the exact old graph before reply."""
    app, config_path = _make_production_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        context = app.state.runtime_context
        before = client.get("/api/model-config").json()
        before_disk = config_path.read_bytes()
        old_consumers = _runtime_consumer_identities(context)
        restart_consumers: list[tuple[object, ...]] = []
        reload_events: list[dict[str, Any]] = []

        async def fail_then_restore(app_arg: object, **kwargs: object) -> None:
            del kwargs
            assert app_arg is app
            restart_consumers.append(_runtime_consumer_identities(context))
            if len(restart_consumers) == 1:
                raise RuntimeError("new background task failed")

        async def publish(event: dict[str, Any]) -> bool:
            if event.get("type") == "config_reloaded":
                reload_events.append(dict(event))
            return True

        monkeypatch.setattr(context, "restart_background_tasks", fail_then_restore)
        monkeypatch.setattr(context.event_hub, "publish", publish)
        changed = replace(
            _native_models(),
            chat=replace(_native_models().chat, timeout_seconds=120),
        )

        response = client.put(
            "/api/model-config",
            json=_put_payload(before["revision"], changed),
        )

        assert response.status_code == 400
        assert response.json()["errors"][0]["code"] == "runtime_swap_failed"
        assert response.json()["rollback_applied"] is True
        assert config_path.read_bytes() == before_disk
        assert _runtime_consumer_identities(context) == old_consumers
        assert len(restart_consumers) == 2
        assert restart_consumers[0] != old_consumers
        assert restart_consumers[1] == old_consumers
        assert reload_events == []


def test_stale_put_returns_latest_snapshot_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, config_path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    before = config_path.read_bytes()
    payload = _put_payload("stale-revision", _native_models())

    response = client.put("/api/model-config", json=payload)

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "revision_conflict"
    assert body["latest_revision"] == current["revision"]
    assert body["latest"]["revision"] == current["revision"]
    assert config_path.read_bytes() == before


def test_put_model_config_env_action_persists_only_variable_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PRIMARY_MODEL_API_KEY", "resolved-secret-never-persisted")
    client, config_path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    payload = _put_payload(current["revision"], _native_models())
    payload["models"]["chat"]["connections"][0]["credential"] = _credential(
        "env", "PRIMARY_MODEL_API_KEY"
    )

    response = client.put("/api/model-config", json=payload)

    assert response.status_code == 200, response.text
    saved = load_config(config_path).models.chat.connections[0].credential
    assert saved.source == "env"
    assert saved.value == "PRIMARY_MODEL_API_KEY"
    text = config_path.read_text(encoding="utf-8")
    assert "PRIMARY_MODEL_API_KEY" in text
    assert "resolved-secret-never-persisted" not in text
    public = response.json()["snapshot"]["models"]["chat"]["connections"][0]["credential"]
    assert public["env_name"] == "PRIMARY_MODEL_API_KEY"
    assert "value" not in public


def test_model_put_fieldizes_domain_validation_and_never_echoes_submitted_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, config_path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    before = config_path.read_bytes()
    payload = _put_payload(current["revision"], _native_models())
    payload["models"]["chat"]["connections"][0]["base_url"] = "file:///tmp/secret"
    payload["models"]["chat"]["connections"][0]["credential"] = _credential("set", _INLINE_SECRET)

    response = client.put("/api/model-config", json=payload)

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_failed"
    assert body["errors"][0]["path"].endswith(".base_url")
    assert _INLINE_SECRET not in response.text
    assert config_path.read_bytes() == before


def test_masked_secret_and_extra_field_validation_responses_do_not_echo_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    payload = _put_payload(current["revision"], _native_models())
    payload["models"]["chat"]["connections"][0]["credential"] = _credential("set", "sk-****-masked")

    masked = client.put("/api/model-config", json=payload)
    extra = client.put(
        "/api/model-config",
        json={**payload, "raw_secret": _INLINE_SECRET},
    )

    assert masked.status_code == 400
    assert masked.json()["errors"][0]["code"] == "masked_credential_value"
    assert "sk-****-masked" not in masked.text
    assert extra.status_code == 422
    assert _INLINE_SECRET not in extra.text


def test_exact_chat_probe_uses_only_draft_and_does_not_persist_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, object]] = []

    async def probe(self: object, draft: object, settings: object = None) -> object:
        calls.append((draft, settings))
        return ModelConfigProbeResult(ok=True, connection_id="draft-chat", capability="chat")

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        probe,
    )
    client, config_path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    before = config_path.read_bytes()
    draft = replace(
        _native_models().chat.connections[0],
        id="draft-chat",
        model="unsaved-model",
    )

    response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "chat",
            "revision": current["revision"],
            "connection": _chat_payload(draft, action="set", value="unsaved-secret"),
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "connection_id": "draft-chat",
        "capability": "chat",
        "observed_dimension": 0,
        "error_code": "",
        "message": "",
        "probed_at": response.json()["probed_at"],
        "revision": current["revision"],
    }
    assert response.json()["probed_at"].endswith("Z")
    assert len(calls) == 1
    called_draft = cast("ChatConnection", calls[0][0])
    assert called_draft.id == "draft-chat"
    assert called_draft.model == "unsaved-model"
    assert called_draft.credential.value == "unsaved-secret"
    assert config_path.read_bytes() == before


def test_exact_embedding_probe_passes_shared_settings_and_never_persists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, object]] = []

    async def probe(self: object, draft: object, settings: object = None) -> object:
        calls.append((draft, settings))
        return ModelConfigProbeResult(
            ok=True,
            connection_id="embedding-draft",
            capability="embedding",
            observed_dimension=768,
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        probe,
    )
    client, config_path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    before = config_path.read_bytes()
    provider = replace(
        _native_models().embedding.providers[0],
        id="embedding-draft",
    )

    response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "embedding",
            "revision": current["revision"],
            "provider": _embedding_payload(provider, action="set", value="unsaved-embed-key"),
            "settings": {
                "model": "shared-model",
                "output_dimensionality": 768,
                "similarity_threshold": 0.8,
                "multimodal_enabled": True,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["observed_dimension"] == 768
    assert len(calls) == 1
    called_provider = cast("EmbeddingProviderConfig", calls[0][0])
    called_settings = cast("EmbeddingModelSettings", calls[0][1])
    assert called_provider.id == "embedding-draft"
    assert called_settings.model == "shared-model"
    assert called_settings.multimodal_enabled is True
    assert config_path.read_bytes() == before


def test_embedding_probe_with_unsaved_shared_settings_is_not_live_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.llm.route import CircuitTable

    async def success(self: object, draft: object, settings: object = None) -> object:
        return ModelConfigProbeResult(
            ok=True,
            connection_id=cast("EmbeddingProviderConfig", draft).id,
            capability="embedding",
            observed_dimension=768,
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        success,
    )
    client, _path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    revision = current["revision"]
    ctx = client.app.state.runtime_context
    circuits = CircuitTable()
    circuits.record_failure(
        "embedding-main",
        revision,
        "auth_failed",
        RuntimeError("must-not-appear"),
    )
    old_bundle = ctx.model_bundle
    assert old_bundle is not None
    ctx.model_bundle = replace(
        old_bundle,
        embedding_service=SimpleNamespace(
            _provider=SimpleNamespace(circuits=circuits),
        ),
    )

    response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "embedding",
            "revision": revision,
            "provider": _embedding_payload(_native_models().embedding.providers[0]),
            "settings": {
                "model": "unsaved-shared-model",
                "output_dimensionality": 768,
                "similarity_threshold": 0.72,
                "multimodal_enabled": True,
            },
        },
    )

    assert response.status_code == 200
    assert circuits.state_for("embedding-main", revision) is not None
    refreshed = client.get("/api/model-config").json()
    assert refreshed["models"]["embedding"]["providers"][0]["probe"] is None


def test_embedding_probe_history_is_cleared_when_only_shared_settings_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def success(self: object, draft: object, settings: object = None) -> object:
        return ModelConfigProbeResult(
            ok=True,
            connection_id=cast("EmbeddingProviderConfig", draft).id,
            capability="embedding",
            observed_dimension=1536,
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        success,
    )
    client, _path = _make_client(monkeypatch, tmp_path)
    models = _native_models()
    current = client.get("/api/model-config").json()
    response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "embedding",
            "revision": current["revision"],
            "provider": _embedding_payload(models.embedding.providers[0]),
            "settings": {
                "model": models.embedding.settings.model,
                "output_dimensionality": models.embedding.settings.output_dimensionality,
                "similarity_threshold": models.embedding.settings.similarity_threshold,
                "multimodal_enabled": models.embedding.settings.multimodal_enabled,
            },
        },
    )
    assert response.status_code == 200
    assert (
        client.get("/api/model-config").json()["models"]["embedding"]["providers"][0]["probe"]
        is not None
    )

    changed_models = replace(
        models,
        embedding=replace(
            models.embedding,
            settings=replace(
                models.embedding.settings,
                similarity_threshold=0.73,
            ),
        ),
    )
    saved = client.put(
        "/api/model-config",
        json=_put_payload(current["revision"], changed_models),
    )

    assert saved.status_code == 200
    assert saved.json()["snapshot"]["models"]["embedding"]["providers"][0]["probe"] is None


def test_probe_programming_failure_is_classified_without_exception_or_secret_echo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "probe-exception-secret"

    async def crash(self: object, draft: object, settings: object = None) -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        crash,
    )
    client, _path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()

    response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "chat",
            "revision": current["revision"],
            "connection": _chat_payload(_native_models().chat.connections[0]),
        },
    )

    assert response.status_code == 200
    assert response.json()["error_code"] == "probe_failed"
    assert secret not in response.text


def test_successful_exact_persisted_probe_closes_only_its_live_circuit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.llm.route import CircuitTable

    async def success(self: object, draft: object, settings: object = None) -> object:
        return ModelConfigProbeResult(
            ok=True,
            connection_id=cast("ChatConnection", draft).id,
            capability="chat",
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        success,
    )
    client, _path = _make_client(monkeypatch, tmp_path)
    ctx = client.app.state.runtime_context
    current = client.get("/api/model-config").json()
    revision = current["revision"]
    circuits = CircuitTable()
    circuits.record_failure("primary-openai", revision, "auth_failed", RuntimeError("first"))
    circuits.record_failure("fallback-openai", revision, "auth_failed", RuntimeError("second"))
    old_bundle = ctx.model_bundle
    assert old_bundle is not None
    ctx.model_bundle = replace(old_bundle, chat_route=SimpleNamespace(circuits=circuits))

    response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "chat",
            "revision": revision,
            "connection": _chat_payload(_native_models().chat.connections[0]),
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert circuits.state_for("primary-openai", revision) is None
    assert circuits.state_for("fallback-openai", revision) is not None


def test_successful_unsaved_draft_probe_does_not_close_live_circuit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.llm.route import CircuitTable

    async def success(self: object, draft: object, settings: object = None) -> object:
        return ModelConfigProbeResult(
            ok=True,
            connection_id=cast("ChatConnection", draft).id,
            capability="chat",
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        success,
    )
    client, _path = _make_client(monkeypatch, tmp_path)
    ctx = client.app.state.runtime_context
    current = client.get("/api/model-config").json()
    revision = current["revision"]
    circuits = CircuitTable()
    circuits.record_failure("primary-openai", revision, "auth_failed", RuntimeError("first"))
    old_bundle = ctx.model_bundle
    assert old_bundle is not None
    ctx.model_bundle = replace(old_bundle, chat_route=SimpleNamespace(circuits=circuits))
    unsaved = replace(
        _native_models().chat.connections[0],
        id="unsaved-new-id",
    )

    response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "chat",
            "revision": revision,
            "connection": _chat_payload(unsaved, action="set", value="unsaved-key"),
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert circuits.state_for("primary-openai", revision) is not None


def test_probe_summary_survives_reorder_by_stable_id_but_not_record_edit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def success(self: object, draft: object, settings: object = None) -> object:
        return ModelConfigProbeResult(
            ok=True,
            connection_id=cast("ChatConnection", draft).id,
            capability="chat",
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        success,
    )
    client, _path = _make_client(monkeypatch, tmp_path)
    models = _native_models()
    first = client.get("/api/model-config").json()
    probe = client.post(
        "/api/model-config/probe",
        json={
            "kind": "chat",
            "revision": first["revision"],
            "connection": _chat_payload(models.chat.connections[0]),
        },
    )
    probed_at = probe.json()["probed_at"]

    reordered = replace(
        models,
        chat=replace(
            models.chat,
            connections=(
                models.chat.connections[1],
                models.chat.connections[0],
                models.chat.connections[2],
            ),
        ),
    )
    saved = client.put(
        "/api/model-config",
        json=_put_payload(first["revision"], reordered),
    ).json()
    after_reorder = saved["snapshot"]["models"]["chat"]["connections"][1]
    assert after_reorder["id"] == "primary-openai"
    assert after_reorder["probe"]["probed_at"] == probed_at
    assert after_reorder["probe"]["revision"] == first["revision"]

    edited = replace(
        reordered,
        chat=replace(
            reordered.chat,
            connections=(
                reordered.chat.connections[0],
                replace(reordered.chat.connections[1], model="edited-model"),
                reordered.chat.connections[2],
            ),
        ),
    )
    edited_response = client.put(
        "/api/model-config",
        json=_put_payload(saved["revision"], edited),
    )

    assert edited_response.status_code == 200
    edited_record = edited_response.json()["snapshot"]["models"]["chat"]["connections"][1]
    assert edited_record["probe"] is None


def test_unsaved_draft_probe_does_not_evict_exact_persisted_probe_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def probe(self: object, draft: object, settings: object = None) -> object:
        connection = cast("ChatConnection", draft)
        if connection.model == "edited-unsaved-model":
            return ModelConfigProbeResult(
                ok=False,
                connection_id=connection.id,
                capability="chat",
                error_code="probe_failed",
                message="The exact model draft probe failed.",
            )
        return ModelConfigProbeResult(
            ok=True,
            connection_id=connection.id,
            capability="chat",
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        probe,
    )
    client, _path = _make_client(monkeypatch, tmp_path)
    models = _native_models()
    current = client.get("/api/model-config").json()
    exact = client.post(
        "/api/model-config/probe",
        json={
            "kind": "chat",
            "revision": current["revision"],
            "connection": _chat_payload(models.chat.connections[0]),
        },
    )
    exact_timestamp = exact.json()["probed_at"]
    edited = replace(models.chat.connections[0], model="edited-unsaved-model")

    draft_response = client.post(
        "/api/model-config/probe",
        json={
            "kind": "chat",
            "revision": current["revision"],
            "connection": _chat_payload(edited),
        },
    )

    assert draft_response.status_code == 200
    assert draft_response.json()["ok"] is False
    persisted = client.get("/api/model-config").json()["models"]["chat"]["connections"][0]
    assert persisted["probe"]["ok"] is True
    assert persisted["probe"]["probed_at"] == exact_timestamp


def test_probe_keep_requires_same_stable_id_and_current_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    draft = replace(_native_models().chat.connections[0], id="brand-new")
    body = {
        "kind": "chat",
        "revision": current["revision"],
        "connection": _chat_payload(draft),
    }

    unknown = client.post("/api/model-config/probe", json=body)
    stale = client.post(
        "/api/model-config/probe",
        json={**body, "revision": "stale"},
    )

    assert unknown.status_code == 400
    assert unknown.json()["errors"][0]["code"] == "credential_action_required"
    assert stale.status_code == 409
    assert stale.json()["error"] == "revision_conflict"


def test_probe_waiting_for_gate_never_resolves_keep_from_a_newer_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Revision A cannot borrow revision B's credential after gate waiting."""
    calls: list[ChatConnection] = []

    async def probe(self: object, draft: object, settings: object = None) -> object:
        calls.append(cast("ChatConnection", draft))
        return ModelConfigProbeResult(
            ok=True,
            connection_id=cast("ChatConnection", draft).id,
            capability="chat",
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        probe,
    )
    client, config_path = _make_client(monkeypatch, tmp_path)
    ctx = client.app.state.runtime_context
    gate = _BlockingProbeGate()
    ctx.llm_concurrency_gate = gate
    current = client.get("/api/model-config").json()
    body = {
        "kind": "chat",
        "revision": current["revision"],
        "connection": _chat_payload(_native_models().chat.connections[0]),
    }

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/api/model-config/probe", json=body)
        assert gate.entered.wait(timeout=2)
        latest_revision = _replace_primary_credential_on_disk(
            config_path,
            "revision-b-secret-never-probed",
        )
        gate.release.set()
        response = future.result(timeout=5)

    assert response.status_code == 409
    assert response.json()["error"] == "revision_conflict"
    assert response.json()["latest_revision"] == latest_revision
    assert calls == []
    assert "revision-b-secret-never-probed" not in response.text


def test_probe_rechecks_init_after_gate_before_credential_or_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[ChatConnection] = []

    async def probe(self: object, draft: object, settings: object = None) -> object:
        calls.append(cast("ChatConnection", draft))
        return ModelConfigProbeResult(
            ok=True,
            connection_id=cast("ChatConnection", draft).id,
            capability="chat",
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        probe,
    )
    client, _config_path = _make_client(monkeypatch, tmp_path)
    context = client.app.state.runtime_context
    active = {"value": False}
    context._init_coordinator = SimpleNamespace(
        init_active=lambda: active["value"],
    )
    gate = _BlockingProbeGate()
    context.llm_concurrency_gate = gate
    current = client.get("/api/model-config").json()
    body = {
        "kind": "chat",
        "revision": current["revision"],
        "connection": _chat_payload(_native_models().chat.connections[0]),
    }

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/api/model-config/probe", json=body)
        assert gate.entered.wait(timeout=2)
        active["value"] = True
        gate.release.set()
        response = future.result(timeout=5)

    assert response.status_code == 409
    assert response.json()["error"] == "init_running"
    assert calls == []


def test_probe_rechecks_init_after_waiting_for_model_path_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Init may reserve while a probe queues behind a slow model save."""
    from openbiliclaw.model_config import service as service_module
    from openbiliclaw.model_config.service import ModelConfigService

    app, _config_path = _make_production_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        context = app.state.runtime_context
        build_entered = threading.Event()
        build_release = threading.Event()
        gate_entered = threading.Event()
        capture_waiting = threading.Event()
        network_calls: list[str] = []
        credential_merges: list[tuple[str, ...]] = []
        init_active = {"value": False}
        context._init_coordinator = SimpleNamespace(
            init_active=lambda: init_active["value"],
        )
        real_build = context.build_model_candidate
        real_gate = context.llm_concurrency_gate
        real_capture = ModelConfigService.capture_probe
        real_apply = service_module._apply_credential_actions

        class ImmediateProbeGate:
            @asynccontextmanager
            async def slot(self, *, caller: str):
                assert caller == "api.config_probe"
                gate_entered.set()
                yield

        async def build_then_wait(models: ModelConfig, revision: str) -> object:
            candidate = await real_build(models, revision)
            build_entered.set()
            while not build_release.is_set():
                await asyncio.sleep(0.001)
            return candidate

        async def capture_after_route_check(
            self: ModelConfigService,
            draft: ChatConnection | EmbeddingProviderConfig,
            **kwargs: Any,
        ) -> object:
            capture_waiting.set()
            return await real_capture(self, draft, **kwargs)

        def track_credential_merge(
            candidate: ModelConfig,
            persisted: ModelConfig,
            actions: Any,
        ) -> ModelConfig:
            credential_merges.append(tuple(sorted(actions)))
            return real_apply(candidate, persisted, actions)

        async def probe_network(
            draft: ChatConnection | EmbeddingProviderConfig,
            settings: EmbeddingModelSettings | None = None,
        ) -> ModelConfigProbeResult:
            del settings
            network_calls.append(draft.id)
            return ModelConfigProbeResult(
                ok=True,
                connection_id=draft.id,
                capability="chat",
            )

        monkeypatch.setattr(context, "build_model_candidate", build_then_wait)
        monkeypatch.setattr(context, "probe_model_draft", probe_network)
        monkeypatch.setattr(ModelConfigService, "capture_probe", capture_after_route_check)
        monkeypatch.setattr(service_module, "_apply_credential_actions", track_credential_merge)
        current = client.get("/api/model-config").json()
        changed = replace(
            _native_models(),
            chat=replace(_native_models().chat, timeout_seconds=123),
        )
        probe_payload = {
            "kind": "chat",
            "revision": current["revision"],
            "connection": _chat_payload(_native_models().chat.connections[0]),
        }

        pool = ThreadPoolExecutor(max_workers=2)
        save_future = None
        probe_future = None
        try:
            save_future = pool.submit(
                client.put,
                "/api/model-config",
                json=_put_payload(current["revision"], changed),
            )
            assert build_entered.wait(timeout=5)
            credential_merges.clear()
            context.llm_concurrency_gate = ImmediateProbeGate()
            probe_future = pool.submit(
                client.post,
                "/api/model-config/probe",
                json=probe_payload,
            )
            assert gate_entered.wait(timeout=5)
            assert capture_waiting.wait(timeout=5)
            init_active["value"] = True
        finally:
            init_active["value"] = True
            build_release.set()
        try:
            assert save_future is not None and probe_future is not None
            save_response = save_future.result(timeout=10)
            probe_response = probe_future.result(timeout=10)
        finally:
            context.llm_concurrency_gate = real_gate
            pool.shutdown(wait=True, cancel_futures=True)

        assert save_response.status_code == 409
        assert save_response.json()["error"] == "init_running"
        assert probe_response.status_code == 409
        assert probe_response.json()["error"] == "init_running"
        assert credential_merges == []
        assert network_calls == []


def test_probe_completion_is_revalidated_before_history_or_live_circuit_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A captured A probe stays on A and cannot attach after a B-only secret edit."""
    probe_entered = threading.Event()
    probe_release = threading.Event()
    probed_secrets: list[str] = []
    circuit_resets: list[tuple[str, str, str]] = []

    async def probe(self: object, draft: object, settings: object = None) -> object:
        connection = cast("ChatConnection", draft)
        probed_secrets.append(connection.credential.value)
        probe_entered.set()
        while not probe_release.is_set():
            await asyncio.sleep(0.001)
        return ModelConfigProbeResult(
            ok=True,
            connection_id=connection.id,
            capability="chat",
        )

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.RuntimeContext.probe_model_draft",
        probe,
    )
    client, config_path = _make_client(monkeypatch, tmp_path)
    ctx = client.app.state.runtime_context
    monkeypatch.setattr(
        ctx,
        "record_model_probe_success",
        lambda connection_id, capability, revision: circuit_resets.append(
            (connection_id, capability, revision)
        ),
    )
    current = client.get("/api/model-config").json()
    body = {
        "kind": "chat",
        "revision": current["revision"],
        "connection": _chat_payload(_native_models().chat.connections[0]),
    }

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/api/model-config/probe", json=body)
        assert probe_entered.wait(timeout=2)
        latest_revision = _replace_primary_credential_on_disk(
            config_path,
            "revision-b-secret-never-borrowed",
        )
        probe_release.set()
        response = future.result(timeout=5)

    assert response.status_code == 409
    assert response.json()["error"] == "revision_conflict"
    assert response.json()["latest_revision"] == latest_revision
    assert probed_secrets == [_INLINE_SECRET]
    assert circuit_resets == []
    latest = client.get("/api/model-config").json()
    assert latest["models"]["chat"]["connections"][0]["probe"] is None
    assert "revision-b-secret-never-borrowed" not in response.text


def test_get_config_native_projection_is_non_authoritative_limited_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _make_client(monkeypatch, tmp_path)

    ordinary = client.get("/api/config").json()
    revealed = client.get("/api/config?reveal_keys=true").json()

    projection = ordinary["llm"]
    assert projection["authoritative"] is False
    assert projection["read_only"] is True
    assert projection["projection"] == "primary_and_first_fallback"
    assert projection["default_provider"] == "openai"
    # The first fallback is representable in its own legacy custom-gateway
    # bucket. The later Ollama record is never substituted or exposed.
    assert projection["fallback_provider"] == "openai_compatible"
    assert projection["openai"]["model"] == "gpt-4.1-mini"
    assert projection["openai_compatible"]["model"] == "gateway-model"
    assert projection["ollama"]["model"] == ""
    assert projection["openai"]["api_key"] == ""
    assert revealed["llm"]["openai"]["api_key"] == ""
    assert _INLINE_SECRET not in json.dumps(revealed)


def test_legacy_projection_does_not_collapse_duplicate_provider_buckets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    models = _native_models()
    duplicate = replace(
        models.chat.connections[1],
        preset="openai",
        model="must-not-overwrite-primary",
    )
    models = replace(
        models,
        chat=replace(
            models.chat,
            connections=(models.chat.connections[0], duplicate, models.chat.connections[2]),
        ),
    )
    client, _path = _make_client(monkeypatch, tmp_path, models)

    projection = client.get("/api/config").json()["llm"]

    assert projection["default_provider"] == "openai"
    assert projection["fallback_provider"] == ""
    assert projection["openai"]["model"] == "gpt-4.1-mini"
    assert projection["ollama"]["model"] == ""


def test_legacy_config_put_cannot_overwrite_native_model_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, config_path = _make_client(monkeypatch, tmp_path)
    before = client.get("/api/model-config").json()
    before_disk = config_path.read_bytes()

    response = client.put(
        "/api/config",
        json={
            "language": "en",
            "llm": {
                "default_provider": "ollama",
                "embedding": {"provider": "ollama", "model": "other"},
                "soul": {"provider": "ollama", "model": "other"},
            },
            "reset_fields": ["llm.openai.api_key"],
        },
    )
    after = client.get("/api/model-config").json()

    assert response.status_code == 200, response.text
    assert response.json()["warnings"] == ["model_config_not_updated"]
    assert after["revision"] == before["revision"]
    assert after["models"] == before["models"]
    assert load_config(config_path).language == "en"
    assert b'language = "en"' in config_path.read_bytes()
    assert config_path.read_bytes() != before_disk


def test_legacy_probe_endpoint_retains_only_network_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "llm", "config": {"llm": {"default_provider": "openai"}}},
    )

    assert response.status_code == 422


def test_model_endpoints_refuse_mutation_during_active_init(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _make_client(monkeypatch, tmp_path)
    current = client.get("/api/model-config").json()
    client.app.state.runtime_context._init_coordinator = SimpleNamespace(init_active=lambda: True)

    response = client.put(
        "/api/model-config",
        json=_put_payload(current["revision"], _native_models()),
    )

    assert response.status_code == 409
    assert response.json()["error"] == "init_running"


def test_model_save_rechecks_init_inside_canonical_precommit_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Init winning during candidate build prevents disk, runtime, and event changes."""
    app, config_path = _make_production_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        context = app.state.runtime_context
        active = {"value": False}
        context._init_coordinator = SimpleNamespace(
            init_active=lambda: active["value"],
        )
        build_entered = threading.Event()
        build_release = threading.Event()
        real_build = context.build_model_candidate

        async def build_then_wait(models: ModelConfig, revision: str) -> object:
            candidate = await real_build(models, revision)
            build_entered.set()
            while not build_release.is_set():
                await asyncio.sleep(0.001)
            return candidate

        reload_events: list[dict[str, Any]] = []

        async def publish(event: dict[str, Any]) -> bool:
            if event.get("type") == "config_reloaded":
                reload_events.append(dict(event))
            return True

        monkeypatch.setattr(context, "build_model_candidate", build_then_wait)
        monkeypatch.setattr(context.event_hub, "publish", publish)
        current = client.get("/api/model-config").json()
        before_disk = config_path.read_bytes()
        before_consumers = _runtime_consumer_identities(context)
        changed = replace(
            _native_models(),
            chat=replace(_native_models().chat, timeout_seconds=121),
        )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                client.put,
                "/api/model-config",
                json=_put_payload(current["revision"], changed),
            )
            assert build_entered.wait(timeout=3)
            active["value"] = True
            build_release.set()
            response = future.result(timeout=10)

        assert response.status_code == 409
        assert response.json()["error"] == "init_running"
        assert config_path.read_bytes() == before_disk
        assert _runtime_consumer_identities(context) == before_consumers
        assert reload_events == []


def test_openapi_and_validation_error_shapes_contain_no_raw_secret_field_names_or_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _path = _make_client(monkeypatch, tmp_path)

    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    dedicated_response_names = [
        name
        for name in schemas
        if name.startswith("ModelConfig")
        or name.startswith("PublicCredential")
        or name.startswith("ChatConnectionOut")
        or name.startswith("EmbeddingProviderOut")
    ]
    response_schema_text = json.dumps(
        {name: schemas[name] for name in dedicated_response_names if not name.endswith("In")}
    )
    for forbidden in ("api_key", "access_token", "refresh_token", _INLINE_SECRET):
        assert forbidden not in response_schema_text
    assert set(schemas["PublicCredentialOut"]["properties"]) == {
        "source",
        "configured",
        "env_name",
        "credential_ref",
        "oauth_logged_in",
    }
    assert schemas["CredentialActionIn"]["properties"]["value"]["writeOnly"] is True
