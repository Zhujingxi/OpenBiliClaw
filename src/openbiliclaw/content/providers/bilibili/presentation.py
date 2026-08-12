"""Trusted Bilibili presentation descriptor consumed by later hosts."""

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class BilibiliPresentationDescriptor(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_label: str = Field(max_length=100)
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    supported_kinds: tuple[str, ...]


BILIBILI_PRESENTATION = BilibiliPresentationDescriptor(
    provider_label="Bilibili",
    accent_color="#00AEEC",
    supported_kinds=("video", "article"),
)
