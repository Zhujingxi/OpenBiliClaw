"""Strict, secret-safe schemas for the dedicated model configuration API."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """API base model that rejects unknown fields and implicit coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class CredentialActionIn(StrictModel):
    """Write-only credential transition; ``value`` is never represented in output."""

    action: Literal["keep", "set", "clear", "env"]
    value: str = Field(default="", repr=False, json_schema_extra={"writeOnly": True})


class PublicCredentialOut(StrictModel):
    """Credential metadata that cannot carry a secret value."""

    source: Literal["none", "inline", "env", "oauth"]
    configured: bool
    env_name: str = ""
    credential_ref: str = ""
    oauth_logged_in: bool = False


class ProbeSummaryOut(StrictModel):
    ok: bool
    error_code: str = ""
    message: str = ""
    observed_dimension: int = 0
    probed_at: str
    revision: str


class CircuitSummaryOut(StrictModel):
    state: Literal["closed", "open"] = "closed"
    failure_kind: str = ""
    retry_after_seconds: float | None = None
    permanent: bool = False


class ChatConnectionIn(StrictModel):
    id: str
    name: str
    type: str
    model: str
    preset: str = ""
    base_url: str = ""
    credential: CredentialActionIn
    api_mode: str = ""
    reasoning_effort: str = ""
    http_referer: str = ""
    x_title: str = ""
    num_ctx: int = Field(default=0, ge=0)


class ChatConnectionOut(StrictModel):
    id: str
    name: str
    type: str
    model: str
    preset: str = ""
    base_url: str = ""
    credential: PublicCredentialOut
    api_mode: str = ""
    reasoning_effort: str = ""
    http_referer: str = ""
    x_title: str = ""
    num_ctx: int = 0
    probe: ProbeSummaryOut | None = None
    circuit: CircuitSummaryOut = Field(default_factory=CircuitSummaryOut)


class ChatRouteIn(StrictModel):
    connections: list[ChatConnectionIn] = Field(min_length=1, max_length=10)
    concurrency: int = Field(ge=1, le=16)
    timeout_seconds: int = Field(ge=10)


class ChatRouteOut(StrictModel):
    connections: list[ChatConnectionOut]
    concurrency: int
    timeout_seconds: int


class EmbeddingSettingsIn(StrictModel):
    model: str
    output_dimensionality: int = Field(ge=0)
    similarity_threshold: float = Field(ge=0.0, le=1.0)
    multimodal_enabled: bool = False


class EmbeddingSettingsOut(StrictModel):
    model: str
    output_dimensionality: int
    similarity_threshold: float
    multimodal_enabled: bool


class EmbeddingProviderIn(StrictModel):
    id: str
    name: str
    type: str
    preset: str = ""
    base_url: str = ""
    credential: CredentialActionIn


class EmbeddingProviderOut(StrictModel):
    id: str
    name: str
    type: str
    preset: str = ""
    base_url: str = ""
    credential: PublicCredentialOut
    probe: ProbeSummaryOut | None = None
    circuit: CircuitSummaryOut = Field(default_factory=CircuitSummaryOut)


class EmbeddingRouteIn(StrictModel):
    enabled: bool
    settings: EmbeddingSettingsIn
    providers: list[EmbeddingProviderIn] = Field(max_length=10)


class EmbeddingRouteOut(StrictModel):
    enabled: bool
    settings: EmbeddingSettingsOut
    providers: list[EmbeddingProviderOut]


class ModelConfigIn(StrictModel):
    schema_version: Literal[1]
    chat: ChatRouteIn
    embedding: EmbeddingRouteIn


class ModelConfigOut(StrictModel):
    schema_version: int
    chat: ChatRouteOut
    embedding: EmbeddingRouteOut


class MigrationIssueOut(StrictModel):
    id: str
    code: str
    field: str
    provider: str = ""
    credential_configured: bool = False
    reason: str = ""
    severity: Literal["warning", "blocking"] = "blocking"
    allowed_actions: list[str] = Field(default_factory=list)


class MigrationSummaryOut(StrictModel):
    state: str = "none"
    confirmed: bool = True
    issues: list[MigrationIssueOut] = Field(default_factory=list)


class ModelConfigOverrideOut(StrictModel):
    path: str
    source: str


class ModelConfigSnapshotOut(StrictModel):
    revision: str
    source: str
    models: ModelConfigOut
    migration: MigrationSummaryOut
    overrides: list[ModelConfigOverrideOut] = Field(default_factory=list)


class MigrationResolutionIn(StrictModel):
    action: Literal[
        "add_to_chat_route",
        "confirm_remove_after_backup",
        "cancel",
        "accept_global_route",
        "apply_shared_embedding_settings",
        "remove_embedding_fallback",
    ]
    position: int | None = None
    embedding_settings: EmbeddingSettingsIn | None = None


class ModelConfigPutIn(StrictModel):
    revision: str
    models: ModelConfigIn
    migration_resolutions: dict[str, MigrationResolutionIn] = Field(default_factory=dict)


class ModelConfigPutResponse(StrictModel):
    ok: bool
    revision: str
    reloaded: bool
    rollback_applied: bool = False
    snapshot: ModelConfigSnapshotOut


class ModelConfigFieldErrorOut(StrictModel):
    path: str
    code: str
    message: str
    source: str = ""
    connection_id: str | None = None


class ModelConfigProbeIn(StrictModel):
    kind: Literal["chat", "embedding"]
    revision: str
    connection: ChatConnectionIn | None = None
    provider: EmbeddingProviderIn | None = None
    settings: EmbeddingSettingsIn | None = None

    @model_validator(mode="after")
    def require_exact_draft_shape(self) -> Self:
        if self.kind == "chat":
            if self.connection is None or self.provider is not None or self.settings is not None:
                raise ValueError("chat probes require exactly one connection draft")
        elif self.provider is None or self.settings is None or self.connection is not None:
            raise ValueError("embedding probes require exactly one provider and shared settings")
        return self


class ModelConfigProbeResponse(StrictModel):
    ok: bool
    connection_id: str
    capability: Literal["chat", "embedding"]
    observed_dimension: int = 0
    error_code: str = ""
    message: str = ""
    probed_at: str
    revision: str


class DescriptorFieldOut(StrictModel):
    name: str
    label: str
    input_type: Literal["text", "secret", "number", "select"]
    required: bool
    capabilities: list[Literal["chat", "embedding"]]
    presets: list[str]
    help: str
    placeholder: str
    choices: list[str]


class PresetDescriptorOut(StrictModel):
    id: str
    label: str
    capabilities: list[Literal["chat", "embedding"]]
    defaults: dict[str, str | int | float | bool]
    help: str


class ConnectionTypeDescriptorOut(StrictModel):
    id: str
    label: str
    category: Literal["api_protocol", "local_runtime", "oauth"]
    capabilities: list[Literal["chat", "embedding"]]
    fields: list[DescriptorFieldOut]
    presets: list[str]
    preset_definitions: list[PresetDescriptorOut]
    help: str


class ConnectionTypeGroupOut(StrictModel):
    category: Literal["api_protocol", "local_runtime", "oauth"]
    connection_types: list[ConnectionTypeDescriptorOut]


class ConnectionTypesResponse(StrictModel):
    capability: Literal["chat", "embedding"] | None = None
    connection_types: list[ConnectionTypeDescriptorOut]
    groups: list[ConnectionTypeGroupOut]
