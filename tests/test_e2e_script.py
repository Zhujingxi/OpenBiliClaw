from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "e2e.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_layer_argument_is_strict() -> None:
    script = load_script()
    assert script.parse_args(["l1a"]).layer == "l1a"
    with pytest.raises(SystemExit):
        script.parse_args(["unknown"])


def test_report_is_machine_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = load_script()
    monkeypatch.setattr(script, "REPORT_DIR", tmp_path)
    report = script.Report("l0", 3, 1, 1.25, ("one failure",))
    path = script.write_report(report)
    assert path.read_text(encoding="utf-8") == (
        '{\n  "layer": "l0",\n  "passed": 3,\n  "failed": 1,\n'
        '  "duration_seconds": 1.25,\n  "failures": [\n    "one failure"\n  ]\n}\n'
    )


def test_pytest_summary_counts_are_extracted() -> None:
    script = load_script()
    count = cast("int", script._pytest_count("2 failed, 4 passed in 1.00s", "passed"))
    assert count == 4
    assert script._pytest_count("2 failed, 4 passed in 1.00s", "failed") == 2
    assert script._pytest_count(".... [100%]\n4 passed in 5.64s\n", "passed") == 4
    assert script._pytest_count("collection interrupted", "failed") == 0
