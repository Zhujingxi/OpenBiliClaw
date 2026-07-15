"""Dedicated model configuration routes around :class:`ModelConfigService`."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from openbiliclaw.api.model_config_models import (
    ChatConnectionIn,
    ChatConnectionOut,
    ChatRouteOut,
    CircuitSummaryOut,
    ConnectionTypeDescriptorOut,
    ConnectionTypeGroupOut,
    ConnectionTypesResponse,
    EmbeddingProviderIn,
    EmbeddingProviderOut,
    EmbeddingRouteOut,
    EmbeddingSettingsIn,
    EmbeddingSettingsOut,
    MigrationIssueOut,
    MigrationSummaryOut,
    ModelConfigFieldErrorOut,
    ModelConfigOut,
    ModelConfigOverrideOut,
    ModelConfigProbeIn,
    ModelConfigProbeResponse,
    ModelConfigPutIn,
    ModelConfigPutResponse,
    ModelConfigSnapshotOut,
    ProbeSummaryOut,
    PublicCredentialOut,
)
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    MigrationResolution,
    ModelConfig,
    connection_type_registry,
)
from openbiliclaw.model_config.service import (
    CredentialAction,
    ModelConfigCommitBlockedError,
    ModelConfigFieldError,
    ModelConfigProbeResult,
    ModelConfigRevisionConflictError,
    ModelConfigSaveRequest,
    ModelConfigService,
    ModelConfigSnapshot,
    ModelConfigValidationError,
    ModelRuntimeCoordinator,
    PublicCredentialStatus,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from pathlib import Path

    from openbiliclaw.api.runtime_context import RuntimeContext

    EventPublisher = Callable[[dict[str, Any]], Awaitable[object]]


_GROUP_ORDER = ("api_protocol", "local_runtime", "oauth")


@dataclass(frozen=True)
class _StoredProbe:
    """Safe, draft-aware probe history keyed by stable route ID."""

    result: ModelConfigProbeResult
    revision: str
    record_fingerprint: str
    probed_at: str


@dataclass(frozen=True)
class _AppModelLifecycleState:
    """Runtime, degraded-mode, and task ownership before one API save."""

    runtime_state: object
    refresh_active: bool
    account_sync_active: bool
    auto_update_active: bool
    app_degraded: bool
    app_degraded_reason: str
    app_degraded_issues: list[object]


def _task_slot_active(app: FastAPI, name: str) -> bool:
    task = getattr(app.state, name, None)
    if task is None:
        return False
    done = getattr(task, "done", None)
    return not bool(done()) if callable(done) else True


class _AppModelRuntimeCoordinator:
    """Own model publication, app task restart, degraded recovery, and event order."""

    def __init__(
        self,
        app: FastAPI,
        context: RuntimeContext,
        event_publisher: EventPublisher | None,
    ) -> None:
        self.app = app
        self.context = context
        self._event_publisher = event_publisher

    @property
    def current_model_candidate(self) -> object:
        return _AppModelLifecycleState(
            runtime_state=self.context.capture_model_runtime_state(),
            refresh_active=_task_slot_active(self.app, "refresh_task"),
            account_sync_active=_task_slot_active(self.app, "account_sync_task"),
            auto_update_active=_task_slot_active(self.app, "auto_update_task"),
            app_degraded=bool(getattr(self.app.state, "degraded", False)),
            app_degraded_reason=str(getattr(self.app.state, "degraded_reason", "")),
            app_degraded_issues=list(getattr(self.app.state, "degraded_issues", [])),
        )

    async def build_model_candidate(self, models: ModelConfig, revision: str) -> object:
        return await self.context.build_model_candidate(models, revision)

    def restage_model_candidate(
        self,
        candidate: object,
        models: ModelConfig,
        revision: str,
    ) -> object:
        return self.context.restage_model_candidate(candidate, models, revision)

    async def swap_model_candidate(self, candidate: object) -> object | None:
        previous = await self.context.activate_model_candidate(cast("Any", candidate))
        await self.context.restart_background_tasks(self.app)
        self.context.degraded = False
        self.context.degraded_reason = ""
        self.context.degraded_issues = []
        self.app.state.degraded = False
        self.app.state.degraded_reason = ""
        self.app.state.degraded_issues = []
        publish = getattr(self.context.event_hub, "publish", None)
        if not callable(publish):
            publish = self._event_publisher
        if callable(publish):
            with suppress(Exception):
                await publish(
                    {
                        "type": "config_reloaded",
                        "revision": cast("Any", candidate).revision,
                    }
                )
        return previous

    async def restore_model_candidate(self, candidate: object | None) -> None:
        if not isinstance(candidate, _AppModelLifecycleState):
            raise TypeError("candidate must be an app model lifecycle state")
        await self.context.restore_model_runtime_state(cast("Any", candidate.runtime_state))
        self.app.state.degraded = candidate.app_degraded
        self.app.state.degraded_reason = candidate.app_degraded_reason
        self.app.state.degraded_issues = list(candidate.app_degraded_issues)
        active = (
            candidate.refresh_active,
            candidate.account_sync_active,
            candidate.auto_update_active,
        )
        if not any(active):
            await self.context.stop_background_tasks(self.app)
            return
        await self.context.restart_background_tasks(
            self.app,
            run_post_reload_llm_work=(candidate.refresh_active or candidate.account_sync_active),
        )
        for slot, expected in zip(
            ("refresh_task", "account_sync_task", "auto_update_task"),
            active,
            strict=True,
        ):
            if expected:
                continue
            task = getattr(self.app.state, slot, None)
            if task is not None:
                task.cancel()
                with suppress(BaseException):
                    await task
            setattr(self.app.state, slot, None)

    async def probe_model_draft(
        self,
        draft: ChatConnection | EmbeddingProviderConfig,
        settings: EmbeddingModelSettings | None = None,
    ) -> ModelConfigProbeResult:
        return await self.context.probe_model_draft(draft, settings)


def _record_fingerprint(
    record: object,
    capability: Literal["chat", "embedding"],
    *,
    credential_tag: str = "persisted",
    embedding_settings: object | None = None,
) -> str:
    names = (
        (
            "id",
            "name",
            "type",
            "model",
            "preset",
            "base_url",
            "api_mode",
            "reasoning_effort",
            "http_referer",
            "x_title",
            "num_ctx",
        )
        if capability == "chat"
        else ("id", "name", "type", "preset", "base_url")
    )
    payload = {
        "capability": capability,
        "credential": credential_tag,
        "record": {name: getattr(record, name, "") for name in names},
    }
    if capability == "embedding":
        payload["settings"] = {
            name: getattr(embedding_settings, name, None)
            for name in (
                "model",
                "output_dimensionality",
                "similarity_threshold",
                "multimodal_enabled",
            )
        }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _probe_credential_tag(action: CredentialAction) -> str:
    if action.action == "keep":
        return "persisted"
    digest = hashlib.sha256((action.value or "").encode("utf-8")).hexdigest()
    return f"{action.action}:{digest}"


def _probe_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_validation_detail(exc: RequestValidationError) -> list[dict[str, object]]:
    """Return fieldized validation details without submitted inputs or values."""
    details: list[dict[str, object]] = []
    for error in exc.errors():
        details.append(
            {
                "loc": list(error.get("loc", ())),
                "msg": str(error.get("msg", "Invalid request.")),
                "type": str(error.get("type", "value_error")),
            }
        )
    return details


def _public_credential(
    status: PublicCredentialStatus,
    *,
    connection_type: str,
) -> PublicCredentialOut:
    oauth_logged_in = False
    if status.source == "oauth" and connection_type == "codex_oauth":
        try:
            from openbiliclaw.llm.codex_auth import load_codex_credentials

            credentials = load_codex_credentials()
            oauth_logged_in = credentials is not None and not credentials.is_expired()
        except Exception:
            oauth_logged_in = False
    return PublicCredentialOut(
        source=status.source,
        configured=status.configured,
        env_name=status.env_var,
        credential_ref=status.credential_ref,
        oauth_logged_in=oauth_logged_in,
    )


def _probe_summary(
    probe_results: Mapping[str, _StoredProbe],
    record: object,
    capability: Literal["chat", "embedding"],
    *,
    embedding_settings: object | None = None,
) -> ProbeSummaryOut | None:
    stored = probe_results.get(str(getattr(record, "id", "")))
    if stored is None or stored.record_fingerprint != _record_fingerprint(
        record,
        capability,
        embedding_settings=embedding_settings,
    ):
        return None
    result = stored.result
    return ProbeSummaryOut(
        ok=result.ok,
        error_code=result.error_code,
        message=result.message,
        observed_dimension=result.observed_dimension,
        probed_at=stored.probed_at,
        revision=stored.revision,
    )


def _circuit_table_for(context: RuntimeContext, capability: str) -> object | None:
    bundle = context.model_bundle
    if bundle is None:
        return None
    if capability == "chat":
        return getattr(bundle.chat_route, "circuits", None)
    embedding_service = bundle.embedding_service
    route = getattr(embedding_service, "_provider", None)
    return getattr(route, "circuits", None)


def _circuit_summary(
    context: RuntimeContext,
    connection_id: str,
    revision: str,
    capability: str,
) -> CircuitSummaryOut:
    table = _circuit_table_for(context, capability)
    state_for = getattr(table, "state_for", None)
    if not callable(state_for):
        return CircuitSummaryOut()
    state = state_for(connection_id, revision)
    if state is None:
        return CircuitSummaryOut()
    retry_at = getattr(state, "retry_at", None)
    retry_after = None if retry_at is None else max(0.0, float(retry_at) - time.monotonic())
    return CircuitSummaryOut(
        state="open",
        failure_kind=str(getattr(state, "failure_kind", "")),
        retry_after_seconds=retry_after,
        permanent=bool(getattr(state, "permanent", False)),
    )


def _snapshot_out(
    snapshot: ModelConfigSnapshot,
    context: RuntimeContext,
    probe_results: Mapping[str, _StoredProbe],
) -> ModelConfigSnapshotOut:
    chat_connections = [
        ChatConnectionOut(
            id=item.id,
            name=item.name,
            type=item.type,
            model=item.model,
            preset=item.preset,
            base_url=item.base_url,
            credential=_public_credential(item.credential, connection_type=item.type),
            api_mode=item.api_mode,
            reasoning_effort=item.reasoning_effort,
            http_referer=item.http_referer,
            x_title=item.x_title,
            num_ctx=item.num_ctx,
            probe=_probe_summary(probe_results, item, "chat"),
            circuit=_circuit_summary(context, item.id, snapshot.revision, "chat"),
        )
        for item in snapshot.models.chat.connections
    ]
    embedding_providers = [
        EmbeddingProviderOut(
            id=item.id,
            name=item.name,
            type=item.type,
            preset=item.preset,
            base_url=item.base_url,
            credential=_public_credential(item.credential, connection_type=item.type),
            probe=_probe_summary(
                probe_results,
                item,
                "embedding",
                embedding_settings=snapshot.models.embedding.settings,
            ),
            circuit=_circuit_summary(context, item.id, snapshot.revision, "embedding"),
        )
        for item in snapshot.models.embedding.providers
    ]
    report = snapshot.migration
    migration_issues = []
    if report is not None:
        migration_issues = [
            MigrationIssueOut(
                id=item.id,
                code=item.code,
                field=item.field,
                provider=item.provider,
                credential_configured=item.credential_configured,
                reason=item.reason,
                severity=item.severity,
                allowed_actions=list(item.allowed_actions),
            )
            for item in report.issues
        ]
    return ModelConfigSnapshotOut(
        revision=snapshot.revision,
        source=snapshot.source,
        models=ModelConfigOut(
            schema_version=snapshot.models.schema_version,
            chat=ChatRouteOut(
                connections=chat_connections,
                concurrency=snapshot.models.chat.concurrency,
                timeout_seconds=snapshot.models.chat.timeout_seconds,
            ),
            embedding=EmbeddingRouteOut(
                enabled=snapshot.models.embedding.enabled,
                settings=EmbeddingSettingsOut(**asdict(snapshot.models.embedding.settings)),
                providers=embedding_providers,
            ),
        ),
        migration=MigrationSummaryOut(
            state=snapshot.migration_state,
            confirmed=snapshot.migration_state == "none",
            issues=migration_issues,
        ),
        overrides=[
            ModelConfigOverrideOut(path=item.path, source=item.source)
            for item in snapshot.overrides
        ],
    )


def _credential_action(value: object) -> CredentialAction:
    action = cast("Any", value)
    return CredentialAction(action=action.action, value=action.value)


def _chat_from_input(value: ChatConnectionIn) -> ChatConnection:
    return ChatConnection(
        id=value.id,
        name=value.name,
        type=value.type,
        model=value.model,
        preset=value.preset,
        base_url=value.base_url,
        credential=CredentialConfig(),
        api_mode=value.api_mode,
        reasoning_effort=value.reasoning_effort,
        http_referer=value.http_referer,
        x_title=value.x_title,
        num_ctx=value.num_ctx,
    )


def _embedding_from_input(value: EmbeddingProviderIn) -> EmbeddingProviderConfig:
    return EmbeddingProviderConfig(
        id=value.id,
        name=value.name,
        type=value.type,
        preset=value.preset,
        base_url=value.base_url,
        credential=CredentialConfig(),
    )


def _settings_from_input(value: EmbeddingSettingsIn) -> EmbeddingModelSettings:
    return EmbeddingModelSettings(
        model=value.model,
        output_dimensionality=value.output_dimensionality,
        similarity_threshold=value.similarity_threshold,
        multimodal_enabled=value.multimodal_enabled,
    )


def _save_request(payload: ModelConfigPutIn) -> ModelConfigSaveRequest:
    connections = tuple(_chat_from_input(item) for item in payload.models.chat.connections)
    providers = tuple(_embedding_from_input(item) for item in payload.models.embedding.providers)
    actions = {
        item.id: _credential_action(item.credential) for item in payload.models.chat.connections
    }
    actions.update(
        {
            item.id: _credential_action(item.credential)
            for item in payload.models.embedding.providers
        }
    )
    resolutions = {
        issue_id: MigrationResolution(
            action=value.action,
            position=value.position,
            embedding_settings=(
                _settings_from_input(value.embedding_settings)
                if value.embedding_settings is not None
                else None
            ),
        )
        for issue_id, value in payload.migration_resolutions.items()
    }
    return ModelConfigSaveRequest(
        revision=payload.revision,
        models=ModelConfig(
            schema_version=payload.models.schema_version,
            chat=ChatRouteConfig(
                connections=connections,
                concurrency=payload.models.chat.concurrency,
                timeout_seconds=payload.models.chat.timeout_seconds,
            ),
            embedding=EmbeddingRouteConfig(
                enabled=payload.models.embedding.enabled,
                settings=_settings_from_input(payload.models.embedding.settings),
                providers=providers,
            ),
        ),
        credential_actions=actions,
        migration_resolutions=resolutions,
    )


def _error_out(error: ModelConfigFieldError) -> dict[str, object]:
    return ModelConfigFieldErrorOut(
        path=error.path,
        code=error.code,
        message=error.message,
        source=error.source,
        connection_id=error.connection_id,
    ).model_dump(mode="json")


def _validation_response(exc: ModelConfigValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": "validation_failed",
            "errors": [_error_out(error) for error in exc.errors],
        },
    )


def _descriptor_payload(capability: str | None) -> ConnectionTypesResponse:
    registry = connection_type_registry()
    definitions = (
        registry.definitions if capability is None else registry.for_capability(capability)
    )
    descriptors: list[ConnectionTypeDescriptorOut] = []
    for definition in definitions:
        raw = definition.public_descriptor()
        if capability is not None:
            raw["fields"] = [
                item.public_descriptor()
                for item in definition.fields
                if capability in item.capabilities
            ]
            presets = [item for item in definition.presets if capability in item.capabilities]
            raw["presets"] = [item.id for item in presets]
            raw["preset_definitions"] = [item.public_descriptor() for item in presets]
        descriptors.append(ConnectionTypeDescriptorOut.model_validate(raw))
    groups = [
        ConnectionTypeGroupOut(
            category=cast("Any", category),
            connection_types=[item for item in descriptors if item.category == category],
        )
        for category in _GROUP_ORDER
        if any(item.category == category for item in descriptors)
    ]
    return ConnectionTypesResponse(
        capability=cast("Any", capability),
        connection_types=descriptors,
        groups=groups,
    )


def install_model_config_routes(
    app: FastAPI,
    *,
    context: RuntimeContext,
    config_path: Path,
    init_active: Callable[[], bool],
    event_publisher: EventPublisher | None = None,
) -> ModelConfigService:
    """Install model endpoints with one explicit app-lifecycle owner."""
    lifecycle = _AppModelRuntimeCoordinator(app, context, event_publisher)
    service = ModelConfigService(
        config_path,
        cast("ModelRuntimeCoordinator", lifecycle),
        precommit_guard=init_active,
    )
    probe_results: dict[str, _StoredProbe] = {}
    known_revision = ""

    def observe_revision(snapshot: ModelConfigSnapshot) -> None:
        """Discard history after an unexplained out-of-band revision change."""
        nonlocal known_revision
        if known_revision and snapshot.revision != known_revision:
            probe_results.clear()
        known_revision = snapshot.revision

    def accept_saved_revision(payload: ModelConfigPutIn, snapshot: ModelConfigSnapshot) -> None:
        """Keep only unchanged, keep-credential records across an API reorder."""
        nonlocal known_revision
        input_records: dict[str, tuple[object, str, str]] = {
            item.id: (item, "chat", item.credential.action)
            for item in payload.models.chat.connections
        }
        input_records.update(
            {
                item.id: (item, "embedding", item.credential.action)
                for item in payload.models.embedding.providers
            }
        )
        for connection_id, stored in tuple(probe_results.items()):
            candidate = input_records.get(connection_id)
            if candidate is None:
                probe_results.pop(connection_id, None)
                continue
            record, capability, action = candidate
            fingerprint = _record_fingerprint(
                record,
                cast("Any", capability),
                embedding_settings=(
                    payload.models.embedding.settings if capability == "embedding" else None
                ),
            )
            if action != "keep" or stored.record_fingerprint != fingerprint:
                probe_results.pop(connection_id, None)
        known_revision = snapshot.revision

    # FastAPI's default validation shape includes the submitted ``input``. A
    # malformed credential action could therefore echo a secret. Keep field
    # locations and messages while dropping all submitted values globally.
    @app.exception_handler(RequestValidationError)
    async def _secret_safe_request_validation(
        _request: object,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _safe_validation_detail(exc)})

    @app.get("/api/model-config", response_model=ModelConfigSnapshotOut)
    def get_model_config() -> ModelConfigSnapshotOut | JSONResponse:
        try:
            snapshot = service.read()
            observe_revision(snapshot)
            return _snapshot_out(snapshot, context, probe_results)
        except ModelConfigValidationError as exc:
            return _validation_response(exc)

    @app.get("/api/model-connection-types", response_model=ConnectionTypesResponse)
    def get_model_connection_types(
        capability: Literal["chat", "embedding"] | None = Query(default=None),
    ) -> ConnectionTypesResponse:
        return _descriptor_payload(capability)

    @app.put("/api/model-config", response_model=ModelConfigPutResponse)
    async def put_model_config(payload: ModelConfigPutIn) -> ModelConfigPutResponse | JSONResponse:
        if init_active():
            return JSONResponse(
                status_code=409,
                content={"error": "init_running", "detail": "Initialization is active."},
            )
        try:
            result = await service.save(_save_request(payload))
        except ModelConfigCommitBlockedError:
            return JSONResponse(
                status_code=409,
                content={"error": "init_running", "detail": "Initialization is active."},
            )
        except ModelConfigValidationError as exc:
            return _validation_response(exc)
        except Exception:
            return JSONResponse(
                status_code=500,
                content={"error": "model_config_failed", "message": "Model configuration failed."},
            )
        if result.conflict:
            observe_revision(result.snapshot)
            snapshot = _snapshot_out(result.snapshot, context, probe_results)
            return JSONResponse(
                status_code=409,
                content={
                    "error": "revision_conflict",
                    "latest_revision": result.latest_revision,
                    "latest": snapshot.model_dump(mode="json"),
                },
            )
        if not result.ok:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "validation_failed",
                    "errors": [_error_out(error) for error in result.errors],
                    "rollback_applied": result.rollback_applied,
                },
            )
        accept_saved_revision(payload, result.snapshot)
        snapshot = _snapshot_out(result.snapshot, context, probe_results)
        return ModelConfigPutResponse(
            ok=True,
            revision=result.revision,
            reloaded=result.reloaded,
            rollback_applied=result.rollback_applied,
            snapshot=snapshot,
        )

    @app.post("/api/model-config/probe", response_model=ModelConfigProbeResponse)
    async def probe_model_config(
        payload: ModelConfigProbeIn,
    ) -> ModelConfigProbeResponse | JSONResponse:
        if init_active():
            return JSONResponse(
                status_code=409,
                content={"error": "init_running", "detail": "Initialization is active."},
            )
        try:
            snapshot = service.read()
        except ModelConfigValidationError as exc:
            return _validation_response(exc)
        observe_revision(snapshot)
        if payload.revision != snapshot.revision:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "revision_conflict",
                    "latest_revision": snapshot.revision,
                    "latest": _snapshot_out(
                        snapshot,
                        context,
                        probe_results,
                    ).model_dump(mode="json"),
                },
            )
        draft: ChatConnection | EmbeddingProviderConfig
        if payload.kind == "chat":
            assert payload.connection is not None
            draft = _chat_from_input(payload.connection)
            action = _credential_action(payload.connection.credential)
            current_ids = {item.id for item in snapshot.models.chat.connections}
            settings = None
        else:
            assert payload.provider is not None and payload.settings is not None
            draft = _embedding_from_input(payload.provider)
            action = _credential_action(payload.provider.credential)
            current_ids = {item.id for item in snapshot.models.embedding.providers}
            settings = _settings_from_input(payload.settings)
        if action.action == "keep" and draft.id not in current_ids:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "validation_failed",
                    "errors": [
                        _error_out(
                            ModelConfigFieldError(
                                path=f"models.credentials.{draft.id}",
                                code="credential_action_required",
                                message=(
                                    "A keep action requires the matching stable ID "
                                    "at this revision."
                                ),
                                connection_id=draft.id,
                            )
                        )
                    ],
                },
            )
        capture = None
        try:
            gate = context.llm_concurrency_gate
            if gate is None:
                if init_active():
                    return JSONResponse(
                        status_code=409,
                        content={
                            "error": "init_running",
                            "detail": "Initialization is active.",
                        },
                    )
                capture = await service.capture_probe(
                    draft,
                    revision=payload.revision,
                    settings=settings,
                    credential_action=action,
                )
                result = await service.probe_captured(capture)
            else:
                async with gate.slot(caller="api.config_probe"):
                    if init_active():
                        return JSONResponse(
                            status_code=409,
                            content={
                                "error": "init_running",
                                "detail": "Initialization is active.",
                            },
                        )
                    capture = await service.capture_probe(
                        draft,
                        revision=payload.revision,
                        settings=settings,
                        credential_action=action,
                    )
                    result = await service.probe_captured(capture)
            snapshot = await service.revalidate_probe_capture(capture)
        except ModelConfigRevisionConflictError as exc:
            observe_revision(exc.snapshot)
            return JSONResponse(
                status_code=409,
                content={
                    "error": "revision_conflict",
                    "latest_revision": exc.snapshot.revision,
                    "latest": _snapshot_out(
                        exc.snapshot,
                        context,
                        probe_results,
                    ).model_dump(mode="json"),
                },
            )
        except ModelConfigValidationError as exc:
            return _validation_response(exc)
        except Exception:
            result = ModelConfigProbeResult(
                ok=False,
                connection_id=draft.id,
                capability=payload.kind,
                error_code="probe_failed",
                message="The exact model draft probe failed.",
            )
            if capture is not None:
                try:
                    snapshot = await service.revalidate_probe_capture(capture)
                except ModelConfigRevisionConflictError as exc:
                    observe_revision(exc.snapshot)
                    return JSONResponse(
                        status_code=409,
                        content={
                            "error": "revision_conflict",
                            "latest_revision": exc.snapshot.revision,
                            "latest": _snapshot_out(
                                exc.snapshot,
                                context,
                                probe_results,
                            ).model_dump(mode="json"),
                        },
                    )
        probed_at = _probe_timestamp()
        record_fingerprint = _record_fingerprint(
            draft,
            payload.kind,
            credential_tag=_probe_credential_tag(action),
            embedding_settings=settings,
        )
        current_records = (
            snapshot.models.chat.connections
            if payload.kind == "chat"
            else snapshot.models.embedding.providers
        )
        persisted_record = next(
            (item for item in current_records if item.id == draft.id),
            None,
        )
        exact_persisted_draft = (
            action.action == "keep"
            and persisted_record is not None
            and record_fingerprint
            == _record_fingerprint(
                persisted_record,
                payload.kind,
                embedding_settings=(
                    snapshot.models.embedding.settings if payload.kind == "embedding" else None
                ),
            )
        )
        if exact_persisted_draft:
            probe_results[result.connection_id] = _StoredProbe(
                result=result,
                revision=payload.revision,
                record_fingerprint=record_fingerprint,
                probed_at=probed_at,
            )
        if result.ok and exact_persisted_draft:
            context.record_model_probe_success(
                result.connection_id,
                result.capability,
                payload.revision,
            )
        return ModelConfigProbeResponse(
            ok=result.ok,
            connection_id=result.connection_id,
            capability=result.capability,
            observed_dimension=result.observed_dimension,
            error_code=result.error_code,
            message=result.message,
            probed_at=probed_at,
            revision=payload.revision,
        )

    return service


__all__ = ["install_model_config_routes"]
