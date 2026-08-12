from __future__ import annotations

import os
import subprocess

from openbiliclaw.infrastructure import process


def test_creationflags_are_zero_on_posix_and_headless_on_windows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(os, "name", "posix")
    assert process.creationflags() == 0
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 123, raising=False)
    assert process.creationflags() == 123
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW")
    assert process.creationflags() == process.CREATE_NO_WINDOW
