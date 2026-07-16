"""Code-defined, non-executable metadata for supported connection types."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from .types import ChatConnection

ConnectionCapability: TypeAlias = Literal["chat", "embedding"]
ConnectionCategory: TypeAlias = Literal["api_protocol", "local_runtime", "oauth"]
FieldInput: TypeAlias = Literal["text", "secret", "number", "select"]
DescriptorScalar: TypeAlias = str | int | float | bool


@dataclass(frozen=True)
class FieldDefinition:
    """Safe UI and validation metadata for one connection field."""

    name: str
    label: str
    input_type: FieldInput = "text"
    required: bool = False
    capabilities: tuple[ConnectionCapability, ...] = ("chat", "embedding")
    presets: tuple[str, ...] = ()
    help: str = ""
    placeholder: str = ""
    choices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Normalize every ordered metadata collection to an immutable tuple."""
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "presets", tuple(self.presets))
        object.__setattr__(self, "choices", tuple(self.choices))

    def applies_to(self, capability: str, preset: str) -> bool:
        """Return whether this field applies to the selected route and preset."""
        return capability in self.capabilities and (not self.presets or preset in self.presets)

    def public_descriptor(self) -> dict[str, object]:
        """Return JSON-safe rendering metadata without implementation objects."""
        return {
            "name": self.name,
            "label": self.label,
            "input_type": self.input_type,
            "required": self.required,
            "capabilities": list(self.capabilities),
            "presets": list(self.presets),
            "help": self.help,
            "placeholder": self.placeholder,
            "choices": list(self.choices),
        }


@dataclass(frozen=True)
class PresetDefinition:
    """Defaults and visibility metadata for a named connection preset."""

    id: str
    label: str
    capabilities: tuple[ConnectionCapability, ...]
    defaults: Mapping[str, DescriptorScalar] = field(default_factory=dict)
    help: str = ""

    def __post_init__(self) -> None:
        """Defensively freeze caller-owned defaults and capability order."""
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "defaults", MappingProxyType(dict(self.defaults)))

    def public_descriptor(self) -> dict[str, object]:
        """Return a JSON-safe preset descriptor."""
        return {
            "id": self.id,
            "label": self.label,
            "capabilities": list(self.capabilities),
            "defaults": dict(self.defaults),
            "help": self.help,
        }


@dataclass(frozen=True)
class ConnectionTypeDefinition:
    """Declarative metadata for one protocol, local runtime, or OAuth type."""

    id: str
    label: str
    category: ConnectionCategory
    capabilities: tuple[ConnectionCapability, ...]
    fields: tuple[FieldDefinition, ...]
    presets: tuple[PresetDefinition, ...] = ()
    help: str = ""

    def __post_init__(self) -> None:
        """Normalize every ordered metadata collection to an immutable tuple."""
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "presets", tuple(self.presets))

    def allowed_fields(self, capability: str, preset: str) -> tuple[str, ...]:
        """Return fields applicable to the selected route kind and preset."""
        return tuple(
            definition.name
            for definition in self.fields
            if definition.applies_to(capability, preset)
        )

    def public_descriptor(self) -> dict[str, object]:
        """Return JSON-safe metadata with no adapter classes or callables."""
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "capabilities": list(self.capabilities),
            "fields": [definition.public_descriptor() for definition in self.fields],
            "presets": [definition.id for definition in self.presets],
            "preset_definitions": [definition.public_descriptor() for definition in self.presets],
            "help": self.help,
        }


