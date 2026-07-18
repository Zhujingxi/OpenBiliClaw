"""Persistent persona pool for caching and reusing generated test personas.

Stores successfully generated personas on disk so they can be reused
across optimization runs, avoiding expensive SDK calls.

Directory structure:
    data/eval/persona_pool/
    ├── init/                          # For init profile task
    │   ├── INTJ_hardcore_specialist_a1b2.json
    │   └── ENFP_casual_generalist_c3d4.json
    └── update/                        # For incremental update task
        ├── INTJ_hardcore_specialist_new_interest_e5f6.json
        └── ISTP_moderate_specialist_abandon_g7h8.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PersonaPool:
    """Cache of generated personas, keyed by constraint signature."""

    def __init__(self, pool_dir: Path | None = None) -> None:
        self._dir = pool_dir or Path("data/eval/persona_pool")
        self._dir.mkdir(parents=True, exist_ok=True)

    def _task_dir(self, task: str) -> Path:
        d = self._dir / task
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _signature(constraints: dict[str, str]) -> str:
        """Build a human-readable + unique key from constraints."""
        parts = [
            constraints.get("mbti", "X"),
            constraints.get("depth", "X"),
            constraints.get("interest_breadth", "X"),
        ]
        # Include shift type for update task personas
        shift = constraints.get("shift")
        if shift:
            parts.append(shift)
        base = "_".join(parts)
        # Add short hash to avoid collisions from same constraint set
        h = hashlib.md5(json.dumps(constraints, sort_keys=True).encode()).hexdigest()[:6]
        return f"{base}_{h}"

    def save(
        self,
        task: str,
        constraints: dict[str, str],
        data: dict[str, Any],
    ) -> Path:
        """Save a successfully generated persona to the pool."""
        sig = self._signature(constraints)
        task_dir = self._task_dir(task)
        # Find next available index for this signature
        existing = list(task_dir.glob(f"{sig}_*.json"))
        idx = len(existing)
        path = task_dir / f"{sig}_{idx:02d}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"constraints": constraints, "data": data},
                f,
                ensure_ascii=False,
                indent=2,
            )
        logger.info("Persona saved to pool: %s", path.name)
        return path

    def load_matching(
        self,
        task: str,
        constraints: dict[str, str],
    ) -> dict[str, Any] | None:
        """Load a random cached persona matching the given constraints.

        Returns the persona data dict, or None if no match found.
        """
        task_dir = self._task_dir(task)
        sig = self._signature(constraints)
        matches = list(task_dir.glob(f"{sig}_*.json"))
        if not matches:
            # Fallback: try any persona with same mbti + depth
            mbti = constraints.get("mbti", "")
            depth = constraints.get("depth", "")
            matches = [p for p in task_dir.glob("*.json") if f"{mbti}_{depth}" in p.stem]
        if not matches:
            return None
        path = random.choice(matches)
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        logger.info("Persona loaded from pool: %s", path.name)
        data = cached.get("data") if isinstance(cached, dict) else None
        return data if isinstance(data, dict) else None

    def load_any(self, task: str) -> dict[str, Any] | None:
        """Load any random persona from the pool for the given task."""
        task_dir = self._task_dir(task)
        matches = list(task_dir.glob("*.json"))
        if not matches:
            return None
        path = random.choice(matches)
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        logger.info("Persona loaded (any) from pool: %s", path.name)
        data = cached.get("data") if isinstance(cached, dict) else None
        return data if isinstance(data, dict) else None

    def count(self, task: str) -> int:
        """Return number of cached personas for a task."""
        return len(list(self._task_dir(task).glob("*.json")))
