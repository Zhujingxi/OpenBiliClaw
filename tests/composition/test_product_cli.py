"""Product CLI is a JSON-only thin adapter over the in-process application facade."""

from __future__ import annotations

import json
import sqlite3
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.composition.entrypoints import main

if TYPE_CHECKING:
    from pathlib import Path


class Result(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: str


CONTENT_REF = {
    "provider_id": {"value": "bilibili"},
    "content_kind": {"value": "video"},
    "provider_content_id": "BV1test",
    "canonical_url": "https://www.bilibili.com/video/BV1test",
}


class Facade:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _result(self, name: str, value: str, *args: object) -> Result:
        self.calls.append((name, args))
        return Result(value=value)

    async def list_sources(self, account_id: str | None, limit: int) -> Result:
        return self._result("list_sources", "sources", account_id, limit)

    async def source_status(self, provider: str, account_id: str | None) -> Result:
        return self._result("source_status", "status", provider, account_id)

    async def source_form(self, provider: str, method: str) -> Result:
        return self._result("source_form", "form", provider, method)

    def provider_capabilities(self, provider: str) -> Result:
        return self._result("provider_capabilities", "capabilities", provider)

    def access_recipe(self, provider: str) -> Result:
        return self._result("access_recipe", "recipe", provider)

    async def submit_access_material(self, command: object) -> Result:
        return self._result("submit_access_material", "material", command)

    async def connect_source(self, command: object) -> Result:
        return self._result("connect_source", "added", command)

    async def disconnect_source(self, command: object) -> Result:
        return self._result("disconnect_source", "removed", command)

    async def sync_source(self, provider_id: str) -> Result:
        return self._result("sync_source", "synced", provider_id)

    async def import_provider_evidence(self, provider_id: str, path: Path) -> Result:
        return self._result("import_provider_evidence", "imported", provider_id, path)

    async def get_recommendations(self, limit: int) -> Result:
        return self._result("get_recommendations", "feed", limit)

    async def refresh_recommendations(self, command: object) -> Result:
        return self._result("refresh_recommendations", "refreshed", command)

    async def record_feedback(self, command: object) -> Result:
        return self._result("record_feedback", "recorded-feedback", command)

    async def record_feedback_for_shown(
        self, shown_id: str, kind: str, idempotency_key: str, exposed: bool = False
    ) -> Result:
        if shown_id == "bad":
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "shown record not found")
        return self._result(
            "record_feedback_for_shown", "feedback", shown_id, kind, idempotency_key, exposed
        )

    async def record_observations(self, command: object) -> Result:
        return self._result("record_observations", "observations", command)

    async def edit_profile(self, command: object) -> Result:
        return self._result("edit_profile", "profile-edited", command)

    async def show_profile(self, profile_id: str) -> Result:
        return self._result("show_profile", "profile", profile_id)

    async def assistant_turn(self, request: object, device_id: str) -> Result:
        return self._result("assistant_turn", "assistant", request, device_id)

    async def conversation(self, conversation_id: str, device_id: str) -> Result:
        return self._result("conversation", "conversation", conversation_id, device_id)

    async def conversation_messages(
        self, conversation_id: str, device_id: str, limit: int
    ) -> Result:
        return self._result("conversation_messages", "messages", conversation_id, device_id, limit)

    async def search_content(self, provider: str, query: str, limit: int) -> Result:
        return self._result("search_content", "search", provider, query, limit)

    async def get_content_details(self, reference: str) -> Result:
        return self._result("get_content_details", "details", reference)

    async def propose_action(self, command: object) -> Result:
        return self._result("propose_action", "proposed", command)

    async def confirm_action(self, command: object) -> Result:
        return self._result("confirm_action", "confirmed", command)

    async def reject_action(self, command: object) -> Result:
        return self._result("reject_action", "rejected", command)

    async def job_health(self) -> Result:
        return self._result("job_health", "health")

    async def config_diagnostics(self) -> Result:
        return self._result("config_diagnostics", "config-diagnostics")

    async def model_diagnostics(self) -> Result:
        return self._result("model_diagnostics", "model-diagnostics")


class Events:
    async def replay(self, after: int, limit: int) -> tuple[Result, ...]:
        return (Result(value=f"events:{after}:{limit}"),)


