"""Drive the retained product journey through real Docker API boundaries."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from uuid import uuid4

API_ORIGIN = f"http://127.0.0.1:{os.environ['OBC_E2E_API_PORT']}"
API = f"{API_ORIGIN}/api/v1"
LITELLM = f"http://127.0.0.1:{os.environ['OBC_E2E_LITELLM_PORT']}"
HEADERS = {"Origin": API_ORIGIN, "X-OBC-Auth": "1"}
LITELLM_HEADERS = {
    "Authorization": f"Bearer {os.environ['LITELLM_MASTER_KEY']}",
}
COOKIE_JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIE_JAR))
ALIASES = {"obc-interactive", "obc-analysis", "obc-embedding"}
DISCOVERY_PHASE = "initial"


def request(
    method: str,
    path: str,
    payload: object | None = None,
    *,
    base: str = API,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    body = None if payload is None else json.dumps(payload).encode()
    request_headers = {**(headers or HEADERS)}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    target = f"{base}{path}"
    try:
        with OPENER.open(
            urllib.request.Request(target, data=body, method=method, headers=request_headers),
            timeout=20,
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def json_request(method: str, path: str, payload: object | None = None) -> Any:
    status, body = request(method, path, payload)
    if not 200 <= status < 300:
        raise AssertionError(f"{method} {path} returned {status}: {body.decode()}")
    return None if not body else json.loads(body)


def litellm_json_request(method: str, path: str, payload: object | None = None) -> Any:
    status, body = request(
        method,
        path,
        payload,
        base=LITELLM,
        headers=LITELLM_HEADERS,
    )
    if not 200 <= status < 300:
        # LiteLLM management responses can contain deployment parameters. Keep
        # failures secret-safe by reporting only the operation and HTTP status.
        raise AssertionError(f"LiteLLM {method} {path} returned {status}")
    return None if not body else json.loads(body)


def authenticate_browser_clients() -> None:
    status = json_request("GET", "/auth/status")
    assert status["enabled"] is True
    assert status["password_configured"] is True
    assert status["authenticated"] is False
    logged_in = json_request(
        "POST",
        "/auth/login",
        {"password": os.environ["OBC_E2E_WEB_PASSWORD"]},
    )
    assert logged_in == {"authenticated": True}
    assert json_request("GET", "/auth/status")["authenticated"] is True

    extension_status, extension_body = request(
        "POST",
        "/auth/extension-token",
        {"key": os.environ["OBC_E2E_EXTENSION_KEY"]},
        headers={"Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )
    assert extension_status == 200, extension_body.decode()
    extension_session = json.loads(extension_body)
    assert extension_session["token"]


def configure_litellm_aliases() -> None:
    deployments = (
        {
            "model_name": "obc-interactive",
            "litellm_params": {
                "model": "openai/fake-chat",
                "api_base": "http://fake-provider:8080/v1",
                "api_key": "os.environ/OBC_E2E_FAKE_PROVIDER_KEY",
            },
        },
        {
            "model_name": "obc-analysis",
            "litellm_params": {
                "model": "openai/fake-chat",
                "api_base": "http://fake-provider:8080/v1",
                "api_key": "os.environ/OBC_E2E_FAKE_PROVIDER_KEY",
            },
        },
        {
            "model_name": "obc-embedding",
            "model_info": {"mode": "embedding"},
            "litellm_params": {
                "model": "openai/fake-embedding",
                "api_base": "http://fake-provider:8080/v1",
                "api_key": "os.environ/OBC_E2E_FAKE_PROVIDER_KEY",
            },
        },
    )
    for deployment in deployments:
        litellm_json_request("POST", "/model/new", deployment)


def wait_for_alias_health() -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        health = json_request("GET", "/system/ai-health")
        if {item["alias"] for item in health["aliases"]} == ALIASES and all(
            item["available"] for item in health["aliases"]
        ):
            return health
        time.sleep(0.5)
    raise TimeoutError("LiteLLM aliases did not become healthy after database configuration")


def configure_aliases_phase() -> None:
    authenticate_browser_clients()
    assert json_request("GET", "/system/readiness")["ready"] is True
    unconfigured = json_request("GET", "/system/ai-health")
    assert unconfigured["proxy_reachable"] is True
    assert {item["alias"] for item in unconfigured["aliases"]} == ALIASES
    assert not any(item["available"] for item in unconfigured["aliases"])

    configure_litellm_aliases()
    aliases = wait_for_alias_health()
    assert aliases["proxy_reachable"] is True
    print("LiteLLM aliases configured through the database-backed management API")


def source_items(operation: str) -> list[dict[str, object]]:
    if operation == "bootstrap_import":
        return [
            {
                "id": "bootstrap-architecture",
                "title": "Deterministic backend architecture",
                "url": "https://www.zhihu.com/question/bootstrap-architecture",
                "scope": "like",
            }
        ]
    start = 1 if DISCOVERY_PHASE == "initial" else 7
    titles = {
        1: "Graph database internals",
        7: "Graph database scaling patterns",
        8: "Typed API boundary design",
    }
    descriptions = {
        8: "API contracts with strict validation",
    }
    return [
        {
            "id": f"candidate-{index}",
            "title": titles.get(index, f"Deterministic backend candidate {index}"),
            "url": f"https://www.zhihu.com/question/candidate-{index}",
            "description": descriptions.get(index, f"Typed modular backend example {index}"),
            "author": {"name": "E2E Author"},
        }
        for index in range(start, start + 6)
    ]


def complete_pending_source_task() -> bool:
    query = urllib.parse.urlencode({"source_id": "zhihu", "wait_seconds": 0})
    status, body = request("GET", f"/source-tasks/claim?{query}")
    if status == 204:
        return False
    if status != 200:
        raise AssertionError(f"source task claim returned {status}: {body.decode()}")
    task = json.loads(body)
    operation = task["payload"]["operation"]
    completion = {
        "lease_token": task["lease_token"],
        "result": {"operation": operation, "items": source_items(operation)},
    }
    json_request("POST", f"/source-tasks/{task['id']}/complete", completion)
    return True


def wait_for_onboarding() -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        complete_pending_source_task()
        settings = json_request("GET", "/onboarding")
        if settings["onboarding_complete"]:
            return
        jobs = json_request("GET", "/jobs")
        failed = [job for job in jobs if job["status"] == "failed"]
        if failed:
            raise AssertionError(f"onboarding job failed: {failed}")
        time.sleep(0.2)
    raise TimeoutError("onboarding did not complete")


def wait_for_job(run_id: str, *, complete_source_tasks: bool = False) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if complete_source_tasks:
            complete_pending_source_task()
        job = json_request("GET", f"/jobs/{run_id}")
        if job["status"] == "succeeded":
            return job
        if job["status"] in {"failed", "cancelled"}:
            raise AssertionError(f"job terminated: {job}")
        time.sleep(0.2)
    raise TimeoutError(f"job did not complete: {run_id}")


def main() -> None:
    global DISCOVERY_PHASE

    authenticate_browser_clients()
    assert json_request("GET", "/system/readiness")["ready"] is True
    aliases = wait_for_alias_health()
    assert aliases["proxy_reachable"] is True

    manifests = json_request("GET", "/sources")
    assert len(manifests) == 7
    json_request(
        "PATCH",
        "/settings",
        {
            "sources": {"enabled": {"zhihu": True}},
            "feed": {
                "low_watermark": 1,
                "high_watermark": 3,
                "candidate_multiplier": 2,
                "max_batch_candidates": 10,
                "max_per_source": 10,
                "max_per_topic": 10,
            },
        },
    )
    root = json_request("POST", "/onboarding/start", {"source_ids": ["zhihu"]})
    assert root["job_name"] == "source_sync"
    wait_for_onboarding()

    profile = json_request("GET", "/profile")
    assert profile["facets"][0]["evidence_ids"]
    feed_before = json_request("GET", "/feed")
    assert len(feed_before) == 3
    first_id = feed_before[0]["content"]["id"]
    second_id = feed_before[1]["content"]["id"]
    json_request(
        "POST",
        "/interactions",
        {"content_id": first_id, "kind": "negative"},
    )
    feed_after = json_request("GET", "/feed")
    assert feed_after[0]["content"]["id"] == second_id
    assert feed_after[0]["content"]["id"] != first_id

    projection = json_request(
        "POST",
        "/jobs",
        {"job_name": "profile_projection", "idempotency_key": "e2e-negative-feedback"},
    )
    wait_for_job(projection["id"])
    learned_profile = json_request("GET", "/profile")
    assert learned_profile["revision"] == profile["revision"] + 1
    avoidances = [
        facet["value"] for facet in learned_profile["facets"] if facet["name"] == "avoidances"
    ]
    assert any("Graph database internals" in value for value in avoidances)

    for item in feed_before:
        json_request(
            "POST",
            "/interactions",
            {"content_id": item["content"]["id"], "kind": "open"},
        )
    DISCOVERY_PHASE = "later"
    replenish = json_request(
        "POST",
        "/jobs",
        {"job_name": "feed_replenishment", "idempotency_key": "e2e-later-candidates"},
    )
    wait_for_job(replenish["id"], complete_source_tasks=True)
    later_feed = json_request("GET", "/feed?limit=20")
    new_items = [
        item for item in later_feed if int(item["content"]["external_id"].split("-")[-1]) >= 7
    ]
    assert new_items
    new_order = [(item["content"]["external_id"], item["entry"]["position"]) for item in new_items]
    new_ids = [external_id for external_id, _position in new_order]
    candidate_7 = next((item for item in new_ids if item.endswith("candidate-7")), None)
    candidate_8 = next((item for item in new_ids if item.endswith("candidate-8")), None)
    assert candidate_7 is not None and candidate_8 is not None, new_order
    assert new_ids.index(candidate_8) < new_ids.index(candidate_7), new_order

    conversation_id = str(uuid4())
    status, chat_stream = request(
        "POST",
        "/chat/stream",
        {"conversation_id": conversation_id, "message": "Explain this feed", "learn": True},
    )
    assert status == 200
    chat_events = []
    for block in chat_stream.decode().strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        chat_events.append((fields["event"], json.loads(fields["data"])))
    delta_content = "".join(
        payload["content"] for event, payload in chat_events if event == "delta"
    )
    assert delta_content == "Deterministic Docker E2E chat response."
    assert chat_events[-1][0] == "done"
    history = json_request("GET", f"/chat/{conversation_id}")
    assert [turn["role"] for turn in history["items"]] == ["user", "assistant"]
    assert history["items"][-1]["content"] == delta_content

    for collection in ("favorites", "watch_later"):
        saved = json_request(
            "POST",
            f"/library/{collection}",
            {"content_id": second_id, "note": "Docker E2E"},
        )
        assert saved["collection"] == collection
        assert len(json_request("GET", f"/library/{collection}")) == 1
        removed_status, _removed_body = request("DELETE", f"/library/{collection}/{second_id}")
        assert removed_status == 204
        assert json_request("GET", f"/library/{collection}") == []

    embedding_status, embedding_body = request(
        "POST",
        "/v1/embeddings",
        {"model": "obc-embedding", "input": ["deterministic"]},
        base=LITELLM,
        headers=LITELLM_HEADERS,
    )
    assert embedding_status == 200, embedding_body.decode()
    assert json.loads(embedding_body)["data"][0]["embedding"] == [0.125, 0.25, 0.5, 1.0]

    jobs = json_request("GET", "/jobs")
    completed = {job["job_name"] for job in jobs if job["status"] == "succeeded"}
    assert {"source_sync", "profile_projection", "feed_replenishment"} <= completed
    print(
        "Docker product E2E passed: DB-backed alias setup, source task, profile, "
        "feed ranking, chat, library"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure-litellm", action="store_true")
    arguments = parser.parse_args()
    configure_aliases_phase() if arguments.configure_litellm else main()
