#!/usr/bin/env python3
"""Run one opt-in real-stack E2E layer and write a JSON report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve() and VENV_PYTHON.exists():
    os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])
sys.path.insert(0, str(ROOT / "src"))

from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend  # noqa: E402
from openbiliclaw.infrastructure.credentials.vault import CredentialVault  # noqa: E402

DATA_DIR = ROOT / "data-e2e"
CONFIG_PATH = DATA_DIR / "config.e2e.toml"
TEMPLATE_PATH = ROOT / "config.e2e.example.toml"
KEY_PATH = DATA_DIR / "kimi_api_key.txt"
REPORT_DIR = DATA_DIR / "reports"
LAYERS = ("l0", "l1a", "l1b", "l2", "l3", "l4", "l5", "l6", "l7")
EMBEDDING_LAYERS = frozenset(("l0", "l3", "l4", "l5", "l6", "l7"))


@dataclass(frozen=True, slots=True)
class Report:
    layer: str
    passed: int
    failed: int
    duration_seconds: float
    failures: tuple[str, ...]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layer", choices=LAYERS)
    return parser.parse_args(argv)


def _read_secret_ref() -> str | None:
    if not CONFIG_PATH.exists():
        return None
    with CONFIG_PATH.open("rb") as stream:
        values = tomllib.load(stream)
    model = values.get("model")
    if not isinstance(model, dict):
        return None
    reference = model.get("secret_ref")
    if not isinstance(reference, str) or not reference.startswith("vault:"):
        return None
    return reference.removeprefix("vault:")


def seed_profile() -> None:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.is_file():
        raise RuntimeError(f"missing test key: {KEY_PATH}")
    os.chmod(KEY_PATH, 0o600)
    backend = ProtectedFileBackend(DATA_DIR / "credentials.json")
    vault = CredentialVault(backend)
    secret_ref = _read_secret_ref()
    try:
        if secret_ref is not None:
            vault.resolve(secret_ref, lambda _secret: None)
            return
    except KeyError:
        pass
    secret_ref = vault.store(KEY_PATH.read_bytes().strip())
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    CONFIG_PATH.write_text(template.replace("E2E_SECRET_REF", secret_ref), encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)


def _embedding_ready() -> bool:
    request = urllib.request.Request("http://127.0.0.1:7997/health")
    try:
        with urllib.request.urlopen(request, timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def ensure_embedding_server() -> None:
    if _embedding_ready():
        return
    executable = ROOT / ".venv" / "bin" / "infinity_emb"
    if not executable.exists():
        raise RuntimeError("infinity_emb is not installed in .venv")
    log = (DATA_DIR / "infinity.log").open("ab")
    subprocess.Popen(
        [
            str(executable),
            "v2",
            "--model-id",
            "BAAI/bge-small-zh-v1.5",
            "--port",
            "7997",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(180):
        if _embedding_ready():
            return
        time.sleep(1)
    raise RuntimeError(f"embedding server did not become ready; see {DATA_DIR / 'infinity.log'}")


def write_report(report: Report) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{report.layer}.json"
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    return path


def run_layer(layer: str) -> int:
    seed_profile()
    if layer in EMBEDDING_LAYERS:
        ensure_embedding_server()
    started = time.monotonic()
    command = [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        "-q",
        "-o",
        "addopts=",
        "-m",
        f"e2e_{layer}",
        "tests/e2e",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    duration = time.monotonic() - started
    output = f"{result.stdout}\n{result.stderr}".strip()
    passed = _pytest_count(output, "passed")
    failed = _pytest_count(output, "failed")
    failure_lines = tuple(
        line for line in output.splitlines() if line.startswith(("FAILED ", "ERROR "))
    )
    if result.returncode and failed == 0:
        failed = 1
    failures = failure_lines or (
        (f"pytest exited {result.returncode}",) if result.returncode else ()
    )
    report = Report(layer, passed, failed, duration, failures)
    path = write_report(report)
    print(output)
    print(f"report: {path}")
    return result.returncode


def _pytest_count(output: str, outcome: str) -> int:
    import re

    matches = re.findall(rf"(?:^|[=, ])(\d+) {outcome}(?:[, =]|$)", output, re.MULTILINE)
    return int(matches[-1]) if matches else 0


def main() -> None:
    arguments = parse_args()
    try:
        raise SystemExit(run_layer(arguments.layer))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"e2e setup failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
