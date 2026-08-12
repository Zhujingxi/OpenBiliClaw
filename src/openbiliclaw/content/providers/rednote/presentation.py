"""Trusted RedNote presentation descriptor."""

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class RednotePresentationDescriptor(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_label: str = Field(max_length=100)
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    supported_kinds: tuple[str, ...]


REDNOTE_PRESENTATION = RednotePresentationDescriptor(
    provider_label="RedNote", accent_color="#FF2442", supported_kinds=("note",)
)
