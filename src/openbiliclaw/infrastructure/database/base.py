"""SQLAlchemy engine, metadata, and session construction."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import MetaData, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from openbiliclaw.features.system.domain import DatabaseSettings

__all__ = [
    "Base",
    "DatabaseSettings",
    "create_engine_and_session",
    "ensure_database_parent",
]


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by the vNext models and Alembic."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def ensure_database_parent(url: str) -> None:
    """Create the parent directory required by a file-backed SQLite URL."""

    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or parsed.database in {None, "", ":memory:"}:
        return
    database = parsed.database
    assert database is not None
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(engine: Engine, *, busy_timeout_seconds: float) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={round(busy_timeout_seconds * 1000)}")
        finally:
            cursor.close()


def create_engine_and_session(
    settings: DatabaseSettings | None = None,
) -> tuple[Engine, sessionmaker[Session]]:
    """Create a synchronous engine and non-expiring session factory."""

    from sqlalchemy import create_engine

    resolved = settings or DatabaseSettings()
    ensure_database_parent(resolved.url)
    parsed = make_url(resolved.url)
    kwargs: dict[str, object] = {"echo": resolved.echo}
    if parsed.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": resolved.busy_timeout_seconds,
        }
        if parsed.database in {None, "", ":memory:"}:
            kwargs["poolclass"] = StaticPool

    engine = create_engine(resolved.url, **kwargs)
    if parsed.get_backend_name() == "sqlite":
        _configure_sqlite(engine, busy_timeout_seconds=resolved.busy_timeout_seconds)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return engine, factory
