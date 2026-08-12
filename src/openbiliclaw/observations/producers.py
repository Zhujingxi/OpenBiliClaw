"""Future observation producer contract."""

from typing import Protocol

from openbiliclaw.core.extensions import ObservationProviderRegistration

from .models import Observation


class ObservationProvider(Protocol):
    """Typed producer registered through Core's closed extension category."""

    @property
    def registration(self) -> ObservationProviderRegistration: ...

    @property
    def allowed_event_types(self) -> frozenset[str]: ...

    async def observations(self) -> tuple[Observation, ...]: ...
