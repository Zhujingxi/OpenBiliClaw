"""L6: build, run, exercise, restart, and tear down the Docker stack."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l6]
_ROOT = Path(__file__).resolve().parents[2]
_PROJECT = "openbiliclaw-e2e-l6"
_BASE_URL = "http://127.0.0.1:18420/v1"
_MUTATION_HEADERS = {"X-Device-ID": "e2e-l6", "X-CSRF-Token": "e2e-l6"}
_BEARER_TOKEN: str | None = None
_COMPOSE_ENV = {
    **os.environ,
    "OPENBILICLAW_API_PORT": "18420",
    "OPENBILICLAW_MODEL_KEY_FILE": str(_ROOT / "data-e2e/kimi_api_key.txt"),
}


def _compose(*arguments: str, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    command = "docker compose --project-name " + _PROJECT + " " + " ".join(arguments)
    return subprocess.run(
        ["sg", "docker", "-c", command],
        cwd=_ROOT,
        env=_COMPOSE_ENV,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _request(
    method: str, path: str, body: dict[str, object] | None = None
) -> tuple[int, dict[str, object]]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if _BEARER_TOKEN is not None:
        headers["Authorization"] = f"Bearer {_BEARER_TOKEN}"
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


def _wait_healthy(timeout: float = 420) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _request("GET", "/runtime/health")[0] == 200:
                return
        except (OSError, URLError):
            pass
        time.sleep(1)
    logs = _compose("logs", "--no-color", timeout=30)
    pytest.fail(f"Docker stack did not become healthy; logs:\n{logs.stdout[-4000:]}")


def _wait_for_job(job_id: str, before: int, timeout: float) -> tuple[dict[str, object], float]:
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        status, health = _request("GET", "/runtime/health")
        assert status == 200
        current = _job(health, job_id)
        if current is not None and _integer(current["runs_completed"]) > before:
            assert current["last_result"] == "success", current
            return current, time.monotonic() - started
        time.sleep(1)
    pytest.fail(f"job {job_id} did not complete within {timeout}s")


def _recommendation_id(item: dict[str, object]) -> str:
    selection = item.get("selection")
    assert isinstance(selection, dict)
    recommendation_id = selection.get("recommendation_id")
    assert isinstance(recommendation_id, str)
    return recommendation_id


def _feed() -> tuple[dict[str, object], ...]:
    status, page = _request("GET", "/recommendations?limit=20")
    assert status == 200
    items = page.get("items")
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return tuple(item for item in items if isinstance(item, dict))


def _load_bearer_token() -> str:
    # Read the throwaway token only through an in-container process. Never emit
    # it in logs, reports, compose config, or docker inspect output.
    script = (
        "import tomllib; from pathlib import Path; "
        "from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend; "
        "from openbiliclaw.infrastructure.credentials.vault import CredentialVault; "
        "c=tomllib.load(open('/app/runtime/config.toml','rb')); "
        "r=c['host']['bearer_secret_ref'].removeprefix('vault:'); "
        "CredentialVault(ProtectedFileBackend(Path('/app/runtime/credentials.json')))"
        ".resolve(r, lambda s: print(bytes(s).decode()))"
    )
    result = _compose(
        "exec", "-T", "openbiliclaw-backend", "python", "-c", repr(script), timeout=30
    )
    assert result.returncode == 0, result.stderr
    token = result.stdout.strip()
    assert token and " " not in token
    return token


def _start() -> None:
    global _BEARER_TOKEN
    result = _compose("up", "-d", "--build", timeout=1800)
    assert result.returncode == 0, result.stderr
    _BEARER_TOKEN = _load_bearer_token()
    _wait_healthy()


def test_docker_build_boot_core_flow_and_persistence() -> None:
    global _BEARER_TOKEN
    run = uuid.uuid4().hex
    # Dedicated Compose project scopes every volume/network/container. Always
    # tear down with -v so this test cannot affect a user deployment.
    _compose("down", "-v", "--remove-orphans", timeout=120)
    try:
        _start()

        assert _BEARER_TOKEN is not None
        spa = urlopen(
            Request(
                "http://127.0.0.1:18420/",
                headers={"Authorization": f"Bearer {_BEARER_TOKEN}"},
            ),
            timeout=10,
        )
        assert spa.status == 200 and spa.read()
        check = _compose(
            "exec",
            "-T",
            "openbiliclaw-backend",
            "openbiliclaw",
            "check",
            "--config",
            "/app/runtime/config.toml",
            "--data-dir",
            "/app/runtime",
            timeout=180,
        )
        assert check.returncode == 0, check.stderr

        status, connected = _request(
            "POST",
            "/sources/connect",
            {
                "provider_id": "bilibili",
                "method_id": "builtin.anonymous",
                "idempotency_key": f"e2e:l6:connect:{run}",
            },
        )
        assert status == 200, connected

        status, health = _request("GET", "/runtime/health")
        assert status == 200
        current = _job(health, "recommendation.replenishment")
        before = 0 if current is None else _integer(current["runs_completed"])
        status, refresh = _request(
            "POST",
            "/recommendations/refresh",
            {"idempotency_key": f"e2e:l6:refresh:{run}", "maximum_items": 20},
        )
        assert status == 200 and refresh.get("decision") == "run"
        _, refresh_seconds = _wait_for_job("recommendation.replenishment", before, 90)
        assert refresh_seconds < 90

        items = _feed()
        assert items
        item = items[0]
        assert isinstance(item.get("shown_id"), str)
        status, feedback = _request(
            "POST",
            "/feedback",
            {
                "idempotency_key": f"e2e:l6:feedback:{run}",
                "shown_id": item["shown_id"],
                "content_ref": item["ref"],
                "kind": "liked",
                "account_id": "e2e",
            },
        )
        assert status == 200
        result = feedback.get("result")
        assert isinstance(result, dict) and result.get("inserted") is True
        selection = item.get("selection")
        assert isinstance(selection, dict)
        interacted_id = selection["recommendation_id"]
        before_ids = tuple(
            _recommendation_id(candidate)
            for candidate in items
            if _recommendation_id(candidate) != interacted_id
        )
        status, before_profile = _request("GET", "/profiles/default")
        assert status == 200

        restart = _compose("restart", "openbiliclaw-backend", timeout=120)
        assert restart.returncode == 0, restart.stderr
        _BEARER_TOKEN = _load_bearer_token()
        _wait_healthy()
        after_ids = tuple(_recommendation_id(candidate) for candidate in _feed())
        assert after_ids == before_ids
        assert _request("GET", "/profiles/default") == (200, before_profile)
        status, duplicate = _request(
            "POST",
            "/feedback",
            {
                "idempotency_key": f"e2e:l6:feedback:{run}",
                "shown_id": item["shown_id"],
                "content_ref": item["ref"],
                "kind": "liked",
                "account_id": "e2e",
            },
        )
        assert status == 200
        duplicate_result = duplicate.get("result")
        assert isinstance(duplicate_result, dict)
        assert duplicate_result.get("inserted") is False

        # Access handles are process-local. After restart, the stored vault has
        # no provider/account mapping, so the source is honestly disconnected;
        # clients must resubmit their credential (or reconnect anonymously).
        status, source = _request("GET", "/sources/bilibili/status")
        assert status == 200
        source_status = source.get("status")
        assert isinstance(source_status, dict)
        assert source_status.get("state") == "disconnected"
        status, reconnected = _request(
            "POST",
            "/sources/connect",
            {
                "provider_id": "bilibili",
                "method_id": "builtin.anonymous",
                "idempotency_key": f"e2e:l6:reconnect:{run}",
            },
        )
        assert status == 200, reconnected
    finally:
        teardown = _compose("down", "-v", "--remove-orphans", timeout=180)
        assert teardown.returncode == 0, teardown.stderr
