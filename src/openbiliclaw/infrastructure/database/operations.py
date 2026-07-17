"""Operational diagnostics and safe SQLite backups.

This adapter is the sole owner of operational SQL used by the CLI and runtime
startup gates. Product features continue to use repositories and units of work.
"""

from __future__ import annotations

import ctypes
import errno
import os
import secrets
import sqlite3
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url


@dataclass(frozen=True, slots=True)
class RuntimeDatabaseHealth:
    """Secret-free health facts for the application and queue databases."""

    database_exists: bool
    database_reachable: bool
    database_integrity_ok: bool
    migration_at_head: bool
    queue_exists: bool
    queue_integrity_ok: bool
    queue_writable: bool
    paths_separate: bool

    @property
    def ready(self) -> bool:
        """Return whether every persistence prerequisite is healthy."""

        return all(
            (
                self.database_exists,
                self.database_reachable,
                self.database_integrity_ok,
                self.migration_at_head,
                self.queue_exists,
                self.queue_integrity_ok,
                self.queue_writable,
                self.paths_separate,
            )
        )


class DatabaseBackupError(RuntimeError):
    """A safe, user-facing backup failure without sensitive path internals."""


class SchemaNotReadyError(RuntimeError):
    """The application database is absent, invalid, or not at Alembic head."""


def require_schema_at_head(*, database_url: str, alembic_ini: Path) -> None:
    """Fail closed unless a file-backed SQLite database is already migrated.

    Runtime processes use this read-only check. Migration ownership stays with
    the installer or the one-shot Compose migration service, so concurrent API
    and worker startup can never compete for SQLite DDL locks.
    """

    database_path = _sqlite_path(database_url)
    if not (
        _regular_file(database_path)
        and _integrity_ok(database_path)
        and _migration_at_head(
            database_path=database_path,
            database_url=database_url,
            alembic_ini=alembic_ini,
        )
    ):
        raise SchemaNotReadyError(
            "vNext database schema is not at Alembic head; run openbiliclaw db migrate"
        )


@dataclass(frozen=True, slots=True)
class _DestinationDirectory:
    """Pinned destination directory used for relative no-follow operations."""

    path: Path
    descriptor: int | None
    identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _OwnedFile:
    """A file descriptor and the inode exclusively created by this invocation."""

    path: Path
    descriptor: int
    identity: tuple[int, int]


_SNAPSHOT_ATTEMPTS = 3
_COPY_BUFFER_SIZE = 1024 * 1024


