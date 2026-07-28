"""Spawn helper CLIs without flashing a console window on Windows.

The desktop / installer build runs the backend from a window-less parent
(``pythonw`` style). On Windows every console executable a window-less
process starts gets a **brand new console window** unless
``CREATE_NO_WINDOW`` is passed, so background probes such as
``rdt status``, ``git rev-parse`` or ``taskkill`` popped visible black
terminals in the user's face — several in a row whenever a settings save
re-ran the source probes.

Every ``subprocess`` / ``asyncio.create_subprocess_exec`` call that the
backend makes on its own initiative must therefore splat
``**no_window_kwargs()``. The only exceptions are commands the user
explicitly invoked from a terminal (``openbiliclaw`` CLI, ``codex login``)
— those already own a console and must keep it for interactive prompts.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

# Value of subprocess.CREATE_NO_WINDOW; defined here so non-Windows type
# checking and tests do not depend on the platform-only attribute.
CREATE_NO_WINDOW = 0x08000000


def no_window_kwargs() -> dict[str, Any]:
    """Return Popen kwargs that keep a child process headless on Windows.

    Empty on POSIX, so call sites can splat it unconditionally. The value
    type is ``Any`` so the mapping can be splatted into the heavily
    overloaded ``subprocess`` / ``asyncio`` signatures under mypy strict.
    """

    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)}
