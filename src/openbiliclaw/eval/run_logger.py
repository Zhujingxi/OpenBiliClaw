"""Structured run logger for evaluation sessions.

Creates a directory per run with all artifacts: raw inputs, prompts,
LLM responses, intermediate results, and final evaluation reports.

Directory structure:
    data/eval/runs/<task>_<timestamp>/
    ├── 00_input/
    │   ├── history.json
    │   ├── favorites.json
    │   ├── following.json
    │   └── events.json
    ├── 01_preference/
    │   ├── prompt.txt
    │   ├── response.txt
    │   └── result.json
    ├── 02_profile/
    │   ├── prompt.txt
    │   ├── response.txt
    │   └── result.json
    ├── 03_eval/
    │   ├── ground_truth.json
    │   ├── predicted.json
    │   └── eval_report.json
    ├── 04_optimizer/
    │   ├── prompt.txt
    │   ├── response.txt
    │   └── changes.json
    └── summary.json
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(name)s %(message)s"
_LOG_DATEFMT = "%H:%M:%S"


def setup_logging(*, log_file: Path | None = None, level: int = logging.INFO) -> None:
    """Configure root logger with stderr console + optional file handler.

    Call once at script startup.  If *log_file* is given every log record
    (from any logger) is also written to that file so the full run is
    preserved on disk.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (e.g. from prior basicConfig)
    for h in root.handlers[:]:
        root.removeHandler(h)

    # Console → stderr (always unbuffered)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    root.addHandler(console)

    # File → run_dir/run.log
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s %(name)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(fh)


class RunLogger:
    """Structured logger for a single evaluation run."""

    def __init__(
        self,
        *,
        task: str,
        data_dir: Path | None = None,
        run_id: str | None = None,
    ) -> None:
        self._task = task
        ts = run_id or datetime.now().strftime("%Y%m%dT%H%M%S")
        base = (data_dir or Path("data")) / "eval" / "runs" / f"{task}_{ts}"
        base.mkdir(parents=True, exist_ok=True)
        self._base = base
        self._step_counter = 0
        self._summary: dict[str, Any] = {
            "task": task,
            "run_id": ts,
            "started_at": datetime.now().isoformat(),
            "steps": [],
        }
        logger.info("RunLogger: %s", self._base)

    def setup_file_logging(self, level: int = logging.INFO) -> Path:
        """Set up root logging with a file handler in the run directory.

        Returns the log file path.
        """
        log_path = self._base / "run.log"
        setup_logging(log_file=log_path, level=level)
        logger.info("File logging enabled: %s", log_path)
        return log_path

    @property
    def run_dir(self) -> Path:
        return self._base

    # -- Step management --

    def step(self, name: str) -> RunStep:
        """Create a new step directory."""
        self._step_counter += 1
        prefix = f"{self._step_counter:02d}"
        step_dir = self._base / f"{prefix}_{name}"
        step_dir.mkdir(parents=True, exist_ok=True)
        step = RunStep(step_dir, name)
        self._summary["steps"].append({"step": prefix, "name": name, "dir": str(step_dir.name)})
        return step

    # -- Epoch support (for auto-optimize) --

    def epoch_dir(self, epoch: int) -> Path:
        d = self._base / f"epoch_{epoch:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def persona_dir(self, epoch: int, persona_idx: int) -> RunStep:
        d = self.epoch_dir(epoch) / f"persona_{persona_idx:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return RunStep(d, f"epoch{epoch}_persona{persona_idx}")

    # -- Summary --

    def finish(self, **extra: Any) -> Path:
        """Write summary.json and return its path."""
        self._summary["finished_at"] = datetime.now().isoformat()
        self._summary.update(extra)
        path = self._base / "summary.json"
        _write_json(path, self._summary)
        logger.info("Run finished: %s", path)
        return path


class RunStep:
    """A single step within a run, backed by a directory."""

    def __init__(self, directory: Path, name: str) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._name = name

    @property
    def dir(self) -> Path:
        return self._dir

    def save_json(self, filename: str, data: Any) -> Path:
        """Save a JSON artifact."""
        path = self._dir / filename
        _write_json(path, data)
        return path

    def save_text(self, filename: str, text: str) -> Path:
        """Save a text artifact (prompt, response, etc.)."""
        path = self._dir / filename
        path.write_text(text, encoding="utf-8")
        return path

    def save_prompt(self, messages: list[dict[str, str]]) -> Path:
        """Save LLM prompt messages as readable text."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            parts.append(f"=== {role.upper()} ===\n{content}")
        return self.save_text("prompt.txt", "\n\n".join(parts))

    def save_prompt_and_response(
        self,
        messages: list[dict[str, str]],
        response: str,
        parsed: Any = None,
    ) -> None:
        """Save prompt, raw response, and parsed result together."""
        self.save_prompt(messages)
        self.save_text("response.txt", response)
        if parsed is not None:
            self.save_json("result.json", parsed)


def _write_json(path: Path, data: Any) -> None:
    """Write JSON with Chinese support."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