class Application:
    def __init__(self, facade: Facade) -> None:
        self.services = SimpleNamespace(facade=facade)
        self.hosts = SimpleNamespace(dependencies=SimpleNamespace(events=Events(), models=object()))
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False


def invoke(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    facade: Facade,
    tmp_path: Path,
    *args: str,
) -> object:
    application = Application(facade)
    monkeypatch.setattr(
        "openbiliclaw.composition.entrypoints.build_application",
        lambda *_args, **_kwargs: application,
    )
    monkeypatch.setattr(sys, "argv", ["openbiliclaw", *args, "--data-dir", str(tmp_path)])
    main()
    assert not application.started
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


@pytest.mark.parametrize(
    ("args", "call", "value"),
    (
        (("sources", "list"), "list_sources", "sources"),
        (("sources", "status", "bilibili"), "source_status", "status"),
        (("sources", "form", "bilibili", "manual"), "source_form", "form"),
        (
            ("sources", "capabilities", "bilibili"),
            "provider_capabilities",
            "capabilities",
        ),
        (("sources", "access-recipe", "bilibili"), "access_recipe", "recipe"),
        (("feed", "--limit", "3"), "get_recommendations", "feed"),
        (("profile", "show"), "show_profile", "profile"),
        (("assistant", "hello"), "assistant_turn", "assistant"),
        (("conversations", "show", "conv_" + "1" * 32), "conversation", "conversation"),
        (
            ("conversations", "messages", "conv_" + "1" * 32),
            "conversation_messages",
            "messages",
        ),
        (("search", "bilibili", "typed systems"), "search_content", "search"),
        (("content", "detail", json.dumps(CONTENT_REF)), "get_content_details", "details"),
        (("runtime", "health"), "job_health", "health"),
        (
            ("runtime", "config-diagnostics"),
            "config_diagnostics",
            "config-diagnostics",
        ),
        (("runtime", "model-diagnostics"), "model_diagnostics", "model-diagnostics"),
    ),
)
def test_read_commands_emit_one_json_document_and_call_one_workflow(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    args: tuple[str, ...],
    call: str,
    value: str,
) -> None:
    facade = Facade()
    assert invoke(monkeypatch, capsys, facade, tmp_path, *args) == {"value": value}
    assert [name for name, _ in facade.calls] == [call]


def test_mutation_commands_emit_json_and_call_one_workflow(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    facade = Facade()
    output = invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "sources",
        "add",
        "bilibili",
        "builtin.anonymous",
        "--permission",
        "read_public",
        "--idempotency-key",
        "connect-123",
    )
    assert output == {"value": "added"}
    assert facade.calls[0][0] == "connect_source"

    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "sources",
        "remove",
        "bilibili",
        "--idempotency-key",
        "remove-123",
    ) == {"value": "removed"}
    assert facade.calls[-1][0] == "disconnect_source"

    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "sources",
        "sync",
        "bilibili",
    ) == {"value": "synced"}
    assert facade.calls[-1] == ("sync_source", ("bilibili",))

    takeout = tmp_path / "takeout.zip"
    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "import",
        "youtube",
        str(takeout),
    ) == {"value": "imported"}
    assert facade.calls[-1] == ("import_provider_evidence", ("youtube", takeout))

    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "feedback",
        "shown_1",
        "like",
        "--idempotency-key",
        "feedback-123",
    ) == {"value": "feedback"}
    assert facade.calls[-1] == (
        "record_feedback_for_shown",
        ("shown_1", "liked", "feedback-123", False),
    )

    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "profile",
        "exploration",
        "disable",
        "--idempotency-key",
        "profile-explore-123",
    ) == {"value": "profile-edited"}
    assert facade.calls[-1][0] == "edit_profile"
    command = cast("Any", facade.calls[-1][1][0])
    assert command.value == "true"


