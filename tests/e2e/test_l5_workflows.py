"""L5: the real application loop through a live HTTP server only."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from tests.e2e.server import production_server

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l5]
_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _ROOT / "data-e2e"
_BASE_URL = "http://127.0.0.1:8430/v1"
_MUTATION_HEADERS = {"X-Device-ID": "e2e-l5", "X-CSRF-Token": "e2e-l5"}


@contextmanager
def _model_configuration_server() -> Iterator[Path]:
    run = uuid.uuid4().hex
    config = _DATA_DIR / f"config.model-settings.{run}.toml"
    runtime = _DATA_DIR / f"model-settings-{run}"
    runtime.mkdir(mode=0o700)
    shutil.copy2(_DATA_DIR / "config.e2e.deepseek.toml", config)
    shutil.copy2(_DATA_DIR / "credentials.json", runtime / "credentials.json")
    shutil.copy2(_DATA_DIR / "models.dev.json", runtime / "models.dev.json")
    process = subprocess.Popen(
        [
            str(_ROOT / ".venv/bin/openbiliclaw"),
            "serve",
            "--config",
            str(config),
            "--data-dir",
            str(runtime),
        ],
        cwd=_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(120):
            try:
                if _request("GET", "/runtime/health")[0] == 200:
                    break
            except (OSError, URLError):
                pass
            time.sleep(0.25)
        else:
            pytest.fail("throwaway model-configuration server did not become healthy")
        yield config
    finally:
        process.terminate()
        process.wait(timeout=15)
        config.unlink(missing_ok=True)
        shutil.rmtree(runtime)


def _request(
    method: str, path: str, body: dict[str, object] | None = None
) -> tuple[int, dict[str, object]]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if method != "GET":
        headers.update(_MUTATION_HEADERS)
    request = Request(_BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=125) as response:
            payload = json.loads(response.read())
            assert isinstance(payload, dict)
            return response.status, payload
    except HTTPError as error:
        payload = json.loads(error.read())
        assert isinstance(payload, dict)
        return error.code, payload


def _job(health: dict[str, object], job_id: str) -> dict[str, object] | None:
    snapshot = health.get("health")
    assert isinstance(snapshot, dict)
    jobs = snapshot.get("jobs")
    assert isinstance(jobs, list)
    return next(
        (item for item in jobs if isinstance(item, dict) and item.get("job_id") == job_id),
        None,
    )


def _integer(value: object) -> int:
    assert isinstance(value, int)
    return value


def _wait_for_job(job_id: str, before: int, timeout: float) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, health = _request("GET", "/runtime/health")
        assert status == 200
        current = _job(health, job_id)
        if current is not None and _integer(current["runs_completed"]) > before:
            assert current["last_result"] == "success"
            return current
        # Stay below the host's real 120 requests/minute security limit.
        time.sleep(1)
    pytest.fail(f"job {job_id} did not complete within {timeout}s")


def _feedback_body(item: dict[str, object], key: str) -> dict[str, object]:
    return {
        "idempotency_key": key,
        "shown_id": item["shown_id"],
        "content_ref": item["ref"],
        "kind": "liked",
        "account_id": "e2e",
    }


def _preference_summaries() -> tuple[str, ...]:
    status, response = _request("GET", "/profiles/default")
    assert status == 200
    profile = response.get("profile")
    assert isinstance(profile, dict)
    summaries = profile.get("preference_summary")
    assert isinstance(summaries, list)
    assert all(isinstance(item, str) for item in summaries)
    return tuple(item for item in summaries if isinstance(item, str))


def _claim_id(summary: str) -> str:
    dimension, separator, value = summary.partition(": ")
    assert separator and dimension and value
    normalized = " ".join(f"{dimension}:{value}".casefold().split())
    digest = hashlib.sha256(f"preference:{normalized}".encode()).hexdigest()[:32]
    return f"claim_{digest}"


def _remove_preferences(summaries: tuple[str, ...], run: str) -> None:
    for index, summary in enumerate(summaries):
        status, response = _request(
            "POST",
            "/profiles/edit",
            {
                "idempotency_key": f"e2e:l5:cleanup:{run}:{index}",
                "profile_id": "default",
                "account_id": "e2e",
                "claim_id": _claim_id(summary),
                "operation": "remove",
                "value": None,
            },
        )
        assert status == 200, response


def _cleanup_prior_l5_preferences(run: str) -> None:
    junk = tuple(summary for summary in _preference_summaries() if "HTTP 工作流测试" in summary)
    _remove_preferences(junk, f"prior:{run}")


def _submit_preference(item: dict[str, object], run: str, attempt: int) -> str:
    marker = f"{run[:8]}-{attempt}"
    now = datetime.now(UTC).isoformat()
    observation_id = "obs_" + uuid.uuid4().hex
    statement = (
        "只提取内容主题偏好。内容主题：HTTP 工作流测试 "
        f"{marker}。将标识 {marker} 原样保留在 value 中。维度必须是 content。"
    )
    status, before_health = _request("GET", "/runtime/health")
    assert status == 200
    analysis = _job(before_health, "understanding.analysis")
    before = 0 if analysis is None else _integer(analysis["runs_completed"])
    status, recorded = _request(
        "POST",
        "/observations",
        {
            "idempotency_key": f"e2e:l5:batch:{run}:{attempt}",
            "observations": [
                {
                    "event_type": "preference_statement",
                    "observation_id": observation_id,
                    "idempotency_key": f"e2e:l5:preference:{run}:{attempt}",
                    "occurred_at": now,
                    "received_at": now,
                    "account_id": "e2e",
                    "content_ref": item["ref"],
                    "provenance": {
                        "producer_id": "host.e2e",
                        "source": "assistant",
                        "authenticated": True,
                        "trust_level": "high",
                    },
                    "payload": {"statement": statement},
                    "schema_version": 1,
                }
            ],
            "allowed_event_types": ["preference_statement"],
        },
    )
    assert status == 200
    result = recorded.get("result")
    assert isinstance(result, dict)
    receipts = result.get("items")
    assert isinstance(receipts, list)
    assert any(
        isinstance(receipt, dict) and receipt.get("observation_id") == observation_id
        for receipt in receipts
    )
    _wait_for_job("understanding.analysis", before, 190)
    return marker


def _derive_run_preference(item: dict[str, object], run: str) -> tuple[str, ...]:
    diagnostics: list[str] = []
    for attempt in range(1, 4):
        before = frozenset(_preference_summaries())
        marker = _submit_preference(item, run, attempt)
        after = _preference_summaries()
        created = tuple(summary for summary in after if summary not in before)
        diagnostics.extend(created)
        matched = tuple(summary for summary in created if marker in summary)
        if matched:
            return matched
        _remove_preferences(created, f"retry:{run}:{attempt}")
    pytest.fail(
        "Kimi retained no run marker after three content-scoped attempts; "
        f"derived summaries={diagnostics!r}"
    )


def test_catalog_and_model_configuration_round_trip_uses_throwaway_profile() -> None:
    key_path = _DATA_DIR / "deepseek_api_key.txt"
    assert key_path.is_file()
    key = key_path.read_text(encoding="utf-8").strip()
    try:
        with _model_configuration_server() as config:
            status, catalog = _request("GET", "/models/catalog")
            assert status == 200
            providers = catalog.get("providers")
            assert isinstance(providers, list)
            provider_ids = {item.get("id") for item in providers if isinstance(item, dict)}
            assert {"deepseek", "kimi-for-coding"} <= provider_ids
            status, saved = _request(
                "PUT",
                "/models/current",
                {
                    "provider": "deepseek",
                    "model_name": "deepseek-chat",
                    "api_key": key,
                },
            )
            assert status == 200
            assert saved.get("restart_required") is True
            status, current = _request("GET", "/models/current")
            assert status == 200
            configured = current.get("current")
            assert isinstance(configured, dict)
            model = configured.get("model")
            assert isinstance(model, dict)
            assert model.get("provider") == "deepseek"
            assert model.get("protocol") == "openai"
            assert model.get("secret_configured") is True
            assert key not in json.dumps(current)
            assert key not in config.read_text(encoding="utf-8")
    finally:
        key = ""


def test_live_http_full_loop_profile_shift_and_errors() -> None:
    run = uuid.uuid4().hex
    with production_server(log_name="l5-server.log"):
        _cleanup_prior_l5_preferences(run)
        status, health = _request("GET", "/runtime/health")
        assert status == 200
        assert isinstance(health.get("health"), dict)
        assert _request("GET", "/sources")[0] == 200
        status, bad = _request("GET", "/recommendations?limit=bad")
        assert status == 422
        assert bad.get("error") == {
            "code": "validation",
            "message": "request validation failed",
        }
        # The loopback E2E profile intentionally has no bearer policy: reads are public.
        assert _request("GET", "/profiles/default")[0] == 200

        refresh_before = _job(health, "recommendation.replenishment")
        before_completed = (
            0 if refresh_before is None else _integer(refresh_before["runs_completed"])
        )
        status, refresh = _request(
            "POST",
            "/recommendations/refresh",
            {"idempotency_key": f"e2e:l5:refresh:{run}", "maximum_items": 20},
        )
        assert status == 200 and refresh.get("decision") == "run"
        _wait_for_job("recommendation.replenishment", before_completed, 70)

        status, page = _request("GET", "/recommendations?limit=20")
        assert status == 200
        items = page.get("items")
        assert isinstance(items, list) and items
        item = items[0]
        assert isinstance(item, dict)
        assert isinstance(item.get("shown_id"), str)
        assert isinstance(item.get("reason"), str) and item["reason"].strip()
        selection = item.get("selection")
        assert isinstance(selection, dict) and _integer(selection["rank"]) >= 1

        key = f"e2e:l5:feedback:{run}"
        status, feedback = _request("POST", "/feedback", _feedback_body(item, key))
        assert status == 200
        result = feedback.get("result")
        assert isinstance(result, dict) and result.get("inserted") is True
        observation_id = result.get("observation_id")
        assert isinstance(observation_id, str)
        status, duplicate = _request("POST", "/feedback", _feedback_body(item, key))
        assert status == 200
        duplicate_result = duplicate.get("result")
        assert isinstance(duplicate_result, dict) and duplicate_result.get("inserted") is False
        assert duplicate_result.get("observation_id") == observation_id

        status, unknown = _request(
            "POST",
            "/feedback",
            {**_feedback_body(item, f"e2e:l5:unknown:{run}"), "shown_id": "shown_" + "0" * 32},
        )
        assert status == 404
        unknown_error = unknown.get("error")
        assert isinstance(unknown_error, dict)
        assert unknown_error.get("code") == "not_found"

        run_preferences = _derive_run_preference(item, run)
        assert any(run[:8] in summary for summary in run_preferences)
        _remove_preferences(run_preferences, f"complete:{run}")
        assert not any(run[:8] in summary for summary in _preference_summaries())


def test_live_http_restart_persists_feed_profile_and_feedback() -> None:
    with production_server(log_name="l5-server.log"):
        status, before_feed = _request("GET", "/recommendations?limit=20")
        assert status == 200
        before_items = before_feed.get("items")
        assert isinstance(before_items, list) and before_items
        before_ids = [
            item["selection"]["recommendation_id"]
            for item in before_items
            if isinstance(item, dict) and isinstance(item.get("selection"), dict)
        ]
        assert before_ids
        status, before_profile = _request("GET", "/profiles/default")
        assert status == 200

    with production_server(log_name="l5-server.log"):
        status, after_feed = _request("GET", "/recommendations?limit=20")
        assert status == 200
        after_items = after_feed.get("items")
        assert isinstance(after_items, list)
        after_ids = [
            item["selection"]["recommendation_id"]
            for item in after_items
            if isinstance(item, dict) and isinstance(item.get("selection"), dict)
        ]
        assert after_ids == before_ids
        assert _request("GET", "/profiles/default") == (200, before_profile)
