from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.content.integration.identity import ProviderId

from openbiliclaw.access.broker import AccessBroker
from openbiliclaw.access.manual import ManualAccessMethod, ManualProviderSpec
from openbiliclaw.access.methods import AccessMethodRegistry
from openbiliclaw.access.models import (
    AccessStatusKind,
    CredentialAccessHandle,
    Permission,
    VerificationResult,
    VerificationStrength,
)
from openbiliclaw.access.service import AccessService
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.application.plugin_access import (
    PluginAssistedAccess,
    SubmitAccessMaterialCommand,
    SubmittedAccessArtifact,
)
from openbiliclaw.content.integration.manifest import AccessArtifactKind, ProviderManifest
from openbiliclaw.content.providers.bilibili.auth import BILIBILI_CONNECTION_FORM
from openbiliclaw.content.providers.bilibili.manifest import BILIBILI_MANIFEST
from openbiliclaw.content.providers.v2ex.manifest import V2EX_MANIFEST
from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend
from openbiliclaw.infrastructure.credentials.vault import CredentialVault

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class Verifier:
    def __init__(self) -> None:
        self.seen: list[dict[str, str]] = []

    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult:
        self.seen.append(json.loads(credential.tobytes()))
        return VerificationResult(
            strength=VerificationStrength.LIVE,
            verified_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            safe_account_identity="tester",
            granted_permissions=handle.permissions,
        )


def service(path: Path, verifier: Verifier) -> tuple[AccessService, CredentialVault]:
    vault = CredentialVault(ProtectedFileBackend(path))
    manual = ManualAccessMethod(
        vault,
        (
            ManualProviderSpec(
                form=BILIBILI_CONNECTION_FORM,
                capabilities=frozenset(
                    {Permission.READ_PUBLIC, Permission.READ_PRIVATE, Permission.WRITE}
                ),
                verifier=verifier,
            ),
        ),
    )
    registry = AccessMethodRegistry((manual,))
    return AccessService(AccessBroker(registry), registry, clock=lambda: NOW), vault


class Manifests:
    def manifest(self, provider_id: ProviderId) -> ProviderManifest:
        if provider_id.value == "bilibili":
            return BILIBILI_MANIFEST
        if provider_id.value == "v2ex":
            return V2EX_MANIFEST
        raise KeyError("provider")


def workflow(path: Path, verifier: Verifier) -> tuple[PluginAssistedAccess, AccessService]:
    access, _vault = service(path, verifier)
    return PluginAssistedAccess(Manifests(), access), access


def material(
    *, session: str = "session-value", csrf: str = "csrf-value"
) -> SubmitAccessMaterialCommand:
    return SubmitAccessMaterialCommand(
        provider_id="bilibili",
        artifacts=(
            SubmittedAccessArtifact(
                kind=AccessArtifactKind.COOKIE,
                domain="bilibili.com",
                name="SESSDATA",
                value=session,
            ),
            SubmittedAccessArtifact(
                kind=AccessArtifactKind.COOKIE,
                domain="bilibili.com",
                name="bili_jct",
                value=csrf,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_recipe_material_converges_on_manual_vault_and_verifier(tmp_path: Path) -> None:
    verifier = Verifier()
    plugin, access = workflow(tmp_path / "credentials.json", verifier)

    assert plugin.recipe("bilibili") == BILIBILI_MANIFEST.access_recipe
    status = await plugin.submit(material())

    assert status.state is AccessStatusKind.CONNECTED
    assert status.method_id == "builtin.manual"
    assert verifier.seen == [{"cookie": "SESSDATA=session-value; bili_jct=csrf-value"}]
    assert access.connected_handle("bilibili", None) is not None


@pytest.mark.asyncio
async def test_second_submission_replaces_connected_material(tmp_path: Path) -> None:
    verifier = Verifier()
    plugin, access = workflow(tmp_path / "credentials.json", verifier)

    await plugin.submit(material(session="first"))
    status = await plugin.submit(material(session="second"))

    handle = access.connected_handle("bilibili", None)
    assert status.state is AccessStatusKind.CONNECTED
    assert isinstance(handle, CredentialAccessHandle)
    assert handle.revision == 2
    assert verifier.seen == [
        {"cookie": "SESSDATA=first; bili_jct=csrf-value"},
        {"cookie": "SESSDATA=second; bili_jct=csrf-value"},
    ]


def test_provider_without_recipe_is_typed_not_found(tmp_path: Path) -> None:
    plugin, _access = workflow(tmp_path / "credentials.json", Verifier())

    with pytest.raises(ApplicationError) as caught:
        plugin.recipe("v2ex")

    assert caught.value.code is ApplicationErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_malformed_material_is_rejected_before_vault_write(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    verifier = Verifier()
    plugin, _access = workflow(path, verifier)
    incomplete = SubmitAccessMaterialCommand(
        provider_id="bilibili", artifacts=material().artifacts[:1]
    )

    with pytest.raises(ApplicationError, match="does not match recipe"):
        await plugin.submit(incomplete)
    injected = material(session="session; injected=cookie")
    with pytest.raises(ApplicationError, match="invalid shape"):
        await plugin.submit(injected)

    assert not path.exists()
    assert verifier.seen == []


@pytest.mark.asyncio
async def test_fresh_service_rehydrates_and_verifies_durable_connection(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    first_verifier = Verifier()
    plugin, _first = workflow(path, first_verifier)
    await plugin.submit(material())

    restarted_verifier = Verifier()
    restarted, _vault = service(path, restarted_verifier)
    await restarted.rehydrate()
    await restarted.rehydrate()

    status = await restarted.status("bilibili", None)
    assert status.state is AccessStatusKind.CONNECTED
    assert restarted_verifier.seen == [{"cookie": "SESSDATA=session-value; bili_jct=csrf-value"}]
