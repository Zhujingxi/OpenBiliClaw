from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.runtime_context import build_runtime_context
from openbiliclaw.config import Config, save_config
from openbiliclaw.llm.registry import RegistryBuildError
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingRouteConfig,
    ModelConfig,
)


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _invalid_config(tmp_path) -> Config:
    return Config(
        models=ModelConfig(
            chat=ChatRouteConfig(
                connections=(
                    ChatConnection(
                        id="invalid-openai",
                        name="Invalid OpenAI",
                        type="openai_compatible",
                        preset="openai",
                        model="gpt-4o-mini",
                        base_url="https://api.openai.com/v1",
                        api_mode="chat_completions",
                    ),
                )
            )
        ),
        data_dir=str(tmp_path / "data"),
    )


def _valid_config(tmp_path) -> Config:
    return Config(
        models=ModelConfig(
            chat=ChatRouteConfig(
                connections=(
                    ChatConnection(
                        id="valid-openai",
                        name="Valid OpenAI",
                        type="openai_compatible",
                        preset="openai",
                        model="gpt-4o-mini",
                        base_url="https://api.openai.com/v1",
                        credential=CredentialConfig(
                            source="inline",
                            value="sk-valid-openai-key",
                        ),
                        api_mode="chat_completions",
                    ),
                )
            )
        ),
        data_dir=str(tmp_path / "data"),
    )


def _save_project_config(monkeypatch: pytest.MonkeyPatch, tmp_path, cfg: Config) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(cfg, tmp_path / "config.toml")


def _recovery_models() -> ModelConfig:
    return ModelConfig(
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="local-main",
                    name="Local main",
                    type="ollama",
                    model="qwen3:8b",
                    base_url="http://127.0.0.1:11434/v1",
                ),
            ),
            concurrency=2,
            timeout_seconds=30,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=False,
            settings=EmbeddingModelSettings(model="bge-m3"),
        ),
    )


def _model_put_payload(revision: str, models: ModelConfig) -> dict[str, object]:
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
                        "credential": {"action": "keep", "value": ""},
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


def test_build_runtime_context_stays_strict_for_invalid_llm_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)

    with pytest.raises(RegistryBuildError):
        build_runtime_context(_invalid_config(tmp_path))


def test_create_app_boots_degraded_when_registry_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))

    app = create_app()
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["reason"] == "llm_registry_unavailable"
    assert body["issues"]
    assert body["issues"][0]["severity"] == "blocking"


def test_degraded_config_get_includes_recovery_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["degraded_reason"] == "llm_registry_unavailable"
    assert any(issue["severity"] == "blocking" for issue in body["issues"])