@dataclass(frozen=True)
class ConnectionTypeRegistry:
    """Immutable ordered registry of supported connection definitions."""

    definitions: tuple[ConnectionTypeDefinition, ...]

    def __post_init__(self) -> None:
        """Freeze the ordered definitions supplied by callers."""
        definitions = tuple(self.definitions)
        ids = tuple(definition.id for definition in definitions)
        if len(ids) != len(set(ids)):
            raise ValueError("connection type IDs must be unique")
        object.__setattr__(self, "definitions", definitions)

    def get(self, connection_type: str) -> ConnectionTypeDefinition | None:
        """Look up a connection type without raising for untrusted input."""
        return next(
            (definition for definition in self.definitions if definition.id == connection_type),
            None,
        )

    def definition(self, connection_type: str) -> ConnectionTypeDefinition:
        """Return a known connection type or raise ``KeyError``."""
        definition = self.get(connection_type)
        if definition is None:
            raise KeyError(connection_type)
        return definition

    def preset(self, connection_type: str, preset_id: str) -> PresetDefinition:
        """Return one preset definition or raise ``KeyError``."""
        definition = self.definition(connection_type)
        preset = next((item for item in definition.presets if item.id == preset_id), None)
        if preset is None:
            raise KeyError((connection_type, preset_id))
        return preset

    def presets_for(self, connection_type: str, capability: str) -> tuple[str, ...]:
        """Return preset IDs available for a connection type and route kind."""
        definition = self.get(connection_type)
        if definition is None or capability not in definition.capabilities:
            return ()
        return tuple(
            preset.id for preset in definition.presets if capability in preset.capabilities
        )

    def for_capability(self, capability: str) -> tuple[ConnectionTypeDefinition, ...]:
        """Return connection definitions that support a route kind, in registry order."""
        return tuple(
            definition for definition in self.definitions if capability in definition.capabilities
        )

    def public_descriptors(self) -> list[dict[str, object]]:
        """Return fresh JSON-safe public descriptors in stable registry order."""
        return [definition.public_descriptor() for definition in self.definitions]


def apply_preset_defaults(
    connection: ChatConnection,
    definition: PresetDefinition,
    touched_fields: frozenset[str],
) -> ChatConnection:
    """Fill blank untouched fields from a preset without mutating the connection."""
    updates = {
        key: value
        for key, value in definition.defaults.items()
        if key not in touched_fields and not str(getattr(connection, key, "")).strip()
    }
    # Preset keys are defined by this module and checked by the dataclass
    # constructor at runtime. ``replace`` cannot express dynamic keyword
    # correlation to MyPy, so the mapping alone crosses an ``Any`` boundary.
    return replace(connection, **cast("Any", updates))


def _field(
    name: str,
    label: str,
    *,
    input_type: FieldInput = "text",
    required: bool = False,
    capabilities: tuple[ConnectionCapability, ...] = ("chat", "embedding"),
    presets: tuple[str, ...] = (),
    help: str = "",
    placeholder: str = "",
    choices: tuple[str, ...] = (),
) -> FieldDefinition:
    return FieldDefinition(
        name=name,
        label=label,
        input_type=input_type,
        required=required,
        capabilities=capabilities,
        presets=presets,
        help=help,
        placeholder=placeholder,
        choices=choices,
    )


def _openai_compatible() -> ConnectionTypeDefinition:
    return ConnectionTypeDefinition(
        id="openai_compatible",
        label="OpenAI-compatible",
        category="api_protocol",
        capabilities=("chat", "embedding"),
        fields=(
            _field("preset", "Preset", input_type="select", required=True),
            _field("model", "Model", required=True, capabilities=("chat",)),
            _field("base_url", "Base URL", required=True),
            _field("credential", "API credential", input_type="secret", required=True),
            _field(
                "api_mode",
                "API mode",
                input_type="select",
                capabilities=("chat",),
                choices=("chat_completions", "responses"),
            ),
            _field(
                "reasoning_effort",
                "Reasoning effort",
                capabilities=("chat",),
                presets=("deepseek",),
            ),
            _field(
                "http_referer",
                "HTTP-Referer",
                capabilities=("chat",),
                presets=("openrouter",),
            ),
            _field(
                "x_title",
                "X-Title",
                capabilities=("chat",),
                presets=("openrouter",),
            ),
        ),
        presets=(
            PresetDefinition(
                id="openai",
                label="OpenAI",
                capabilities=("chat", "embedding"),
                defaults={
                    "base_url": "https://api.openai.com/v1",
                    "api_mode": "chat_completions",
                },
                help="OpenAI's official API endpoint.",
            ),
            PresetDefinition(
                id="deepseek",
                label="DeepSeek",
                capabilities=("chat",),
                defaults={
                    "model": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_mode": "chat_completions",
                    "reasoning_effort": "max",
                },
                help="DeepSeek chat through its OpenAI-compatible endpoint.",
            ),
            PresetDefinition(
                id="openrouter",
                label="OpenRouter",
                capabilities=("chat",),
                defaults={
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_mode": "chat_completions",
                    "x_title": "OpenBiliClaw",
                },
                help="OpenRouter chat routing and optional attribution headers.",
            ),
            PresetDefinition(
                id="custom",
                label="Custom gateway",
                capabilities=("chat", "embedding"),
                defaults={"api_mode": "chat_completions"},
                help="Any endpoint implementing the selected OpenAI-compatible API.",
            ),
        ),
        help="OpenAI Chat/Responses-compatible chat and embedding endpoints.",
    )


