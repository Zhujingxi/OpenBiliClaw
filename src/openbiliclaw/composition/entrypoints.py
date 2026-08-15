"""The single supported OpenBiliClaw process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path

import uvicorn

from openbiliclaw.core.config_writer import write_host_password
from openbiliclaw.hosts.api.auth import (
    AuthTokenService,
    SqliteAuthTokenRepository,
    hash_password,
)
from openbiliclaw.infrastructure.archive import ArchiveError, export_archive, import_archive
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator

from .build import BuildOptions, build_application, validated_settings
from .product_cli import add_product_parsers, is_product_command, run_product_command


async def _mint_extension_token(data_dir: Path) -> str:
    path = data_dir / "openbiliclaw.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        minted = await AuthTokenService(SqliteAuthTokenRepository(database)).mint("extension")
        return minted.token
    finally:
        await database.close()


async def _check(config: Path | None, data_dir: Path) -> None:
    application = build_application(
        validated_settings(config), options=BuildOptions(data_dir, config_path=config)
    )
    await application.start()
    try:
        assert await application.ready(), "composition graph failed readiness"
    finally:
        await application.stop()


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path)
    common.add_argument("--data-dir", type=Path, default=BuildOptions().data_dir)
    parser = argparse.ArgumentParser(prog="openbiliclaw", parents=[common])
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("check", parents=[common])
    commands.add_parser("serve", parents=[common])
    commands.add_parser("set-password", parents=[common])
    commands.add_parser("ext-token", parents=[common])
    export = commands.add_parser("export", parents=[common])
    export.add_argument("path", type=Path)
    export.add_argument("--include-config", action="store_true")
    imported = commands.add_parser("import", parents=[common])
    imported.add_argument("path", type=Path)
    imported.add_argument("--force", action="store_true")
    add_product_parsers(commands, common)
    return parser


def main() -> None:
    parser = _parser()
    arguments = parser.parse_args()
    arguments.command = arguments.command or "check"
    if arguments.command == "export":
        database_path = arguments.data_dir / "openbiliclaw.db"
        if arguments.path.resolve() == database_path.resolve():
            parser.error("archive path must not be the live database")
        config_path = (
            (arguments.config or Path("config.toml")) if arguments.include_config else None
        )
        try:
            manifest = asyncio.run(
                export_archive(database_path, arguments.path, config_path=config_path)
            )
        except ArchiveError as error:
            parser.error(str(error))
        print(f"exported format {manifest.format_version} archive to {arguments.path}")
        return
    if arguments.command == "import":
        try:
            manifest = asyncio.run(
                import_archive(arguments.path, arguments.data_dir, force=arguments.force)
            )
        except ArchiveError as error:
            parser.error(str(error))
        print(f"imported format {manifest.format_version} archive into {arguments.data_dir}")
        restored_config = arguments.data_dir / "config.toml"
        if restored_config.is_file():
            print(
                f"restored config to {restored_config}; "
                f"serve/check need --config {restored_config} to load it"
            )
        return
    if arguments.command == "set-password":
        config_path = arguments.config or Path("config.toml")
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            parser.error("passwords do not match")
        write_host_password(config_path, hash_password(password))
        print(f"password configured in {config_path}")
        if arguments.config is None:
            print(f"note: serve/check need --config {config_path} to load it")
        return
    if arguments.command == "ext-token":
        token = asyncio.run(_mint_extension_token(arguments.data_dir))
        print(f"token: {token}")
        print("Store it now; this token will not be shown again.")
        return
    settings = validated_settings(arguments.config)
    if arguments.command == "check":
        asyncio.run(_check(arguments.config, arguments.data_dir))
        return
    application = build_application(
        settings, options=BuildOptions(arguments.data_dir, config_path=arguments.config)
    )
    if is_product_command(arguments.command):
        if asyncio.run(run_product_command(application, arguments)):
            raise SystemExit(1)
        return
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