class SQLiteOperationalStore:
    """Perform file-backed SQLite diagnostics and no-overwrite backups."""

    def diagnose(
        self,
        *,
        database_url: str,
        queue_path: Path,
        alembic_ini: Path,
    ) -> RuntimeDatabaseHealth:
        """Inspect both SQLite files and compare the app revision with Alembic head."""

        database_path = _sqlite_path(database_url)
        queue = queue_path.expanduser().absolute()
        database_exists = _regular_file(database_path)
        queue_exists = _regular_file(queue)
        database_integrity = database_exists and _integrity_ok(database_path)
        queue_integrity = queue_exists and _integrity_ok(queue)
        queue_writable = queue_integrity and _write_transaction_available(queue)
        return RuntimeDatabaseHealth(
            database_exists=database_exists,
            database_reachable=database_integrity,
            database_integrity_ok=database_integrity,
            migration_at_head=(
                database_integrity
                and _migration_at_head(
                    database_path=database_path,
                    database_url=database_url,
                    alembic_ini=alembic_ini,
                )
            ),
            queue_exists=queue_exists,
            queue_integrity_ok=queue_integrity,
            queue_writable=queue_writable,
            paths_separate=_paths_separate(database_path, queue),
        )

    def backup(self, *, source: Path, destination: Path) -> Path:
        """Publish a consistent private backup without ever replacing a path."""

        try:
            return self._backup(source=source, destination=destination)
        except DatabaseBackupError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseBackupError("database backup failed") from exc

    def _backup(self, *, source: Path, destination: Path) -> Path:
        if not _secure_backup_platform_supported():
            raise DatabaseBackupError(
                "secure database backup is not supported on Windows or other platforms; "
                "it is supported only on Linux and macOS"
            )
        source_path = source.expanduser().absolute()
        target = _prepare_destination(destination)
        if source_path == target:
            raise DatabaseBackupError("backup destination must differ from the database")

        source_descriptor, source_identity = _open_source(source_path)
        try:
            directory = _open_destination_directory(target.parent)
            try:
                return self._backup_in_directory(
                    source=source_path,
                    source_descriptor=source_descriptor,
                    source_identity=source_identity,
                    target=target,
                    directory=directory,
                )
            finally:
                _close_destination_directory(directory)
        finally:
            os.close(source_descriptor)

    def _backup_in_directory(
        self,
        *,
        source: Path,
        source_descriptor: int,
        source_identity: tuple[int, int],
        target: Path,
        directory: _DestinationDirectory,
    ) -> Path:
        temp_path, temp_descriptor, temp_identity = self._create_temp(directory)
        temp = _OwnedFile(temp_path, temp_descriptor, temp_identity)
        try:
            _require_unlinked_payload(temp.descriptor, temp.identity)
            self._backup_into_descriptor(
                source=source,
                source_descriptor=source_descriptor,
                source_identity=source_identity,
                snapshot_parent=directory.path,
                descriptor=temp.descriptor,
            )
            _require_unlinked_payload(temp.descriptor, temp.identity)
            _require_source_identity(source, source_identity)
            _sync_descriptor(temp.descriptor, temp.identity)
            final = _atomic_publish_no_replace(
                directory=directory,
                payload=temp,
                target=target,
            )
            try:
                _sync_directory(directory)
                _require_destination_directory(directory)
                _require_owned_entry(directory, final)
                _require_source_identity(source, source_identity)
                _recycle_macos_payload(temp)
                return target
            finally:
                os.close(final.descriptor)
        finally:
            os.close(temp.descriptor)

    def _create_temp(self, directory: _DestinationDirectory) -> tuple[Path, int, tuple[int, int]]:
        """Create a private payload inode in the destination filesystem."""

        if directory.descriptor is None:
            raise DatabaseBackupError("secure backup publication is unavailable")
        if sys.platform == "darwin":
            payload_path, descriptor = _create_unlinked_macos_payload(directory)
        else:
            descriptor = _create_anonymous_payload(directory)
            payload_path = directory.path / ".anonymous-backup-payload"
        try:
            metadata = os.fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino)
            if not stat.S_ISREG(metadata.st_mode):
                raise DatabaseBackupError("backup payload is not a regular file")
            _require_unlinked_payload(descriptor, identity)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            return payload_path, descriptor, identity
        except BaseException:
            os.close(descriptor)
            raise

    def _backup_into_descriptor(
        self,
        *,
        source: Path,
        source_descriptor: int,
        source_identity: tuple[int, int],
        snapshot_parent: Path,
        descriptor: int,
    ) -> None:
        """Build a consistent snapshot in memory, then write only to the held inode."""

        payload = _snapshot_with_retries(
            source=source,
            source_descriptor=source_descriptor,
            source_identity=source_identity,
            snapshot_parent=snapshot_parent,
        )
        identity = _descriptor_identity(descriptor)
        _require_unlinked_payload(descriptor, identity)
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DatabaseBackupError("database backup failed while writing snapshot")
            view = view[written:]
        _require_unlinked_payload(descriptor, identity)


