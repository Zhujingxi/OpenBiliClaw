"""Secret-safe inspection helpers for raw legacy model configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit, urlunsplit

from ._migration_types import CONFIRM_REMOVE_ACTIONS, MigrationAction, MigrationIssue
from .types import CredentialConfig, IssueSeverity

_SAFE_IDENTIFIER_LIMIT = 80
_GEMINI_ENV_KEYS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
_DASHSCOPE_ENV_KEYS = ("DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY_CN")


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


def safe_identifier(value: object) -> str:
    """Return a bounded printable identifier without coercing arbitrary objects."""
    if not isinstance(value, str):
        return "unknown"
    cleaned = "".join(char for char in value.strip() if char.isprintable())
    return (cleaned or "unknown")[:_SAFE_IDENTIFIER_LIMIT]


def value_configured(value: object) -> bool:
    """Conservatively report whether an opaque raw value carries content."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        return bool(value)
    return True


class IssueCollector:
    """Deterministically collects secret-free migration issues."""

    def __init__(self) -> None:
        self.issues: list[MigrationIssue] = []
        self._used_ids: set[str] = set()
        self._by_semantics: dict[tuple[object, ...], MigrationIssue] = {}

    def add(
        self,
        code: str,
        field: str,
        *,
        provider: str = "",
        credential_configured: bool = False,
        reason: str,
        severity: IssueSeverity = "blocking",
        allowed_actions: tuple[MigrationAction, ...] = CONFIRM_REMOVE_ACTIONS,
    ) -> MigrationIssue:
        """Append one issue whose public fields contain no raw value."""
        safe_provider = safe_identifier(provider)
        actions = tuple(allowed_actions)
        semantics: tuple[object, ...] = (
            code,
            field,
            safe_provider,
            credential_configured,
            reason,
            severity,
            actions,
        )
        existing = self._by_semantics.get(semantics)
        if existing is not None:
            return existing
        issue_id = unique_id(
            slugify_id(f"legacy-issue-{code}-{field}"),
            self._used_ids,
        )
        issue = MigrationIssue(
            id=issue_id,
            code=code,
            field=field,
            provider=safe_provider,
            credential_configured=credential_configured,
            reason=reason,
            severity=severity,
            allowed_actions=actions,
        )
        self.issues.append(issue)
        self._by_semantics[semantics] = issue
        return issue


def raw_table(
    container: Mapping[str, object],
    name: str,
    *,
    field: str,
    collector: IssueCollector,
) -> dict[str, object]:
    """Read one known table and report every malformed shape."""
    if name not in container:
        return {}
    value = container[name]
    if not isinstance(value, Mapping):
        collector.add(
            "invalid_legacy_value",
            field,
            credential_configured=value_configured(value),
            reason="legacy_section_must_be_table",
        )
        return {}

    result: dict[str, object] = {}
    invalid_key = False
    for key, item in value.items():
        if isinstance(key, str):
            result[key] = item
        else:
            invalid_key = True
    if invalid_key:
        collector.add(
            "invalid_legacy_value",
            field,
            reason="legacy_field_name_must_be_string",
        )
    return result


@dataclass(frozen=True)
class RawText:
    """Validated text plus whether an explicit raw value was valid/configured."""

    value: str = field(repr=False)
    valid: bool
    configured: bool
    issue_id: str = ""


def text_field(
    raw: Mapping[str, object],
    name: str,
    *,
    field: str,
    collector: IssueCollector,
    default: str = "",
    reason: str = "legacy_string_value_required",
    credential: bool = False,
) -> RawText:
    """Read an exact string without coercing lists, numbers, or booleans."""
    if name not in raw:
        return RawText(default, True, False)
    value = raw[name]
    if not isinstance(value, str):
        configured = value_configured(value)
        issue = collector.add(
            "invalid_legacy_value",
            field,
            credential_configured=configured if credential else False,
            reason=reason,
        )
        return RawText(default, False, configured, issue.id)
    stripped = value.strip()
    return RawText(stripped, True, bool(stripped))


