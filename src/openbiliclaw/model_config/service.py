"""Secret-safe model configuration reads and transactional saves."""

from __future__ import annotations

import asyncio
import os
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from openbiliclaw.config_write import (
    coordinated_config_disk_write,
    coordinated_config_write,
)

from ._service_storage import (
    AtomicWriteError as _AtomicWriteError,
)
from ._service_storage import (
    DiskState as _DiskState,
)
from ._service_storage import (
    StorageError as _StorageError,
)
from ._service_storage import (
    _atomic_write,
    _create_legacy_backup,
    _remove_backup,
    _restore_disk,
)
from ._service_storage import (
    read_disk_state as _read_disk_state,
)
from ._service_storage import (
    render_document as _render_document,
)
from ._service_storage import (
    split_local_candidate as _split_local_candidate,
)
from .endpoints import InvalidModelEndpointError, validated_native_base_url
from .migration import (
    MigrationReport,
    MigrationResolution,
    MigrationResolutionError,
    apply_migration_resolutions,
)
from .registry import connection_type_registry
from .revision import compute_model_revision
from .types import (
    ChatConnection,
    CredentialConfig,
    CredentialSource,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    ModelConfig,
    ModelConfigIssue,
)
from .validation import validate_model_config

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

CredentialActionName: TypeAlias = Literal["keep", "set", "clear", "env"]
RouteRecord: TypeAlias = ChatConnection | EmbeddingProviderConfig


@dataclass(frozen=True)
class CredentialAction:
    """One explicit credential transition; secret values stay out of repr."""

    action: CredentialActionName
    value: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PublicCredentialStatus:
    """Safe credential metadata for API/UI snapshots."""

    source: CredentialSource
    configured: bool
    env_var: str = ""
    credential_ref: str = ""


@dataclass(frozen=True)
class PublicChatConnection:
    """A chat connection with credential state instead of credential data."""

    id: str
    name: str
    type: str
    model: str
    preset: str
    base_url: str
    credential: PublicCredentialStatus
    api_mode: str
    reasoning_effort: str
    http_referer: str
    x_title: str
    num_ctx: int


@dataclass(frozen=True)
class PublicChatRouteConfig:
    """Public ordered Chat route."""

    connections: tuple[PublicChatConnection, ...]
    concurrency: int
    timeout_seconds: int


@dataclass(frozen=True)
class PublicEmbeddingProvider:
    """An embedding provider with credential state instead of data."""

    id: str
    name: str
    type: str
    preset: str
    base_url: str
    credential: PublicCredentialStatus


@dataclass(frozen=True)
class PublicEmbeddingRouteConfig:
    """Public ordered Embedding route and shared model settings."""

    enabled: bool
    settings: EmbeddingModelSettings
    providers: tuple[PublicEmbeddingProvider, ...]


@dataclass(frozen=True)
class PublicModelConfig:
    """Public model configuration that cannot contain a raw credential."""

    schema_version: int
    chat: PublicChatRouteConfig
    embedding: PublicEmbeddingRouteConfig


@dataclass(frozen=True)
class ModelConfigOverride:
    """One read-only higher-precedence model path and its source file."""

    path: str
    source: str


@dataclass(frozen=True)
class ModelConfigSnapshot:
    """A public-only read model; it never retains credential values."""

    revision: str
    models: PublicModelConfig
    source: str
    migration_state: str = "none"
    migration: MigrationReport | None = None
    overrides: tuple[ModelConfigOverride, ...] = ()

    @property
    def public(self) -> ModelConfigSnapshot:
        """Compatibility view used by API/UI callers."""
        return self

    @property
    def override_paths(self) -> tuple[str, ...]:
        """Return just the stable field paths for compact clients."""
        return tuple(item.path for item in self.overrides)


@dataclass(frozen=True)
class ModelConfigFieldError:
    """A fieldized error with no submitted or persisted value."""

    path: str
    code: str
    message: str
    source: str = ""
    connection_id: str | None = None


class ModelConfigValidationError(ValueError):
    """A collection of fieldized, secret-safe validation errors."""

    def __init__(self, errors: tuple[ModelConfigFieldError, ...]) -> None:
        self.errors = errors
        message = errors[0].message if errors else "Model configuration is invalid."
        super().__init__(message)


