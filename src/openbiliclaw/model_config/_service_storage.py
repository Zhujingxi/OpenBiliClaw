"""Private disk, local-layer, backup, and atomic-write mechanics."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .migration import LegacyMigrationResult, MigrationReport, migrate_legacy_llm
from .revision import compute_model_revision
from .serialization import ModelConfigParseError, parse_model_config, render_model_config

if TYPE_CHECKING:
    from .types import ModelConfig


@dataclass(frozen=True)
class StorageError(ValueError):
    """Fieldized, secret-free persistence failure for the service facade."""

    path: str
    code: str
    message: str
    source: str = ""


@dataclass(frozen=True)
class StorageOverride:
    """One local override leaf supplied by a higher-precedence source."""

    path: str
    source: str


@dataclass(frozen=True)
class DiskState:
    """Private base/effective disk snapshot captured under the path lock."""

    models: ModelConfig = field(repr=False)
    persisted_models: ModelConfig = field(repr=False)
    revision: str
    source: str
    base_source: str
    authority_fingerprint: str = field(repr=False)
    migration_state: str
    migration: MigrationReport | None = None
    migration_result: LegacyMigrationResult | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    original: bytes = field(default=b"", repr=False)
    mode: int = 0o600
    existed: bool = False
    local_models: Mapping[str, object] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    local_legacy: bool = False
    overrides: tuple[StorageOverride, ...] = ()


@dataclass(frozen=True)
class _ModelSelection:
    models: ModelConfig = field(repr=False)
    source: str
    migration_state: str
    migration: MigrationReport | None = None
    migration_result: LegacyMigrationResult | None = field(
        default=None,
        repr=False,
        compare=False,
    )


class AtomicWriteError(OSError):
    """Secret-free atomic-write failure with replace-state metadata."""

    def __init__(self, *, replaced: bool) -> None:
        self.replaced = replaced
        super().__init__("atomic model configuration write failed")


_MISSING = object()


def _models_raw(models: ModelConfig) -> dict[str, object]:
    rendered = "\n".join(render_model_config(models)) + "\n"
    raw = tomllib.loads(rendered)["models"]
    if not isinstance(raw, dict):  # pragma: no cover - renderer invariant
        raise TypeError("models renderer did not produce a table")
    return cast("dict[str, object]", raw)


def _deep_merge(base: Mapping[str, object], override: Mapping[str, object]) -> dict[str, object]:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _leaf_paths(value: object, prefix: tuple[str, ...] = ()) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, Mapping):
        return (prefix,)
    paths: list[tuple[str, ...]] = []
    for key, item in value.items():
        if isinstance(key, str):
            paths.extend(_leaf_paths(item, (*prefix, key)))
    return tuple(paths)


def _path_value(value: Mapping[str, object], path: tuple[str, ...]) -> object:
    current: object = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _restore_path(
    target: dict[str, object],
    source: Mapping[str, object],
    path: tuple[str, ...],
) -> None:
    source_value = _path_value(source, path)
    if not path:
        return
    current: dict[str, object] = target
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    if source_value is _MISSING:
        current.pop(path[-1], None)
    else:
        current[path[-1]] = deepcopy(source_value)


def _select_models(
    raw: Mapping[str, object],
    environment: Mapping[str, str],
) -> _ModelSelection:
    if "models" in raw:
        models_raw = raw["models"]
        if not isinstance(models_raw, Mapping):
            raise ModelConfigParseError("models: expected a table")
        return _ModelSelection(
            models=parse_model_config(models_raw),
            source="native",
            migration_state="none",
        )
    if "llm" in raw:
        llm_raw = raw["llm"]
        if not isinstance(llm_raw, Mapping):
            raise ModelConfigParseError("llm: expected a table")
        migrated = migrate_legacy_llm(llm_raw, environment)
        return _ModelSelection(
            models=migrated.models,
            source="legacy",
            migration_state=("pending" if migrated.report.has_pending_decisions else "ready"),
            migration=migrated.report,
            migration_result=migrated,
        )
    from . import default_model_config

    return _ModelSelection(
        models=default_model_config(),
        source="default",
        migration_state="none",
    )


def _authority_fingerprint(
    raw: Mapping[str, object],
    local_raw: Mapping[str, object],
    *,
    base_source: str,
    effective_source: str,
) -> str:
    """Hash base persistence and effective local authority without disclosure."""
    base_key = "models" if base_source == "native" else "llm" if base_source == "legacy" else ""
    local_key = (
        "models"
        if effective_source == "native" and "models" in local_raw
        else "llm"
        if effective_source == "legacy" and "llm" in local_raw
        else ""
    )
    payload = (
        base_source,
        base_key,
        raw.get(base_key) if base_key else None,
        effective_source,
        local_key,
        local_raw.get(local_key) if local_key else None,
    )
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def _read_toml(path: Path, *, missing_ok: bool) -> tuple[bytes, dict[str, object]]:
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        if missing_ok:
            return b"", {}
        raise
    try:
        parsed = tomllib.loads(payload.decode("utf-8")) if payload else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise StorageError(
            "models",
            "config_parse_failed",
            "Model configuration is not valid TOML.",
            source=str(path),
        ) from None
    return payload, cast("dict[str, object]", parsed)


def read_disk_state(
    path: Path,
    local_path: Path | None,
    environment: Mapping[str, str],
) -> DiskState:
    """Read base and local layers once and return effective provenance."""
    try:
        original, raw = _read_toml(path, missing_ok=True)
        base_selection = _select_models(raw, environment)
    except OSError:
        raise StorageError(
            "models",
            "config_read_failed",
            "Model configuration could not be read safely.",
        ) from None
    except (ModelConfigParseError, TypeError):
        raise StorageError(
            "models",
            "config_parse_failed",
            "Model configuration is not valid TOML.",
        ) from None

    effective_selection = base_selection
    local_raw: dict[str, object] = {}
    local_models: Mapping[str, object] | None = None
    local_legacy = False
    overrides: tuple[StorageOverride, ...] = ()
    if local_path is not None:
        try:
            _local_bytes, local_raw = _read_toml(local_path, missing_ok=True)
        except OSError:
            raise StorageError(
                "models",
                "config_read_failed",
                "Local model overrides could not be read safely.",
                source=str(local_path),
            ) from None
        try:
            effective_selection = _select_models(
                _deep_merge(raw, local_raw),
                environment,
            )
        except (ModelConfigParseError, TypeError, ValueError):
            raise StorageError(
                "models",
                "config_parse_failed",
                "Local model overrides do not form a valid model configuration.",
                source=str(local_path),
            ) from None

        local_models_value = local_raw.get("models")
        local_llm_value = local_raw.get("llm")
        if effective_selection.source == "native" and local_models_value is not None:
            if not isinstance(local_models_value, Mapping):  # pragma: no cover - selection guard
                raise StorageError(
                    "models",
                    "config_parse_failed",
                    "Local model overrides must be a TOML table.",
                    source=str(local_path),
                )
            local_models = dict(local_models_value)
            overrides = tuple(
                StorageOverride(
                    path="models." + ".".join(item_path),
                    source=str(local_path),
                )
                for item_path in _leaf_paths(local_models)
            )
        elif effective_selection.source == "legacy" and local_llm_value is not None:
            if not isinstance(local_llm_value, Mapping):  # pragma: no cover - selection guard
                raise StorageError(
                    "llm",
                    "config_parse_failed",
                    "Local legacy model overrides must be a TOML table.",
                    source=str(local_path),
                )
            local_legacy = True
            overrides = tuple(
                StorageOverride(
                    path="llm." + ".".join(item_path),
                    source=str(local_path),
                )
                for item_path in _leaf_paths(local_llm_value)
            )

    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        existed = True
    except FileNotFoundError:
        mode = 0o600
        existed = False
    return DiskState(
        models=effective_selection.models,
        persisted_models=base_selection.models,
        revision=compute_model_revision(effective_selection.models),
        source=effective_selection.source,
        base_source=base_selection.source,
        authority_fingerprint=_authority_fingerprint(
            raw,
            local_raw,
            base_source=base_selection.source,
            effective_source=effective_selection.source,
        ),
        migration_state=effective_selection.migration_state,
        migration=effective_selection.migration,
        migration_result=effective_selection.migration_result,
        original=original,
        mode=mode,
        existed=existed,
        local_models=local_models,
        local_legacy=local_legacy,
        overrides=overrides,
    )


def split_local_candidate(
    state: DiskState,
    requested: ModelConfig,
    local_path: Path | None,
) -> tuple[ModelConfig, ModelConfig]:
    """Reject local-field edits and produce separate base/effective candidates."""
    source = str(local_path) if local_path is not None else "config.local.toml"
    if state.local_legacy:
        raise StorageError(
            "models",
            "local_override_blocks_migration",
            "Legacy local model overrides must be converted to [models] before saving.",
            source=source,
        )
    if state.local_models is None:
        return requested, requested

    requested_raw = _models_raw(requested)
    current_raw = _models_raw(state.models)
    persisted_raw = deepcopy(requested_raw)
    base_raw = _models_raw(state.persisted_models)
    for item_path in _leaf_paths(state.local_models):
        if _path_value(requested_raw, item_path) != _path_value(current_raw, item_path):
            raise StorageError(
                "models." + ".".join(item_path),
                "local_override_read_only",
                "This field is read-only because it is provided by a local override.",
                source=source,
            )
        _restore_path(persisted_raw, base_raw, item_path)

    try:
        persisted = parse_model_config(persisted_raw)
        effective = parse_model_config(_deep_merge(_models_raw(persisted), state.local_models))
    except (ModelConfigParseError, TypeError, ValueError):
        raise StorageError(
            "models",
            "local_override_merge_failed",
            "The base and local model layers cannot be saved safely.",
            source=source,
        ) from None
    return persisted, effective


def render_document(original: bytes, models: ModelConfig) -> bytes:
    """Render through the config module's source-preserving document editor."""
    from openbiliclaw.config import ConfigError, render_model_config_document

    try:
        return render_model_config_document(original, models)
    except ConfigError:
        raise StorageError(
            "models",
            "config_render_failed",
            "The model configuration could not be rendered safely.",
        ) from None


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EACCES}:
            return
        raise
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    replaced = False
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        replaced = True
        _fsync_directory(path.parent)
    except Exception:
        with suppress(OSError):
            os.close(fd)
        temp.unlink(missing_ok=True)
        raise AtomicWriteError(replaced=replaced) from None


def _restore_disk(state: DiskState, path: Path) -> bool:
    try:
        if state.existed:
            _atomic_write(path, state.original, state.mode)
        else:
            path.unlink(missing_ok=True)
            _fsync_directory(path.parent)
    except OSError:
        return False
    return True


def _backup_candidates(path: Path) -> tuple[Path, ...]:
    primary = path.with_name(f"{path.name}.pre-model-refactor.bak")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    alternates = tuple(
        path.with_name(f"{path.name}.pre-model-refactor.{stamp}.{index}.bak")
        for index in range(1, 101)
    )
    return (primary, *alternates)


def _create_legacy_backup(path: Path, payload: bytes, mode: int) -> Path:
    for backup in _backup_candidates(path):
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        except FileExistsError:
            continue
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(path.parent)
        except Exception:
            with suppress(OSError):
                os.close(fd)
            backup.unlink(missing_ok=True)
            with suppress(OSError):
                _fsync_directory(path.parent)
            raise OSError("legacy model backup failed") from None
        return backup
    raise OSError("legacy model backup name allocation failed")


def _remove_backup(path: Path | None) -> None:
    if path is None:
        return
    with suppress(OSError):
        path.unlink()
        _fsync_directory(path.parent)
