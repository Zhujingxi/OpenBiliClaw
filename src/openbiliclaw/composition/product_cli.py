"""JSON-only product CLI adapter over the in-process application facade."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar, cast

from fastapi import HTTPException
from pydantic import BaseModel, TypeAdapter, ValidationError

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.edit_profile import EditProfileCommand
from openbiliclaw.application.errors import ApplicationError
from openbiliclaw.application.plugin_access import SubmitAccessMaterialCommand
from openbiliclaw.application.sources import ConnectSourceCommand, DisconnectSourceCommand
from openbiliclaw.content.integration.identity import ContentRef
from openbiliclaw.hosts.api.routers.models import (
    catalog as model_catalog,
)
from openbiliclaw.hosts.api.routers.models import (
    current as current_model,
)
from openbiliclaw.hosts.api.routers.models import (
    update as update_model,
)
from openbiliclaw.hosts.api.schemas.models import (
    AccessMaterialRequest,
    AssistantTurnRequest,
    ConfirmActionRequest,
    FeedbackRequest,
    ModelConfigurationRequest,
    ObservationsRequest,
    ProfileEditRequest,
    ProposeActionRequest,
    RefreshRequest,
)
from openbiliclaw.recommendation.models import FeedbackKind, record_identity
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.profile import EXPLORATION_DISABLED_CLAIM_ID

if TYPE_CHECKING:
    import argparse

    from openbiliclaw.composition.application import Application
    from openbiliclaw.hosts.api.dependencies import HostDependencies


class ProductFacade(Protocol):
    async def list_sources(self, account_id: str | None, limit: int) -> object: ...
    async def source_status(self, provider_id: str, account_id: str | None) -> object: ...
    async def source_form(self, provider_id: str, method_id: str) -> object: ...
    def provider_capabilities(self, provider_id: str) -> tuple[str, ...]: ...
    def access_recipe(self, provider_id: str) -> object: ...
    async def submit_access_material(self, command: SubmitAccessMaterialCommand) -> object: ...
    async def connect_source(self, command: ConnectSourceCommand) -> object: ...
    async def disconnect_source(self, command: DisconnectSourceCommand) -> object: ...
    async def sync_source(self, provider_id: str) -> object: ...
    async def import_provider_evidence(self, provider_id: str, path: Path) -> object: ...
    async def get_recommendations(self, limit: int) -> object: ...
    async def refresh_recommendations(self, command: object) -> object: ...
    async def record_feedback(self, command: object) -> object: ...
    async def record_feedback_for_shown(
        self, shown_id: str, kind: FeedbackKind, idempotency_key: str, exposed: bool = False
    ) -> object: ...
    async def record_observations(self, command: object) -> object: ...
    async def show_profile(self, profile_id: str) -> object: ...
    async def edit_profile(self, command: EditProfileCommand) -> object: ...
    async def search_content(self, provider_id: str, text: str, limit: int) -> object: ...
    async def get_content_details(self, reference: str) -> object: ...
    async def propose_action(self, command: object) -> object: ...
    async def confirm_action(self, command: object) -> object: ...
    async def reject_action(self, command: object) -> object: ...
    async def assistant_turn(self, request: AssistantTurnRequest, device_id: str) -> object: ...
    async def conversation(self, conversation_id: str, device_id: str) -> object: ...
    async def conversation_messages(
        self, conversation_id: str, device_id: str, limit: int
    ) -> object: ...
    async def job_health(self) -> object: ...
    async def config_diagnostics(self) -> object: ...
    async def model_diagnostics(self) -> object: ...


_PRODUCT_COMMANDS = frozenset(
    {
        "sources",
        "feed",
        "refresh",
        "feedback",
        "record-feedback",
        "observations",
        "profile",
        "assistant",
        "conversations",
        "search",
        "content",
        "actions",
        "runtime",
        "models",
        "import",
    }
)
_FEEDBACK_KIND = {
    "like": FeedbackKind.LIKED,
    "dismiss": FeedbackKind.DISMISSED,
    "save": FeedbackKind.SAVED,
    "open": FeedbackKind.OPENED,
}
_REQUEST_LIMIT = 1_048_576
_REQUEST_MODEL = TypeVar("_REQUEST_MODEL")
_STRING_MAPPING = TypeAdapter(dict[str, str])


def add_product_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    common: argparse.ArgumentParser,
) -> None:
    """Register the product workflow command surface."""

    sources = subparsers.add_parser(
        "sources",
        parents=[common],
        help="manage content source connections",
        description="List, inspect, connect, disconnect, or synchronize content sources.",
    )
    source_commands = sources.add_subparsers(dest="source_command", required=True)
    listing = source_commands.add_parser(
        "list",
        parents=[common],
        help="list source connection states",
        description="List source connection states.",
    )
    listing.add_argument("--account-id")
    listing.add_argument("--limit", type=int, default=50)
    status = source_commands.add_parser(
        "status",
        parents=[common],
        help="show one source state",
        description="Show one source connection state.",
    )
    status.add_argument("provider_id")
    status.add_argument("--account-id")
    form = source_commands.add_parser(
        "form",
        parents=[common],
        help="show a source connection form",
        description="Show a source connection form.",
    )
    form.add_argument("provider_id")
    form.add_argument("method_id")
    capabilities = source_commands.add_parser(
        "capabilities",
        parents=[common],
        help="show provider capabilities",
        description="Show provider capabilities.",
    )
    capabilities.add_argument("provider_id")
    recipe = source_commands.add_parser(
        "access-recipe",
        parents=[common],
        help="show the provider access recipe",
        description="Show the provider access recipe.",
    )
    recipe.add_argument("provider_id")
    material = source_commands.add_parser(
        "submit-material",
        parents=[common],
        help="submit plugin-captured access material",
        description="Submit plugin-captured access material.",
    )
    material.add_argument("provider_id")
    material.add_argument("request", help="JSON request file, or - for stdin")
    add = source_commands.add_parser(
        "add", parents=[common], help="connect a source", description="Connect a source."
    )
    add.add_argument("provider_id")
    add.add_argument("method_id")
    add.add_argument("--account-id")
    add.add_argument(
        "--permission", action="append", required=True, choices=[x.value for x in Permission]
    )
    fields = add.add_mutually_exclusive_group()
    fields.add_argument("--field", action="append", default=[], metavar="KEY=VALUE")
    fields.add_argument("--fields-file", help="secret-safe JSON object file, or - for stdin")
    add.add_argument("--idempotency-key", required=True)
    remove = source_commands.add_parser(
        "remove", parents=[common], help="disconnect a source", description="Disconnect a source."
    )
    remove.add_argument("provider_id")
    remove.add_argument("--account-id")
    remove.add_argument("--idempotency-key", required=True)
    sync = source_commands.add_parser(
        "sync", parents=[common], help="synchronize a source", description="Synchronize evidence."
    )
    sync.add_argument("provider_id")

    feed = subparsers.add_parser(
        "feed",
        parents=[common],
        help="show recommendations",
        description="Show the current recommendation feed.",
    )
    feed.add_argument("--limit", type=int, default=20)
    refresh = subparsers.add_parser(
        "refresh",
        parents=[common],
        help="request a bounded recommendation refresh",
        description="Request a bounded recommendation refresh.",
    )
    refresh.add_argument("--idempotency-key", required=True)
    refresh.add_argument("--maximum-items", type=int, default=50)

    feedback = subparsers.add_parser(
        "feedback",
        parents=[common],
        help="record recommendation feedback",
        description="Record explicit feedback for a delivered recommendation.",
    )
    feedback.add_argument("shown_id")
    feedback.add_argument("kind", choices=tuple(_FEEDBACK_KIND))
    feedback.add_argument("--idempotency-key", required=True)
    feedback.add_argument("--exposed", action="store_true")
    raw_feedback = subparsers.add_parser(
        "record-feedback",
        parents=[common],
        help="record a complete typed feedback request",
        description="Record a complete typed feedback request.",
    )
    raw_feedback.add_argument("request", help="JSON request file, or - for stdin")

    observations = subparsers.add_parser(
        "observations",
        parents=[common],
        help="record a typed observation batch",
        description="Record a typed observation batch.",
    )
    observations.add_argument("request", help="JSON request file, or - for stdin")

    profile = subparsers.add_parser(
        "profile",
        parents=[common],
        help="inspect or adjust the profile",
        description="Inspect the profile or control exploration.",
    )
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    show = profile_commands.add_parser(
        "show",
        parents=[common],
        help="show the profile",
        description="Show the bounded preference profile.",
    )
    show.add_argument("--profile-id", default="default")
    exploration = profile_commands.add_parser(
        "exploration",
        parents=[common],
        help="control exploration",
        description="Enable or disable recommendation exploration.",
    )
    exploration.add_argument("state", choices=("disable", "enable"))
    exploration.add_argument("--profile-id", default="default")
    exploration.add_argument("--account-id", default="local")
    exploration.add_argument("--idempotency-key", required=True)
    profile_edit = profile_commands.add_parser(
        "edit",
        parents=[common],
        help="apply a complete typed profile edit",
        description="Apply a complete typed profile edit.",
    )
    profile_edit.add_argument("request", help="JSON request file, or - for stdin")

    assistant = subparsers.add_parser(
        "assistant",
        parents=[common],
        help="send an Assistant message",
        description="Send one message to the bounded Assistant workflow.",
    )
    assistant.add_argument("message")
    assistant.add_argument("--conversation-id", default=record_identity("conv", "cli"))
    assistant.add_argument("--device-id", default="cli")
    assistant.add_argument("--locale", default="en-US")
    conversations = subparsers.add_parser(
        "conversations",
        parents=[common],
        help="inspect Assistant conversations",
        description="Inspect Assistant conversations and messages.",
    )
    conversation_commands = conversations.add_subparsers(dest="conversation_command", required=True)
    for name in ("show", "messages"):
        command = conversation_commands.add_parser(
            name,
            parents=[common],
            description=(
                "Show one Assistant conversation."
                if name == "show"
                else "Show messages in one Assistant conversation."
            ),
        )
        command.add_argument("conversation_id")
        command.add_argument("--device-id", default="cli")
        if name == "messages":
            command.add_argument("--limit", type=int, default=50)

    search = subparsers.add_parser(
        "search",
        parents=[common],
        help="search connected content",
        description="Search content from one connected provider.",
    )
    search.add_argument("provider_id")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    content = subparsers.add_parser(
        "content", parents=[common], help="inspect content", description="Inspect content details."
    )
    content_commands = content.add_subparsers(dest="content_command", required=True)
    detail = content_commands.add_parser(
        "detail",
        parents=[common],
        help="fetch content details",
        description="Fetch provider-native content details.",
    )
    detail.add_argument("reference", help="JSON-serialized ContentRef")

    actions = subparsers.add_parser(
        "actions",
        parents=[common],
        help="propose, confirm, or reject pending actions",
        description="Propose, confirm, or reject pending content actions.",
    )
    action_commands = actions.add_subparsers(dest="action_command", required=True)
    propose = action_commands.add_parser(
        "propose", parents=[common], description="Propose a pending content action."
    )
    propose.add_argument("request", help="JSON request file, or - for stdin")
    for name in ("confirm", "reject"):
        command = action_commands.add_parser(
            name,
            parents=[common],
            description=f"{name.title()} a pending content action.",
        )
        command.add_argument("pending_action_id")
        command.add_argument("--user-id", default="local")

    runtime = subparsers.add_parser(
        "runtime",
        parents=[common],
        help="inspect runtime health and events",
        description="Inspect runtime health, diagnostics, and replayable events.",
    )
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_commands.add_parser(
        "health", parents=[common], description="Show supervised runtime health."
    )
    runtime_commands.add_parser(
        "config-diagnostics", parents=[common], description="Show configuration diagnostics."
    )
    runtime_commands.add_parser(
        "model-diagnostics", parents=[common], description="Show model diagnostics."
    )
    events = runtime_commands.add_parser(
        "events", parents=[common], description="Replay bounded runtime events."
    )
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=100)

    models = subparsers.add_parser(
        "models",
        parents=[common],
        help="manage model settings",
        description="Inspect or update model settings.",
    )
    model_commands = models.add_subparsers(dest="model_command", required=True)
    model_commands.add_parser(
        "catalog", parents=[common], description="Show the supported model catalog."
    )
    model_commands.add_parser(
        "current", parents=[common], description="Show current model settings."
    )
    model_set = model_commands.add_parser(
        "set", parents=[common], description="Validate and persist model settings."
    )
    model_set.add_argument("request", help="JSON request file, or - for stdin")


def is_product_command(command: str | None) -> bool:
    return command in _PRODUCT_COMMANDS


def _read_request(location: str) -> bytes:
    if location == "-":
        payload = sys.stdin.buffer.read(_REQUEST_LIMIT + 1)
    else:
        with Path(location).open("rb") as source:
            payload = source.read(_REQUEST_LIMIT + 1)
    if len(payload) > _REQUEST_LIMIT:
        raise ValueError("request exceeds 1 MiB")
    return payload


def _request(model: type[_REQUEST_MODEL], location: str) -> _REQUEST_MODEL:
    return TypeAdapter(model).validate_json(_read_request(location))


def _fields(values: list[str], location: str | None = None) -> dict[str, str] | None:
    if location is not None:
        return _STRING_MAPPING.validate_json(_read_request(location)) or None
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise ValueError("--field must be KEY=VALUE")
        result[key] = item
    return result or None


def _host_dependencies(application: Application) -> HostDependencies:
    hosts = getattr(application, "hosts", None)
    dependencies = getattr(hosts, "dependencies", None)
    if dependencies is None:
        raise ValueError("host dependencies are unavailable")
    return cast("HostDependencies", dependencies)


async def _dispatch(
    facade: ProductFacade,
    args: argparse.Namespace,
    application: Application,
) -> object:
    if args.command == "sources":
        if args.source_command == "list":
            return await facade.list_sources(args.account_id, args.limit)
        if args.source_command == "status":
            return await facade.source_status(args.provider_id, args.account_id)
        if args.source_command == "form":
            return await facade.source_form(args.provider_id, args.method_id)
        if args.source_command == "capabilities":
            return facade.provider_capabilities(args.provider_id)
        if args.source_command == "access-recipe":
            return facade.access_recipe(args.provider_id)
        if args.source_command == "submit-material":
            body = _request(AccessMaterialRequest, args.request)
            return await facade.submit_access_material(
                SubmitAccessMaterialCommand(provider_id=args.provider_id, artifacts=body.artifacts)
            )
        if args.source_command == "sync":
            return await facade.sync_source(args.provider_id)
        if args.source_command == "add":
            command = ConnectSourceCommand(
                idempotency_key=args.idempotency_key,
                request=AccessRequest(
                    provider_id=args.provider_id,
                    account_id=args.account_id,
                    permissions=frozenset(Permission(item) for item in args.permission),
                    supported_method_ids=(args.method_id,),
                ),
                allowed_method_ids=frozenset({args.method_id}),
                submission=_fields(args.field, args.fields_file),
            )
            return await facade.connect_source(command)
        return await facade.disconnect_source(
            DisconnectSourceCommand(
                idempotency_key=args.idempotency_key,
                provider_id=args.provider_id,
                account_id=args.account_id,
            )
        )
    if args.command == "feed":
        return await facade.get_recommendations(args.limit)
    if args.command == "refresh":
        return await facade.refresh_recommendations(
            RefreshRequest(
                idempotency_key=args.idempotency_key, maximum_items=args.maximum_items
            ).to_command()
        )
    if args.command == "feedback":
        return await facade.record_feedback_for_shown(
            args.shown_id, _FEEDBACK_KIND[args.kind], args.idempotency_key, args.exposed
        )
    if args.command == "record-feedback":
        return await facade.record_feedback(_request(FeedbackRequest, args.request).to_command())
    if args.command == "observations":
        return await facade.record_observations(
            _request(ObservationsRequest, args.request).to_command()
        )
    if args.command == "profile":
        if args.profile_command == "show":
            return await facade.show_profile(args.profile_id)
        if args.profile_command == "edit":
            return await facade.edit_profile(
                _request(ProfileEditRequest, args.request).to_command()
            )
        disabled = args.state == "disable"
        return await facade.edit_profile(
            EditProfileCommand(
                idempotency_key=args.idempotency_key,
                profile_id=args.profile_id,
                account_id=args.account_id,
                claim_id=EXPLORATION_DISABLED_CLAIM_ID,
                operation=OverrideOperation.SET if disabled else OverrideOperation.REMOVE,
                value="true" if disabled else None,
            )
        )
    if args.command == "assistant":
        return await facade.assistant_turn(
            AssistantTurnRequest(
                conversation_id=args.conversation_id,
                text=args.message,
                locale=args.locale,
            ),
            args.device_id,
        )
    if args.command == "conversations":
        if args.conversation_command == "show":
            return await facade.conversation(args.conversation_id, args.device_id)
        return await facade.conversation_messages(args.conversation_id, args.device_id, args.limit)
    if args.command == "search":
        return await facade.search_content(args.provider_id, args.query, args.limit)
    if args.command == "content":
        reference = ContentRef.model_validate_json(args.reference).model_dump_json()
        return await facade.get_content_details(reference)
    if args.command == "actions":
        if args.action_command == "propose":
            return await facade.propose_action(
                _request(ProposeActionRequest, args.request).to_command()
            )
        decision = ConfirmActionRequest(
            pending_action_id=args.pending_action_id, user_id=args.user_id
        )
        if args.action_command == "confirm":
            return await facade.confirm_action(decision.to_command())
        return await facade.reject_action(decision.to_reject_command())
    if args.command == "runtime":
        if args.runtime_command == "health":
            return await facade.job_health()
        if args.runtime_command == "config-diagnostics":
            return await facade.config_diagnostics()
        if args.runtime_command == "model-diagnostics":
            return await facade.model_diagnostics()
        dependencies = _host_dependencies(application)
        if dependencies.events is None:
            raise ValueError("runtime events are unavailable")
        return await dependencies.events.replay(args.after, args.limit)
    if args.command == "models":
        dependencies = _host_dependencies(application)
        if args.model_command == "catalog":
            return model_catalog(dependencies)
        if args.model_command == "current":
            return current_model(dependencies)
        return update_model(_request(ModelConfigurationRequest, args.request), dependencies)
    if args.command == "import":
        return await facade.import_provider_evidence(args.provider_id, args.file)
    raise ValueError(f"unsupported product command: {args.command}")


def _json(value: object) -> str:
    def encode(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        raise TypeError(f"{type(item).__name__} is not JSON serializable")

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=encode)


async def run_product_command(application: Application, args: argparse.Namespace) -> int:
    """Start the graph, call exactly one workflow, print exactly one JSON document."""

    await application.start()
    try:
        facade = application.services.facade
        if facade is None:
            raise RuntimeError("composition did not construct application facade")
        print(_json(await _dispatch(cast("ProductFacade", facade), args, application)))
        return 0
    except ApplicationError as error:
        print(
            _json({"error": {"code": error.code.value, "message": error.safe_message}}),
            file=sys.stderr,
        )
        return 1
    except HTTPException as error:
        print(
            _json({"error": {"code": "unavailable", "message": str(error.detail)}}),
            file=sys.stderr,
        )
        return 1
    except ValidationError:
        print(
            _json({"error": {"code": "validation", "message": "request validation failed"}}),
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as error:
        print(
            _json({"error": {"code": "validation", "message": str(error)}}),
            file=sys.stderr,
        )
        return 1
    finally:
        await application.stop()
