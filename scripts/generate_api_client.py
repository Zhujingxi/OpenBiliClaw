"""Generate Plan 14's TypeScript API types from deterministic OpenAPI.

The frontend workspace is intentionally absent until Plan 14; this script is
checked in now so generation has one reproducible entrypoint when that target
exists.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from openbiliclaw.proc import no_window_kwargs
from scripts.export_openapi import export


def generate(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        schema = Path(directory) / "openapi.json"
        export(schema)
        subprocess.run(
            [
                "npx",
                "--yes",
                "openapi-typescript@7.10.1",
                str(schema),
                "--output",
                str(output),
            ],
            check=True,
            **no_window_kwargs(),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/packages/api-client/src/generated.ts"),
    )
    args = parser.parse_args()
    generate(args.output)


if __name__ == "__main__":
    main()
