"""Headless child-process policy.

Target owner of the spawn-policy portion of legacy ``proc.py`` (refactor
disposition ledger). Legacy ``proc.py`` remains until the composition
cutover deletes it.
"""

from __future__ import annotations

import os
import subprocess

# Win32 CREATE_NO_WINDOW; duplicated so type checking and tests do not
# depend on the platform-only attribute.
CREATE_NO_WINDOW = 0x08000000


def creationflags() -> int:
    """Process creation flags that keep a child headless on Windows.

    Zero on POSIX, so call sites can pass it unconditionally.
    """

    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)