def _create_unlinked_macos_payload(directory: _DestinationDirectory) -> tuple[Path, int]:
    """Create a never-reused payload and unlink it before snapshot bytes exist."""

    assert directory.descriptor is not None
    create_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(32):
        name = f".backup-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(name, create_flags, 0o600, dir_fd=directory.descriptor)
        except FileExistsError:
            continue
        except OSError as exc:
            raise DatabaseBackupError("could not reserve a private backup payload") from exc
        try:
            held = os.fstat(descriptor)
            named = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(held.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or held.st_nlink != 1
                or held.st_size != 0
                or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise DatabaseBackupError("backup payload changed during creation")
            os.unlink(name, dir_fd=directory.descriptor)
            unlinked = os.fstat(descriptor)
            if unlinked.st_nlink != 0 or (unlinked.st_dev, unlinked.st_ino) != (
                held.st_dev,
                held.st_ino,
            ):
                raise DatabaseBackupError("backup payload changed during creation")
            try:
                os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise DatabaseBackupError("backup payload path reappeared during creation")
            return directory.path / name, descriptor
        except BaseException:
            os.close(descriptor)
            raise
    raise DatabaseBackupError("could not reserve a unique private backup payload")


def _recycle_macos_payload(payload: _OwnedFile) -> None:
    if sys.platform != "darwin":
        return
    _require_unlinked_payload(payload.descriptor, payload.identity)
    os.ftruncate(payload.descriptor, 0)
    os.fsync(payload.descriptor)


def _snapshot_with_retries(
    *,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
    snapshot_parent: Path,
) -> bytes:
    last_churn: DatabaseBackupError | None = None
    for attempt in range(_SNAPSHOT_ATTEMPTS):
        try:
            return _snapshot_once(
                source=source,
                source_descriptor=source_descriptor,
                source_identity=source_identity,
                snapshot_parent=snapshot_parent,
            )
        except DatabaseBackupError as exc:
            if not _retryable_identity_churn(exc) or attempt + 1 == _SNAPSHOT_ATTEMPTS:
                raise
            last_churn = exc
        except sqlite3.Error:
            if last_churn is None:
                raise
            raise last_churn from None
    raise DatabaseBackupError("database source or sidecar changed during backup")


def _snapshot_once(
    *,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
    snapshot_parent: Path,
) -> bytes:
    del snapshot_parent
    _require_source_identity(source, source_identity)
    sidecar_descriptors, sidecar_identities = _pin_backup_sidecars(source)
    allowed_identities = frozenset(
        {source_identity, *(_descriptor_identity(value) for value in sidecar_descriptors)}
    )
    before_fds = _process_file_descriptors()
    if before_fds is None:
        _close_descriptors(sidecar_descriptors)
        raise DatabaseBackupError("secure database source verification is unavailable")
    uri = f"file:{quote(str(source), safe='/')}?mode=ro"
    try:
        with (
            sqlite3.connect(uri, uri=True) as source_db,
            sqlite3.connect(":memory:") as snapshot,
        ):
            _require_pinned_backup_connection(
                source=source,
                source_descriptor=source_descriptor,
                source_identity=source_identity,
                sidecar_identities=sidecar_identities,
                before_fds=before_fds,
                allowed_identities=allowed_identities,
            )
            source_db.backup(snapshot)
            _require_pinned_backup_connection(
                source=source,
                source_descriptor=source_descriptor,
                source_identity=source_identity,
                sidecar_identities=sidecar_identities,
                before_fds=before_fds,
                allowed_identities=allowed_identities,
            )
            result = snapshot.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise DatabaseBackupError("database backup failed integrity verification")
            payload = snapshot.serialize()
        _require_source_identity(source, source_identity)
        _require_sidecar_identities(sidecar_identities)
        return payload
    finally:
        _close_descriptors(sidecar_descriptors)


def _pin_backup_sidecars(
    source: Path,
) -> tuple[tuple[int, ...], tuple[tuple[Path, tuple[int, int] | None], ...]]:
    descriptors: list[int] = []
    identities: list[tuple[Path, tuple[int, int] | None]] = []
    try:
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = source.with_name(f"{source.name}{suffix}")
            try:
                descriptor = os.open(
                    sidecar,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
            except FileNotFoundError:
                identities.append((sidecar, None))
                continue
            if not _descriptor_matches_regular_path(sidecar, descriptor):
                os.close(descriptor)
                raise DatabaseBackupError("database sidecar changed during backup")
            descriptors.append(descriptor)
            identities.append((sidecar, _descriptor_identity(descriptor)))
        return tuple(descriptors), tuple(identities)
    except BaseException:
        _close_descriptors(descriptors)
        raise


def _require_pinned_backup_connection(
    *,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
    sidecar_identities: tuple[tuple[Path, tuple[int, int] | None], ...],
    before_fds: frozenset[int],
    allowed_identities: frozenset[tuple[int, int]],
) -> None:
    if not _connection_opened_held_inode(
        source_descriptor,
        before_fds,
        allowed_identities,
    ):
        raise DatabaseBackupError("database source changed during backup")
    _require_source_identity(source, source_identity)
    _require_sidecar_identities(sidecar_identities)


def _retryable_identity_churn(error: DatabaseBackupError) -> bool:
    message = str(error)
    return message in {
        "database source changed during backup",
        "database sidecar changed during backup",
    }


def _sqlite_path(database_url: str) -> Path:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database:
        raise ValueError("operations require file-backed SQLite")
    if parsed.database == ":memory:":
        raise ValueError("operations require file-backed SQLite")
    return Path(parsed.database).expanduser().absolute()


def _regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode)


def _integrity_ok(path: Path) -> bool:
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(result == ("ok",))
    except sqlite3.Error:
        return False


def _write_transaction_available(path: Path) -> bool:
    """Check queue write access without committing a persistent mutation."""

    descriptor = -1
    pinned_sidecars: list[int] = []
    try:
        parent_mode = path.parent.lstat().st_mode
        if not stat.S_ISDIR(parent_mode) or parent_mode & 0o222 == 0:
            return False
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        if not _descriptor_matches_regular_path(path, descriptor):
            return False
        allowed_identities = {_descriptor_identity(descriptor)}
        if os.name != "nt":
            for suffix in ("-wal", "-shm"):
                sidecar = path.with_name(f"{path.name}{suffix}")
                try:
                    sidecar_descriptor = os.open(
                        sidecar, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                    )
                except FileNotFoundError:
                    continue
                if not _descriptor_matches_regular_path(sidecar, sidecar_descriptor):
                    os.close(sidecar_descriptor)
                    return False
                pinned_sidecars.append(sidecar_descriptor)
                allowed_identities.add(_descriptor_identity(sidecar_descriptor))
        before_fds = _process_file_descriptors() if os.name != "nt" else None
        uri = f"file:{quote(str(path), safe='/')}?mode=rw"
        probe_table = f"__openbiliclaw_write_probe_{secrets.token_hex(8)}"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            if before_fds is not None and not _connection_opened_held_inode(
                descriptor, before_fds, frozenset(allowed_identities)
            ):
                return False
            if not _descriptor_matches_regular_path(path, descriptor):
                return False
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f'CREATE TABLE "{probe_table}" (value INTEGER NOT NULL)')
            connection.execute(f'INSERT INTO "{probe_table}" (value) VALUES (1)')
            connection.execute("ROLLBACK")
        return _descriptor_matches_regular_path(path, descriptor)
    except (OSError, sqlite3.Error):
        return False
    finally:
        for sidecar_descriptor in pinned_sidecars:
            os.close(sidecar_descriptor)
        if descriptor >= 0:
            os.close(descriptor)


