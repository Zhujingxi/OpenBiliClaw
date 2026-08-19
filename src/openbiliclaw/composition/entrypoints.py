"""The single supported OpenBiliClaw process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

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
    root_common = argparse.ArgumentParser(add_help=False)
    root_common.add_argument("--config", type=Path)
    root_common.add_argument("--data-dir", type=Path, default=BuildOptions().data_dir)
    # Suppressed subparser defaults preserve global options supplied before a command.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--config", type=Path)
    common.add_argument("--data-dir", type=Path)
    parser = argparse.ArgumentParser(prog="openbiliclaw", parents=[root_common])
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "check",
        parents=[common],
        help="validate configuration and runtime readiness",
        description="Validate configuration and runtime readiness.",
    )
    commands.add_parser(
        "serve",
        parents=[common],
        help="serve the local web application and API",
        description="Serve the local web application and API.",
    )
    password = commands.add_parser(
        "set-password",
        parents=[common],
        help="configure the web login password",
        description="Configure the web login password.",
    )
    password.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin once for non-interactive automation",
    )
    commands.add_parser(
        "ext-token",
        parents=[common],
        help="mint a durable browser-extension token",
        description="Mint a durable browser-extension token.",
    )
    export = commands.add_parser(
        "export",
        parents=[common],
        help="export a backup ZIP archive",
        description="Export a backup ZIP archive regardless of the output file extension.",
    )
    export.add_argument("path", type=Path, help="output ZIP archive path")
    export.add_argument("--include-config", action="store_true", help="include configuration")
    imported = commands.add_parser(
        "import",
        parents=[common],
        help="import an archive or provider evidence",
        description="Import a backup archive or provider evidence file.",
    )
    imported.add_argument("provider_id")
    imported.add_argument("file", type=Path, nargs="?")
    imported.add_argument("--force", action="store_true")
    add_product_parsers(commands, common)
    return parser


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _fail(message: str, *, code: str = "validation") -> NoReturn:
    print(_json({"error": {"code": code, "message": message}}), file=sys.stderr)
    raise SystemExit(2)


def _stdin_password() -> str:
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        _fail("password exceeds 4096 bytes")
    try:
        password = raw.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        _fail("password must be UTF-8")
    if not password:
        _fail("password must not be empty")
    return password


def main() -> None:
    parser = _parser()
    arguments = parser.parse_args()
    arguments.command = arguments.command or "check"
    if arguments.command == "export":
        database_path = arguments.data_dir / "openbiliclaw.db"
        if arguments.path.resolve() == database_path.resolve():
            _fail("archive path must not be the live database")
        config_path = (
            (arguments.config or Path("config.toml")) if arguments.include_config else None
        )
        try:
            manifest = asyncio.run(
                export_archive(database_path, arguments.path, config_path=config_path)
            )
        except ArchiveError as error:
            _fail(str(error), code="archive")
        print(
            _json(
                {
                    "format_version": manifest.format_version,
                    "archive": str(arguments.path),
                    "exported": True,
                }
            )
        )
        return
    if arguments.command == "import" and arguments.file is None:
        try:
            manifest = asyncio.run(
                import_archive(
                    Path(arguments.provider_id), arguments.data_dir, force=arguments.force
                )
            )
        except ArchiveError as error:
            _fail(str(error), code="archive")
        restored_config = arguments.data_dir / "config.toml"
        print(
            _json(
                {
                    "format_version": manifest.format_version,
                    "data_dir": str(arguments.data_dir),
                    "imported": True,
                    "restored_config": str(restored_config) if restored_config.is_file() else None,
                }
            )
        )
        return
    if arguments.command == "set-password":
        config_path = arguments.config or Path("config.toml")
        password = _stdin_password() if arguments.password_stdin else getpass.getpass("Password: ")
        confirmation = (
            password if arguments.password_stdin else getpass.getpass("Confirm password: ")
        )
        if password != confirmation:
            _fail("passwords do not match")
        write_host_password(config_path, hash_password(password))
        print(_json({"configured": True, "config": str(config_path)}))
        return
    if arguments.command == "ext-token":
        token = asyncio.run(_mint_extension_token(arguments.data_dir))
        print(_json({"token": token, "shown_once": True}))
        return
    try:
        settings = validated_settings(arguments.config)
    except FileNotFoundError as error:
        _fail(str(error))
    if arguments.command == "check":
        asyncio.run(_check(arguments.config, arguments.data_dir))
        print(_json({"ready": True}))
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
