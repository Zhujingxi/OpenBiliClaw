"""Run the production mobile model-settings controller behavior suite."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(_NODE is None, reason="node is required for mobile web JS tests")
def test_mobile_model_settings_controller_js_suite_passes() -> None:
    assert _NODE is not None
    result = subprocess.run(
        [_NODE, "--test", "tests/js/mobile-model-settings-controller.test.mjs"],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