def _process_file_descriptors() -> frozenset[int] | None:
    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            candidates = (int(name) for name in os.listdir(directory) if name.isdigit())
            opened: set[int] = set()
            for descriptor in candidates:
                try:
                    os.fstat(descriptor)
                except OSError:
                    continue
                opened.add(descriptor)
            return frozenset(opened)
        except OSError:
            continue
    return None


def _connection_opened_held_inode(
    descriptor: int,
    before: frozenset[int] | None,
    allowed_identities: frozenset[tuple[int, int]] | None = None,
) -> bool:
    if before is None:
        return False
    held = os.fstat(descriptor)
    after = _process_file_descriptors()
    if after is None:
        return False
    regular_identities: set[tuple[int, int]] = set()
    for candidate in after - before:
        try:
            opened = os.fstat(candidate)
        except OSError:
            continue
        if stat.S_ISREG(opened.st_mode):
            regular_identities.add((opened.st_dev, opened.st_ino))
    held_identity = (held.st_dev, held.st_ino)
    allowed = allowed_identities or frozenset({held_identity})
    return held_identity in regular_identities and regular_identities.issubset(allowed)


def _descriptor_matches_regular_path(path: Path, descriptor: int) -> bool:
    """Return whether a pathname still names the held regular-file inode."""

    try:
        path_metadata = path.lstat()
        held_metadata = os.fstat(descriptor)
    except OSError:
        return False
    return (
        stat.S_ISREG(path_metadata.st_mode)
        and stat.S_ISREG(held_metadata.st_mode)
        and (path_metadata.st_dev, path_metadata.st_ino)
        == (held_metadata.st_dev, held_metadata.st_ino)
    )


