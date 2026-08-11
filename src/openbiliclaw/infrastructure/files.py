"""Root-bounded atomic filesystem operations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class BoundedFiles:
    """Read and atomically write bounded files below one fixed root."""

    def __init__(self, root: Path, *, max_bytes: int = 16 * 1024 * 1024) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes

    def read(self, relative_path: str) -> bytes:
        """Read at most ``max_bytes`` and reject path escapes."""

        path = self._resolve(relative_path)
        with path.open("rb") as stream:
            value = stream.read(self._max_bytes + 1)
        if len(value) > self._max_bytes:
            raise ValueError("file exceeds configured size bound")
        return value

    def write(self, relative_path: str, value: bytes) -> None:
        """Atomically replace a bounded file."""

        if len(value) > self._max_bytes:
            raise ValueError("file exceeds configured size bound")
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after directory creation to catch a concurrently introduced symlink.
        path = self._resolve(relative_path)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or not candidate.parts:
            raise ValueError("path must be relative")
        resolved = (self._root / candidate).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError("path escapes configured root")
        return resolved
