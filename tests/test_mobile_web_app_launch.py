"""CI wrapper for the mobile web app-launch deep-link helper tests.

The actual assertions live in ``tests/js/mobile-app-launch.test.mjs`` (node:test);
this wrapper makes ``pytest -q`` (and therefore CI) execute them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(_NODE is None, reason="node is required for mobile web JS tests")
def test_app_launch_js_suite_passes() -> None:
    assert _NODE is not None
    result = subprocess.run(
        [_NODE, "--test", "tests/js/mobile-app-launch.test.mjs"],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_mobile_app_shell_loads_the_model_settings_view() -> None:
    app = (_REPO_ROOT / "src/openbiliclaw/web/js/app.js").read_text(encoding="utf-8")

    assert 'import { openMobileSettings } from "./views/model-settings.js";' in app
    assert "openMobileSettings(settings)" in app
