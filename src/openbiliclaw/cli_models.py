"""Secret-safe CLI editor for ordered Chat and Embedding model routes."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, cast

import typer

from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    ConnectionTypeDefinition,
    CredentialAction,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    MigrationAction,
    MigrationResolution,
    ModelConfig,
    ModelConfigFieldError,
    ModelConfigSaveRequest,
    ModelConfigService,
    ModelConfigSnapshot,
    ModelConfigValidationError,
    ModelRuntimeCoordinator,
    PublicCredentialStatus,
    PublicModelConfig,
    connection_type_registry,
)
from openbiliclaw.model_config.service import (
    ModelConfigProbeResult,
    ModelConfigRevisionConflictError,
    PublicChatConnection,
    PublicEmbeddingProvider,
)

if TYPE_CHECKING:
    from typing import Any

ModelKind: TypeAlias = Literal["chat", "embedding"]
RouteRecord: TypeAlias = ChatConnection | EmbeddingProviderConfig
PublicRouteRecord: TypeAlias = PublicChatConnection | PublicEmbeddingProvider
Mutation: TypeAlias = Callable[
    [ModelConfig, ModelConfigSnapshot],
    tuple[ModelConfig, dict[str, CredentialAction]],
]


class ModelsCliError(ValueError):
    """A fixed, credential-free command error."""


class ModelsSaveError(ModelsCliError):
    """A failed save whose fieldized errors are already secret-safe."""

    def __init__(self, errors: tuple[ModelConfigFieldError, ...]) -> None:
        self.errors = errors
        super().__init__("The model configuration was not saved.")


@dataclass(frozen=True)
class RecordOptions:
    """Optional CLI values applied through descriptor-aware record construction."""

    connection_type: str | None = None
    preset: str | None = None
    name: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_mode: str | None = None
    reasoning_effort: str | None = None
    http_referer: str | None = None
    x_title: str | None = None
    num_ctx: int | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    credential_ref: str | None = None
    clear_credential: bool = False


@dataclass(frozen=True)
class EmbeddingSettingsOptions:
    """Presence-aware updates for the one shared Embedding model space."""

    model: str | None = None
    output_dimensionality: int | None = None
    similarity_threshold: float | None = None
    multimodal_enabled: bool | None = None


models_app = typer.Typer(
    help="Inspect and edit ordered Chat and Embedding model routes.",
    no_args_is_help=True,
)

_INLINE_CREDENTIAL_PLACEHOLDER = "cli-inline-credential-preserved"
_RESOLVE_OPTION = typer.Option(
    None,
    "--resolve",
    help="Resolve a blocking migration issue as ISSUE=ACTION[@POSITION].",
)


def _build_model_config_service() -> ModelConfigService:
    """Build the native service without starting an API server or provider call."""
    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.config import _default_config_path

    coordinator = cast("ModelRuntimeCoordinator", RuntimeContext())
    return ModelConfigService(_default_config_path(), coordinator)


def _interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _circuit_statuses() -> Mapping[str, str]:
    """Return optional live circuit summaries; offline CLI defaults to unknown."""
    return {}


def _safe_circuit_status(statuses: Mapping[str, str], connection_id: str) -> str:
    status = statuses.get(connection_id, "unknown")
    return status if status in {"closed", "open", "half_open", "unknown"} else "unknown"


def _credential_shell(status: PublicCredentialStatus) -> CredentialConfig:
    """Rebuild only public credential references; inline values remain unavailable."""
    if status.source == "env":
        return CredentialConfig(source="env", value=status.env_var)
    if status.source == "oauth":
        return CredentialConfig(source="oauth", value=status.credential_ref)
    if status.source == "inline" and status.configured:
        # Pure service.add/edit validation runs before save-time keep/set actions.
        # This fixed non-secret sentinel proves the source is populated without
        # reconstructing the unavailable value; save replaces it inside the
        # revision boundary before rendering or runtime construction.
        return CredentialConfig(source="inline", value=_INLINE_CREDENTIAL_PLACEHOLDER)
    return CredentialConfig()


def _chat_from_public(value: PublicChatConnection) -> ChatConnection:
    return ChatConnection(
        id=value.id,
        name=value.name,
        type=value.type,
        model=value.model,
        preset=value.preset,
        base_url=value.base_url,
        credential=_credential_shell(value.credential),
        api_mode=value.api_mode,
        reasoning_effort=value.reasoning_effort,
        http_referer=value.http_referer,
        x_title=value.x_title,
        num_ctx=value.num_ctx,
    )


def _embedding_from_public(value: PublicEmbeddingProvider) -> EmbeddingProviderConfig:
    return EmbeddingProviderConfig(
        id=value.id,
        name=value.name,
        type=value.type,
        preset=value.preset,
        base_url=value.base_url,
        credential=_credential_shell(value.credential),
    )


def public_models_to_domain(value: PublicModelConfig) -> ModelConfig:
    """Create an editable secret-free draft from one public service snapshot."""
    return ModelConfig(
        schema_version=value.schema_version,
        chat=ChatRouteConfig(
            connections=tuple(_chat_from_public(item) for item in value.chat.connections),
            concurrency=value.chat.concurrency,
            timeout_seconds=value.chat.timeout_seconds,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=value.embedding.enabled,
            settings=value.embedding.settings,
            providers=tuple(_embedding_from_public(item) for item in value.embedding.providers),
        ),
    )


def safe_credential_label(value: CredentialConfig | PublicCredentialStatus) -> str:
    """Render credential provenance without ever rendering an inline value."""
    if isinstance(value, PublicCredentialStatus):
        source = value.source
        configured = value.configured
        env_name = value.env_var
        credential_ref = value.credential_ref
    else:
        source = value.source
        configured = source != "none" and bool(value.value)
        env_name = value.value if source == "env" else ""
        credential_ref = value.value if source == "oauth" else ""
    if source == "env":
        return f"env:{env_name}" if configured else "env (missing)"
    if source == "oauth":
        return f"oauth:{credential_ref}" if configured else "oauth (missing)"
    if source == "inline":
        return "inline" if configured else "inline (missing)"
    return "none"


def _find_public_record(
    snapshot: ModelConfigSnapshot,
    connection_id: str,
) -> tuple[ModelKind, PublicChatConnection | PublicEmbeddingProvider]:
    for chat_item in snapshot.models.chat.connections:
        if chat_item.id == connection_id:
            return "chat", chat_item
    for embedding_item in snapshot.models.embedding.providers:
        if embedding_item.id == connection_id:
            return "embedding", embedding_item
    raise ModelsCliError("Connection ID was not found.")


def _find_domain_record(models: ModelConfig, connection_id: str) -> tuple[ModelKind, RouteRecord]:
    for chat_item in models.chat.connections:
        if chat_item.id == connection_id:
            return "chat", chat_item
    for embedding_item in models.embedding.providers:
        if embedding_item.id == connection_id:
            return "embedding", embedding_item
    raise ModelsCliError("Connection ID was not found.")


def _print_model_snapshot(snapshot: ModelConfigSnapshot) -> None:
    circuits = _circuit_statuses()
    typer.echo(f"source={snapshot.source} revision={snapshot.revision}")
    typer.echo("Chat route:")
    for index, chat_item in enumerate(snapshot.models.chat.connections):
        role = "primary" if index == 0 else f"fallback_{index}"
        preset = chat_item.preset or "-"
        typer.echo(
            f"  {index + 1} {role} id={chat_item.id} name={chat_item.name} "
            f"type={chat_item.type} preset={preset} model={chat_item.model} "
            f"credential={safe_credential_label(chat_item.credential)} "
            f"circuit={_safe_circuit_status(circuits, chat_item.id)}"
        )

    embedding = snapshot.models.embedding
    settings = embedding.settings
    typer.echo(
        "Embedding route: "
        f"enabled={str(embedding.enabled).lower()} shared_model={settings.model or '-'} "
        f"dimension={settings.output_dimensionality} "
        f"similarity={settings.similarity_threshold:g} "
        f"multimodal={str(settings.multimodal_enabled).lower()}"
    )
    for index, provider_item in enumerate(embedding.providers):
        preset = provider_item.preset or "-"
        typer.echo(
            f"  {index + 1} embedding id={provider_item.id} name={provider_item.name} "
            f"type={provider_item.type} preset={preset} "
            f"shared_model={settings.model or '-'} "
            f"credential={safe_credential_label(provider_item.credential)} "
            f"circuit={_safe_circuit_status(circuits, provider_item.id)}"
        )

    if snapshot.overrides:
        typer.echo("Read-only overrides:")
        for override_item in snapshot.overrides:
            typer.echo(f"  {override_item.path} source={override_item.source}")
    if snapshot.migration is not None:
        typer.echo(f"Migration state: {snapshot.migration_state}")
        for issue in snapshot.migration.issues:
            actions = ",".join(issue.allowed_actions) or "none"
            typer.echo(
                f"  migration {issue.severity} id={issue.id} code={issue.code} "
                f"field={issue.field} resolutions={actions}"
            )


def _definition_for(
    capability: ModelKind,
    requested: str | None,
    *,
    current: str | None,
    interactive: bool,
) -> ConnectionTypeDefinition:
    registry = connection_type_registry()
    available = registry.for_capability(capability)
    selected = requested
    if selected is None and current is not None:
        selected = current
    if selected is None and interactive:
        typer.echo(f"Available {capability} connection types:")
        for available_definition in available:
            typer.echo(
                f"  {available_definition.id}: {available_definition.label} "
                f"({available_definition.category})"
            )
        selected = typer.prompt("Connection type", default=available[0].id).strip()
    if selected is None:
        raise ModelsCliError("Non-interactive mode requires --connection-type.")
    selected_definition = registry.get(selected.strip())
    if selected_definition is None or capability not in selected_definition.capabilities:
        raise ModelsCliError("Connection type is not available for this route kind.")
    return selected_definition


def _preset_for(
    definition: ConnectionTypeDefinition,
    capability: ModelKind,
    requested: str | None,
    *,
    current: str | None,
    interactive: bool,
) -> str:
    registry = connection_type_registry()
    available = registry.presets_for(definition.id, capability)
    if not available:
        if requested not in {None, ""}:
            raise ModelsCliError("This connection type does not accept --preset.")
        return ""
    selected = requested
    if selected is None and current in available:
        selected = current
    if selected is None and interactive:
        typer.echo("Available presets:")
        for preset_id in available:
            preset = registry.preset(definition.id, preset_id)
            typer.echo(f"  {preset.id}: {preset.label}")
        selected = typer.prompt("Preset", default=available[0]).strip()
    if selected is None:
        raise ModelsCliError("Non-interactive mode requires --preset for this connection type.")
    if selected.strip() not in available:
        raise ModelsCliError("Preset is not available for this connection type and route kind.")
    return selected.strip()


def _preset_defaults(definition: ConnectionTypeDefinition, preset: str) -> Mapping[str, object]:
    if not preset:
        return {}
    return connection_type_registry().preset(definition.id, preset).defaults


def _string_value(
    supplied: str | None,
    existing: object | None,
    field_name: str,
    defaults: Mapping[str, object],
    *,
    preserve_existing: bool,
) -> str:
    if supplied is not None:
        return supplied.strip()
    if preserve_existing and existing is not None:
        return str(getattr(existing, field_name, ""))
    value = defaults.get(field_name, "")
    return str(value) if value is not None else ""


def _required_string(
    value: str,
    *,
    option: str,
    label: str,
    interactive: bool,
    default: str = "",
) -> str:
    if value.strip():
        return value.strip()
    if not interactive:
        raise ModelsCliError(f"Non-interactive mode requires {option}.")
    return str(typer.prompt(label, default=default, show_default=bool(default))).strip()


def _field_is_allowed(
    definition: ConnectionTypeDefinition,
    capability: ModelKind,
    preset: str,
    field_name: str,
) -> bool:
    return field_name in definition.allowed_fields(capability, preset)


def _reject_inapplicable_options(
    definition: ConnectionTypeDefinition,
    capability: ModelKind,
    preset: str,
    options: RecordOptions,
) -> None:
    supplied: tuple[tuple[str, object | None], ...] = (
        ("base_url", options.base_url),
        ("api_mode", options.api_mode),
        ("reasoning_effort", options.reasoning_effort),
        ("http_referer", options.http_referer),
        ("x_title", options.x_title),
        ("num_ctx", options.num_ctx),
    )
    for field_name, value in supplied:
        if value is not None and not _field_is_allowed(
            definition,
            capability,
            preset,
            field_name,
        ):
            raise ModelsCliError("A supplied field is not valid for this type and preset.")


def _credential_selection(
    definition: ConnectionTypeDefinition,
    capability: ModelKind,
    preset: str,
    options: RecordOptions,
    *,
    existing: RouteRecord | None,
    existing_status: PublicCredentialStatus | None,
    interactive: bool,
) -> tuple[CredentialConfig, CredentialAction | None]:
    choices = sum(
        value is not None
        for value in (options.api_key, options.api_key_env, options.credential_ref)
    ) + int(options.clear_credential)
    if choices > 1:
        raise ModelsCliError("Choose exactly one credential transition.")

    allowed = _field_is_allowed(definition, capability, preset, "credential")
    required = any(
        field.name == "credential" and field.required and field.applies_to(capability, preset)
        for field in definition.fields
    )
    same_type = existing is not None and existing.type == definition.id

    if definition.id == "codex_oauth":
        if options.api_key is not None or options.api_key_env is not None:
            raise ModelsCliError("Codex OAuth accepts only the imported codex reference.")
        if options.clear_credential:
            raise ModelsCliError("Codex OAuth credentials cannot be cleared from a route edit.")
        if options.credential_ref not in {None, "codex"}:
            raise ModelsCliError("Codex OAuth accepts only the imported codex reference.")
        return CredentialConfig(source="oauth", value="codex"), CredentialAction("keep")

    if options.credential_ref is not None:
        raise ModelsCliError("Credential references are only valid for an OAuth connection type.")
    if not allowed and (options.api_key is not None or options.api_key_env is not None):
        raise ModelsCliError("This connection type does not accept API credentials.")
    if options.api_key is not None:
        return (
            CredentialConfig(source="inline", value=_INLINE_CREDENTIAL_PLACEHOLDER),
            CredentialAction("set", options.api_key),
        )
    if options.api_key_env is not None:
        return (
            CredentialConfig(source="env", value=options.api_key_env),
            CredentialAction("env", options.api_key_env),
        )
    if options.clear_credential:
        return CredentialConfig(), CredentialAction("clear")

    if same_type and existing_status is not None and (existing_status.configured or not required):
        return _credential_shell(existing_status), CredentialAction("keep")
    if not allowed:
        action = CredentialAction("clear") if existing is not None else None
        return CredentialConfig(), action
    if not required:
        return CredentialConfig(), None
    if not interactive:
        raise ModelsCliError(
            "Non-interactive mode requires --api-key, --api-key-env, or --credential-ref."
        )
    api_key = typer.prompt("API credential", hide_input=True, show_default=False).strip()
    if not api_key:
        raise ModelsCliError("API credential is required for this connection type.")
    return (
        CredentialConfig(source="inline", value=_INLINE_CREDENTIAL_PLACEHOLDER),
        CredentialAction("set", api_key),
    )


def _chat_record(
    connection_id: str,
    options: RecordOptions,
    *,
    existing: ChatConnection | None,
    existing_status: PublicCredentialStatus | None,
    interactive: bool,
) -> tuple[ChatConnection, CredentialAction | None]:
    definition = _definition_for(
        "chat",
        options.connection_type,
        current=existing.type if existing is not None else None,
        interactive=interactive,
    )
    current_preset = (
        existing.preset if existing is not None and existing.type == definition.id else None
    )
    preset = _preset_for(
        definition,
        "chat",
        options.preset,
        current=current_preset,
        interactive=interactive,
    )
    _reject_inapplicable_options(definition, "chat", preset, options)
    preserve = existing is not None and existing.type == definition.id and existing.preset == preset
    defaults = _preset_defaults(definition, preset)
    model = _string_value(
        options.model,
        existing,
        "model",
        defaults,
        preserve_existing=preserve,
    )
    if _field_is_allowed(definition, "chat", preset, "model"):
        model = _required_string(
            model,
            option="--model",
            label="Model",
            interactive=interactive,
            default=str(defaults.get("model", "")),
        )
    name = options.name.strip() if options.name is not None else ""
    if not name and existing is not None:
        name = existing.name
    if not name and interactive:
        name = typer.prompt("Connection name", default=definition.label).strip()
    if not name:
        raise ModelsCliError("Non-interactive mode requires --name.")

    base_url = _string_value(
        options.base_url,
        existing,
        "base_url",
        defaults,
        preserve_existing=preserve,
    )
    if any(
        field.name == "base_url" and field.required and field.applies_to("chat", preset)
        for field in definition.fields
    ):
        base_url = _required_string(
            base_url,
            option="--base-url",
            label="Base URL",
            interactive=interactive,
            default=str(defaults.get("base_url", "")),
        )
    credential, action = _credential_selection(
        definition,
        "chat",
        preset,
        options,
        existing=existing,
        existing_status=existing_status,
        interactive=interactive,
    )
    num_ctx = options.num_ctx
    if num_ctx is None:
        num_ctx = (
            existing.num_ctx
            if preserve and existing is not None
            else int(str(defaults.get("num_ctx", 0)))
        )
    return (
        ChatConnection(
            id=connection_id,
            name=name,
            type=definition.id,
            preset=preset,
            model=model,
            base_url=base_url,
            credential=credential,
            api_mode=_string_value(
                options.api_mode,
                existing,
                "api_mode",
                defaults,
                preserve_existing=preserve,
            ),
            reasoning_effort=_string_value(
                options.reasoning_effort,
                existing,
                "reasoning_effort",
                defaults,
                preserve_existing=preserve,
            ),
            http_referer=_string_value(
                options.http_referer,
                existing,
                "http_referer",
                defaults,
                preserve_existing=preserve,
            ),
            x_title=_string_value(
                options.x_title,
                existing,
                "x_title",
                defaults,
                preserve_existing=preserve,
            ),
            num_ctx=num_ctx,
        ),
        action,
    )


def _embedding_record(
    connection_id: str,
    options: RecordOptions,
    *,
    existing: EmbeddingProviderConfig | None,
    existing_status: PublicCredentialStatus | None,
    interactive: bool,
) -> tuple[EmbeddingProviderConfig, CredentialAction | None]:
    definition = _definition_for(
        "embedding",
        options.connection_type,
        current=existing.type if existing is not None else None,
        interactive=interactive,
    )
    current_preset = (
        existing.preset if existing is not None and existing.type == definition.id else None
    )
    preset = _preset_for(
        definition,
        "embedding",
        options.preset,
        current=current_preset,
        interactive=interactive,
    )
    _reject_inapplicable_options(definition, "embedding", preset, options)
    preserve = existing is not None and existing.type == definition.id and existing.preset == preset
    defaults = _preset_defaults(definition, preset)
    name = options.name.strip() if options.name is not None else ""
    if not name and existing is not None:
        name = existing.name
    if not name and interactive:
        name = typer.prompt("Provider name", default=definition.label).strip()
    if not name:
        raise ModelsCliError("Non-interactive mode requires --name.")
    base_url = _string_value(
        options.base_url,
        existing,
        "base_url",
        defaults,
        preserve_existing=preserve,
    )
    if any(
        field.name == "base_url" and field.required and field.applies_to("embedding", preset)
        for field in definition.fields
    ):
        base_url = _required_string(
            base_url,
            option="--base-url",
            label="Base URL",
            interactive=interactive,
            default=str(defaults.get("base_url", "")),
        )
    credential, action = _credential_selection(
        definition,
        "embedding",
        preset,
        options,
        existing=existing,
        existing_status=existing_status,
        interactive=interactive,
    )
    return (
        EmbeddingProviderConfig(
            id=connection_id,
            name=name,
            type=definition.id,
            preset=preset,
            base_url=base_url,
            credential=credential,
        ),
        action,
    )


def _embedding_settings(
    current: EmbeddingModelSettings,
    options: EmbeddingSettingsOptions,
    *,
    interactive: bool,
) -> EmbeddingModelSettings:
    model = options.model.strip() if options.model is not None else current.model
    if not model and interactive:
        model = typer.prompt("Shared embedding model", default="bge-m3").strip()
    if not model:
        raise ModelsCliError("Non-interactive mode requires --model for Embedding settings.")
    return EmbeddingModelSettings(
        model=model,
        output_dimensionality=(
            options.output_dimensionality
            if options.output_dimensionality is not None
            else current.output_dimensionality
        ),
        similarity_threshold=(
            options.similarity_threshold
            if options.similarity_threshold is not None
            else current.similarity_threshold
        ),
        multimodal_enabled=(
            options.multimodal_enabled
            if options.multimodal_enabled is not None
            else current.multimodal_enabled
        ),
    )


def _parse_resolution_spec(value: str) -> tuple[str, str, int | None]:
    issue_id, separator, choice = value.partition("=")
    if not separator or not issue_id.strip() or not choice.strip():
        raise ModelsCliError("Migration resolutions use ISSUE=ACTION or ISSUE=ACTION@POSITION.")
    action, position_separator, raw_position = choice.strip().partition("@")
    position = None
    if position_separator:
        try:
            position = int(raw_position)
        except ValueError:
            raise ModelsCliError("Migration route positions must be integers.") from None
    return issue_id.strip(), action.strip(), position


def _migration_resolutions(
    snapshot: ModelConfigSnapshot,
    specs: Sequence[str],
    candidate: ModelConfig,
    *,
    interactive: bool,
) -> dict[str, MigrationResolution]:
    report = snapshot.migration
    if report is None:
        if specs:
            raise ModelsCliError("Migration resolutions were supplied for a native configuration.")
        return {}
    required = tuple(issue for issue in report.issues if issue.severity == "blocking")
    parsed: dict[str, tuple[str, int | None]] = {}
    for spec in specs:
        issue_id, action, position = _parse_resolution_spec(spec)
        if issue_id in parsed:
            raise ModelsCliError("A migration issue can be resolved only once.")
        parsed[issue_id] = (action, position)
    resolutions: dict[str, MigrationResolution] = {}
    for issue in required:
        selected = parsed.pop(issue.id, None)
        if selected is None and interactive:
            allowed = tuple(action for action in issue.allowed_actions if action != "cancel")
            typer.echo(f"Migration issue {issue.id}: {issue.code} ({issue.field})")
            typer.echo(f"Allowed resolutions: {', '.join(allowed)}")
            action = typer.prompt("Resolution", default=allowed[0]).strip()
            position = None
        elif selected is None:
            raise ModelsCliError(
                "Migration decisions are pending; pass --resolve ISSUE=ACTION[@POSITION]."
            )
        else:
            action, position = selected
        if action not in issue.allowed_actions or action == "cancel":
            raise ModelsCliError("Migration resolution action is not allowed for this issue.")
        embedding_settings = (
            candidate.embedding.settings if action == "apply_shared_embedding_settings" else None
        )
        resolutions[issue.id] = MigrationResolution(
            action=cast("MigrationAction", action),
            position=position,
            embedding_settings=embedding_settings,
        )
    if parsed:
        raise ModelsCliError("A migration resolution references an unknown or nonblocking issue.")
    return resolutions


async def _save_with_rebase(
    service: ModelConfigService,
    mutation: Mutation,
    *,
    resolution_specs: Sequence[str],
    interactive: bool,
) -> ModelConfigSnapshot:
    for attempt in range(2):
        snapshot = service.read()
        models = public_models_to_domain(snapshot.models)
        candidate, actions = mutation(models, snapshot)
        resolutions = _migration_resolutions(
            snapshot,
            resolution_specs,
            candidate,
            interactive=interactive,
        )
        result = await service.save(
            ModelConfigSaveRequest(
                revision=snapshot.revision,
                models=candidate,
                credential_actions=actions,
                migration_resolutions=resolutions,
            )
        )
        if result.ok:
            return result.snapshot
        if result.conflict and attempt == 0:
            typer.echo("Revision changed; rebasing once on latest ordered routes.")
            continue
        if result.errors:
            raise ModelsSaveError(result.errors)
        if result.conflict:
            raise ModelsCliError("Revision changed again; no changes were saved.")
        raise ModelsCliError("The model configuration was not saved.")
    raise ModelsCliError("The model configuration was not saved.")


def _print_field_errors(errors: Sequence[ModelConfigFieldError]) -> None:
    for error in errors:
        typer.echo(f"Error: {error.path} [{error.code}] {error.message}", err=True)


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _run_safe(operation: Callable[[], None]) -> None:
    try:
        operation()
    except typer.Exit:
        raise
    except ModelsSaveError as exc:
        _print_field_errors(exc.errors)
        raise typer.Exit(code=1) from None
    except ModelConfigValidationError as exc:
        _print_field_errors(exc.errors)
        raise typer.Exit(code=1) from None
    except ModelsCliError as exc:
        _fail(str(exc))
    except Exception:
        _fail("The model command failed safely; no credential value was displayed.")


def _record_options(
    *,
    connection_type: str | None,
    preset: str | None,
    name: str | None,
    model: str | None,
    base_url: str | None,
    api_mode: str | None,
    api_key: str | None,
    api_key_env: str | None,
    credential_ref: str | None,
    reasoning_effort: str | None,
    http_referer: str | None,
    x_title: str | None,
    num_ctx: int | None,
    clear_credential: bool,
) -> RecordOptions:
    return RecordOptions(
        connection_type=connection_type,
        preset=preset,
        name=name,
        model=model,
        base_url=base_url,
        api_mode=api_mode,
        api_key=api_key,
        api_key_env=api_key_env,
        credential_ref=credential_ref,
        reasoning_effort=reasoning_effort,
        http_referer=http_referer,
        x_title=x_title,
        num_ctx=num_ctx,
        clear_credential=clear_credential,
    )


@models_app.command("list")
def list_models() -> None:
    """List ordered routes, shared settings, safe credentials, and migration state."""

    def operation() -> None:
        _print_model_snapshot(_build_model_config_service().read())

    _run_safe(operation)


@models_app.command("add")
def add_model(
    kind: str = typer.Option(..., "--kind", help="Route kind: chat or embedding."),
    connection_id: str | None = typer.Option(None, "--id", help="Globally stable route ID."),
    connection_type: str | None = typer.Option(None, "--connection-type"),
    preset: str | None = typer.Option(None, "--preset"),
    name: str | None = typer.Option(None, "--name"),
    model: str | None = typer.Option(None, "--model"),
    base_url: str | None = typer.Option(None, "--base-url"),
    api_mode: str | None = typer.Option(None, "--api-mode"),
    api_key: str | None = typer.Option(None, "--api-key", help="Inline API key (shell-visible)."),
    api_key_env: str | None = typer.Option(None, "--api-key-env"),
    credential_ref: str | None = typer.Option(None, "--credential-ref"),
    reasoning_effort: str | None = typer.Option(None, "--reasoning-effort"),
    http_referer: str | None = typer.Option(None, "--http-referer"),
    x_title: str | None = typer.Option(None, "--x-title"),
    num_ctx: int | None = typer.Option(None, "--num-ctx", min=0),
    position: int | None = typer.Option(None, "--position", min=1, max=10),
    output_dimensionality: int | None = typer.Option(
        None,
        "--output-dimensionality",
        min=0,
    ),
    similarity_threshold: float | None = typer.Option(
        None,
        "--similarity-threshold",
        min=0.0,
        max=1.0,
    ),
    multimodal_enabled: bool | None = typer.Option(
        None,
        "--multimodal/--no-multimodal",
    ),
    resolve: list[str] | None = _RESOLVE_OPTION,
) -> None:
    """Add one Chat connection or Embedding provider at a one-based position."""

    def operation() -> None:
        route_kind = kind.strip().lower()
        if route_kind not in {"chat", "embedding"}:
            raise ModelsCliError("--kind must be chat or embedding.")
        selected_kind = cast("ModelKind", route_kind)
        interactive = _interactive_terminal()
        stable_id = (connection_id or "").strip()
        if not stable_id and interactive:
            stable_id = typer.prompt("Stable connection ID").strip()
        if not stable_id:
            raise ModelsCliError("Non-interactive mode requires --id.")
        options = _record_options(
            connection_type=connection_type,
            preset=preset,
            name=name,
            model=model if selected_kind == "chat" else None,
            base_url=base_url,
            api_mode=api_mode,
            api_key=api_key,
            api_key_env=api_key_env,
            credential_ref=credential_ref,
            reasoning_effort=reasoning_effort,
            http_referer=http_referer,
            x_title=x_title,
            num_ctx=num_ctx,
            clear_credential=False,
        )
        settings_options = EmbeddingSettingsOptions(
            model=model if selected_kind == "embedding" else None,
            output_dimensionality=output_dimensionality,
            similarity_threshold=similarity_threshold,
            multimodal_enabled=multimodal_enabled,
        )
        if selected_kind == "chat" and any(
            value is not None
            for value in (output_dimensionality, similarity_threshold, multimodal_enabled)
        ):
            raise ModelsCliError("Embedding shared settings require --kind embedding.")

        service = _build_model_config_service()

        def mutation(
            models: ModelConfig,
            snapshot: ModelConfigSnapshot,
        ) -> tuple[ModelConfig, dict[str, CredentialAction]]:
            if selected_kind == "chat":
                chat_record, action = _chat_record(
                    stable_id,
                    options,
                    existing=None,
                    existing_status=None,
                    interactive=interactive,
                )
                candidate = service.add(models, chat_record, position=position)
            else:
                embedding_record, action = _embedding_record(
                    stable_id,
                    options,
                    existing=None,
                    existing_status=None,
                    interactive=interactive,
                )
                settings = _embedding_settings(
                    models.embedding.settings,
                    settings_options,
                    interactive=interactive,
                )
                shell = replace(
                    models,
                    embedding=replace(models.embedding, enabled=True, settings=settings),
                )
                candidate = service.add(shell, embedding_record, position=position)
            del snapshot
            return candidate, ({stable_id: action} if action is not None else {})

        saved = asyncio.run(
            _save_with_rebase(
                service,
                mutation,
                resolution_specs=resolve or (),
                interactive=interactive,
            )
        )
        typer.echo(f"Added {stable_id}; revision={saved.revision}")

    _run_safe(operation)


@models_app.command("edit")
def edit_model(
    connection_id: str = typer.Argument(..., help="Stable route ID."),
    connection_type: str | None = typer.Option(None, "--connection-type"),
    preset: str | None = typer.Option(None, "--preset"),
    name: str | None = typer.Option(None, "--name"),
    model: str | None = typer.Option(None, "--model"),
    base_url: str | None = typer.Option(None, "--base-url"),
    api_mode: str | None = typer.Option(None, "--api-mode"),
    api_key: str | None = typer.Option(None, "--api-key", help="Inline API key (shell-visible)."),
    api_key_env: str | None = typer.Option(None, "--api-key-env"),
    credential_ref: str | None = typer.Option(None, "--credential-ref"),
    clear_credential: bool = typer.Option(False, "--clear-credential"),
    reasoning_effort: str | None = typer.Option(None, "--reasoning-effort"),
    http_referer: str | None = typer.Option(None, "--http-referer"),
    x_title: str | None = typer.Option(None, "--x-title"),
    num_ctx: int | None = typer.Option(None, "--num-ctx", min=0),
    output_dimensionality: int | None = typer.Option(
        None,
        "--output-dimensionality",
        min=0,
    ),
    similarity_threshold: float | None = typer.Option(
        None,
        "--similarity-threshold",
        min=0.0,
        max=1.0,
    ),
    multimodal_enabled: bool | None = typer.Option(
        None,
        "--multimodal/--no-multimodal",
    ),
    resolve: list[str] | None = _RESOLVE_OPTION,
) -> None:
    """Edit one route record without changing its stable ID."""

    def operation() -> None:
        service = _build_model_config_service()
        interactive = _interactive_terminal()
        record_options = _record_options(
            connection_type=connection_type,
            preset=preset,
            name=name,
            model=model,
            base_url=base_url,
            api_mode=api_mode,
            api_key=api_key,
            api_key_env=api_key_env,
            credential_ref=credential_ref,
            reasoning_effort=reasoning_effort,
            http_referer=http_referer,
            x_title=x_title,
            num_ctx=num_ctx,
            clear_credential=clear_credential,
        )
        settings_options = EmbeddingSettingsOptions(
            model=model,
            output_dimensionality=output_dimensionality,
            similarity_threshold=similarity_threshold,
            multimodal_enabled=multimodal_enabled,
        )

        def mutation(
            models: ModelConfig,
            snapshot: ModelConfigSnapshot,
        ) -> tuple[ModelConfig, dict[str, CredentialAction]]:
            kind, existing = _find_domain_record(models, connection_id)
            public_kind, public = _find_public_record(snapshot, connection_id)
            if public_kind != kind:
                raise ModelsCliError("Connection route kind changed during the edit.")
            if kind == "chat":
                if any(
                    value is not None
                    for value in (
                        output_dimensionality,
                        similarity_threshold,
                        multimodal_enabled,
                    )
                ):
                    raise ModelsCliError("Embedding shared settings cannot edit a Chat route.")
                assert isinstance(existing, ChatConnection)
                assert isinstance(public, PublicChatConnection)
                chat_replacement, action = _chat_record(
                    connection_id,
                    record_options,
                    existing=existing,
                    existing_status=public.credential,
                    interactive=interactive,
                )
                candidate = service.edit(models, connection_id, chat_replacement)
            else:
                assert isinstance(existing, EmbeddingProviderConfig)
                assert isinstance(public, PublicEmbeddingProvider)
                provider_options = replace(record_options, model=None)
                embedding_replacement, action = _embedding_record(
                    connection_id,
                    provider_options,
                    existing=existing,
                    existing_status=public.credential,
                    interactive=interactive,
                )
                settings = _embedding_settings(
                    models.embedding.settings,
                    settings_options,
                    interactive=interactive,
                )
                shell = replace(
                    models,
                    embedding=replace(models.embedding, settings=settings),
                )
                candidate = service.edit(shell, connection_id, embedding_replacement)
            return candidate, ({connection_id: action} if action is not None else {})

        saved = asyncio.run(
            _save_with_rebase(
                service,
                mutation,
                resolution_specs=resolve or (),
                interactive=interactive,
            )
        )
        typer.echo(f"Edited {connection_id}; revision={saved.revision}")

    _run_safe(operation)


@models_app.command("remove")
def remove_model(
    connection_id: str = typer.Argument(..., help="Stable route ID."),
    resolve: list[str] | None = _RESOLVE_OPTION,
) -> None:
    """Remove one stable route ID; the final Chat connection is protected."""

    def operation() -> None:
        service = _build_model_config_service()

        def mutation(
            models: ModelConfig,
            snapshot: ModelConfigSnapshot,
        ) -> tuple[ModelConfig, dict[str, CredentialAction]]:
            kind, _record = _find_domain_record(models, connection_id)
            shell = models
            if kind == "embedding" and len(models.embedding.providers) == 1:
                shell = replace(
                    models,
                    embedding=replace(models.embedding, enabled=False),
                )
            del snapshot
            return service.remove(shell, connection_id), {}

        saved = asyncio.run(
            _save_with_rebase(
                service,
                mutation,
                resolution_specs=resolve or (),
                interactive=_interactive_terminal(),
            )
        )
        typer.echo(f"Removed {connection_id}; revision={saved.revision}")

    _run_safe(operation)


@models_app.command("move")
def move_model(
    connection_id: str = typer.Argument(..., help="Stable route ID."),
    position: int = typer.Option(..., "--position", min=1, max=10),
    resolve: list[str] | None = _RESOLVE_OPTION,
) -> None:
    """Move one stable ID to an exact one-based position in its current route."""

    def operation() -> None:
        service = _build_model_config_service()

        def mutation(
            models: ModelConfig,
            snapshot: ModelConfigSnapshot,
        ) -> tuple[ModelConfig, dict[str, CredentialAction]]:
            del snapshot
            return service.move(models, connection_id, position), {}

        saved = asyncio.run(
            _save_with_rebase(
                service,
                mutation,
                resolution_specs=resolve or (),
                interactive=_interactive_terminal(),
            )
        )
        typer.echo(f"Moved {connection_id} to position {position}; revision={saved.revision}")

    _run_safe(operation)


async def _probe_with_revision_retry(
    service: ModelConfigService,
    connection_id: str,
) -> ModelConfigProbeResult:
    for attempt in range(2):
        snapshot = service.read()
        kind, public = _find_public_record(snapshot, connection_id)
        if kind == "chat":
            assert isinstance(public, PublicChatConnection)
            draft: RouteRecord = _chat_from_public(public)
            settings = None
        else:
            assert isinstance(public, PublicEmbeddingProvider)
            draft = _embedding_from_public(public)
            settings = snapshot.models.embedding.settings
        try:
            capture = await service.capture_probe(
                draft,
                revision=snapshot.revision,
                settings=settings,
                credential_action=CredentialAction("keep"),
            )
            result = await service.probe_captured(capture)
            await service.revalidate_probe_capture(capture)
            return result
        except ModelConfigRevisionConflictError:
            if attempt == 0:
                typer.echo("Revision changed; retrying the exact latest stable ID once.")
                continue
            raise ModelsCliError("Revision changed again; probe result was discarded.") from None
    raise ModelsCliError("Probe result was discarded.")


@models_app.command("probe")
def probe_model(connection_id: str = typer.Argument(..., help="Stable route ID.")) -> None:
    """Probe exactly one persisted stable ID without route fallback."""

    def operation() -> None:
        result = asyncio.run(
            _probe_with_revision_retry(_build_model_config_service(), connection_id)
        )
        if not result.ok:
            code = result.error_code or "probe_failed"
            _fail(f"Exact probe failed safely ({code}).")
        suffix = (
            f" observed_dimension={result.observed_dimension}" if result.observed_dimension else ""
        )
        typer.echo(f"Exact probe succeeded: id={result.connection_id}{suffix}")

    _run_safe(operation)


def _guided_type_and_preset(
    capability: ModelKind,
    current_type: str | None,
    current_preset: str | None,
) -> tuple[str, str]:
    registry = connection_type_registry()
    definitions = registry.for_capability(capability)
    typer.echo(f"Available {capability} connection types:")
    for definition in definitions:
        typer.echo(f"  {definition.id}: {definition.label} ({definition.category})")
    selected_type = typer.prompt(
        "Connection type",
        default=current_type or definitions[0].id,
    ).strip()
    definition = _definition_for(
        capability,
        selected_type,
        current=None,
        interactive=True,
    )
    presets = registry.presets_for(definition.id, capability)
    if not presets:
        return definition.id, ""
    typer.echo("Available presets:")
    for preset_id in presets:
        typer.echo(f"  {preset_id}: {registry.preset(definition.id, preset_id).label}")
    default = (
        current_preset
        if current_type == definition.id and current_preset in presets
        else presets[0]
    )
    selected_preset = typer.prompt("Preset", default=default).strip()
    return definition.id, _preset_for(
        definition,
        capability,
        selected_preset,
        current=None,
        interactive=True,
    )


def _guided_record_options(
    capability: ModelKind,
    definition: ConnectionTypeDefinition,
    preset: str,
    *,
    name: str,
    existing: PublicRouteRecord | None,
) -> RecordOptions:
    """Prompt only descriptor fields that apply to the selected route shape."""
    options = RecordOptions(
        connection_type=definition.id,
        preset=preset,
        name=name,
    )
    defaults = _preset_defaults(definition, preset)
    preserve = existing is not None and existing.type == definition.id and existing.preset == preset
    for field in definition.fields:
        if field.name in {"preset", "credential"} or not field.applies_to(
            capability,
            preset,
        ):
            continue
        raw_default = (
            getattr(existing, field.name, "")
            if preserve and existing is not None
            else defaults.get(field.name, "")
        )
        if field.choices:
            typer.echo(f"{field.label} choices: {', '.join(field.choices)}")
        if field.input_type == "number":
            value: object = typer.prompt(
                field.label,
                default=int(str(raw_default or 0)),
                type=int,
            )
        else:
            value = str(
                typer.prompt(
                    field.label,
                    default=str(raw_default or ""),
                    show_default=bool(raw_default),
                )
            ).strip()
        options = replace(options, **cast("Any", {field.name: value}))
    return options


def guided_chat_editor() -> None:
    """Interactively replace the Chat route with one descriptor-driven connection."""
    service = _build_model_config_service()
    snapshot = service.read()
    current = snapshot.models.chat.connections[0] if snapshot.models.chat.connections else None
    connection_type, preset = _guided_type_and_preset(
        "chat",
        current.type if current is not None else None,
        current.preset if current is not None else None,
    )
    stable_id = typer.prompt(
        "Stable connection ID",
        default=current.id if current is not None else f"{connection_type}-main",
    ).strip()
    name = typer.prompt(
        "Connection name",
        default=current.name if current is not None else connection_type,
    ).strip()
    options = _guided_record_options(
        "chat",
        connection_type_registry().definition(connection_type),
        preset,
        name=name,
        existing=current,
    )

    def mutation(
        models: ModelConfig,
        latest: ModelConfigSnapshot,
    ) -> tuple[ModelConfig, dict[str, CredentialAction]]:
        existing_domain: ChatConnection | None = None
        existing_status: PublicCredentialStatus | None = None
        for domain_item in models.chat.connections:
            if domain_item.id == stable_id:
                existing_domain = domain_item
                break
        for public_item in latest.models.chat.connections:
            if public_item.id == stable_id:
                existing_status = public_item.credential
                break
        record, action = _chat_record(
            stable_id,
            options,
            existing=existing_domain,
            existing_status=existing_status,
            interactive=True,
        )
        candidate = replace(models, chat=replace(models.chat, connections=(record,)))
        return candidate, ({stable_id: action} if action is not None else {})

    saved = asyncio.run(
        _save_with_rebase(
            service,
            mutation,
            resolution_specs=(),
            interactive=True,
        )
    )
    typer.echo(f"Configured Chat route; revision={saved.revision}")


def guided_embedding_editor() -> None:
    """Interactively add/edit/disable providers in one shared Embedding space."""
    service = _build_model_config_service()
    snapshot = service.read()
    providers = snapshot.models.embedding.providers
    action = "add"
    selected_id = ""
    current: PublicEmbeddingProvider | None = None
    if providers:
        typer.echo("Embedding providers: " + ", ".join(item.id for item in providers))
        action = typer.prompt("Action (add/edit/disable)", default="edit").strip().lower()
        if action == "disable":

            def disable_mutation(
                models: ModelConfig,
                latest: ModelConfigSnapshot,
            ) -> tuple[ModelConfig, dict[str, CredentialAction]]:
                del latest
                return (
                    replace(
                        models,
                        embedding=replace(models.embedding, enabled=False, providers=()),
                    ),
                    {},
                )

            saved = asyncio.run(
                _save_with_rebase(
                    service,
                    disable_mutation,
                    resolution_specs=(),
                    interactive=True,
                )
            )
            typer.echo(f"Disabled Embedding route; revision={saved.revision}")
            return
        if action == "edit":
            selected_id = typer.prompt("Provider ID", default=providers[0].id).strip()
            current = next((item for item in providers if item.id == selected_id), None)
            if current is None:
                raise ModelsCliError("Connection ID was not found.")
        elif action != "add":
            raise ModelsCliError("Embedding action must be add, edit, or disable.")
    connection_type, preset = _guided_type_and_preset(
        "embedding",
        current.type if current is not None else None,
        current.preset if current is not None else None,
    )
    if action == "add":
        selected_id = typer.prompt(
            "Stable provider ID",
            default=f"{connection_type}-embedding-{len(providers) + 1}",
        ).strip()
    provider_name = typer.prompt(
        "Provider name",
        default=current.name if current is not None else connection_type,
    ).strip()
    settings = snapshot.models.embedding.settings
    settings_options = EmbeddingSettingsOptions(
        model=typer.prompt("Shared embedding model", default=settings.model or "bge-m3").strip(),
        output_dimensionality=typer.prompt(
            "Output dimensionality",
            default=settings.output_dimensionality,
            type=int,
        ),
        similarity_threshold=typer.prompt(
            "Similarity threshold",
            default=settings.similarity_threshold,
            type=float,
        ),
        multimodal_enabled=typer.confirm(
            "Enable multimodal embeddings?",
            default=settings.multimodal_enabled,
        ),
    )
    record_options = _guided_record_options(
        "embedding",
        connection_type_registry().definition(connection_type),
        preset,
        name=provider_name,
        existing=current,
    )

    def mutation(
        models: ModelConfig,
        latest: ModelConfigSnapshot,
    ) -> tuple[ModelConfig, dict[str, CredentialAction]]:
        existing_domain = None
        existing_status = None
        if action == "edit":
            kind, found = _find_domain_record(models, selected_id)
            if kind != "embedding" or not isinstance(found, EmbeddingProviderConfig):
                raise ModelsCliError("Connection ID was not found in the Embedding route.")
            existing_domain = found
            public_kind, public = _find_public_record(latest, selected_id)
            if public_kind != "embedding" or not isinstance(public, PublicEmbeddingProvider):
                raise ModelsCliError("Connection ID was not found in the Embedding route.")
            existing_status = public.credential
        record, credential_action = _embedding_record(
            selected_id,
            record_options,
            existing=existing_domain,
            existing_status=existing_status,
            interactive=True,
        )
        shared = _embedding_settings(
            models.embedding.settings,
            settings_options,
            interactive=True,
        )
        shell = replace(
            models,
            embedding=replace(models.embedding, enabled=True, settings=shared),
        )
        candidate = (
            service.edit(shell, selected_id, record)
            if action == "edit"
            else service.add(shell, record)
        )
        actions = {selected_id: credential_action} if credential_action is not None else {}
        return candidate, actions

    saved = asyncio.run(
        _save_with_rebase(
            service,
            mutation,
            resolution_specs=(),
            interactive=True,
        )
    )
    typer.echo(f"Configured Embedding route; revision={saved.revision}")


__all__ = [
    "guided_chat_editor",
    "guided_embedding_editor",
    "models_app",
    "public_models_to_domain",
    "safe_credential_label",
]
