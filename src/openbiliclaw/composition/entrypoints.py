"""The single supported OpenBiliClaw process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from .build import BuildOptions, build_application, validated_settings


async def _check(config: Path | None, data_dir: Path) -> None:
    application = build_application(validated_settings(config), options=BuildOptions(data_dir))
    await application.start()
    try:
        if not await application.ready():
            raise RuntimeError("composition graph failed readiness")
    finally:
        await application.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="openbiliclaw")
    parser.add_argument("command", choices=("check", "serve"), nargs="?", default="check")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    arguments = parser.parse_args()
    settings = validated_settings(arguments.config)
    if arguments.command == "check":
        asyncio.run(_check(arguments.config, arguments.data_dir))
        return
    application = build_application(settings, options=BuildOptions(arguments.data_dir))
    if application.hosts.api is None:
        raise RuntimeError("composition did not construct the API host")
    uvicorn.run(
        application.hosts.api,
        host=settings.host.api_host,
        port=settings.host.api_port,
    )


if __name__ == "__main__":
    main()
