"""Shared production-server process helper for real-stack E2E layers."""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import urlopen

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data-e2e"
BASE_URL = "http://127.0.0.1:8430"


@contextmanager
def production_server(*, log_name: str, frontend_dir: Path | None = None) -> Iterator[str]:
    """Run the production composition and wait for its real HTTP host."""

    executable = ROOT / ".venv/bin/openbiliclaw"
    config = DATA_DIR / "config.e2e.toml"
    if not executable.is_file():
        pytest.fail(f"missing E2E executable: {executable}; install the project in .venv")
    if not config.is_file():
        pytest.fail(f"missing E2E config: {config}; run scripts/e2e.py once to seed it")
    if frontend_dir is not None and not (frontend_dir / "index.html").is_file():
        pytest.fail(
            f"missing built Vue assets: {frontend_dir}; run npm --prefix frontend run build"
        )

    log_path = DATA_DIR / "reports" / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    env = {**os.environ, "ALLOW_MODEL_REQUESTS": "True"}
    if frontend_dir is not None:
        env["OPENBILICLAW_FRONTEND_DIR"] = str(frontend_dir)
    process = subprocess.Popen(
        [
            str(executable),
            "serve",
            "--config",
            str(config),
            "--data-dir",
            str(DATA_DIR),
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(240):
            if process.poll() is not None:
                pytest.fail(f"server exited {process.returncode}; see {log_path}")
            try:
                with urlopen(f"{BASE_URL}/v1/runtime/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                pass
            time.sleep(0.25)
        else:
            pytest.fail(f"server did not become healthy; see {log_path}")
        yield BASE_URL
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
