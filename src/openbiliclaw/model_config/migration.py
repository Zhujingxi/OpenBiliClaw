"""Read-only conversion of legacy ``[llm]`` tables into model routes.

The adapter deliberately consumes raw mappings.  Legacy dataclasses discard
unknown fields, invalid enum values, and unused provider blocks before a
migration report can explain them.  This module never writes configuration;
authoritative persistence and backups belong to the model-config service.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal, TypeAlias
from urllib.parse import urlparse

from .types import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    IssueSeverity,
    ModelConfig,
)

MigrationAction: TypeAlias = Literal[
    "add_to_chat_route",
    "confirm_remove_after_backup",
    "cancel",
    "accept_global_route",
    "apply_shared_embedding_settings",
    "remove_embedding_fallback",
]

_CONFIRM_REMOVE_ACTIONS: tuple[MigrationAction, ...] = (
    "confirm_remove_after_backup",
    "cancel",
)
_UNROUTED_ACTIONS: tuple[MigrationAction, ...] = (
    "add_to_chat_route",
    "confirm_remove_after_backup",
    "cancel",
)
_MODULE_OVERRIDE_ACTIONS: tuple[MigrationAction, ...] = (
    "accept_global_route",
    "cancel",
)
_EMBEDDING_MISMATCH_ACTIONS: tuple[MigrationAction, ...] = (
    "apply_shared_embedding_settings",
    "remove_embedding_fallback",
    "cancel",
)

_CHAT_PROVIDER_ORDER = (
    "openai",
    "claude",
    "gemini",
    "deepseek",
    "ollama",
    "openrouter",
    "openai_compatible",
)
_EMBEDDING_PROVIDERS = frozenset(
    {"openai", "gemini", "ollama", "openai_compatible", "openrouter", "dashscope"}
)
_MODULE_NAMES = ("soul", "discovery", "recommendation", "evaluation")
_CHAT_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "claude": "claude-sonnet-4-20250514",
    "gemini": "gemini-2.5-flash",
    "deepseek": "deepseek-v4-flash",
    "ollama": "llama3",
    "openrouter": "openai/gpt-4o-mini",
    "openai_compatible": "gpt-4o-mini",
}
_EMBEDDING_DEFAULT_MODELS = {
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-001",
    "ollama": "bge-m3",
    "openai_compatible": "text-embedding-3-small",
    "openrouter": "",
    "dashscope": "qwen3-vl-embedding",
}
_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "claude": "Anthropic",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "ollama": "Ollama",
    "openrouter": "OpenRouter",
    "openai_compatible": "OpenAI-compatible",
    "dashscope": "DashScope",
}
_PROVIDER_FIELDS = {
    "openai": frozenset({"api_key", "model", "base_url", "auth_mode", "api_flavor"}),
    "claude": frozenset({"api_key", "model", "base_url"}),
    "gemini": frozenset({"api_key", "model", "base_url"}),
    "deepseek": frozenset({"api_key", "model", "base_url", "reasoning_effort"}),
    "ollama": frozenset({"api_key", "model", "base_url", "num_ctx"}),
    "openrouter": frozenset({"api_key", "model", "base_url", "http_referer", "x_title"}),
    "openai_compatible": frozenset({"api_key", "model", "base_url", "api_flavor"}),
}
_EMBEDDING_FIELDS = frozenset(
    {
        "provider",
        "model",
        "api_key",
        "base_url",
        "output_dimensionality",
        "similarity_threshold",
        "fallback_enabled",
        "fallback_provider",
        "multimodal_enabled",
    }
)
_KNOWN_LLM_FIELDS = frozenset(
    {
        "default_provider",
        "concurrency",
        "timeout",
        "fallback_provider",
        "embedding",
        *_CHAT_PROVIDER_ORDER,
        *_MODULE_NAMES,
    }
)
_GEMINI_ENV_KEYS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
_DASHSCOPE_ENV_KEYS = ("DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY_CN")
_SAFE_IDENTIFIER_LIMIT = 80


@dataclass(frozen=True)
class MigrationIssue:
    """One public, secret-free legacy migration decision or notice."""

    id: str
    code: str
    field: str
    provider: str = ""
    credential_configured: bool = False
    reason: str = ""
    severity: IssueSeverity = "blocking"
    allowed_actions: tuple[MigrationAction, ...] = ()

    def __post_init__(self) -> None:
        """Freeze action order supplied by permissive callers."""
        object.__setattr__(self, "allowed_actions", tuple(self.allowed_actions))


@dataclass(frozen=True)
class MigrationReport:
    """Deterministic public report produced from one legacy table."""

    issues: tuple[MigrationIssue, ...] = ()

    def __post_init__(self) -> None:
        """Keep issue order immutable for stable revisions and API output."""
        object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def issue_codes(self) -> set[str]:
        """Return a fresh set of issue codes for callers and tests."""
        return {issue.code for issue in self.issues}

    @property
    def has_pending_decisions(self) -> bool:
        """Whether explicit blocking choices are still required."""
        return any(issue.severity == "blocking" for issue in self.issues)


@dataclass(frozen=True)
class MigrationResolution:
    """JSON-translatable choice for one migration issue."""

    action: MigrationAction
    position: int | None = None
    embedding_settings: EmbeddingModelSettings | None = None


@dataclass(frozen=True)
class _PendingValue:
    issue_id: str
    chat_connection: ChatConnection | None = field(default=None, repr=False)
    embedding_provider: EmbeddingProviderConfig | None = field(default=None, repr=False)


@dataclass(frozen=True)
class LegacyMigrationResult:
    """In-memory candidate, safe report, and private resolution payloads."""

    models: ModelConfig
    report: MigrationReport
    _pending: tuple[_PendingValue, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        """Prevent callers from mutating pending resolution order."""
        object.__setattr__(self, "_pending", tuple(self._pending))


class MigrationResolutionError(ValueError):
    """Raised when migration choices are missing, unknown, or malformed."""


def slugify_id(value: str) -> str:
    """Return a stable lowercase identifier containing only ``a-z0-9-``."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "legacy-item"


