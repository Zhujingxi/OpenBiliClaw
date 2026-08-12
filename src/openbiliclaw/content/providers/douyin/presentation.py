"""Trusted Douyin presentation descriptor."""

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class DouyinPresentationDescriptor(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_label: str = Field(max_length=100)
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    supported_kinds: tuple[str, ...]


DOUYIN_PRESENTATION = DouyinPresentationDescriptor(
    provider_label="Douyin", accent_color="#FE2C55", supported_kinds=("short_video",)
)