def exact_int_field(
    raw: Mapping[str, object],
    name: str,
    *,
    field: str,
    collector: IssueCollector,
    default: int,
    minimum: int,
    maximum: int | None,
    reason: str,
) -> int:
    """Read an exact integer; booleans and floats are never truncated."""
    if name not in raw:
        return default
    value = raw[name]
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        collector.add("invalid_legacy_value", field, reason=reason)
        return default
    return value


def exact_bool_field(
    raw: Mapping[str, object],
    name: str,
    *,
    field: str,
    collector: IssueCollector,
    default: bool,
    reason: str,
) -> bool:
    """Read an exact boolean without truthiness coercion."""
    if name not in raw:
        return default
    value = raw[name]
    if type(value) is not bool:
        collector.add("invalid_legacy_value", field, reason=reason)
        return default
    return value


def bounded_float_field(
    raw: Mapping[str, object],
    name: str,
    *,
    field: str,
    collector: IssueCollector,
    default: float,
    minimum: float,
    maximum: float,
    reason: str,
) -> float:
    """Read an exact numeric scalar within a closed range."""
    if name not in raw:
        return default
    value = raw[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        collector.add("invalid_legacy_value", field, reason=reason)
        return default
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        collector.add("invalid_legacy_value", field, reason=reason)
        return default
    return normalized


@dataclass(frozen=True)
class NormalizedEndpoint:
    """Audited endpoint classification without retaining rejected raw input."""

    value: str
    valid: bool
    official: bool = False
    issue_id: str = ""


def _normalized_netloc(parsed: SplitResult, hostname: str, port: int | None) -> str:
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return display_host if port in {None, default_port} else f"{display_host}:{port}"


def inspect_endpoint(
    raw_value: object,
    *,
    field: str,
    collector: IssueCollector,
    default: str = "",
    required: bool = False,
    official_host: str = "",
    official_paths: frozenset[str] = frozenset(),
    canonical_official: str = "",
) -> NormalizedEndpoint:
    """Validate and normalize one HTTP(S) endpoint through a single policy.

    Userinfo, query strings, fragments, malformed ports, control/whitespace
    characters, and non-HTTP schemes are rejected.  A URL using an official
    hostname must also match its exact HTTPS port/path policy; it cannot fall
    through to a custom endpoint classification.
    """
    if isinstance(raw_value, str) and raw_value == "":
        if canonical_official:
            return NormalizedEndpoint(canonical_official, True, True)
        if default:
            return NormalizedEndpoint(default, True)
        if required:
            issue = collector.add(
                "invalid_legacy_value",
                field,
                reason="legacy_endpoint_is_required",
            )
            return NormalizedEndpoint("", False, issue_id=issue.id)
        return NormalizedEndpoint("", True)

    if not isinstance(raw_value, str):
        issue = collector.add(
            "invalid_legacy_value",
            field,
            reason="legacy_endpoint_must_be_string",
        )
        return NormalizedEndpoint("", False, issue_id=issue.id)

    value = raw_value.strip()
    if not value:
        issue = collector.add(
            "invalid_legacy_value",
            field,
            reason="legacy_endpoint_is_invalid",
        )
        return NormalizedEndpoint("", False, issue_id=issue.id)

    invalid_characters = (
        any(not char.isprintable() or char.isspace() for char in raw_value) or "\\" in raw_value
    )
    has_query_delimiter = "?" in value
    has_fragment_delimiter = "#" in value
    try:
        parsed = urlsplit(value)
        hostname_value = parsed.hostname
        port = parsed.port
        parsed_hostname = (
            hostname_value.encode("idna").decode("ascii").lower()
            if hostname_value is not None
            else ""
        )
        if parsed_hostname.endswith(".."):
            raise ValueError("ambiguous trailing DNS dots")
        hostname = parsed_hostname[:-1] if parsed_hostname.endswith(".") else parsed_hostname
    except (UnicodeError, ValueError):
        parsed = urlsplit("")
        hostname = ""
        port = None

    base_valid = (
        not invalid_characters
        and parsed.scheme.lower() in {"http", "https"}
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and not has_query_delimiter
        and not has_fragment_delimiter
    )
    official_name = official_host.lower()
    normalized_path = (
        ""
        if parsed.path in {"", "/"}
        else parsed.path[:-1]
        if parsed.path.endswith("/")
        else parsed.path
    )
    official = (
        base_valid
        and bool(official_name)
        and hostname == official_name
        and parsed.scheme.lower() == "https"
        and port in {None, 443}
        and normalized_path in official_paths
    )
    official_host_mismatch = bool(official_name) and hostname == official_name and not official

    if not base_valid or official_host_mismatch:
        issue = collector.add(
            "invalid_legacy_value",
            field,
            reason="legacy_endpoint_is_invalid",
        )
        return NormalizedEndpoint("", False, issue_id=issue.id)
    if official:
        return NormalizedEndpoint(canonical_official, True, True)

    normalized = urlunsplit(
        (
            parsed.scheme.lower(),
            _normalized_netloc(parsed, hostname, port),
            parsed.path,
            "",
            "",
        )
    )
    return NormalizedEndpoint(normalized, True)


def normalized_ollama_endpoint(value: str) -> str:
    """Append the OpenAI-compatible ``/v1`` suffix to a validated URL."""
    if not value:
        return ""
    normalized = value.rstrip("/")
    return normalized if normalized.endswith("/v1") else normalized + "/v1"


@dataclass(frozen=True)
class InspectedCredential:
    """One secret-hidden credential plus safe raw-inspection metadata."""

    credential: CredentialConfig = field(default_factory=CredentialConfig, repr=False)
    valid: bool = True
    configured: bool = False
    issue_id: str = ""


def inspect_credential_from_raw(
    provider: str,
    raw: Mapping[str, object],
    env: Mapping[str, str],
    *,
    prefix: str,
    collector: IssueCollector,
) -> InspectedCredential:
    """Read an inline or approved environment credential without coercion."""
    inline = text_field(
        raw,
        "api_key",
        field=f"{prefix}.api_key",
        collector=collector,
        reason="legacy_credential_must_be_string",
        credential=True,
    )
    if inline.value:
        return InspectedCredential(
            credential=CredentialConfig(source="inline", value=inline.value),
            valid=inline.valid,
            configured=inline.configured,
            issue_id=inline.issue_id,
        )

    env_names: tuple[str, ...] = ()
    if provider == "gemini":
        env_names = _GEMINI_ENV_KEYS
    elif provider == "dashscope":
        env_names = _DASHSCOPE_ENV_KEYS
    for name in env_names:
        value = env.get(name, "")
        if isinstance(value, str) and value.strip():
            return InspectedCredential(
                credential=CredentialConfig(source="env", value=name),
                valid=inline.valid,
                configured=True,
                issue_id=inline.issue_id,
            )
    return InspectedCredential(
        valid=inline.valid,
        configured=inline.configured,
        issue_id=inline.issue_id,
    )


def credential_from_raw(
    provider: str,
    raw: Mapping[str, object],
    env: Mapping[str, str],
    *,
    prefix: str,
    collector: IssueCollector,
) -> CredentialConfig:
    """Return only the credential value for callers that need no inspection metadata."""
    return inspect_credential_from_raw(
        provider,
        raw,
        env,
        prefix=prefix,
        collector=collector,
    ).credential


def unknown_credential_configured(value: object) -> bool:
    """Conservatively detect credentials in an unknown table or scalar."""
    if not isinstance(value, Mapping):
        return value_configured(value)
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if any(
            marker in lowered for marker in ("key", "token", "credential", "secret")
        ) and value_configured(item):
            return True
    return False


__all__ = [
    "IssueCollector",
    "InspectedCredential",
    "NormalizedEndpoint",
    "RawText",
    "bounded_float_field",
    "credential_from_raw",
    "exact_bool_field",
    "exact_int_field",
    "inspect_endpoint",
    "inspect_credential_from_raw",
    "legacy_connection_id",
    "normalized_ollama_endpoint",
    "raw_table",
    "safe_identifier",
    "slugify_id",
    "text_field",
    "unique_id",
    "unknown_credential_configured",
    "value_configured",
]