def unique_id(base: str, used: set[str]) -> str:
    """Reserve ``base`` or its first deterministic numeric suffix."""
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def legacy_connection_id(kind: str, provider: str, used: set[str]) -> str:
    """Reserve a deterministic ID shared by Chat and Embedding migration."""
    base = slugify_id("legacy-" + kind + "-" + provider)
    return unique_id(base, used)


def _table(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(key, str)}


def _text(raw: Mapping[str, object], name: str) -> str:
    value = raw.get(name, "")
    return value if isinstance(value, str) else ""


def _safe_identifier(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    cleaned = "".join(char for char in value.strip() if char.isprintable())
    return (cleaned or "unknown")[:_SAFE_IDENTIFIER_LIMIT]


def _normalized_provider(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _credential_present(raw: Mapping[str, object]) -> bool:
    value = raw.get("api_key", "")
    return isinstance(value, str) and bool(value.strip())


def _credential_from_raw(
    provider: str,
    raw: Mapping[str, object],
    env: Mapping[str, str],
) -> CredentialConfig:
    inline = _text(raw, "api_key").strip()
    if inline:
        return CredentialConfig(source="inline", value=inline)
    env_names: tuple[str, ...] = ()
    if provider == "gemini":
        env_names = _GEMINI_ENV_KEYS
    elif provider == "dashscope":
        env_names = _DASHSCOPE_ENV_KEYS
    for name in env_names:
        value = env.get(name, "")
        if isinstance(value, str) and value.strip():
            return CredentialConfig(source="env", value=name)
    return CredentialConfig()


def _is_official_endpoint(base_url: str, hostname: str) -> bool:
    raw = base_url.strip()
    if not raw:
        return True
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return parsed.scheme.lower() == "https" and (parsed.hostname or "").lower() == hostname


def _ollama_base_url(base_url: str) -> str:
    normalized = base_url.strip() or "http://127.0.0.1:11434/v1"
    if not normalized.rstrip("/").endswith("/v1"):
        normalized = normalized.rstrip("/") + "/v1"
    return normalized


def _normalized_int(value: object, *, default: int, minimum: int, maximum: int | None) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        normalized = int(value)
    elif isinstance(value, str):
        try:
            normalized = int(value.strip())
        except ValueError:
            return default
    else:
        return default
    if normalized < minimum or (maximum is not None and normalized > maximum):
        return default
    return normalized


class _IssueCollector:
    def __init__(self) -> None:
        self.issues: list[MigrationIssue] = []
        self._used_ids: set[str] = set()

    def add(
        self,
        code: str,
        field: str,
        *,
        provider: str = "",
        credential_configured: bool = False,
        reason: str,
        severity: IssueSeverity = "blocking",
        allowed_actions: tuple[MigrationAction, ...] = _CONFIRM_REMOVE_ACTIONS,
    ) -> MigrationIssue:
        issue_id = unique_id(
            slugify_id(f"legacy-issue-{code}-{field}"),
            self._used_ids,
        )
        issue = MigrationIssue(
            id=issue_id,
            code=code,
            field=field,
            provider=_safe_identifier(provider),
            credential_configured=credential_configured,
            reason=reason,
            severity=severity,
            allowed_actions=allowed_actions,
        )
        self.issues.append(issue)
        return issue


def _report_unknown_fields(
    raw: Mapping[str, object],
    *,
    allowed: frozenset[str],
    prefix: str,
    provider: str,
    collector: _IssueCollector,
) -> None:
    for name in sorted(set(raw) - allowed):
        collector.add(
            "unknown_legacy_field",
            f"{prefix}.{name}",
            provider=provider,
            credential_configured=False,
            reason="legacy_field_has_no_safe_mapping",
        )


def _chat_connection(
    provider: str,
    raw: Mapping[str, object],
    env: Mapping[str, str],
    used_ids: set[str],
    collector: _IssueCollector,
) -> ChatConnection:
    model = _text(raw, "model").strip() or _CHAT_DEFAULT_MODELS[provider]
    credential = _credential_from_raw(provider, raw, env)
    connection_id = legacy_connection_id("chat", provider, used_ids)
    name = _PROVIDER_LABELS[provider]

    if provider == "openai":
        auth_mode = _text(raw, "auth_mode").strip().lower()
        if auth_mode == "codex_oauth":
            if _credential_present(raw):
                collector.add(
                    "unused_credential",
                    "llm.openai.api_key",
                    provider=provider,
                    credential_configured=True,
                    reason="codex_oauth_does_not_use_inline_api_key",
                )
            base_url = _text(raw, "base_url")
            if not _is_official_endpoint(base_url, "api.openai.com"):
                collector.add(
                    "invalid_auth_mode",
                    "llm.openai.base_url",
                    provider=provider,
                    credential_configured=True,
                    reason="codex_oauth_requires_official_endpoint",
                )
            return ChatConnection(
                id=connection_id,
                name="Codex OAuth",
                type="codex_oauth",
                model=model,
                credential=CredentialConfig(source="oauth", value="codex"),
            )
        if auth_mode not in {"", "api_key"}:
            collector.add(
                "invalid_auth_mode",
                "llm.openai.auth_mode",
                provider=provider,
                credential_configured=credential.source != "none",
                reason="legacy_auth_mode_is_not_supported",
            )
        if credential.source == "none":
            collector.add(
                "invalid_legacy_value",
                "llm.openai.api_key",
                provider=provider,
                credential_configured=False,
                reason="configured_provider_has_no_credential",
            )
        raw_base_url = _text(raw, "base_url")
        official = _is_official_endpoint(raw_base_url, "api.openai.com")
        api_flavor = _text(raw, "api_flavor").strip().lower()
        if api_flavor:
            collector.add(
                "translated_legacy_field",
                "llm.openai.api_flavor",
                provider=provider,
                reason="renamed_to_api_mode",
                severity="warning",
                allowed_actions=(),
            )
        if api_flavor not in {"", "chat_completions", "responses"}:
            collector.add(
                "invalid_legacy_value",
                "llm.openai.api_flavor",
                provider=provider,
                reason="legacy_api_mode_is_not_supported",
            )
            api_flavor = ""
        return ChatConnection(
            id=connection_id,
            name=name,
            type="openai_compatible",
            preset="openai" if official else "custom",
            model=model,
            base_url=raw_base_url.strip() or "https://api.openai.com/v1",
            credential=credential,
            api_mode=api_flavor or "chat_completions",
        )

    if provider != "ollama" and credential.source == "none":
        collector.add(
            "invalid_legacy_value",
            f"llm.{provider}.api_key",
            provider=provider,
            credential_configured=False,
            reason="configured_provider_has_no_credential",
        )

    if provider == "claude":
        raw_base_url = _text(raw, "base_url")
        official = _is_official_endpoint(raw_base_url, "api.anthropic.com")
        return ChatConnection(
            id=connection_id,
            name=name,
            type="anthropic_compatible",
            preset="anthropic" if official else "custom",
            model=model,
            base_url=raw_base_url.strip() or "https://api.anthropic.com",
            credential=credential,
        )

    if provider == "gemini":
        return ChatConnection(
            id=connection_id,
            name=name,
            type="gemini_api",
            model=model,
            base_url=_text(raw, "base_url").strip(),
            credential=credential,
        )

    if provider == "deepseek":
        raw_base_url = _text(raw, "base_url").strip()
        if raw_base_url and not _is_official_endpoint(raw_base_url, "api.deepseek.com"):
            collector.add(
                "invalid_legacy_value",
                "llm.deepseek.base_url",
                provider=provider,
                credential_configured=credential.source != "none",
                reason="legacy_runtime_ignored_custom_deepseek_endpoint",
            )
        return ChatConnection(
            id=connection_id,
            name=name,
            type="openai_compatible",
            preset="deepseek",
            model=model,
            base_url="https://api.deepseek.com",
            credential=credential,
            api_mode="chat_completions",
            reasoning_effort=_text(raw, "reasoning_effort").strip().lower(),
        )

    if provider == "ollama":
        if _credential_present(raw):
            collector.add(
                "unused_credential",
                "llm.ollama.api_key",
                provider=provider,
                credential_configured=True,
                reason="ollama_does_not_use_legacy_api_key",
            )
        return ChatConnection(
            id=connection_id,
            name=name,
            type="ollama",
            model=model,
            base_url=_ollama_base_url(_text(raw, "base_url")),
            num_ctx=_normalized_int(
                raw.get("num_ctx"),
                default=0,
                minimum=0,
                maximum=None,
            ),
        )

    if provider == "openrouter":
        return ChatConnection(
            id=connection_id,
            name=name,
            type="openai_compatible",
            preset="openrouter",
            model=model,
            base_url=_text(raw, "base_url").strip() or "https://openrouter.ai/api/v1",
            credential=credential,
            api_mode="chat_completions",
            http_referer=_text(raw, "http_referer"),
            x_title=_text(raw, "x_title"),
        )

    raw_base_url = _text(raw, "base_url").strip()
    if not raw_base_url:
        collector.add(
            "invalid_legacy_value",
            "llm.openai_compatible.base_url",
            provider=provider,
            credential_configured=credential.source != "none",
            reason="custom_openai_endpoint_is_required",
        )
    api_flavor = _text(raw, "api_flavor").strip().lower()
    if api_flavor:
        collector.add(
            "translated_legacy_field",
            "llm.openai_compatible.api_flavor",
            provider=provider,
            reason="renamed_to_api_mode",
            severity="warning",
            allowed_actions=(),
        )
    if api_flavor not in {"", "chat_completions", "responses"}:
        collector.add(
            "invalid_legacy_value",
            "llm.openai_compatible.api_flavor",
            provider=provider,
            reason="legacy_api_mode_is_not_supported",
        )
        api_flavor = ""
    return ChatConnection(
        id=connection_id,
        name=name,
        type="openai_compatible",
        preset="custom",
        model=model,
        base_url=raw_base_url,
        credential=credential,
        api_mode=api_flavor or "chat_completions",
    )


def _embedding_type_and_preset(provider: str) -> tuple[str, str]:
    if provider == "openai":
        return "openai_compatible", "openai"
    if provider in {"openai_compatible", "openrouter"}:
        return "openai_compatible", "custom"
    if provider == "gemini":
        return "gemini_api", ""
    if provider == "dashscope":
        return "dashscope_api", ""
    return "ollama", ""


def _embedding_base_url(provider: str, value: str) -> str:
    if provider == "ollama":
        return _ollama_base_url(value)
    if provider == "openai":
        return value.strip() or "https://api.openai.com/v1"
    if provider == "openrouter":
        return value.strip() or "https://openrouter.ai/api/v1"
    return value.strip()


def _embedding_provider(
    provider: str,
    source_raw: Mapping[str, object],
    env: Mapping[str, str],
    used_ids: set[str],
    *,
    credential_raw: Mapping[str, object],
) -> EmbeddingProviderConfig:
    connection_type, preset = _embedding_type_and_preset(provider)
    credential = (
        CredentialConfig()
        if provider == "ollama"
        else _credential_from_raw(provider, credential_raw, env)
    )
    return EmbeddingProviderConfig(
        id=legacy_connection_id("embedding", provider, used_ids),
        name=_PROVIDER_LABELS[provider],
        type=connection_type,
        preset=preset,
        base_url=_embedding_base_url(provider, _text(source_raw, "base_url")),
        credential=credential,
    )


def _embedding_settings(
    raw: Mapping[str, object],
    provider: str,
    collector: _IssueCollector,
) -> EmbeddingModelSettings:
    model = _text(raw, "model").strip() or _EMBEDDING_DEFAULT_MODELS.get(provider, "")

    output_raw = raw.get("output_dimensionality", 1024)
    output = _normalized_int(output_raw, default=1024, minimum=0, maximum=None)
    if output_raw is not None and (
        isinstance(output_raw, bool)
        or output != _normalized_int(output_raw, default=-1, minimum=0, maximum=None)
    ):
        collector.add(
            "invalid_legacy_value",
            "llm.embedding.output_dimensionality",
            provider=provider,
            reason="embedding_dimension_is_invalid",
        )

    threshold_raw = raw.get("similarity_threshold", 0.82)
    if isinstance(threshold_raw, bool) or not isinstance(threshold_raw, int | float):
        threshold = 0.82
        collector.add(
            "invalid_legacy_value",
            "llm.embedding.similarity_threshold",
            provider=provider,
            reason="embedding_similarity_threshold_is_invalid",
        )
    else:
        threshold = float(threshold_raw)
        if not 0.0 <= threshold <= 1.0:
            threshold = 0.82
            collector.add(
                "invalid_legacy_value",
                "llm.embedding.similarity_threshold",
                provider=provider,
                reason="embedding_similarity_threshold_is_invalid",
            )

    multimodal_raw = raw.get("multimodal_enabled", False)
    if not isinstance(multimodal_raw, bool):
        multimodal = False
        collector.add(
            "invalid_legacy_value",
            "llm.embedding.multimodal_enabled",
            provider=provider,
            reason="embedding_multimodal_flag_is_invalid",
        )
    else:
        multimodal = multimodal_raw

    return EmbeddingModelSettings(
        model=model,
        output_dimensionality=output,
        similarity_threshold=threshold,
        multimodal_enabled=multimodal,
    )


def _unknown_credential_configured(raw: Mapping[str, object]) -> bool:
    for key, value in raw.items():
        lowered = key.lower()
        if not any(marker in lowered for marker in ("key", "token", "credential", "secret")):
            continue
        if isinstance(value, str) and value.strip():
            return True
    return False


def migrate_legacy_llm(
    raw_llm: Mapping[str, object],
    env: Mapping[str, str],
) -> LegacyMigrationResult:
    """Build a deterministic in-memory candidate from a raw legacy table.

    The function has no filesystem side effects.  Only the explicit legacy
    default and fallback become active Chat connections; every other saved
    credential remains a reportable pending candidate.
    """
    raw = _table(raw_llm)
    environment = {str(key): str(value) for key, value in env.items()}
    collector = _IssueCollector()
    used_ids: set[str] = set()
    pending: list[_PendingValue] = []

    for provider in _CHAT_PROVIDER_ORDER:
        provider_raw = _table(raw.get(provider, {}))
        _report_unknown_fields(
            provider_raw,
            allowed=_PROVIDER_FIELDS[provider],
            prefix=f"llm.{provider}",
            provider=provider,
            collector=collector,
        )

    embedding_raw = _table(raw.get("embedding", {}))
    _report_unknown_fields(
        embedding_raw,
        allowed=_EMBEDDING_FIELDS,
        prefix="llm.embedding",
        provider=_normalized_provider(embedding_raw.get("provider", "")),
        collector=collector,
    )

    route_names: list[str] = []
    for field_name in ("default_provider", "fallback_provider"):
        raw_name = raw.get(field_name, "")
        provider = _normalized_provider(raw_name)
        if not provider:
            if field_name == "default_provider":
                collector.add(
                    "unknown_provider",
                    "llm.default_provider",
                    provider=_safe_identifier(raw_name),
                    reason="legacy_default_provider_is_missing",
                )
            continue
        if isinstance(raw_name, str) and raw_name != provider:
            collector.add(
                "translated_legacy_value",
                f"llm.{field_name}",
                provider=provider,
                reason="provider_identifier_was_normalized",
                severity="warning",
                allowed_actions=(),
            )
        if provider not in _CHAT_PROVIDER_ORDER:
            provider_raw = _table(raw.get(provider, {}))
            collector.add(
                "unknown_provider",
                f"llm.{field_name}",
                provider=_safe_identifier(raw_name),
                credential_configured=_unknown_credential_configured(provider_raw),
                reason="legacy_chat_provider_has_no_safe_mapping",
            )
            continue
        if provider not in route_names:
            route_names.append(provider)

    chat_connections = tuple(
        _chat_connection(
            provider,
            _table(raw.get(provider, {})),
            environment,
            used_ids,
            collector,
        )
        for provider in route_names
    )

    for provider in _CHAT_PROVIDER_ORDER:
        provider_raw = _table(raw.get(provider, {}))
        credential = _credential_from_raw(provider, provider_raw, environment)
        is_oauth = (
            provider == "openai"
            and _text(provider_raw, "auth_mode").strip().lower() == "codex_oauth"
        )
        credential_configured = credential.source != "none" or is_oauth
        if provider in route_names or not credential_configured:
            continue
        candidate = _chat_connection(
            provider,
            provider_raw,
            environment,
            used_ids,
            collector,
        )
        issue = collector.add(
            "unrouted_credential",
            f"llm.{provider}.api_key",
            provider=provider,
            credential_configured=True,
            reason="configured_credential_is_not_in_explicit_chat_route",
            allowed_actions=_UNROUTED_ACTIONS,
        )
        pending.append(_PendingValue(issue_id=issue.id, chat_connection=candidate))

    for module_name in _MODULE_NAMES:
        module_raw = _table(raw.get(module_name, {}))
        provider = _normalized_provider(module_raw.get("provider", ""))
        model = _text(module_raw, "model").strip()
        if provider or model:
            collector.add(
                "module_override_removed",
                f"llm.{module_name}",
                provider=provider,
                reason="module_override_must_use_global_route",
                allowed_actions=_MODULE_OVERRIDE_ACTIONS,
            )
        _report_unknown_fields(
            module_raw,
            allowed=frozenset({"provider", "model"}),
            prefix=f"llm.{module_name}",
            provider=provider,
            collector=collector,
        )

    embedding_name = _normalized_provider(embedding_raw.get("provider", ""))
    embedding_settings = _embedding_settings(embedding_raw, embedding_name, collector)
    embedding_providers: list[EmbeddingProviderConfig] = []
    if _credential_present(embedding_raw) and embedding_name in {"", "ollama"}:
        collector.add(
            "unused_credential",
            "llm.embedding.api_key",
            provider=embedding_name,
            credential_configured=True,
            reason="embedding_credential_has_no_remote_provider",
        )
    if embedding_name:
        if embedding_name not in _EMBEDDING_PROVIDERS:
            collector.add(
                "unknown_provider",
                "llm.embedding.provider",
                provider=embedding_name,
                credential_configured=_credential_present(embedding_raw),
                reason="legacy_embedding_provider_has_no_safe_mapping",
            )
        else:
            primary_credentials = embedding_raw
            fallback_enabled = embedding_raw.get("fallback_enabled", False) is True
            if (
                not _credential_present(embedding_raw)
                and fallback_enabled
                and not _text(embedding_raw, "base_url").strip()
            ):
                primary_credentials = _table(raw.get(embedding_name, {}))
            primary_provider = _embedding_provider(
                embedding_name,
                embedding_raw,
                environment,
                used_ids,
                credential_raw=primary_credentials,
            )
            embedding_providers.append(primary_provider)
            if embedding_name != "ollama" and primary_provider.credential.source == "none":
                collector.add(
                    "invalid_legacy_value",
                    "llm.embedding.api_key",
                    provider=embedding_name,
                    credential_configured=False,
                    reason="configured_embedding_provider_has_no_credential",
                )

    fallback_embedding_name = _normalized_provider(embedding_raw.get("fallback_provider", ""))
    if fallback_embedding_name and fallback_embedding_name != embedding_name:
        if fallback_embedding_name not in _EMBEDDING_PROVIDERS:
            collector.add(
                "unknown_provider",
                "llm.embedding.fallback_provider",
                provider=fallback_embedding_name,
                credential_configured=_unknown_credential_configured(
                    _table(raw.get(fallback_embedding_name, {}))
                ),
                reason="legacy_embedding_fallback_has_no_safe_mapping",
            )
        else:
            fallback_raw = _table(raw.get(fallback_embedding_name, {}))
            fallback_candidate = _embedding_provider(
                fallback_embedding_name,
                fallback_raw,
                environment,
                used_ids,
                credential_raw=(
                    fallback_raw if embedding_raw.get("fallback_enabled", False) is True else {}
                ),
            )
            fallback_model = _EMBEDDING_DEFAULT_MODELS[fallback_embedding_name]
            model_matches = bool(fallback_model) and fallback_model == embedding_settings.model
            multimodal_matches = not embedding_settings.multimodal_enabled or (
                fallback_embedding_name in {"gemini", "dashscope", "openrouter"}
            )
            credential_matches = (
                fallback_embedding_name == "ollama"
                or fallback_candidate.credential.source != "none"
            )
            if embedding_providers and model_matches and multimodal_matches and credential_matches:
                embedding_providers.append(fallback_candidate)
            else:
                issue = collector.add(
                    "embedding_space_mismatch",
                    "llm.embedding.fallback_provider",
                    provider=fallback_embedding_name,
                    credential_configured=fallback_candidate.credential.source != "none",
                    reason="effective_embedding_space_differs",
                    allowed_actions=_EMBEDDING_MISMATCH_ACTIONS,
                )
                pending.append(
                    _PendingValue(
                        issue_id=issue.id,
                        embedding_provider=fallback_candidate,
                    )
                )

    for name in sorted(set(raw) - _KNOWN_LLM_FIELDS):
        value = raw[name]
        unknown_raw = _table(value)
        if unknown_raw:
            collector.add(
                "unknown_provider",
                f"llm.{name}",
                provider=name,
                credential_configured=_unknown_credential_configured(unknown_raw),
                reason="legacy_provider_table_has_no_safe_mapping",
            )
        else:
            collector.add(
                "unknown_legacy_field",
                f"llm.{name}",
                reason="legacy_field_has_no_safe_mapping",
            )

    models = ModelConfig(
        schema_version=1,
        chat=ChatRouteConfig(
            connections=chat_connections,
            concurrency=_normalized_int(
                raw.get("concurrency"),
                default=4,
                minimum=1,
                maximum=16,
            ),
            timeout_seconds=_normalized_int(
                raw.get("timeout"),
                default=300,
                minimum=10,
                maximum=None,
            ),
        ),
        embedding=EmbeddingRouteConfig(
            enabled=bool(embedding_name and embedding_providers),
            settings=embedding_settings,
            providers=tuple(embedding_providers),
        ),
    )
    return LegacyMigrationResult(
        models=models,
        report=MigrationReport(tuple(collector.issues)),
        _pending=tuple(pending),
    )


def _resolution_error() -> MigrationResolutionError:
    return MigrationResolutionError("migration resolutions are incomplete or invalid")


def _valid_embedding_settings(value: object) -> bool:
    return (
        isinstance(value, EmbeddingModelSettings)
        and bool(value.model.strip())
        and type(value.output_dimensionality) is int
        and value.output_dimensionality >= 0
        and isinstance(value.similarity_threshold, int | float)
        and not isinstance(value.similarity_threshold, bool)
        and 0.0 <= float(value.similarity_threshold) <= 1.0
        and type(value.multimodal_enabled) is bool
    )


def _insert_chat_connections(
    existing: tuple[ChatConnection, ...],
    additions: list[tuple[int, ChatConnection]],
) -> tuple[ChatConnection, ...]:
    final_count = len(existing) + len(additions)
    if not 1 <= final_count <= 10:
        raise _resolution_error()
    positions = [position for position, _connection in additions]
    if len(positions) != len(set(positions)):
        raise _resolution_error()
    if any(position < 1 or position > final_count for position in positions):
        raise _resolution_error()

    by_position = {position: connection for position, connection in additions}
    remaining = iter(existing)
    resolved: list[ChatConnection] = []
    for position in range(1, final_count + 1):
        connection = by_position.get(position)
        resolved.append(connection if connection is not None else next(remaining))
    return tuple(resolved)


def apply_migration_resolutions(
    result: LegacyMigrationResult,
    choices: Mapping[str, MigrationResolution],
) -> ModelConfig:
    """Apply a complete set of closed migration decisions in memory.

    The backup acknowledgement action does not create a backup here.  It only
    records a decision for the later transactional persistence service.
    """
    if not isinstance(result, LegacyMigrationResult) or not isinstance(choices, Mapping):
        raise _resolution_error()

    required = tuple(issue for issue in result.report.issues if issue.severity == "blocking")
    required_ids = {issue.id for issue in required}
    if set(choices) != required_ids:
        raise _resolution_error()

    pending = {item.issue_id: item for item in result._pending}
    chat_additions: list[tuple[int, ChatConnection]] = []
    embedding_addition: tuple[EmbeddingProviderConfig, EmbeddingModelSettings] | None = None

    for issue in required:
        resolution = choices.get(issue.id)
        if not isinstance(resolution, MigrationResolution):
            raise _resolution_error()
        if resolution.action not in issue.allowed_actions or resolution.action == "cancel":
            raise _resolution_error()

        if resolution.action == "add_to_chat_route":
            if type(resolution.position) is not int or resolution.embedding_settings is not None:
                raise _resolution_error()
            value = pending.get(issue.id)
            if value is None or value.chat_connection is None:
                raise _resolution_error()
            chat_additions.append((resolution.position, value.chat_connection))
            continue

        if resolution.action == "apply_shared_embedding_settings":
            if resolution.position is not None or not _valid_embedding_settings(
                resolution.embedding_settings
            ):
                raise _resolution_error()
            value = pending.get(issue.id)
            if value is None or value.embedding_provider is None:
                raise _resolution_error()
            if embedding_addition is not None or resolution.embedding_settings is None:
                raise _resolution_error()
            embedding_addition = (value.embedding_provider, resolution.embedding_settings)
            continue

        if resolution.position is not None or resolution.embedding_settings is not None:
            raise _resolution_error()

    chat_connections = _insert_chat_connections(
        tuple(result.models.chat.connections),
        chat_additions,
    )
    embedding = result.models.embedding
    if embedding_addition is not None:
        provider, settings = embedding_addition
        providers = (*embedding.providers, provider)
        if len(providers) > 10:
            raise _resolution_error()
        embedding = replace(
            embedding,
            enabled=True,
            settings=settings,
            providers=providers,
        )

    all_ids = [connection.id for connection in chat_connections] + [
        provider.id for provider in embedding.providers
    ]
    if any(not item.strip() for item in all_ids) or len(all_ids) != len(set(all_ids)):
        raise _resolution_error()

    return replace(
        result.models,
        chat=replace(result.models.chat, connections=chat_connections),
        embedding=embedding,
    )


__all__ = [
    "LegacyMigrationResult",
    "MigrationAction",
    "MigrationIssue",
    "MigrationReport",
    "MigrationResolution",
    "MigrationResolutionError",
    "apply_migration_resolutions",
    "legacy_connection_id",
    "migrate_legacy_llm",
    "slugify_id",
    "unique_id",
]