def test_degraded_legacy_config_put_cannot_mutate_model_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.put(
        "/api/config",
        json={"llm": {"openai": {"api_key": "sk-new-valid-key"}}},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["reloaded"] is False
    assert body["rollback_applied"] is False
    assert body["warnings"] == ["model_config_not_updated"]
    assert "sk-new-valid-key" not in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_model_config_save_fully_recovers_degraded_runtime_before_reload_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Dedicated model repair opens the API only after its new task graph starts."""
    from openbiliclaw.api import runtime_context as runtime_module

    models = _recovery_models()
    config = Config(data_dir=str(tmp_path / "data"), models=models)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(config, tmp_path / "config.toml", models_authoritative=True)
    real_build_runtime_context = runtime_module.build_runtime_context

    def fail_initial_build(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RegistryBuildError("forced initial registry failure")

    monkeypatch.setattr(runtime_module, "build_runtime_context", fail_initial_build)
    app = create_app()
    monkeypatch.setattr(runtime_module, "build_runtime_context", real_build_runtime_context)

    with TestClient(app) as client:
        context = app.state.runtime_context
        assert context.degraded is True
        assert context.runtime_controller is None
        lifecycle: list[str] = []
        restarted_tasks: list[tuple[object, object, object]] = []
        real_restart = context.restart_background_tasks

        async def restart_recovered_graph(app_arg: Any, **kwargs: object) -> None:
            assert app_arg is app
            assert context.degraded is True
            assert context.runtime_controller is not None
            await real_restart(app_arg, **kwargs)
            restarted_tasks.append(
                (
                    app.state.refresh_task,
                    app.state.account_sync_task,
                    app.state.auto_update_task,
                )
            )
            lifecycle.append("restart")

        async def publish(event: dict[str, Any]) -> bool:
            if event.get("type") == "config_reloaded":
                assert context.degraded is False
                assert app.state.degraded is False
                lifecycle.append("event")
            return True

        monkeypatch.setattr(context, "restart_background_tasks", restart_recovered_graph)
        monkeypatch.setattr(context.event_hub, "publish", publish)
        current = client.get("/api/model-config").json()
        changed = replace(models, chat=replace(models.chat, timeout_seconds=45))

        response = client.put(
            "/api/model-config",
            json=_model_put_payload(current["revision"], changed),
        )

        assert response.status_code == 200, response.text
        assert response.json()["reloaded"] is True
        assert lifecycle == ["restart", "event"]
        assert context.degraded is False
        assert app.state.degraded is False
        assert context.model_bundle is not None
        assert context.runtime_controller is not None
        assert len(restarted_tasks) == 1
        refresh_task, account_task, auto_update_task = restarted_tasks[0]
        assert refresh_task is not None and not refresh_task.done()
        assert account_task is not None and not account_task.done()
        assert auto_update_task is not None
        assert client.get("/api/profile-summary").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/web",
        "/web/",
        "/web/assets/css/app.css",
        "/web/shared/model-config-state.js",
        "/setup/",
        "/m/",
        "/m/js/app.js",
        "/favicon.ico",
    ],
)
def test_degraded_mode_keeps_frontend_shells_and_assets_reachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    path: str,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get(path)

    assert response.status_code == 200


def test_degraded_mode_keeps_favicon_content_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("image/png")


@pytest.mark.parametrize(
    ("method", "path", "json_payload"),
    [
        ("get", "/api/recommendations", None),
        ("get", "/api/profile-summary", None),
        ("post", "/api/events", {"events": []}),
        ("post", "/api/sources/xhs/observed-urls", {"items": []}),
    ],
)
def test_degraded_non_config_endpoints_return_503(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    method: str,
    path: str,
    json_payload: dict[str, object] | None,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    request = getattr(client, method)
    response = request(path, json=json_payload) if json_payload is not None else request(path)

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["reason"] == "llm_registry_unavailable"


def test_degraded_update_status_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Update status must bypass the degraded 503 gate.

    A backend that can't build its model routes is exactly when the user may
    need to pull a fix-carrying release, so ``/api/update-status`` (and manual
    check/apply) stay on the degraded allow-list and the degraded context now
    builds a real ``AutoUpdateService`` to back them.
    """
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/update-status")

    assert response.status_code == 200
    body = response.json()
    assert "backend" in body
    # Not the 503 degraded envelope.
    assert body.get("status") != "degraded"
    assert "install_mode" in body["backend"]


def test_degraded_runtime_stream_sends_degraded_event_and_stays_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    with client.websocket_connect("/api/runtime-stream") as websocket:
        event = websocket.receive_json()
        assert event["type"] == "degraded"
        assert event["reason"] == "llm_registry_unavailable"
        assert event["issues"]


def test_normal_boot_health_payload_reports_profile_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _valid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "openbiliclaw-api"
    assert body["profile_ready"] is False


def test_restart_after_degraded_recovery_config_boots_normal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    degraded_client = TestClient(create_app())

    snapshot = degraded_client.get("/api/model-config").json()
    response = degraded_client.put(
        "/api/model-config",
        json=_model_put_payload(snapshot["revision"], _recovery_models()),
    )

    assert response.status_code == 200
    normal_client = TestClient(create_app())
    health = normal_client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["service"] == "openbiliclaw-api"
    assert health["profile_ready"] is False
