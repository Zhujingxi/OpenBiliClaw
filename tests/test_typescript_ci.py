from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _json(relative: str) -> dict[str, object]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_extension_ci_typechecks_strict_runtime_projects_before_tests() -> None:
    package = _json("extension/package.json")
    scripts = package["scripts"]
    assert isinstance(scripts, dict)
    assert scripts["typecheck"] == (
        "tsc -p tsconfig.json --noEmit && tsc -p tsconfig.popup.json --noEmit"
    )
    assert scripts["test"] == "node --test --experimental-strip-types tests/*.test.ts"
    assert scripts["pretest"] == "npm run build:popup"

    source_config = _json("extension/tsconfig.json")
    source_options = source_config["compilerOptions"]
    assert isinstance(source_options, dict)
    assert source_options["strict"] is True

    popup_config = _json("extension/tsconfig.popup.json")
    assert popup_config["extends"] == "./tsconfig.json"
    assert not (ROOT / "extension/tsconfig.tests.json").exists()

    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    _, separator, extension_and_following_jobs = ci.partition("\n  extension-test:\n")
    assert separator, "extension-test CI job is missing"
    extension_job, separator, _ = extension_and_following_jobs.partition("\n  web-test:\n")
    assert separator, "extension-test CI job boundary is missing"

    commands = ("run: npm ci", "run: npm run typecheck", "run: npm test")
    for command in commands:
        assert command in extension_job, f"extension-test CI job is missing: {command}"
    command_positions = [extension_job.index(command) for command in commands]
    assert command_positions == sorted(command_positions)