def _secure_backup_platform_supported() -> bool:
    """Return whether handle-relative, no-follow backup operations are available."""

    if os.name == "nt":
        return False
    libc = ctypes.CDLL(None)
    if sys.platform == "darwin":
        return hasattr(libc, "fclonefileat")
    if sys.platform.startswith("linux"):
        return hasattr(os, "O_TMPFILE") and hasattr(libc, "linkat")
    return False


def _migration_at_head(*, database_path: Path, database_url: str, alembic_ini: Path) -> bool:
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", database_url)
    try:
        heads = set(ScriptDirectory.from_config(config).get_heads())
        uri = f"file:{quote(str(database_path), safe='/')}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except (OSError, sqlite3.Error):
        return False
    return bool(heads) and {str(row[0]) for row in rows} == heads


def _paths_separate(first: Path, second: Path) -> bool:
    if first == second:
        return False
    try:
        return not os.path.samefile(first, second)
    except OSError:
        return True


def _open_source(source: Path) -> tuple[int, tuple[int, int]]:
    """Open and pin the requested database inode without following symlinks."""

    try:
        metadata = source.lstat()
    except OSError as exc:
        raise DatabaseBackupError("configured vNext database does not exist") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise DatabaseBackupError("database source must not be a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise DatabaseBackupError("configured vNext database is not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise DatabaseBackupError("database source changed before it could be opened") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise DatabaseBackupError("configured vNext database is not a regular file")
        identity = (opened.st_dev, opened.st_ino)
        expected = (metadata.st_dev, metadata.st_ino)
        if identity != expected:
            raise DatabaseBackupError("database source changed before it could be opened")
        _require_source_identity(source, identity)
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _open_directory(directory: Path) -> int | None:
    if os.name == "nt":
        return None
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(directory, directory_flags)


def _require_sidecar_identities(
    sidecars: tuple[tuple[Path, tuple[int, int] | None], ...],
) -> None:
    for path, identity in sidecars:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if identity is None:
                continue
            raise DatabaseBackupError("database sidecar changed during backup") from None
        if identity is None:
            raise DatabaseBackupError("database sidecar changed during backup")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise DatabaseBackupError("database sidecar changed during backup")
        if (metadata.st_dev, metadata.st_ino) != identity:
            raise DatabaseBackupError("database sidecar changed during backup")


def _close_descriptors(descriptors: tuple[int, ...] | list[int]) -> None:
    first_error: OSError | None = None
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise DatabaseBackupError("database backup descriptor cleanup failed") from first_error


def _require_source_identity(source: Path, identity: tuple[int, int]) -> None:
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise DatabaseBackupError("database source changed during backup") from exc
    if stat.S_ISLNK(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
        raise DatabaseBackupError("database source changed during backup")


def _path_identity(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    return metadata.st_dev, metadata.st_ino


def _open_destination_directory(path: Path) -> _DestinationDirectory:
    identity = _path_identity(path)
    descriptor = _open_directory(path)
    if descriptor is not None:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != identity:
            os.close(descriptor)
            raise DatabaseBackupError("backup destination directory changed")
    directory = _DestinationDirectory(path=path, descriptor=descriptor, identity=identity)
    _require_destination_directory(directory)
    return directory


def _create_anonymous_payload(directory: _DestinationDirectory) -> int:
    descriptor = directory.descriptor
    if descriptor is None:
        raise DatabaseBackupError("secure backup publication is unavailable")
    if sys.platform.startswith("linux") and hasattr(os, "O_TMPFILE"):
        try:
            return os.open(
                ".",
                os.O_RDWR | os.O_TMPFILE,
                0o600,
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise DatabaseBackupError("secure backup publication is unavailable") from exc
    raise DatabaseBackupError("secure backup publication is unavailable")


def _descriptor_identity(descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    return metadata.st_dev, metadata.st_ino


def _require_unlinked_payload(descriptor: int, identity: tuple[int, int]) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 0
        or (metadata.st_dev, metadata.st_ino) != identity
    ):
        raise DatabaseBackupError("backup payload lost private ownership")


def _atomic_publish_no_replace(
    *, directory: _DestinationDirectory, payload: _OwnedFile, target: Path
) -> _OwnedFile:
    return _atomic_publish_no_replace_impl(
        directory=directory,
        payload=payload,
        target=target,
    )


def _atomic_publish_no_replace_impl(
    *, directory: _DestinationDirectory, payload: _OwnedFile, target: Path
) -> _OwnedFile:
    """Publish a held anonymous payload atomically without replacing a name."""

    descriptor = directory.descriptor
    if descriptor is None:
        raise DatabaseBackupError("backup payload changed before publication")
    _require_unlinked_payload(payload.descriptor, payload.identity)
    _require_destination_directory(directory)
    if sys.platform.startswith("linux"):
        _link_anonymous_linux(payload.descriptor, descriptor, target.name)
        same_inode_required = True
    elif sys.platform == "darwin":
        _clone_held_macos(payload.descriptor, descriptor, target.name)
        same_inode_required = False
    else:
        raise DatabaseBackupError("secure backup publication is unavailable")
    final_descriptor = -1
    try:
        final_descriptor = os.open(
            target.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        final_identity = _descriptor_identity(final_descriptor)
        if same_inode_required and final_identity != payload.identity:
            raise DatabaseBackupError("backup destination changed during publication")
        if not same_inode_required and (
            not _descriptors_equal(payload.descriptor, final_descriptor)
            or not _sqlite_descriptor_integrity_ok(final_descriptor)
        ):
            raise DatabaseBackupError("backup destination failed held-payload validation")
        final = _OwnedFile(target, final_descriptor, final_identity)
        _require_owned_entry(directory, final)
        os.fchmod(final_descriptor, 0o600)
        os.fsync(final_descriptor)
        return final
    except BaseException:
        if final_descriptor >= 0:
            os.close(final_descriptor)
        raise


def _link_anonymous_linux(source: int, destination_directory: int, name: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.linkat(
        ctypes.c_int(source),
        ctypes.c_char_p(b""),
        ctypes.c_int(destination_directory),
        ctypes.c_char_p(os.fsencode(name)),
        ctypes.c_int(0x1000),
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise DatabaseBackupError("backup destination already exists")
    fallback_errors = {errno.EINVAL, errno.ENOENT, errno.EPERM}
    if hasattr(errno, "EOPNOTSUPP"):
        fallback_errors.add(errno.EOPNOTSUPP)
    if error in fallback_errors and _proc_fd_matches(source):
        proc_descriptor = os.fsencode(f"/proc/self/fd/{source}")
        result = libc.linkat(
            ctypes.c_int(-100),
            ctypes.c_char_p(proc_descriptor),
            ctypes.c_int(destination_directory),
            ctypes.c_char_p(os.fsencode(name)),
            ctypes.c_int(0x400),
        )
        if result == 0:
            return
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise DatabaseBackupError("backup destination already exists")
    cause = OSError(error, os.strerror(error))
    raise DatabaseBackupError("secure backup publication failed") from cause


def _proc_fd_matches(descriptor: int) -> bool:
    """Verify the procfs descriptor link still resolves to the held inode."""

    try:
        held = os.fstat(descriptor)
        proc = os.stat(f"/proc/self/fd/{descriptor}", follow_symlinks=True)
    except OSError:
        return False
    return (
        stat.S_ISREG(held.st_mode)
        and stat.S_ISREG(proc.st_mode)
        and (held.st_dev, held.st_ino) == (proc.st_dev, proc.st_ino)
    )


def _clone_held_macos(source: int, destination_directory: int, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.fclonefileat(
        ctypes.c_int(source),
        ctypes.c_int(destination_directory),
        ctypes.c_char_p(os.fsencode(destination)),
        ctypes.c_int(0),
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise DatabaseBackupError("backup destination already exists")
    cause = OSError(error, os.strerror(error))
    raise DatabaseBackupError("secure backup publication failed") from cause


def _descriptors_equal(first: int, second: int) -> bool:
    first_size = os.fstat(first).st_size
    if os.fstat(second).st_size != first_size:
        return False
    offset = 0
    while offset < first_size:
        size = min(_COPY_BUFFER_SIZE, first_size - offset)
        if os.pread(first, size, offset) != os.pread(second, size, offset):
            return False
        offset += size
    return True


def _sqlite_descriptor_integrity_ok(descriptor: int) -> bool:
    size = os.fstat(descriptor).st_size
    payload = bytearray()
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(_COPY_BUFFER_SIZE, size - offset), offset)
        if not chunk:
            return False
        payload.extend(chunk)
        offset += len(chunk)
    if len(payload) < 20 or payload[:16] != b"SQLite format 3\x00":
        return False
    # deserialize() cannot open a WAL-mode header without a filesystem WAL/SHM
    # peer.  The held snapshot is already complete, so validate an in-memory
    # copy with only the transient journal-mode bytes normalized.
    if payload[18:20] == b"\x02\x02":
        payload[18:20] = b"\x01\x01"
    try:
        with sqlite3.connect(":memory:") as connection:
            connection.deserialize(bytes(payload))
            result: object = connection.execute("PRAGMA integrity_check").fetchone()
            return result == ("ok",)
    except sqlite3.Error:
        return False


def _close_destination_directory(directory: _DestinationDirectory) -> None:
    if directory.descriptor is not None:
        os.close(directory.descriptor)


def _require_destination_directory(directory: _DestinationDirectory) -> None:
    try:
        metadata = directory.path.lstat()
    except OSError as exc:
        raise DatabaseBackupError("backup destination directory changed") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != directory.identity
    ):
        raise DatabaseBackupError("backup destination directory changed")


def _entry_metadata(directory: _DestinationDirectory, path: Path) -> os.stat_result:
    if directory.descriptor is None:
        return path.lstat()
    return os.stat(path.name, dir_fd=directory.descriptor, follow_symlinks=False)


def _require_owned_entry(directory: _DestinationDirectory, owned: _OwnedFile) -> None:
    message = (
        "backup temporary file changed"
        if owned.path.name.startswith(".backup-")
        else "backup destination changed"
    )
    try:
        metadata = _entry_metadata(directory, owned.path)
    except OSError as exc:
        raise DatabaseBackupError(message) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != owned.identity
    ):
        raise DatabaseBackupError(message)


def _prepare_destination(destination: Path) -> Path:
    expanded = destination.expanduser().absolute()
    expanded.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = expanded.parent.resolve(strict=True)
    target = parent / expanded.name
    try:
        target.lstat()
    except FileNotFoundError:
        return target
    except OSError as exc:
        raise DatabaseBackupError("backup destination cannot be inspected") from exc
    raise DatabaseBackupError("backup destination already exists")


def _sync_descriptor(descriptor: int, identity: tuple[int, int]) -> None:
    metadata = os.fstat(descriptor)
    if (metadata.st_dev, metadata.st_ino) != identity:
        raise DatabaseBackupError("backup temporary file changed during creation")
    os.fchmod(descriptor, 0o600)
    os.fsync(descriptor)


def _sync_directory(directory: _DestinationDirectory) -> None:
    if directory.descriptor is not None:
        os.fsync(directory.descriptor)
        return
    descriptor = os.open(directory.path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "DatabaseBackupError",
    "RuntimeDatabaseHealth",
    "SQLiteOperationalStore",
    "SchemaNotReadyError",
    "require_schema_at_head",
]
