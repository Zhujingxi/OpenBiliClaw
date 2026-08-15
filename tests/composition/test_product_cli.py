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


class Facade:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def list_sources(self, account_id: str | None, limit: int) -> Result:
        self.calls.append(("list_sources", (account_id, limit)))
        return Result(value="sources")

    async def source_status(self, provider: str, account_id: str | None) -> Result:
        self.calls.append(("source_status", (provider, account_id)))
        return Result(value="status")

    async def connect_source(self, command: object) -> Result:
        self.calls.append(("connect_source", (command,)))
        return Result(value="added")

    async def disconnect_source(self, command: object) -> Result:
        self.calls.append(("disconnect_source", (command,)))
        return Result(value="removed")

    async def sync_source(self, provider_id: str) -> Result:
        self.calls.append(("sync_source", (provider_id,)))
        return Result(value="synced")

    async def import_provider_evidence(self, provider_id: str, path: Path) -> Result:
        self.calls.append(("import_provider_evidence", (provider_id, path)))
        return Result(value="imported")

    async def get_recommendations(self, limit: int) -> Result:
        self.calls.append(("get_recommendations", (limit,)))
        return Result(value="feed")

    async def record_feedback_for_shown(
        self, shown_id: str, kind: str, idempotency_key: str, exposed: bool = False
    ) -> Result:
        self.calls.append(("record_feedback_for_shown", (shown_id, kind, idempotency_key, exposed)))
        if shown_id == "bad":
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "shown record not found")
        return Result(value="feedback")

    async def edit_profile(self, command: object) -> Result:
        self.calls.append(("edit_profile", (command,)))
        return Result(value="profile-edited")

    async def show_profile(self, profile_id: str) -> Result:
        self.calls.append(("show_profile", (profile_id,)))
        return Result(value="profile")

    async def assistant_turn(self, request: object, device_id: str) -> Result:
        self.calls.append(("assistant_turn", (request, device_id)))
        return Result(value="assistant")

    async def search_content(self, provider: str, query: str, limit: int) -> Result:
        self.calls.append(("search_content", (provider, query, limit)))
        return Result(value="search")


class Application:
    def __init__(self, facade: Facade) -> None:
        self.services = SimpleNamespace(facade=facade)
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
) -> dict[str, object]:
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
    return cast("dict[str, object]", json.loads(captured.out))


@pytest.mark.parametrize(
    ("args", "call", "value"),
    (
        (("sources", "list"), "list_sources", "sources"),
        (("sources", "status", "bilibili"), "source_status", "status"),
        (("feed", "--limit", "3"), "get_recommendations", "feed"),
        (("profile", "show"), "show_profile", "profile"),
        (("assistant", "hello"), "assistant_turn", "assistant"),
        (("search", "bilibili", "typed systems"), "search_content", "search"),
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
