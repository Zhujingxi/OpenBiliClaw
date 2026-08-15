"""JSON-only product CLI adapter over the in-process application facade."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.edit_profile import EditProfileCommand
from openbiliclaw.application.errors import ApplicationError
from openbiliclaw.application.sources import ConnectSourceCommand, DisconnectSourceCommand
from openbiliclaw.hosts.api.schemas.models import AssistantTurnRequest
from openbiliclaw.recommendation.models import FeedbackKind, record_identity
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.profile import EXPLORATION_DISABLED_CLAIM_ID

if TYPE_CHECKING:
    import argparse

    from openbiliclaw.composition.application import Application


class ProductFacade(Protocol):
    async def list_sources(self, account_id: str | None, limit: int) -> object: ...
    async def source_status(self, provider_id: str, account_id: str | None) -> object: ...
    async def connect_source(self, command: ConnectSourceCommand) -> object: ...
    async def disconnect_source(self, command: DisconnectSourceCommand) -> object: ...
    async def get_recommendations(self, limit: int) -> object: ...
    async def record_feedback_for_shown(
        self, shown_id: str, kind: FeedbackKind, idempotency_key: str, exposed: bool = False
    ) -> object: ...
    async def show_profile(self, profile_id: str) -> object: ...
    async def edit_profile(self, command: EditProfileCommand) -> object: ...
    async def assistant_turn(self, request: AssistantTurnRequest, device_id: str) -> object: ...
    async def search_content(self, provider_id: str, text: str, limit: int) -> object: ...


_PRODUCT_COMMANDS = frozenset({"sources", "feed", "feedback", "profile", "assistant", "search"})
_FEEDBACK_KIND = {
    "like": FeedbackKind.LIKED,
    "dismiss": FeedbackKind.DISMISSED,
    "save": FeedbackKind.SAVED,
    "open": FeedbackKind.OPENED,
}


def add_product_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    common: argparse.ArgumentParser,
) -> None:
    """Register the product workflow command surface."""

    sources = subparsers.add_parser("sources", parents=[common])
    source_commands = sources.add_subparsers(dest="source_command", required=True)
    listing = source_commands.add_parser("list", parents=[common])
    listing.add_argument("--account-id")
    listing.add_argument("--limit", type=int, default=50)
    status = source_commands.add_parser("status", parents=[common])
    status.add_argument("provider_id")
    status.add_argument("--account-id")
    add = source_commands.add_parser("add", parents=[common])
    add.add_argument("provider_id")
    add.add_argument("method_id")
    add.add_argument("--account-id")
    add.add_argument(
        "--permission", action="append", required=True, choices=[x.value for x in Permission]
    )
    add.add_argument("--field", action="append", default=[], metavar="KEY=VALUE")
    add.add_argument("--idempotency-key", required=True)
    remove = source_commands.add_parser("remove", parents=[common])
    remove.add_argument("provider_id")
    remove.add_argument("--account-id")
    remove.add_argument("--idempotency-key", required=True)

    feed = subparsers.add_parser("feed", parents=[common])
    feed.add_argument("--limit", type=int, default=20)

    feedback = subparsers.add_parser("feedback", parents=[common])
    feedback.add_argument("shown_id")
    feedback.add_argument("kind", choices=tuple(_FEEDBACK_KIND))
    feedback.add_argument("--idempotency-key", required=True)
    feedback.add_argument("--exposed", action="store_true")

    profile = subparsers.add_parser("profile", parents=[common])
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    show = profile_commands.add_parser("show", parents=[common])
    show.add_argument("--profile-id", default="default")
    exploration = profile_commands.add_parser("exploration", parents=[common])
    exploration.add_argument("state", choices=("disable", "enable"))
    exploration.add_argument("--profile-id", default="default")
    exploration.add_argument("--account-id", default="local")
    exploration.add_argument("--idempotency-key", required=True)

    assistant = subparsers.add_parser("assistant", parents=[common])
    assistant.add_argument("message")
    assistant.add_argument("--conversation-id", default=record_identity("conv", "cli"))
    assistant.add_argument("--device-id", default="cli")
    assistant.add_argument("--locale", default="en-US")

    search = subparsers.add_parser("search", parents=[common])
    search.add_argument("provider_id")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)


def is_product_command(command: str | None) -> bool:
    return command in _PRODUCT_COMMANDS


def _fields(values: list[str]) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise ValueError("--field must be KEY=VALUE")
        result[key] = item
    return result or None


async def _dispatch(facade: ProductFacade, args: argparse.Namespace) -> object:
    if args.command == "sources":
        if args.source_command == "list":
            return await facade.list_sources(args.account_id, args.limit)
        if args.source_command == "status":
            return await facade.source_status(args.provider_id, args.account_id)
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
                submission=_fields(args.field),
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
    if args.command == "feedback":
        return await facade.record_feedback_for_shown(
            args.shown_id, _FEEDBACK_KIND[args.kind], args.idempotency_key, args.exposed
        )
    if args.command == "profile":
        if args.profile_command == "show":
            return await facade.show_profile(args.profile_id)
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
    return await facade.search_content(args.provider_id, args.query, args.limit)


def _json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def run_product_command(application: Application, args: argparse.Namespace) -> int:
    """Start the graph, call exactly one workflow, print exactly one JSON document."""

    await application.start()
    try:
        facade = application.services.facade
        if facade is None:
            raise RuntimeError("composition did not construct application facade")
        print(_json(await _dispatch(cast("ProductFacade", facade), args)))
        return 0
    except ApplicationError as error:
        print(
            _json({"error": {"code": error.code.value, "message": error.safe_message}}),
            file=sys.stderr,
        )
        return 1
    except ValueError as error:  # pydantic ValidationError is a ValueError subclass
        print(
            _json({"error": {"code": "validation", "message": str(error)}}),
            file=sys.stderr,
        )
        return 1
    finally:
        await application.stop()
