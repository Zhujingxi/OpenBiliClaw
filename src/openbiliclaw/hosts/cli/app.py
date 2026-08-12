"""Typer adapters over the same typed HostFacade used by HTTP."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

import typer
from pydantic import ValidationError

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.edit_profile import EditProfileCommand
from openbiliclaw.application.errors import ApplicationError
from openbiliclaw.application.refresh_recommendations import RefreshRecommendationsCommand
from openbiliclaw.application.sources import ConnectSourceCommand
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.hosts.api.schemas.models import ConnectSourceRequest
from openbiliclaw.understanding.overrides import OverrideOperation

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from rich.console import Console

    from openbiliclaw.hosts.api.dependencies import HostFacade

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CliRuntime:
    facade: HostFacade
    console: Console


def _run(operation: Coroutine[object, object, T]) -> T:
    try:
        return asyncio.run(operation)
    except (ApplicationError, ValidationError) as exc:
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        raise typer.Exit(code=1) from exc


def _render(runtime: CliRuntime, value: StrictBaseModel) -> None:
    runtime.console.print(value.model_dump_json())


def _call(runtime: CliRuntime, operation: Coroutine[object, object, StrictBaseModel]) -> None:
    _render(runtime, _run(operation))


def create_cli(runtime: CliRuntime) -> typer.Typer:
    app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

    @app.command()
    def start() -> None:
        _call(runtime, runtime.facade.start())

    @app.command()
    def status() -> None:
        _call(runtime, runtime.facade.job_health())

    @app.command("config-diagnostics")
    def config_diagnostics() -> None:
        _call(runtime, runtime.facade.config_diagnostics())

    @app.command("model-diagnostics")
    def model_diagnostics() -> None:
        _call(runtime, runtime.facade.model_diagnostics())

    @app.command("connect")
    def connect(
        provider_id: str,
        method_id: str,
        idempotency_key: str,
        credential: str | None = typer.Option(default=None, hidden=True),
    ) -> None:
        try:
            body = ConnectSourceRequest(
                provider_id=provider_id,
                method_id=method_id,
                idempotency_key=idempotency_key,
                credential=credential,
            )
            secret = body.credential.get_secret_value() if body.credential else None
            command = ConnectSourceCommand(
                idempotency_key=body.idempotency_key,
                request=AccessRequest(
                    provider_id=body.provider_id,
                    account_id=None,
                    permissions=frozenset({Permission.READ_PUBLIC}),
                    supported_method_ids=(body.method_id,),
                ),
                allowed_method_ids=frozenset({body.method_id}),
                submission={"credential": secret} if secret else None,
            )
        except ValidationError as exc:
            raise typer.Exit(code=2) from exc
        _call(runtime, runtime.facade.connect_source(command))

    @app.command("profile")
    def profile(profile_id: str = "default") -> None:
        _call(runtime, runtime.facade.show_profile(profile_id))

    @app.command("profile-edit")
    def profile_edit(
        claim_id: str,
        operation: OverrideOperation,
        idempotency_key: str,
        account_id: str,
        profile_id: str = "default",
        value: str | None = None,
    ) -> None:
        try:
            command = EditProfileCommand(
                idempotency_key=idempotency_key,
                profile_id=profile_id,
                account_id=account_id,
                claim_id=claim_id,
                operation=operation,
                value=value,
            )
        except ValidationError as exc:
            raise typer.Exit(code=2) from exc
        _call(runtime, runtime.facade.edit_profile(command))

    @app.command("recommend")
    def recommend(limit: int = typer.Option(default=20, min=1, max=100)) -> None:
        _call(runtime, runtime.facade.get_recommendations(limit))

    @app.command("recommend-refresh")
    def recommend_refresh(idempotency_key: str, maximum_items: int = 50) -> None:
        try:
            command = RefreshRecommendationsCommand(
                idempotency_key=idempotency_key, maximum_items=maximum_items
            )
        except ValidationError as exc:
            raise typer.Exit(code=2) from exc
        _call(runtime, runtime.facade.refresh_recommendations(command))

    @app.command("search")
    def search(
        provider_id: str,
        text: str,
        limit: int = typer.Option(default=20, min=1, max=50),
    ) -> None:
        _call(runtime, runtime.facade.search_content(provider_id, text, limit))

    @app.command("content")
    def content(provider_id: str, kind: str, content_id: str, url: str) -> None:
        try:
            ref = ContentRef(
                provider_id=ProviderId(value=provider_id),
                content_kind=ContentKind(value=kind),
                provider_content_id=content_id,
                canonical_url=url,
            )
        except ValidationError as exc:
            raise typer.Exit(code=2) from exc
        _call(runtime, runtime.facade.get_content_details(ref.model_dump_json()))

    return app
