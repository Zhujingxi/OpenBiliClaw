"""Immutable, server-owned context for a durable dialogue reply.

The context is deliberately small.  It is the one value object copied from
POST-time admission into the interactive prompt, durable history, learning
job, raw event and settlement provenance.  ``captured_at`` is useful for
inspection but is intentionally excluded from the digest so retries at the
same canonical target remain byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Self
from uuid import UUID

CONTEXT_VERSION = 1
BINDING_VERSION = 1


class DialogueBindingError(ValueError):
    """Raised when a dialogue context or binding is not canonical."""


class BindingMode(StrEnum):
    """Closed set of durable dialogue admission modes."""

    BOUND = "bound"
    ORDINARY = "ordinary"
    DETACHED = "detached"


_SOURCE_TYPES = frozenset({"card", "question"})
_KINDS = frozenset({"hypothesis", "confusion"})
_OPAQUE_EVIDENCE_RE = re.compile(
    r"^(?:"
    r"\d{1,24}|"
    r"(?:0x)?[0-9a-f]{8,128}|"
    r"(?:bv[0-9a-z]{10,}|av\d+|cv\d+)|"
    r"(?:event|evt|note|content|awareness|hypothesis|confusion|insight|turn)[#:/_-][A-Za-z0-9._:/-]+"
    r")$",
    re.IGNORECASE,
)


def _clean_text(value: object, *, field_name: str, required: bool = True) -> str:
    if not isinstance(value, str):
        raise DialogueBindingError(f"{field_name} must be a string")
    normalized = " ".join(unicodedata.normalize("NFC", value).split())
    if required and not normalized:
        raise DialogueBindingError(f"{field_name} is required")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise DialogueBindingError(f"{field_name} contains a control character")
    return normalized


def _is_opaque_evidence_label(value: str) -> bool:
    if _OPAQUE_EVIDENCE_RE.fullmatch(value):
        return True
    try:
        UUID(value)
    except (ValueError, AttributeError):
        pass
    else:
        return True
    return (
        len(value) >= 20
        and not re.match(r"^https?://", value, re.IGNORECASE)
        and bool(re.fullmatch(r"[A-Za-z0-9._:/+-]+", value))
    )


def filter_evidence_labels(values: object) -> tuple[str, ...]:
    """Return readable, de-duplicated evidence labels within the UI budget.

    The five-by-240 budget is calibrated to keep the dynamic prompt addition
    below roughly 1.2K characters (2026-08-01 dialogue-card contract).  IDs
    and URLs remain available in the original card payload for internal links,
    but never cross into a user-visible or natural-language projection.
    """

    if values is None:
        return ()
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, (list, tuple)):
        raise DialogueBindingError("evidence_labels must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        label = _clean_text(raw, field_name="evidence label")
        if _is_opaque_evidence_label(label):
            continue
        if label in seen:
            continue
        seen.add(label)
        result.append(label[:240])
        if len(result) == 5:
            break
    return tuple(result)


@dataclass(frozen=True, slots=True)
class DialogueTurnContext:
    """Canonical context captured from one completed card/question turn."""

    reply_to_turn_id: str
    source_type: str
    kind: str
    ref: str
    generation: int
    anchor_origin_turn_id: str
    title: str
    evidence_labels: tuple[str, ...] = field(default_factory=tuple)
    captured_at: str = ""
    version: int = CONTEXT_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DialogueBindingError("context version must be an integer")
        if self.version != CONTEXT_VERSION:
            raise DialogueBindingError(f"Unsupported context version: {self.version!r}")
        for field_name in ("reply_to_turn_id", "ref", "anchor_origin_turn_id", "title"):
            object.__setattr__(
                self,
                field_name,
                _clean_text(getattr(self, field_name), field_name=field_name),
            )
        source_type = _clean_text(self.source_type, field_name="source_type").lower()
        kind = _clean_text(self.kind, field_name="kind").lower()
        if source_type not in _SOURCE_TYPES:
            raise DialogueBindingError(f"Unknown source_type: {source_type!r}")
        if kind not in _KINDS:
            raise DialogueBindingError(f"Unknown kind: {kind!r}")
        expected_source = "card" if kind == "hypothesis" else "question"
        if source_type != expected_source:
            raise DialogueBindingError(f"source_type {source_type!r} conflicts with kind {kind!r}")
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "kind", kind)
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise DialogueBindingError("generation must be a positive integer")
        if self.generation <= 0:
            raise DialogueBindingError("generation must be a positive integer")
        object.__setattr__(self, "evidence_labels", filter_evidence_labels(self.evidence_labels))
        captured_at = _clean_text(self.captured_at, field_name="captured_at", required=False)
        object.__setattr__(self, "captured_at", captured_at)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Self:
        """Parse a strict canonical mapping, rejecting unknown top-level facts."""

        if not isinstance(value, Mapping):
            raise DialogueBindingError("dialogue context must be an object")
        allowed = {
            "version",
            "reply_to_turn_id",
            "source_type",
            "kind",
            "ref",
            "generation",
            "anchor_origin_turn_id",
            "title",
            "evidence_labels",
            "captured_at",
        }
        unknown = set(value) - allowed
        if unknown:
            raise DialogueBindingError(f"Unknown context fields: {sorted(map(str, unknown))}")
        try:
            return cls(
                reply_to_turn_id=value["reply_to_turn_id"],  # type: ignore[arg-type]
                source_type=value["source_type"],  # type: ignore[arg-type]
                kind=value["kind"],  # type: ignore[arg-type]
                ref=value["ref"],  # type: ignore[arg-type]
                generation=value["generation"],  # type: ignore[arg-type]
                anchor_origin_turn_id=value["anchor_origin_turn_id"],  # type: ignore[arg-type]
                title=value["title"],  # type: ignore[arg-type]
                evidence_labels=value.get("evidence_labels", ()),  # type: ignore[arg-type]
                captured_at=value.get("captured_at", ""),  # type: ignore[arg-type]
                version=value.get("version", CONTEXT_VERSION),  # type: ignore[arg-type]
            )
        except KeyError as exc:
            raise DialogueBindingError(f"Missing context field: {exc.args[0]}") from exc

    @property
    def canonical_payload(self) -> Mapping[str, object]:
        """Return the digest-covered, immutable canonical payload."""

        return MappingProxyType(
            {
                "version": self.version,
                "reply_to_turn_id": self.reply_to_turn_id,
                "source_type": self.source_type,
                "kind": self.kind,
                "ref": self.ref,
                "generation": self.generation,
                "anchor_origin_turn_id": self.anchor_origin_turn_id,
                "title": self.title,
                "evidence_labels": list(self.evidence_labels),
            }
        )

    @property
    def context_digest(self) -> str:
        """Return the full SHA-256 digest of the canonical context."""

        encoded = json.dumps(
            dict(self.canonical_payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def digest(self) -> str:
        """Short alias used by callers that treat context as a value object."""

        return self.context_digest

    def to_mapping(self) -> dict[str, object]:
        """Serialize the complete context, including non-digest capture time."""

        return {
            **dict(self.canonical_payload),
            "captured_at": self.captured_at,
        }

    def prompt_projection(self) -> dict[str, object]:
        """Return only readable fields allowed in a natural-language prompt."""

        label = "卡片" if self.source_type == "card" else "疑惑问题"
        return {
            "label": label,
            "title": self.title,
            "evidence_labels": list(self.evidence_labels),
        }

    def history_prefix(self) -> str:
        """Return the stable relation prefix used only for durable LLM history."""

        label = "卡片" if self.source_type == "card" else "疑惑问题"
        return f"[回复{label}「{self.title}」]"


@dataclass(frozen=True, slots=True)
class DialogueTurnBinding:
    """Server-owned mode plus an optional frozen canonical context."""

    mode: BindingMode
    context_digest: str = ""
    context: DialogueTurnContext | None = None
    inventory_settles_allowed: bool = True
    version: int = BINDING_VERSION

    def __post_init__(self) -> None:
        try:
            mode = BindingMode(self.mode)
        except ValueError as exc:
            raise DialogueBindingError(f"Unknown binding mode: {self.mode!r}") from exc
        object.__setattr__(self, "mode", mode)
        if not isinstance(self.inventory_settles_allowed, bool):
            raise DialogueBindingError("inventory_settles_allowed must be a boolean")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DialogueBindingError("binding version must be an integer")
        if self.version != BINDING_VERSION:
            raise DialogueBindingError(f"Unsupported binding version: {self.version!r}")
        if mode is BindingMode.BOUND:
            if self.context is None:
                raise DialogueBindingError("bound binding requires context")
            if self.context_digest != self.context.context_digest:
                raise DialogueBindingError("context_digest does not match context")
            if not self.context_digest or not re.fullmatch(r"[0-9a-f]{64}", self.context_digest):
                raise DialogueBindingError("bound binding requires a full SHA-256 digest")
            if not self.inventory_settles_allowed:
                raise DialogueBindingError("bound binding must allow inventory settles")
        else:
            if self.context is not None or self.context_digest:
                raise DialogueBindingError("unbound mode cannot carry canonical context")
            if mode is BindingMode.ORDINARY and not self.inventory_settles_allowed:
                raise DialogueBindingError("ordinary mode must allow inventory settles")
            if mode is BindingMode.DETACHED and self.inventory_settles_allowed:
                raise DialogueBindingError("detached mode must prohibit inventory settles")

    @classmethod
    def from_context(cls, context: DialogueTurnContext) -> Self:
        return cls(
            mode=BindingMode.BOUND,
            context=context,
            context_digest=context.context_digest,
            inventory_settles_allowed=True,
        )

    @classmethod
    def ordinary(cls) -> Self:
        return cls(mode=BindingMode.ORDINARY, inventory_settles_allowed=True)

    @classmethod
    def detached(cls) -> Self:
        return cls(mode=BindingMode.DETACHED, inventory_settles_allowed=False)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Self:
        if not isinstance(value, Mapping):
            raise DialogueBindingError("dialogue_binding must be an object")
        allowed = {"version", "mode", "context_digest", "context", "inventory_settles_allowed"}
        unknown = set(value) - allowed
        if unknown:
            raise DialogueBindingError(f"Unknown binding fields: {sorted(map(str, unknown))}")
        try:
            mode = BindingMode(value["mode"])  # type: ignore[arg-type]
        except (KeyError, ValueError) as exc:
            raise DialogueBindingError("dialogue_binding mode is invalid") from exc
        raw_digest = value.get("context_digest", "")
        if not isinstance(raw_digest, str):
            raise DialogueBindingError("context_digest must be a string")
        raw_inventory = value.get("inventory_settles_allowed", mode is not BindingMode.DETACHED)
        if not isinstance(raw_inventory, bool):
            raise DialogueBindingError("inventory_settles_allowed must be a boolean")
        raw_context = value.get("context")
        context = (
            DialogueTurnContext.from_mapping(raw_context)  # type: ignore[arg-type]
            if raw_context is not None
            else None
        )
        return cls(
            mode=mode,
            context_digest=raw_digest,
            context=context,
            inventory_settles_allowed=raw_inventory,
            version=value.get("version", BINDING_VERSION),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "version": self.version,
            "mode": self.mode.value,
            "context_digest": self.context_digest,
            "inventory_settles_allowed": self.inventory_settles_allowed,
        }
        if self.context is not None:
            result["context"] = self.context.to_mapping()
        return result

    def render_user_prompt(self, user_message: str) -> str:
        """Add readable bound context to this user suffix without touching system bytes."""

        if self.mode is not BindingMode.BOUND or self.context is None:
            return user_message
        projection = self.context.prompt_projection()
        lines = [
            "<dialogue_context>",
            f"你正在回复一张{projection['label']}。",
            f"卡片内容：{projection['title']}",
        ]
        evidence = projection["evidence_labels"]
        if isinstance(evidence, list) and evidence:
            lines.append(f"可读依据：{'；'.join(str(item) for item in evidence)}")
        lines.extend(["</dialogue_context>", "<user_message>", user_message, "</user_message>"])
        return "\n".join(lines)


def canonical_context_json(context: DialogueTurnContext) -> str:
    """Serialize the digest-covered canonical JSON with stable bytes."""

    return json.dumps(
        dict(context.canonical_payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_context_digest(context: DialogueTurnContext) -> str:
    """Compatibility helper for callers that do not need the binding wrapper."""

    return context.context_digest


def parse_dialogue_turn_context(value: Mapping[str, object]) -> DialogueTurnContext:
    """Compatibility parser with the strict value-object semantics."""

    return DialogueTurnContext.from_mapping(value)


def parse_dialogue_binding(value: Mapping[str, object]) -> DialogueTurnBinding:
    """Compatibility parser for durable payload hydration."""

    return DialogueTurnBinding.from_mapping(value)


__all__ = [
    "BINDING_VERSION",
    "BindingMode",
    "CONTEXT_VERSION",
    "DialogueBindingError",
    "DialogueTurnBinding",
    "DialogueTurnContext",
    "canonical_context_json",
    "compute_context_digest",
    "filter_evidence_labels",
    "parse_dialogue_binding",
    "parse_dialogue_turn_context",
]
