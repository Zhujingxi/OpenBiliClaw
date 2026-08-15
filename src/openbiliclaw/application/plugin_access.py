"""Recipe-driven browser credential submission through canonical Provider Access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import ConfigDict, Field, SecretStr, model_validator

from openbiliclaw.access.models import AccessRequest, AccessStatus, Permission
from openbiliclaw.content.integration.errors import ContentIntegrationError
from openbiliclaw.content.integration.identity import ProviderId
from openbiliclaw.content.integration.manifest import (
    AccessArtifactKind,
    AccessRecipe,
    ProviderManifest,
)
from openbiliclaw.core._pydantic import StrictBaseModel

from .errors import ApplicationError, ApplicationErrorCode


class SubmittedAccessArtifact(StrictBaseModel):
    """One short-lived artifact value received from the generic extension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AccessArtifactKind
    domain: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=256)
    value: SecretStr = Field(min_length=1, max_length=65_536, repr=False, exclude=True)


class SubmitAccessMaterialCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1, max_length=64)
    artifacts: tuple[SubmittedAccessArtifact, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _unique_artifacts(self) -> SubmitAccessMaterialCommand:
        keys = tuple((item.kind, item.domain, item.name) for item in self.artifacts)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate submitted access artifact")
        return self


class ManifestRegistry(Protocol):
    def manifest(self, provider_id: ProviderId) -> ProviderManifest: ...


class RecipeAccess(Protocol):
    async def connect(
        self,
        request: AccessRequest,
        *,
        allowed_method_ids: frozenset[str],
        submission: dict[str, str] | None,
    ) -> AccessStatus: ...

    async def replace(self, request: AccessRequest, submission: dict[str, str]) -> AccessStatus: ...

    def connected_handle(self, provider_id: str, account_id: str | None) -> object | None: ...

    def method_permissions(self, provider_id: str, method_id: str) -> frozenset[Permission]: ...


@dataclass(frozen=True, slots=True)
class PluginAssistedAccess:
    registry: ManifestRegistry
    access: RecipeAccess

    def recipe(self, provider_id: str) -> AccessRecipe:
        try:
            recipe = self.registry.manifest(ProviderId(value=provider_id)).access_recipe
        except (ContentIntegrationError, KeyError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "access recipe not found"
            ) from exc
        if recipe is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "access recipe not found")
        return recipe

    async def submit(self, command: SubmitAccessMaterialCommand) -> AccessStatus:
        recipe = self.recipe(command.provider_id)
        submission = self._submission(recipe, command.artifacts)
        permissions = self.access.method_permissions(command.provider_id, recipe.target_method_id)
        request = AccessRequest(
            provider_id=command.provider_id,
            account_id=None,
            permissions=permissions,
            supported_method_ids=(recipe.target_method_id,),
        )
        if self.access.connected_handle(command.provider_id, None) is not None:
            return await self.access.replace(request, submission)
        return await self.access.connect(
            request,
            allowed_method_ids=frozenset({recipe.target_method_id}),
            submission=submission,
        )

    @staticmethod
    def _submission(
        recipe: AccessRecipe, artifacts: tuple[SubmittedAccessArtifact, ...]
    ) -> dict[str, str]:
        expected = tuple((item.kind, item.domain, item.name) for item in recipe.artifacts)
        supplied = {
            (item.kind, item.domain, item.name): item.value.get_secret_value() for item in artifacts
        }
        if set(supplied) != set(expected):
            raise ApplicationError(
                ApplicationErrorCode.VALIDATION, "access material does not match recipe"
            )
        if any(
            kind is AccessArtifactKind.COOKIE
            and (
                ";" in supplied[(kind, domain, name)]
                or any(
                    ord(char) < 0x20 or ord(char) == 0x7F for char in supplied[(kind, domain, name)]
                )
            )
            for kind, domain, name in expected
        ):
            raise ApplicationError(
                ApplicationErrorCode.VALIDATION, "cookie material has an invalid shape"
            )
        cookie_parts = [
            f"{name}={supplied[(kind, domain, name)]}"
            for kind, domain, name in expected
            if kind is AccessArtifactKind.COOKIE
        ]
        submission = {
            name: supplied[(kind, domain, name)]
            for kind, domain, name in expected
            if kind is not AccessArtifactKind.COOKIE
        }
        if cookie_parts:
            submission["cookie"] = "; ".join(cookie_parts)
        return submission
