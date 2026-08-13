"""The single supported OpenBiliClaw process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import uvicorn

from .build import BuildOptions, build_application, validated_settings


async def _check(config: Path | None, data_dir: Path) -> None:
    application = build_application(
        validated_settings(config), options=BuildOptions(data_dir, config_path=config)
    )
    await application.start()
    try:
        assert await application.ready(), "composition graph failed readiness"
    finally:
        await application.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="openbiliclaw")
    parser.add_argument("command", choices=("check", "serve"), nargs="?", default="check")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-dir", type=Path, default=BuildOptions().data_dir)
    arguments = parser.parse_args()
    settings = validated_settings(arguments.config)
    if arguments.command == "check":
        asyncio.run(_check(arguments.config, arguments.data_dir))
        return
    application = build_application(
        settings, options=BuildOptions(arguments.data_dir, config_path=arguments.config)
    )
    frontend = os.environ.get("OPENBILICLAW_FRONTEND_DIR")
    if frontend:
        application = application.with_api_frontend(Path(frontend))
    # build_application's production contract always constructs the API host.
    assert application.hosts.api is not None
    uvicorn.run(
        application.hosts.api,
        host=settings.host.api_host,
        port=settings.host.api_port,
    )
