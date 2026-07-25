"""Socket-level E2E coverage for ordered LLM endpoint instance routing."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.config import Config, LLMConfig, LLMInstanceConfig, ModuleLLMConfig
from openbiliclaw.llm.registry import build_llm_registry
from openbiliclaw.llm.service import (
    LLMProviderExecutionError,
    LLMService,
    module_overrides_from_config,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def _openai_compatible_server(
    *,
    status: int,
    content: str = "",
) -> Iterator[tuple[str, list[str]]]:
    """Run one real local OpenAI-compatible endpoint on an ephemeral port."""

    paths: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body_length = int(self.headers.get("content-length", "0"))
            self.rfile.read(body_length)
            paths.append(self.path)
            if status >= 400:
                payload = {
                    "error": {
                        "message": "intentional endpoint failure",
                        "type": "server_error",
                    }
                }
            else:
                payload = {
                    "id": "chatcmpl-e2e",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "mock-chat",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 3,
                        "total_tokens": 6,
                    },
                }
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _instance(name: str, base_url: str) -> LLMInstanceConfig:
    return LLMInstanceConfig(
        name=name,
        provider_type="openai_compatible",
        enabled=True,
        api_key="test-key",
        model="mock-chat",
        base_url=base_url,
    )


@pytest.mark.asyncio
async def test_real_http_same_type_fallback_and_module_boundary() -> None:
    """Config → registry → SDK → HTTP fallback works without module spill."""

    with (
        _openai_compatible_server(status=503) as (failing_url, failing_paths),
        _openai_compatible_server(status=200, content="backup ok") as (
            healthy_url,
            healthy_paths,
        ),
    ):
        config = Config(
            llm=LLMConfig(
                instance_routing=True,
                instances={
                    "relay-primary": _instance("故障中转", failing_url),
                    "relay-backup": _instance("健康中转", healthy_url),
                },
                default_chain=["relay-primary", "relay-backup"],
                discovery=ModuleLLMConfig(
                    inherit=False,
                    chain=["relay-primary"],
                ),
            )
        )
        registry = build_llm_registry(config)

        response = await registry.complete(
            [{"role": "user", "content": "ping"}],
            max_tokens=8,
            reasoning_effort="",
        )

        assert response.content == "backup ok"
        assert response.instance_id == "relay-backup"
        assert failing_paths
        assert healthy_paths == ["/v1/chat/completions"]

        successful_calls_before_module = len(healthy_paths)
        service = LLMService(
            registry=registry,
            memory=None,  # type: ignore[arg-type]
            module_overrides=module_overrides_from_config(config),
        )
        with pytest.raises(LLMProviderExecutionError):
            await service.complete_with_core_memory(
                system_instruction="Reply briefly.",
                user_input="ping",
                caller="discovery.keyword.e2e",
                max_tokens=8,
                bypass_semaphore=True,
                inject_core_memory=False,
            )

        assert len(healthy_paths) == successful_calls_before_module
        assert len(failing_paths) > 1