def test_complete_user_workflows_accept_typed_json_requests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    facade = Facade()

    def request(name: str, payload: object) -> str:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    fields = request("fields", {"cookie": "secret"})
    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "sources",
        "add",
        "bilibili",
        "builtin.manual",
        "--permission",
        "read_public",
        "--fields-file",
        fields,
        "--idempotency-key",
        "connect-json-123",
    ) == {"value": "added"}
    assert cast("Any", facade.calls[-1][1][0]).submission == {"cookie": "secret"}

    material = request(
        "material",
        {
            "artifacts": [
                {
                    "kind": "cookie",
                    "domain": "bilibili.com",
                    "name": "SESSDATA",
                    "value": "secret",
                }
            ]
        },
    )
    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "sources",
        "submit-material",
        "bilibili",
        material,
    ) == {"value": "material"}
    assert facade.calls[-1][0] == "submit_access_material"

    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "refresh",
        "--idempotency-key",
        "refresh-123",
        "--maximum-items",
        "25",
    ) == {"value": "refreshed"}
    assert facade.calls[-1][0] == "refresh_recommendations"

    feedback = request(
        "feedback",
        {
            "idempotency_key": "feedback-json-123",
            "shown_id": "shown_1",
            "content_ref": CONTENT_REF,
            "kind": "liked",
        },
    )
    assert invoke(monkeypatch, capsys, facade, tmp_path, "record-feedback", feedback) == {
        "value": "recorded-feedback"
    }
    assert facade.calls[-1][0] == "record_feedback"

    observation = {
        "observation_id": "obs_" + "1" * 32,
        "idempotency_key": "cli-test-observation",
        "occurred_at": "2030-01-01T00:00:00Z",
        "received_at": "2030-01-01T00:00:00Z",
        "account_id": None,
        "content_ref": None,
        "provenance": {
            "producer_id": "host.cli",
            "source": "host",
            "authenticated": False,
            "trust_level": "low",
            "device_id": None,
        },
        "schema_version": 1,
        "event_type": "content_opened",
        "payload": {"surface": "cli"},
    }
    observations = request(
        "observations",
        {
            "idempotency_key": "observations-123",
            "observations": [observation],
            "allowed_event_types": ["content_opened"],
        },
    )
    assert invoke(monkeypatch, capsys, facade, tmp_path, "observations", observations) == {
        "value": "observations"
    }
    assert facade.calls[-1][0] == "record_observations"

    profile = request(
        "profile",
        {
            "idempotency_key": "profile-json-123",
            "profile_id": "default",
            "account_id": "local",
            "field": "exploration.disabled",
            "operation": "set",
            "value": "true",
        },
    )
    assert invoke(monkeypatch, capsys, facade, tmp_path, "profile", "edit", profile) == {
        "value": "profile-edited"
    }

    proposal = request(
        "proposal",
        {
            "idempotency_key": "proposal-123",
            "action_id": "save",
            "ref": CONTENT_REF,
            "user_id": "local",
            "safe_preview": "Save this video",
        },
    )
    assert invoke(monkeypatch, capsys, facade, tmp_path, "actions", "propose", proposal) == {
        "value": "proposed"
    }
    pending_id = "pending_" + "2" * 32
    assert invoke(monkeypatch, capsys, facade, tmp_path, "actions", "confirm", pending_id) == {
        "value": "confirmed"
    }
    assert invoke(monkeypatch, capsys, facade, tmp_path, "actions", "reject", pending_id) == {
        "value": "rejected"
    }
    assert [name for name, _ in facade.calls[-3:]] == [
        "propose_action",
        "confirm_action",
        "reject_action",
    ]


def test_runtime_events_and_model_configuration_are_json_cli_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    facade = Facade()
    assert invoke(
        monkeypatch,
        capsys,
        facade,
        tmp_path,
        "runtime",
        "events",
        "--after",
        "4",
        "--limit",
        "2",
    ) == [{"value": "events:4:2"}]

    monkeypatch.setattr(
        "openbiliclaw.composition.product_cli.model_catalog",
        lambda _dependencies: Result(value="model-catalog"),
    )
    monkeypatch.setattr(
        "openbiliclaw.composition.product_cli.current_model",
        lambda _dependencies: Result(value="model-current"),
    )
    monkeypatch.setattr(
        "openbiliclaw.composition.product_cli.update_model",
        lambda _request, _dependencies: Result(value="model-updated"),
    )
    assert invoke(monkeypatch, capsys, facade, tmp_path, "models", "catalog") == {
        "value": "model-catalog"
    }
    assert invoke(monkeypatch, capsys, facade, tmp_path, "models", "current") == {
        "value": "model-current"
    }
    model = tmp_path / "model.json"
    model.write_text(
        json.dumps({"provider": "openai", "model_name": "gpt-4.1", "api_key": "secret"}),
        encoding="utf-8",
    )
    assert invoke(monkeypatch, capsys, facade, tmp_path, "models", "set", str(model)) == {
        "value": "model-updated"
    }