@dataclass(frozen=True)
class ModelConfigSaveRequest:
    """One revision-guarded complete draft plus explicit credential actions."""

    revision: str
    models: ModelConfig | None = None
    credential_actions: Mapping[str, CredentialAction] = field(default_factory=dict, repr=False)
    migration_resolutions: Mapping[str, MigrationResolution] = field(
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "credential_actions",
            MappingProxyType(dict(self.credential_actions)),
        )
        object.__setattr__(
            self,
            "migration_resolutions",
            MappingProxyType(dict(self.migration_resolutions)),
        )


@dataclass(frozen=True)
class ModelConfigSaveResult:
    """Direct transaction result; backup paths never enter its snapshot."""

    ok: bool
    snapshot: ModelConfigSnapshot
    reloaded: bool = False
    rollback_applied: bool = False
    conflict: bool = False
    errors: tuple[ModelConfigFieldError, ...] = ()
    backup_path: Path | None = field(default=None, repr=False)

    @property
    def revision(self) -> str:
        """Return the resulting or latest known public revision."""
        return self.snapshot.revision

    @property
    def latest_revision(self) -> str:
        """Return the current revision used by conflict responses."""
        return self.snapshot.revision


@dataclass(frozen=True)
class ModelConfigProbeResult:
    """Secret-free result from probing exactly one draft record."""

    ok: bool
    connection_id: str
    capability: Literal["chat", "embedding"]
    observed_dimension: int = 0
    error_code: str = ""
    message: str = ""


@dataclass(frozen=True)
class ModelConfigProbeCapture:
    """One secret-bearing probe draft bound to an exact persisted revision."""

    revision: str
    draft: RouteRecord = field(repr=False)
    settings: EmbeddingModelSettings | None = field(default=None, repr=False)


