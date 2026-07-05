"""Classification + pull behavior of llm/ollama_diagnostics (v0.3.155+).

Field context (2026-07-05): a user's bge-m3 returned HTTP 500 for an hour
while the UI offered only a dead retry — these tests pin down that every
failure shape maps to a distinct, actionable code.
"""

from __future__ import annotations

import json

import httpx

from openbiliclaw.llm.ollama_diagnostics import (
    DIAG_ERROR,
    DIAG_MODEL_BROKEN,
    DIAG_MODEL_MISSING,
    DIAG_NOT_RUNNING,
    DIAG_OK,
    diagnose_ollama_embedding,
    native_root,
    pull_ollama_model,
)

BASE_URL = "http://localhost:11434/v1"


def _tags_response(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n} for n in names]})


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_native_root_strips_v1_suffix() -> None:
    assert native_root("http://localhost:11434/v1") == "http://localhost:11434"
    assert native_root("http://localhost:11434/v1/") == "http://localhost:11434"
    assert native_root("http://localhost:11434") == "http://localhost:11434"


async def test_diagnose_not_running_on_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_NOT_RUNNING
    assert "ollama serve" in detail


async def test_diagnose_model_missing_when_absent_from_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return _tags_response("qwen2.5:7b")

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_MISSING
    assert "ollama pull bge-m3" in detail


async def test_diagnose_matches_tag_suffix_variants() -> None:
    """``bge-m3:latest`` in tags satisfies a config that says ``bge-m3``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3:latest")
        return httpx.Response(200, json={"embedding": [0.1, 0.2]})

    code, _ = await diagnose_ollama_embedding(BASE_URL, "bge-m3", transport=_transport(handler))
    assert code == DIAG_OK


async def test_diagnose_model_broken_when_installed_but_500s() -> None:
    """The exact field-log shape: model listed, /api/embeddings 500s forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(500, json={"error": "failed to load model"})

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_MODEL_BROKEN
    assert "500" in detail
    assert "failed to load model" in detail


async def test_diagnose_model_broken_on_empty_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _tags_response("bge-m3")
        return httpx.Response(200, json={"embedding": []})

    code, _ = await diagnose_ollama_embedding(BASE_URL, "bge-m3", transport=_transport(handler))
    assert code == DIAG_MODEL_BROKEN


async def test_diagnose_error_on_unexpected_tags_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "busy"})

    code, detail = await diagnose_ollama_embedding(
        BASE_URL, "bge-m3", transport=_transport(handler)
    )
    assert code == DIAG_ERROR
    assert "busy" in detail


async def test_pull_streams_progress_and_succeeds() -> None:
    lines = [
        {"status": "pulling manifest"},
        {"status": "downloading", "completed": 100, "total": 400},
        {"status": "downloading", "completed": 400, "total": 400},
        {"status": "success"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pull"
        body = "\n".join(json.dumps(line) for line in lines)
        return httpx.Response(200, text=body)

    seen: list[tuple[str, int, int]] = []
    ok, error = await pull_ollama_model(
        BASE_URL,
        "bge-m3",
        on_progress=lambda s, c, t: seen.append((s, c, t)),
        transport=_transport(handler),
    )
    assert ok is True
    assert error == ""
    assert ("downloading", 400, 400) in seen
    assert seen[-1] == ("success", 0, 0)


async def test_pull_surfaces_stream_error_line() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"error": "pull model manifest: file not found"}
        return httpx.Response(200, text=json.dumps(payload))

    ok, error = await pull_ollama_model(BASE_URL, "bge-m3", transport=_transport(handler))
    assert ok is False
    assert "manifest" in error


async def test_pull_reports_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "disk full"})

    ok, error = await pull_ollama_model(BASE_URL, "bge-m3", transport=_transport(handler))
    assert ok is False
    assert "disk full" in error


async def test_pull_without_success_status_is_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"status": "downloading"}))

    ok, error = await pull_ollama_model(BASE_URL, "bge-m3", transport=_transport(handler))
    assert ok is False
    assert error