def test_secret_bearing_request_validation_never_echoes_input(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    facade = Facade()
    application = Application(facade)
    request = tmp_path / "invalid-material.json"
    request.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "kind": "cookie",
                        "domain": "bilibili.com",
                        "name": "SESSDATA",
                        "value": "SECRET_CANARY" + "x" * 70_000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openbiliclaw.composition.entrypoints.build_application",
        lambda *_args, **_kwargs: application,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openbiliclaw",
            "sources",
            "submit-material",
            "bilibili",
            str(request),
            "--data-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit, match="1"):
        main()

    error = capsys.readouterr().err
    assert "SECRET_CANARY" not in error
    assert json.loads(error) == {
        "error": {"code": "validation", "message": "request validation failed"}
    }
    assert not application.started


def test_youtube_takeout_import_round_trips_real_composition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[content]\nenabled=[]\n", encoding="utf-8")
    takeout = tmp_path / "takeout" / "YouTube and YouTube Music" / "history"
    takeout.mkdir(parents=True)
    (takeout / "watch-history.json").write_text(
        json.dumps(
            [
                {
                    "header": "YouTube",
                    "title": "Watched Typed import",
                    "titleUrl": "https://www.youtube.com/watch?v=abcdefghijk",
                    "time": "2025-01-02T03:04:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    data = tmp_path / "data"
    argv = [
        "openbiliclaw",
        "import",
        "youtube",
        str(tmp_path / "takeout"),
        "--config",
        str(config),
        "--data-dir",
        str(data),
    ]

    monkeypatch.setattr(sys, "argv", argv)
    main()
    assert json.loads(capsys.readouterr().out)["inserted"] == 1
    monkeypatch.setattr(sys, "argv", argv)
    main()
    assert json.loads(capsys.readouterr().out)["duplicates"] == 1
    with sqlite3.connect(data / "openbiliclaw.db") as connection:
        row = connection.execute("SELECT kind,payload_json FROM observations").fetchone()
    assert row is not None and row[0] == "external_history_view"
    assert json.loads(row[1])["payload"]["title"] == "Typed import"


def test_sources_add_and_list_use_real_composition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[content]\nenabled=["bilibili"]\n', encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openbiliclaw",
            "sources",
            "add",
            "bilibili",
            "builtin.anonymous",
            "--permission",
            "read_public",
            "--idempotency-key",
            "connect-real",
            "--config",
            str(config),
            "--data-dir",
            str(data),
        ],
    )
    main()
    connected = json.loads(capsys.readouterr().out)
    assert connected["status"]["state"] == "connected"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openbiliclaw",
            "sources",
            "list",
            "--config",
            str(config),
            "--data-dir",
            str(data),
        ],
    )
    main()
    listed = json.loads(capsys.readouterr().out)
    assert listed["items"][0]["provider_id"] == "bilibili"
    # Existing AccessService connections are process-local. Durable rehydration is
    # intentionally Phase D plugin/access scope, not business logic in this CLI adapter.
    assert listed["items"][0]["state"] == "disconnected"


def test_expected_application_error_is_json_on_stderr_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    facade = Facade()
    application = Application(facade)
    monkeypatch.setattr(
        "openbiliclaw.composition.entrypoints.build_application",
        lambda *_args, **_kwargs: application,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openbiliclaw",
            "feedback",
            "bad",
            "dismiss",
            "--idempotency-key",
            "feedback-123",
            "--data-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as caught:
        main()
    captured = capsys.readouterr()
    assert caught.value.code == 1
    assert json.loads(captured.err) == {
        "error": {"code": "not_found", "message": "shown record not found"}
    }
    assert "Traceback" not in captured.err
    assert not application.started


def test_invalid_input_is_json_on_stderr_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    facade = Facade()
    application = Application(facade)
    monkeypatch.setattr(
        "openbiliclaw.composition.entrypoints.build_application",
        lambda *_args, **_kwargs: application,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openbiliclaw",
            "sources",
            "add",
            "bilibili",
            "builtin.anonymous",
            "--permission",
            "read_public",
            "--idempotency-key",
            "short",
            "--data-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as caught:
        main()
    captured = capsys.readouterr()
    assert caught.value.code == 1
    assert json.loads(captured.err)["error"]["code"] == "validation"
    assert "Traceback" not in captured.err
    assert not application.started
