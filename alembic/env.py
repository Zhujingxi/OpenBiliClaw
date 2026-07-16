"""Alembic environment for the isolated vNext schema."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from openbiliclaw.infrastructure.database import models as database_models  # noqa: F401
from openbiliclaw.infrastructure.database.base import Base, ensure_database_parent

config = context.config
environment_url = os.environ.get("OPENBILICLAW_DATABASE_URL")
if environment_url:
    config.set_main_option("sqlalchemy.url", environment_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without constructing an Engine."""

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through a synchronous SQLAlchemy connection."""

    database_url = config.get_main_option("sqlalchemy.url")
    if not database_url:
        raise RuntimeError("Alembic requires sqlalchemy.url")
    ensure_database_parent(database_url)
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