def _anthropic_compatible() -> ConnectionTypeDefinition:
    return ConnectionTypeDefinition(
        id="anthropic_compatible",
        label="Anthropic-compatible",
        category="api_protocol",
        capabilities=("chat",),
        fields=(
            _field(
                "preset",
                "Preset",
                input_type="select",
                required=True,
                capabilities=("chat",),
            ),
            _field("model", "Model", required=True, capabilities=("chat",)),
            _field("base_url", "Base URL", required=True, capabilities=("chat",)),
            _field(
                "credential",
                "API credential",
                input_type="secret",
                required=True,
                capabilities=("chat",),
            ),
        ),
        presets=(
            PresetDefinition(
                id="anthropic",
                label="Anthropic",
                capabilities=("chat",),
                defaults={
                    "model": "claude-sonnet-4-6",
                    "base_url": "https://api.anthropic.com",
                },
                help="Anthropic's official Messages API.",
            ),
            PresetDefinition(
                id="custom",
                label="Custom gateway",
                capabilities=("chat",),
                help="A gateway implementing the Anthropic Messages API.",
            ),
        ),
        help="Anthropic Messages-compatible chat endpoints.",
    )


def _gemini_api() -> ConnectionTypeDefinition:
    return ConnectionTypeDefinition(
        id="gemini_api",
        label="Gemini API",
        category="api_protocol",
        capabilities=("chat", "embedding"),
        fields=(
            _field("model", "Model", required=True, capabilities=("chat",)),
            _field("base_url", "API endpoint"),
            _field("credential", "API credential", input_type="secret", required=True),
        ),
        help="Google Gemini's native SDK/API for chat and embedding.",
    )


def _dashscope_api() -> ConnectionTypeDefinition:
    return ConnectionTypeDefinition(
        id="dashscope_api",
        label="DashScope API",
        category="api_protocol",
        capabilities=("embedding",),
        fields=(
            _field("base_url", "API endpoint", capabilities=("embedding",)),
            _field(
                "credential",
                "API credential",
                input_type="secret",
                required=True,
                capabilities=("embedding",),
            ),
        ),
        help="Alibaba DashScope native multimodal embedding API.",
    )


def _ollama() -> ConnectionTypeDefinition:
    return ConnectionTypeDefinition(
        id="ollama",
        label="Ollama",
        category="local_runtime",
        capabilities=("chat", "embedding"),
        fields=(
            _field("model", "Model", required=True, capabilities=("chat",)),
            _field("base_url", "Base URL", required=True),
            _field(
                "num_ctx",
                "Context window",
                input_type="number",
                capabilities=("chat",),
                help="Zero uses the Ollama server default.",
            ),
        ),
        help="A local or explicitly addressed Ollama runtime.",
    )


def _codex_oauth() -> ConnectionTypeDefinition:
    return ConnectionTypeDefinition(
        id="codex_oauth",
        label="Codex OAuth",
        category="oauth",
        capabilities=("chat",),
        fields=(
            _field("model", "Model", required=True, capabilities=("chat",)),
            _field(
                "credential",
                "Imported credential",
                input_type="select",
                required=True,
                capabilities=("chat",),
                choices=("codex",),
                help="References imported Codex credentials; no token is serialized.",
            ),
        ),
        help="Imported Codex OAuth credentials restricted to the official OpenAI endpoint.",
    )


@lru_cache(maxsize=1)
def connection_type_registry() -> ConnectionTypeRegistry:
    """Return the immutable built-in connection type registry."""
    return ConnectionTypeRegistry(
        definitions=(
            _openai_compatible(),
            _anthropic_compatible(),
            _gemini_api(),
            _dashscope_api(),
            _ollama(),
            _codex_oauth(),
        )
    )
