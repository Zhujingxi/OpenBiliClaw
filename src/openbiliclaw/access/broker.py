"""Deterministic access-method selection and admission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .methods import AccessMethod, AccessMethodRegistry
    from .models import AccessHandle, AccessRequest


class AccessUnavailableError(RuntimeError):
    """Sanitized access-admission failure with a stable reason code."""

    def __init__(self, reason: str = "no_allowed_method") -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OpenedAccess:
    method: AccessMethod
    handle: AccessHandle


class AccessBroker:
    """Select the first caller-supported method allowed by user configuration."""

    def __init__(self, registry: AccessMethodRegistry) -> None:
        self._registry = registry

    def select(self, request: AccessRequest, *, allowed_method_ids: frozenset[str]) -> AccessMethod:
        for method_id in request.supported_method_ids:
            if method_id not in allowed_method_ids:
                continue
            method = self._registry.get(method_id)
            if method is None:
                continue
            descriptor = method.descriptor
            if request.provider_id not in descriptor.supported_provider_ids:
                continue
            if not request.permissions <= descriptor.capabilities:
                continue
            return method
        raise AccessUnavailableError()

    async def open(
        self,
        request: AccessRequest,
        *,
        allowed_method_ids: frozenset[str],
        submission: Mapping[str, str] | None,
    ) -> OpenedAccess:
        method = self.select(request, allowed_method_ids=allowed_method_ids)
        handle = await method.open(request, submission)
        if (
            handle.provider_id != request.provider_id
            or handle.account_id != request.account_id
            or handle.permissions != request.permissions
        ):
            await method.close(handle)
            raise AccessUnavailableError("method_returned_invalid_scope")
        return OpenedAccess(method=method, handle=handle)
