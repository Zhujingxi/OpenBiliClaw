"""Generate the normalized API route contract manifest.

Combines two views of the FastAPI app:

1. ``app.routes`` ordered metadata — registration index, route class, path,
   methods (sorted), endpoint name, and whether it is a WebSocket or Mount.
   This view covers route kinds (WS, Mount, static) that OpenAPI omits.
2. OpenAPI operations — path, method, operationId, request/response schema
   shapes. This view provides the HTTP surface contract.

The manifest is consumed by ``tests/test_api_route_contract.py`` and by
``scripts/check_quality_baseline.py`` to prove route extraction refactors do
not alter externally visible routing.

Run as::

    python -m scripts.generate_api_route_contract > tests/contracts/api-route-contract.json
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _build_app() -> Any:
    """Construct the FastAPI app without external services.

    ``create_app()`` with no arguments builds a degraded-mode app whose
    routes are all registered (handlers may return 503 but the routing
    surface is complete). This matches how the contract is consumed by
    tests.
    """
    from openbiliclaw.api.app import create_app

    return create_app()


def _flatten_routes(app: Any) -> list[tuple[int, Any, str | None]]:
    """Walk ``app.routes`` and flatten lazy ``_IncludedRouter`` wrappers.

    FastAPI's ``include_router()`` wraps the included APIRouter in a lazy
    ``_IncludedRouter`` marker (instead of copying each child route into
    ``app.routes``). The wrapper preserves matching order semantics, but
    enumeration shape differs from inline ``@app.get`` decorators. For the
    route contract to be invariant across extraction, we flatten wrappers
    into their child routes and record the extraction site so a refactor
    that *changes dispatch order* still trips the diff.

    Yields ``(effective_index, route, parent_wrapper_path)`` triples.
    """
    out: list[tuple[int, Any, str | None]] = []
    effective_index = 0
    for raw in app.routes:
        if type(raw).__name__ == "_IncludedRouter":
            original = getattr(raw, "original_router", None)
            if original is None:
                # Unknown wrapper — record it as-is so the diff still catches it.
                out.append((effective_index, raw, None))
                effective_index += 1
                continue
            for child in original.routes:
                out.append((effective_index, child, None))
                effective_index += 1
        else:
            out.append((effective_index, raw, None))
            effective_index += 1
    return out


def _route_entry(index: int, route: Any) -> dict[str, Any]:
    """Normalize one ``app.routes`` entry into a stable, comparable shape."""
    cls = type(route).__name__
    path = getattr(route, "path", None) or getattr(route, "path_format", None) or ""
    methods = sorted(getattr(route, "methods", None) or [])
    name = getattr(route, "name", None) or ""
    is_websocket = cls in {"WebSocketRoute", "APIWebSocketRoute"} or hasattr(
        route, "websocket_endpoint"
    )
    is_mount = cls == "Mount"
    entry: dict[str, Any] = {
        "index": index,
        "type": cls,
        "path": path,
        "name": name,
        "is_websocket": bool(is_websocket),
        "is_mount": bool(is_mount),
    }
    if methods:
        entry["methods"] = methods
    return entry


def _normalize_schema(schema: Any) -> Any:
    """Normalize an OpenAPI schema fragment into a stable, comparable shape.

    - ``$ref`` values are rewritten to just the referenced component name
      (``#/components/schemas/HealthResponse`` -> ``HealthResponse``) so the
      manifest is stable across Pydantic's internal ref formatting.
    - ``anyOf``/``oneOf``/``allOf`` member lists are recursively normalized
      and sorted by their serialized form (Pydantic emits union members in
      a deterministic but implementation-detail order).
    - Object keys are preserved verbatim (``properties`` order is semantic
      in JSON Schema but Python dict order is already deterministic).
    """
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "$ref" and isinstance(value, str):
                out[key] = value.rsplit("/", 1)[-1]
            elif key in {"anyOf", "oneOf", "allOf"} and isinstance(value, list):
                members = [_normalize_schema(v) for v in value]
                out[key] = sorted(members, key=lambda m: json.dumps(m, sort_keys=True))
            elif key == "items" or isinstance(value, (dict, list)):
                out[key] = _normalize_schema(value)
            else:
                out[key] = value
        return out
    if isinstance(schema, list):
        return [_normalize_schema(v) for v in schema]
    return schema


def _content_schema(content: Any) -> dict[str, Any]:
    """Extract ``{media_type: normalized_schema}`` from a content block."""
    if not isinstance(content, dict):
        return {}
    out: dict[str, Any] = {}
    for media_type in sorted(content):
        media = content[media_type] or {}
        schema = media.get("schema")
        out[media_type] = _normalize_schema(schema) if schema else None
    return out


def _openapi_operations(app: Any) -> list[dict[str, Any]]:
    """Extract the OpenAPI operation-level contract, normalized."""
    schema = app.openapi()
    paths = schema.get("paths", {}) or {}
    operations: list[dict[str, Any]] = []
    for path in sorted(paths):
        path_item = paths[path] or {}
        for method in sorted(path_item):
            op = path_item[method] or {}
            if method.lower() not in {
                "get",
                "post",
                "put",
                "delete",
                "patch",
                "head",
                "options",
                "trace",
            }:
                continue
            request_body = op.get("requestBody") or {}
            required = bool(request_body.get("required", False))
            responses: dict[str, Any] = {}
            for status in sorted((op.get("responses") or {}).keys()):
                resp = (op.get("responses") or {})[status] or {}
                responses[status] = {
                    "description": (resp.get("description") or "").strip(),
                    "content": _content_schema(resp.get("content")),
                }
            parameters = []
            for param in op.get("parameters") or []:
                parameters.append(
                    {
                        "name": param.get("name") or "",
                        "in": param.get("in") or "",
                        "required": bool(param.get("required", False)),
                        "schema": _normalize_schema(param.get("schema") or {}),
                    }
                )
            parameters.sort(key=lambda p: (p["in"], p["name"]))
            operations.append(
                {
                    "path": path,
                    "method": method.upper(),
                    "operation_id": op.get("operationId") or "",
                    "request_body_required": required,
                    "request_body_content": _content_schema(request_body.get("content")),
                    "parameters": parameters,
                    "responses": responses,
                    "tags": sorted(op.get("tags") or []),
                }
            )
    return operations


def build_manifest(app: Any) -> dict[str, Any]:
    flattened = _flatten_routes(app)
    routes = [_route_entry(i, r) for (i, r, _parent) in flattened]
    return {
        "version": 1,
        "app_routes": routes,
        "openapi_operations": _openapi_operations(app),
    }


def main() -> int:
    app = _build_app()
    manifest = build_manifest(app)
    json.dump(manifest, sys.stdout, indent=2, sort_keys=False, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
