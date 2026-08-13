#!/usr/bin/env python3
"""Diagnose Chrome Bilibili cookies through the product's verified connect path."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv/bin/python"
if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve() and VENV_PYTHON.exists():
    os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.e2e.bilibili_chrome import connect_command, extract_bilibili_cookies  # noqa: E402

from openbiliclaw.composition.build import (  # noqa: E402
    BuildOptions,
    build_application,
    validated_settings,
)


async def diagnose() -> None:
    cookies = extract_bilibili_cookies()
    print("found cookie fields: " + ", ".join(cookies.structural_summary()))
    settings = validated_settings(ROOT / "data-e2e/config.e2e.toml")
    application = build_application(settings, options=BuildOptions(data_dir=ROOT / "data-e2e"))
    await application.start()
    try:
        facade = application.services.facade
        if facade is None:
            raise RuntimeError("application facade is unavailable")
        result = await facade.connect_source(
            connect_command(cookies, f"e2e:cookie-check:{uuid.uuid4().hex}")
        )
        print(f"credential verification: {result.status.state.value}")
    finally:
        await application.stop()


if __name__ == "__main__":
    asyncio.run(diagnose())
