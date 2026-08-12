"""Generate TypeScript API types using the workspace-pinned generator."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and _VENV_PYTHON.is_file():
    os.execv(_VENV_PYTHON, (str(_VENV_PYTHON), "-m", "scripts.generate_api_client", *sys.argv[1:]))

# Runnable from the frontend workspace and before an editable install.
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

# Path bootstrapping above must precede project imports in this executable script.
from openbiliclaw.infrastructure.process import creationflags  # noqa: E402
from scripts.export_openapi import export  # noqa: E402


def generate(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        schema = Path(directory) / "openapi.json"
        export(schema)
        _hoist_local_definitions(schema)
        subprocess.run(
            [
                "npm",
                "--prefix",
                "frontend",
                "exec",
                "--",
                "openapi-typescript",
                str(schema),
                "--output",
                str(output),
            ],
            check=True,
            creationflags=creationflags(),
        )


def _hoist_local_definitions(path: Path) -> None:
    """Hoist Pydantic's operation-local ``$defs`` for OpenAPI tooling.

    FastAPI currently leaves the SSE union definitions next to the operation;
    OpenAPI references are document-root relative, so generators cannot resolve
    ``#/$defs/...`` there. Moving those definitions into components preserves
    the schema meaning and makes the export valid for frontend generators.
    """

    document: object = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise ValueError("OpenAPI document must be an object")
    components = document.setdefault("components", {})
    if not isinstance(components, dict):
        raise ValueError("OpenAPI components must be an object")
    schemas = components.setdefault("schemas", {})
    if not isinstance(schemas, dict):
        raise ValueError("OpenAPI schemas must be an object")

    def visit(value: object) -> None:
        if isinstance(value, dict):
            definitions = value.pop("$defs", None)
            if definitions is not None:
                if not isinstance(definitions, dict):
                    raise ValueError("OpenAPI $defs must be an object")
                for name, definition in definitions.items():
                    schemas.setdefault(name, definition)
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                value["$ref"] = reference.replace("#/$defs/", "#/components/schemas/", 1)
            mapping = value.get("mapping")
            if isinstance(mapping, dict):
                for key, target in mapping.items():
                    if isinstance(target, str) and target.startswith("#/$defs/"):
                        mapping[key] = target.replace("#/$defs/", "#/components/schemas/", 1)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/packages/api-client/generated/schema.ts"),
    )
    args = parser.parse_args()
    generate(args.output)


if __name__ == "__main__":
    main()
