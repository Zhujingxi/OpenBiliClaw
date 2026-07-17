"""Credential-free deterministic OpenAI-compatible provider for Docker E2E only."""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from threading import Lock
from typing import Any

_CALL_IDS = count(1)
_CALL_ID_LOCK = Lock()


def _next_call_id() -> str:
    with _CALL_ID_LOCK:
        return f"call_deterministic_e2e_{next(_CALL_IDS)}"


def _prompt_input(payload: dict[str, Any]) -> dict[str, Any]:
    for message in reversed(payload.get("messages", [])):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _structured_output(request: dict[str, Any]) -> dict[str, Any]:
    task_input = _prompt_input(request)
    tools = request.get("tools") or []
    schema: dict[str, Any] = {}
    if tools:
        function = tools[0].get("function", {})
        schema = function.get("parameters", {})
    else:
        response_format = request.get("response_format", {})
        schema = response_format.get("json_schema", {}).get("schema", {})
    properties = schema.get("properties", {})

    if "assessments" in properties:
        revision = task_input.get("profile", {}).get("revision", 0)
        avoidances = " ".join(
            facet.get("value", "").casefold()
            for facet in task_input.get("profile", {}).get("facets", [])
            if facet.get("name") == "avoidances"
        )
        avoidance_terms = {term for term in re.findall(r"[a-z]+", avoidances) if len(term) > 3}
        assessments = []
        for index, item in enumerate(task_input.get("content", [])):
            relevance = max(0.6, 0.95 - index * 0.1)
            content_terms = set(
                re.findall(
                    r"[a-z]+",
                    f"{item.get('title', '')} {item.get('summary', '')}".casefold(),
                )
            )
            semantic_overlap = len(avoidance_terms & content_terms)
            assessments.append(
                {
                    "content_id": item["id"],
                    "profile_revision": revision,
                    "relevance": relevance,
                    "quality": 0.9,
                    "novelty": 0.8,
                    "risk": 0.5 if semantic_overlap >= 2 else 0.0,
                    "topics": [f"topic-{index}"],
                    "explanation": f"Deterministic match {index + 1}",
                }
            )
        return {"assessments": assessments}

    if {"upserts", "removals"} <= set(properties):
        evidence = task_input.get("evidence", [])
        selected = evidence[0] if evidence else {"id": "", "content": ""}
        facet = "interests"
        value = "deterministic software architecture"
        weight = 0.9
        for item in evidence:
            fields = dict(
                field.split("=", 1) for field in item.get("content", "").split("; ") if "=" in field
            )
            if fields.get("facet") == "avoidances":
                selected = item
                facet = "avoidances"
                value = fields.get("value", value)
                weight = -0.8
                break
            value = fields.get("value", value)
        evidence_ids = [selected["id"]] if selected.get("id") else []
        return {
            "narrative": (
                "Interested in deterministic software architecture"
                if facet == "interests"
                else None
            ),
            "upserts": [
                {
                    "name": facet,
                    "value": value,
                    "weight": weight,
                    "confidence": 0.9,
                    "evidence_ids": evidence_ids,
                    "overridden": False,
                }
            ],
            "removals": [],
        }

    if "keywords" in properties:
        return {"keywords": ["deterministic architecture", "typed backend design"]}
    if "content_id" in properties:
        content = task_input.get("content", {})
        return {
            "content_id": content.get("id"),
            "profile_revision": task_input.get("profile", {}).get("revision", 0),
            "relevance": 0.9,
            "quality": 0.9,
            "novelty": 0.8,
            "risk": 0.0,
            "topics": ["architecture"],
            "explanation": "Deterministic architecture match",
        }
    if "content" in properties:
        return {"content": "Deterministic Docker E2E chat response."}
    if "explanation" in properties:
        return {"explanation": "Architecture is a grounded recommendation."}
    return {name: "deterministic" for name in properties}


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenBiliClawFakeOpenAI/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _stream_chat_completion(
        self,
        *,
        request: dict[str, Any],
        output: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> None:
        """Emit the OpenAI SSE shape exercised by PydanticAI's real stream path."""

        completion_id = "chatcmpl-deterministic-e2e"
        created = int(time.time())
        model = request.get("model", "fake-chat")
        chunks: list[dict[str, Any]] = []
        if tools:
            function = tools[0]["function"]
            arguments = json.dumps(output, separators=(",", ":"))
            split_at = max(1, len(arguments) // 2)
            call_id = _next_call_id()
            for index, part in enumerate((arguments[:split_at], arguments[split_at:])):
                function_delta: dict[str, Any] = {"arguments": part}
                tool_delta: dict[str, Any] = {"index": 0, "function": function_delta}
                if index == 0:
                    tool_delta.update({"id": call_id, "type": "function"})
                    function_delta["name"] = function["name"]
                chunks.append(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"tool_calls": [tool_delta]},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            finish_reason = "tool_calls"
        else:
            content = json.dumps(output, separators=(",", ":"))
            split_at = max(1, len(content) // 2)
            for part in (content[:split_at], content[split_at:]):
                chunks.append(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": part},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            finish_reason = "stop"
        chunks.append(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            }
        )
        chunks.append(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            }
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for chunk in chunks:
            encoded = json.dumps(chunk, separators=(",", ":")).encode()
            self.wfile.write(b"data: " + encoded + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
        if self.path.rstrip("/") in {"/health", "/v1/models"}:
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "fake-chat", "object": "model", "owned_by": "e2e"},
                        {"id": "fake-embedding", "object": "model", "owned_by": "e2e"},
                    ],
                },
            )
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/embeddings"):
            inputs = request.get("input", [])
            values = inputs if isinstance(inputs, list) else [inputs]
            self._json(
                200,
                {
                    "object": "list",
                    "model": request.get("model", "fake-embedding"),
                    "data": [
                        {
                            "object": "embedding",
                            "index": index,
                            "embedding": [0.125, 0.25, 0.5, 1.0],
                        }
                        for index, _value in enumerate(values)
                    ],
                    "usage": {"prompt_tokens": len(values), "total_tokens": len(values)},
                },
            )
            return
        if not self.path.endswith("/chat/completions"):
            self._json(404, {"error": "not_found"})
            return

        output = _structured_output(request)
        tools = request.get("tools") or []
        tool_name = tools[0]["function"]["name"] if tools else "json-content"
        prompt = _prompt_input(request)
        print(
            json.dumps(
                {
                    "e2e_request": tool_name,
                    "prompt_fields": sorted(prompt),
                    "content_count": len(prompt.get("content", []))
                    if isinstance(prompt.get("content"), list)
                    else 0,
                    "output_fields": sorted(output),
                    "assessment_debug": [
                        {
                            "external_id": item.get("external_id"),
                            "relevance": assessment["relevance"],
                            "risk": assessment["risk"],
                        }
                        for item, assessment in zip(
                            prompt.get("content", []),
                            output.get("assessments", []),
                            strict=True,
                        )
                    ]
                    if "assessments" in output
                    else [],
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        if request.get("stream") is True:
            self._stream_chat_completion(request=request, output=output, tools=tools)
            return
        message: dict[str, Any] = {"role": "assistant", "content": None}
        finish_reason = "stop"
        if tools:
            function = tools[0]["function"]
            message["tool_calls"] = [
                {
                    "id": _next_call_id(),
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": json.dumps(output, separators=(",", ":")),
                    },
                }
            ]
            finish_reason = "tool_calls"
        else:
            message["content"] = json.dumps(output, separators=(",", ":"))
        self._json(
            200,
            {
                "id": "chatcmpl-deterministic-e2e",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.get("model", "fake-chat"),
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            },
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