class ModelConfigRevisionConflictError(RuntimeError):
    """A revision-bound operation observed a newer public snapshot."""

    def __init__(self, snapshot: ModelConfigSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__("Model configuration revision changed.")


class ModelConfigCommitBlockedError(RuntimeError):
    """The canonical commit guard rejected a candidate before persistence."""


class ModelConfigProbeBlockedError(RuntimeError):
    """The operation guard rejected a probe inside its capture boundary."""


class ModelRuntimeCoordinator(Protocol):
    """Build-before-write and identity-based runtime swap contract."""

    @property
    def current_model_candidate(self) -> object | None: ...

    async def build_model_candidate(self, models: ModelConfig, revision: str) -> object: ...

    def restage_model_candidate(
        self,
        candidate: object,
        models: ModelConfig,
        revision: str,
    ) -> object: ...

    async def swap_model_candidate(self, candidate: object) -> object | None: ...

    async def restore_model_candidate(self, candidate: object | None) -> None: ...

    async def probe_model_draft(
        self,
        draft: RouteRecord,
        settings: EmbeddingModelSettings | None = None,
    ) -> ModelConfigProbeResult: ...


_PATH_LOCKS: dict[Path, asyncio.Lock] = {}


def _path_lock(path: Path) -> asyncio.Lock:
    return _PATH_LOCKS.setdefault(path, asyncio.Lock())


def _public_credential(credential: CredentialConfig) -> PublicCredentialStatus:
    return PublicCredentialStatus(
        source=credential.source,
        configured=credential.source != "none" and bool(credential.value),
        env_var=credential.value if credential.source == "env" else "",
        credential_ref=credential.value if credential.source == "oauth" else "",
    )


def _public_models(models: ModelConfig) -> PublicModelConfig:
    return PublicModelConfig(
        schema_version=models.schema_version,
        chat=PublicChatRouteConfig(
            connections=tuple(
                PublicChatConnection(
                    id=item.id,
                    name=item.name,
                    type=item.type,
                    model=item.model,
                    preset=item.preset,
                    base_url=item.base_url,
                    credential=_public_credential(item.credential),
                    api_mode=item.api_mode,
                    reasoning_effort=item.reasoning_effort,
                    http_referer=item.http_referer,
                    x_title=item.x_title,
                    num_ctx=item.num_ctx,
                )
                for item in models.chat.connections
            ),
            concurrency=models.chat.concurrency,
            timeout_seconds=models.chat.timeout_seconds,
        ),
        embedding=PublicEmbeddingRouteConfig(
            enabled=models.embedding.enabled,
            settings=models.embedding.settings,
            providers=tuple(
                PublicEmbeddingProvider(
                    id=item.id,
                    name=item.name,
                    type=item.type,
                    preset=item.preset,
                    base_url=item.base_url,
                    credential=_public_credential(item.credential),
                )
                for item in models.embedding.providers
            ),
        ),
    )


def _field_error(issue: ModelConfigIssue) -> ModelConfigFieldError:
    return ModelConfigFieldError(
        path=issue.path,
        code=issue.code,
        message=issue.message,
        connection_id=issue.connection_id,
    )


def _validation_error(
    path: str,
    code: str,
    message: str,
    *,
    source: str = "",
) -> ModelConfigValidationError:
    return ModelConfigValidationError((ModelConfigFieldError(path, code, message, source=source),))


def _validate_action(connection_id: str, action: CredentialAction) -> None:
    path = f"models.credentials.{connection_id}"
    if action.action not in {"keep", "set", "clear", "env"}:
        raise _validation_error(
            path,
            "invalid_credential_action",
            "Credential action is invalid.",
        )
    value = action.value
    if action.action in {"keep", "clear"}:
        if value not in {None, ""}:
            raise _validation_error(
                path,
                "unexpected_credential_value",
                "This credential action does not accept a value.",
            )
        return
    if not isinstance(value, str) or not value or value != value.strip():
        raise _validation_error(
            path,
            "invalid_credential_value",
            "Credential input must be a nonempty unmasked value.",
        )
    if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
        raise _validation_error(
            path,
            "invalid_credential_value",
            "Credential input contains invalid whitespace or control characters.",
        )
    if "****" in value or set(value) <= {"*", "•", "●", "·"}:
        raise _validation_error(
            path,
            "masked_credential_value",
            "Masked credential text cannot be saved as a secret.",
        )
    if action.action == "env" and not _valid_env_name(value):
        raise _validation_error(
            path,
            "invalid_environment_name",
            "Environment credential references require a valid variable name.",
        )


def _valid_env_name(value: str) -> bool:
    if not value or not (value[0].isascii() and (value[0].isalpha() or value[0] == "_")):
        return False
    return all(char.isascii() and (char.isalnum() or char == "_") for char in value)


def _apply_credential_actions(
    candidate: ModelConfig,
    persisted: ModelConfig,
    actions: Mapping[str, CredentialAction],
) -> ModelConfig:
    persisted_credentials: dict[str, CredentialConfig] = {
        item.id: item.credential for item in persisted.chat.connections
    }
    persisted_credentials.update(
        {item.id: item.credential for item in persisted.embedding.providers}
    )
    candidate_ids = {item.id for item in candidate.chat.connections} | {
        item.id for item in candidate.embedding.providers
    }
    unknown = sorted(set(actions) - candidate_ids)
    if unknown:
        raise _validation_error(
            f"models.credentials.{unknown[0]}",
            "unknown_credential_target",
            "Credential action target does not exist.",
        )

    def credential_for(
        connection_id: str,
        current: CredentialConfig,
        connection_type: str,
    ) -> CredentialConfig:
        action = actions.get(connection_id)
        if connection_type == "codex_oauth" and (action is None or action.action == "keep"):
            return CredentialConfig(source="oauth", value="codex")
        if action is None or action.action == "keep":
            previous = persisted_credentials.get(connection_id)
            if previous is not None:
                return previous
            if current.source == "none":
                return current
            if current.source == "oauth" and current.value == "codex":
                return current
            raise _validation_error(
                f"models.credentials.{connection_id}",
                "credential_action_required",
                "New credentials require an explicit credential action.",
            )
        if action.action == "clear":
            return CredentialConfig()
        if action.action == "env":
            return CredentialConfig(source="env", value=action.value or "")
        return CredentialConfig(source="inline", value=action.value or "")

    chat = tuple(
        replace(item, credential=credential_for(item.id, item.credential, item.type))
        for item in candidate.chat.connections
    )
    embedding = tuple(
        replace(item, credential=credential_for(item.id, item.credential, item.type))
        for item in candidate.embedding.providers
    )
    return replace(
        candidate,
        chat=replace(candidate.chat, connections=chat),
        embedding=replace(candidate.embedding, providers=embedding),
    )


def _validate_candidate(models: ModelConfig) -> None:
    issues = validate_model_config(models, connection_type_registry())
    if issues:
        raise ModelConfigValidationError(tuple(_field_error(issue) for issue in issues))


def _validate_public_endpoints(*candidates: ModelConfig) -> None:
    """Validate endpoint-only policy across persistence and effective views."""
    errors: list[ModelConfigFieldError] = []
    seen: set[tuple[str, str | None]] = set()
    for models in candidates:
        records: tuple[tuple[RouteRecord, str], ...] = tuple(
            (record, f"models.chat.connections[{index}]")
            for index, record in enumerate(models.chat.connections)
        ) + tuple(
            (record, f"models.embedding.providers[{index}]")
            for index, record in enumerate(models.embedding.providers)
        )
        for record, path in records:
            try:
                validated_native_base_url(record.base_url)
            except InvalidModelEndpointError:
                connection_id = record.id.strip() or None
                key = (path, connection_id)
                if key in seen:
                    continue
                seen.add(key)
                errors.append(
                    ModelConfigFieldError(
                        path=f"{path}.base_url",
                        code="invalid_endpoint",
                        message="Base URL must be a safe HTTP or HTTPS endpoint.",
                        connection_id=connection_id,
                    )
                )
    if errors:
        raise ModelConfigValidationError(tuple(errors))


async def _restore_transaction(
    state: _DiskState,
    path: Path,
    coordinator: ModelRuntimeCoordinator,
    previous: object | None,
) -> bool:
    """Restore both authorities; shield the short runtime-pointer swap."""
    with coordinated_config_disk_write(path):
        disk_restored = _restore_disk(state, path)
    try:
        await asyncio.shield(coordinator.restore_model_candidate(previous))
    except Exception:
        return False
    return disk_restored


async def _capture_current_model_candidate(
    coordinator: ModelRuntimeCoordinator,
) -> object | None:
    """Use lifecycle-aware capture when a coordinator exposes that capability."""
    capture = getattr(coordinator, "capture_current_model_candidate", None)
    if callable(capture):
        callback = cast("Callable[[], Awaitable[object | None]]", capture)
        return await callback()
    return coordinator.current_model_candidate


class ModelConfigService:
    """Read public model snapshots and commit complete candidates atomically."""

    def __init__(
        self,
        path: str | Path,
        coordinator: ModelRuntimeCoordinator,
        *,
        local_path: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        precommit_guard: Callable[[], bool] | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        selected_local = (
            Path(local_path).expanduser().resolve()
            if local_path is not None
            else self.path.with_name("config.local.toml")
        )
        self.local_path = selected_local if selected_local != self.path else None
        self.coordinator = coordinator
        self._precommit_guard = precommit_guard
        self._environment = MappingProxyType(
            dict(os.environ if environment is None else environment)
        )

    def _read_state(self) -> _DiskState:
        try:
            state = _read_disk_state(self.path, self.local_path, self._environment)
        except _StorageError as exc:
            raise _validation_error(
                exc.path,
                exc.code,
                exc.message,
                source=exc.source,
            ) from None
        _validate_public_endpoints(state.persisted_models, state.models)
        return state

    @staticmethod
    def _snapshot(state: _DiskState) -> ModelConfigSnapshot:
        return ModelConfigSnapshot(
            revision=state.revision,
            models=_public_models(state.models),
            source=state.source,
            migration_state=state.migration_state,
            migration=state.migration,
            overrides=tuple(
                ModelConfigOverride(path=item.path, source=item.source) for item in state.overrides
            ),
        )

    def read(self) -> ModelConfigSnapshot:
        """Return a fresh public snapshot without credential values."""
        return self._snapshot(self._read_state())

    @staticmethod
    def _migration_candidate(
        state: _DiskState,
        candidate: ModelConfig,
        resolutions: Mapping[str, MigrationResolution],
    ) -> ModelConfig:
        migration = state.migration_result
        if migration is None:
            if resolutions:
                raise _validation_error(
                    "models.migration",
                    "unexpected_migration_resolution",
                    "Migration resolutions are only valid for a legacy configuration.",
                )
            return candidate

        if migration.report.has_pending_decisions:
            try:
                resolved = apply_migration_resolutions(
                    replace(migration, models=candidate),
                    resolutions,
                )
            except MigrationResolutionError:
                raise _validation_error(
                    "models.migration",
                    "migration_resolution_required",
                    "Every blocking legacy migration issue requires a valid closed resolution.",
                ) from None
            return resolved

        if resolutions:
            raise _validation_error(
                "models.migration",
                "unexpected_migration_resolution",
                "This legacy configuration has no pending migration decisions.",
            )
        return candidate

    def _split_local_candidate(
        self,
        state: _DiskState,
        requested: ModelConfig,
    ) -> tuple[ModelConfig, ModelConfig]:
        try:
            return _split_local_candidate(state, requested, self.local_path)
        except _StorageError as exc:
            raise _validation_error(
                exc.path,
                exc.code,
                exc.message,
                source=exc.source,
            ) from None

    async def save(self, request: ModelConfigSaveRequest) -> ModelConfigSaveResult:
        """Build, persist, then swap one complete model candidate."""
        async with _path_lock(self.path):
            state = self._read_state()
            snapshot = self._snapshot(state)
            if request.revision != state.revision:
                return ModelConfigSaveResult(ok=False, snapshot=snapshot, conflict=True)

            for connection_id, action in request.credential_actions.items():
                _validate_action(connection_id, action)
            requested = _apply_credential_actions(
                request.models or state.models,
                state.models,
                request.credential_actions,
            )
            requested = self._migration_candidate(
                state,
                requested,
                request.migration_resolutions,
            )
            persisted, effective = self._split_local_candidate(state, requested)
            _validate_public_endpoints(persisted, effective)
            _validate_candidate(effective)
            revision = compute_model_revision(effective)
            try:
                runtime_candidate = await self.coordinator.build_model_candidate(
                    effective,
                    revision,
                )
            except Exception:
                error = ModelConfigFieldError(
                    "models",
                    "candidate_build_failed",
                    "The model runtime candidate could not be built.",
                )
                return ModelConfigSaveResult(ok=False, snapshot=snapshot, errors=(error,))

            # Candidate construction can await while ordinary writers finish.
            # Join the canonical boundary only at commit time, then re-read and
            # rebase under its bounded disk gate immediately before replacement.
            async with coordinated_config_write(self.path):
                if self._precommit_guard is not None and self._precommit_guard():
                    raise ModelConfigCommitBlockedError("Model configuration commit is blocked.")
                with coordinated_config_disk_write(self.path):
                    latest = self._read_state()
                    latest_snapshot = self._snapshot(latest)
                    if (
                        latest.revision != state.revision
                        or latest.authority_fingerprint != state.authority_fingerprint
                    ):
                        return ModelConfigSaveResult(
                            ok=False,
                            snapshot=latest_snapshot,
                            conflict=True,
                        )

                    persisted, effective = self._split_local_candidate(latest, requested)
                    _validate_public_endpoints(persisted, effective)
                    _validate_candidate(effective)
                    rebased_revision = compute_model_revision(effective)
                    if rebased_revision != revision:
                        return ModelConfigSaveResult(
                            ok=False,
                            snapshot=latest_snapshot,
                            conflict=True,
                        )
                    try:
                        payload = _render_document(latest.original, persisted)
                    except _StorageError as exc:
                        error = ModelConfigFieldError(
                            exc.path,
                            exc.code,
                            exc.message,
                            source=exc.source,
                        )
                        return ModelConfigSaveResult(
                            ok=False,
                            snapshot=latest_snapshot,
                            errors=(error,),
                        )

                # The first candidate was built before joining the canonical
                # boundary. An ordinary config transaction may have published
                # unrelated settings while that build awaited, so rebuild the
                # full consumer publication from the current live Config now.
                # This operation is synchronous for RuntimeContext: it holds
                # neither the disk gate nor the short model-swap lock.
                try:
                    runtime_candidate = self.coordinator.restage_model_candidate(
                        runtime_candidate,
                        effective,
                        rebased_revision,
                    )
                except Exception:
                    error = ModelConfigFieldError(
                        "models",
                        "candidate_build_failed",
                        "The model runtime candidate could not be restaged.",
                    )
                    return ModelConfigSaveResult(
                        ok=False,
                        snapshot=latest_snapshot,
                        errors=(error,),
                    )

                # Capture the exact post-rebase runtime identity. Rollback must
                # retain any unrelated ordinary-config update that won while
                # the initial model candidate was building.
                previous = await _capture_current_model_candidate(self.coordinator)
                backup_path: Path | None = None
                with coordinated_config_disk_write(self.path):
                    if latest.base_source == "legacy":
                        try:
                            backup_path = _create_legacy_backup(
                                self.path,
                                latest.original,
                                latest.mode,
                            )
                        except OSError:
                            error = ModelConfigFieldError(
                                "models.migration",
                                "migration_backup_failed",
                                "The legacy configuration backup could not be created safely.",
                            )
                            return ModelConfigSaveResult(
                                ok=False,
                                snapshot=latest_snapshot,
                                errors=(error,),
                            )
                    try:
                        _atomic_write(self.path, payload, latest.mode)
                    except OSError as exc:
                        replaced = isinstance(exc, _AtomicWriteError) and exc.replaced
                        rollback_ok = _restore_disk(latest, self.path) if replaced else False
                        if not replaced or rollback_ok:
                            _remove_backup(backup_path)
                        error = ModelConfigFieldError(
                            "models",
                            "config_write_failed",
                            "The model configuration could not be written atomically.",
                        )
                        return ModelConfigSaveResult(
                            ok=False,
                            snapshot=latest_snapshot,
                            rollback_applied=rollback_ok,
                            errors=(error,),
                        )

                try:
                    await self.coordinator.swap_model_candidate(runtime_candidate)
                except asyncio.CancelledError:
                    rollback_ok = await _restore_transaction(
                        latest,
                        self.path,
                        self.coordinator,
                        previous,
                    )
                    if rollback_ok:
                        _remove_backup(backup_path)
                    raise
                except Exception:
                    rollback_ok = await _restore_transaction(
                        latest,
                        self.path,
                        self.coordinator,
                        previous,
                    )
                    if rollback_ok:
                        _remove_backup(backup_path)
                    error = ModelConfigFieldError(
                        "models",
                        "runtime_swap_failed",
                        "The model runtime could not be swapped; restoration was attempted.",
                    )
                    return ModelConfigSaveResult(
                        ok=False,
                        snapshot=latest_snapshot,
                        rollback_applied=rollback_ok,
                        errors=(error,),
                    )

                saved = replace(
                    latest,
                    models=effective,
                    persisted_models=persisted,
                    revision=revision,
                    source="native",
                    base_source="native",
                    authority_fingerprint="",
                    migration_state="none",
                    migration=None,
                    migration_result=None,
                    original=payload,
                    existed=True,
                    local_legacy=False,
                )
                return ModelConfigSaveResult(
                    ok=True,
                    snapshot=self._snapshot(saved),
                    reloaded=True,
                    backup_path=backup_path,
                )

    async def probe(
        self,
        draft: RouteRecord,
        *,
        settings: EmbeddingModelSettings | None = None,
        credential_action: CredentialAction | None = None,
    ) -> ModelConfigProbeResult:
        """Probe one draft for legacy direct callers without API-side effects.

        This compatibility helper has no caller-supplied revision contract and
        must not back an HTTP endpoint.  Revision-guarded callers use
        :meth:`capture_probe`, :meth:`probe_captured`, and
        :meth:`revalidate_probe_capture` so a ``keep`` credential and any live
        effects stay bound to one persisted revision.
        """
        candidate = draft
        if credential_action is not None:
            _validate_action(draft.id, credential_action)
            current = self._read_state().models
            shell = ModelConfig(
                chat=replace(
                    current.chat,
                    connections=(draft,) if isinstance(draft, ChatConnection) else (),
                ),
                embedding=replace(
                    current.embedding,
                    enabled=isinstance(draft, EmbeddingProviderConfig),
                    settings=settings or current.embedding.settings,
                    providers=(draft,) if isinstance(draft, EmbeddingProviderConfig) else (),
                ),
            )
            applied = _apply_credential_actions(
                shell,
                current,
                {draft.id: credential_action},
            )
            candidate = (
                applied.chat.connections[0]
                if isinstance(draft, ChatConnection)
                else applied.embedding.providers[0]
            )
        return await self.coordinator.probe_model_draft(candidate, settings)

    async def capture_probe(
        self,
        draft: RouteRecord,
        *,
        revision: str,
        settings: EmbeddingModelSettings | None = None,
        credential_action: CredentialAction | None = None,
    ) -> ModelConfigProbeCapture:
        """Resolve a draft from exactly ``revision`` without holding a network lock."""
        async with _path_lock(self.path):
            if self._precommit_guard is not None and self._precommit_guard():
                raise ModelConfigProbeBlockedError("Model configuration probe is blocked.")
            state = self._read_state()
            if state.revision != revision:
                raise ModelConfigRevisionConflictError(self._snapshot(state))
            candidate = draft
            if credential_action is not None:
                _validate_action(draft.id, credential_action)
                shell = ModelConfig(
                    chat=replace(
                        state.models.chat,
                        connections=(draft,) if isinstance(draft, ChatConnection) else (),
                    ),
                    embedding=replace(
                        state.models.embedding,
                        enabled=isinstance(draft, EmbeddingProviderConfig),
                        settings=settings or state.models.embedding.settings,
                        providers=((draft,) if isinstance(draft, EmbeddingProviderConfig) else ()),
                    ),
                )
                applied = _apply_credential_actions(
                    shell,
                    state.models,
                    {draft.id: credential_action},
                )
                candidate = (
                    applied.chat.connections[0]
                    if isinstance(draft, ChatConnection)
                    else applied.embedding.providers[0]
                )
            return ModelConfigProbeCapture(
                revision=state.revision,
                draft=candidate,
                settings=settings,
            )

    async def probe_captured(
        self,
        capture: ModelConfigProbeCapture,
    ) -> ModelConfigProbeResult:
        """Run one captured probe after releasing all config locks."""
        return await self.coordinator.probe_model_draft(capture.draft, capture.settings)

    async def revalidate_probe_capture(
        self,
        capture: ModelConfigProbeCapture,
    ) -> ModelConfigSnapshot:
        """Require the captured revision again before attaching live effects."""
        async with _path_lock(self.path):
            state = self._read_state()
            snapshot = self._snapshot(state)
            if state.revision != capture.revision:
                raise ModelConfigRevisionConflictError(snapshot)
            return snapshot

    def add(
        self,
        models: ModelConfig,
        record: RouteRecord,
        *,
        position: int | None = None,
    ) -> ModelConfig:
        """Add a stable-ID record at a one-based position and return a full draft."""
        all_ids = {item.id for item in models.chat.connections} | {
            item.id for item in models.embedding.providers
        }
        if not record.id.strip():
            raise _validation_error(
                "models.connections.id",
                "blank_connection_id",
                "Connection ID must not be blank.",
            )
        if record.id in all_ids:
            raise _validation_error(
                "models.connections.id",
                "duplicate_connection_id",
                "Connection IDs must be unique across model routes.",
            )
        if isinstance(record, ChatConnection):
            records = models.chat.connections
            target = len(records) + 1 if position is None else position
            if type(target) is not int or not 1 <= target <= len(records) + 1:
                raise _validation_error(
                    "models.connections.position",
                    "invalid_route_position",
                    "Route position is outside the one-based list bounds.",
                )
            updated = (*records[: target - 1], record, *records[target - 1 :])
            candidate = replace(
                models,
                chat=replace(models.chat, connections=updated),
            )
        else:
            providers = models.embedding.providers
            target = len(providers) + 1 if position is None else position
            if type(target) is not int or not 1 <= target <= len(providers) + 1:
                raise _validation_error(
                    "models.connections.position",
                    "invalid_route_position",
                    "Route position is outside the one-based list bounds.",
                )
            updated_providers = (
                *providers[: target - 1],
                record,
                *providers[target - 1 :],
            )
            candidate = replace(
                models,
                embedding=replace(models.embedding, providers=updated_providers),
            )
        _validate_candidate(candidate)
        return candidate

    def edit(
        self,
        models: ModelConfig,
        connection_id: str,
        replacement: RouteRecord,
    ) -> ModelConfig:
        """Replace one record while requiring its stable ID and route kind."""
        if replacement.id != connection_id:
            raise _validation_error(
                "models.connections.id",
                "stable_id_mismatch",
                "Editing a connection cannot change its stable ID.",
            )
        for index, chat_item in enumerate(models.chat.connections):
            if chat_item.id == connection_id:
                if not isinstance(replacement, ChatConnection):
                    break
                records = list(models.chat.connections)
                records[index] = replacement
                candidate = replace(
                    models,
                    chat=replace(models.chat, connections=tuple(records)),
                )
                _validate_candidate(candidate)
                return candidate
        for index, embedding_item in enumerate(models.embedding.providers):
            if embedding_item.id == connection_id:
                if not isinstance(replacement, EmbeddingProviderConfig):
                    break
                providers = list(models.embedding.providers)
                providers[index] = replacement
                candidate = replace(
                    models,
                    embedding=replace(models.embedding, providers=tuple(providers)),
                )
                _validate_candidate(candidate)
                return candidate
        raise _validation_error(
            "models.connections.id",
            "missing_connection_id",
            "Connection ID was not found.",
        )

    def remove(self, models: ModelConfig, connection_id: str) -> ModelConfig:
        """Remove one stable ID, never allowing the final Chat connection."""
        for index, chat_item in enumerate(models.chat.connections):
            if chat_item.id == connection_id:
                if len(models.chat.connections) == 1:
                    raise _validation_error(
                        "models.chat.connections",
                        "final_chat_connection",
                        "The final Chat connection cannot be removed.",
                    )
                records = (*models.chat.connections[:index], *models.chat.connections[index + 1 :])
                candidate = replace(models, chat=replace(models.chat, connections=records))
                _validate_candidate(candidate)
                return candidate
        for index, embedding_item in enumerate(models.embedding.providers):
            if embedding_item.id == connection_id:
                providers = (
                    *models.embedding.providers[:index],
                    *models.embedding.providers[index + 1 :],
                )
                candidate = replace(
                    models,
                    embedding=replace(models.embedding, providers=providers),
                )
                _validate_candidate(candidate)
                return candidate
        raise _validation_error(
            "models.connections.id",
            "missing_connection_id",
            "Connection ID was not found.",
        )

    def move(self, models: ModelConfig, connection_id: str, position: int) -> ModelConfig:
        """Move one stable ID to an exact one-based position without rebuilding it."""
        chat_index = next(
            (
                index
                for index, item in enumerate(models.chat.connections)
                if item.id == connection_id
            ),
            None,
        )
        if chat_index is not None:
            records = models.chat.connections
            if type(position) is not int or not 1 <= position <= len(records):
                raise _validation_error(
                    "models.chat.position",
                    "invalid_route_position",
                    "Route position is outside the one-based list bounds.",
                )
            moving = records[chat_index]
            remaining = (*records[:chat_index], *records[chat_index + 1 :])
            moved = (*remaining[: position - 1], moving, *remaining[position - 1 :])
            candidate = replace(
                models,
                chat=replace(models.chat, connections=moved),
            )
            _validate_candidate(candidate)
            return candidate

        embedding_index = next(
            (
                index
                for index, item in enumerate(models.embedding.providers)
                if item.id == connection_id
            ),
            None,
        )
        if embedding_index is not None:
            providers = models.embedding.providers
            if type(position) is not int or not 1 <= position <= len(providers):
                raise _validation_error(
                    "models.embedding.position",
                    "invalid_route_position",
                    "Route position is outside the one-based list bounds.",
                )
            moving_provider = providers[embedding_index]
            remaining_providers = (
                *providers[:embedding_index],
                *providers[embedding_index + 1 :],
            )
            moved_providers = (
                *remaining_providers[: position - 1],
                moving_provider,
                *remaining_providers[position - 1 :],
            )
            candidate = replace(
                models,
                embedding=replace(models.embedding, providers=moved_providers),
            )
            _validate_candidate(candidate)
            return candidate
        raise _validation_error(
            "models.connections.id",
            "missing_connection_id",
            "Connection ID was not found.",
        )


__all__ = [
    "CredentialAction",
    "CredentialActionName",
    "ModelConfigFieldError",
    "ModelConfigOverride",
    "ModelConfigProbeResult",
    "ModelConfigSaveRequest",
    "ModelConfigSaveResult",
    "ModelConfigService",
    "ModelConfigSnapshot",
    "ModelConfigValidationError",
    "ModelRuntimeCoordinator",
    "PublicChatConnection",
    "PublicChatRouteConfig",
    "PublicCredentialStatus",
    "PublicEmbeddingProvider",
    "PublicEmbeddingRouteConfig",
    "PublicModelConfig",
]
