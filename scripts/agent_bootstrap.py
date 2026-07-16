#!/usr/bin/env python3
"""Agent-driven bootstrap script for OpenBiliClaw.

This script is intended to be invoked by an AI coding agent (Claude Code,
Codex CLI, OpenClaw, Cursor, etc.) after the user pastes the README "Agent
deployment prompt" into the agent. The agent parses the prompt, runs this
script with the appropriate flags, then handles any interactive follow-ups
(missing API key, missing Bilibili cookie, or explicit init source decisions)
that the script reports.

The script is machine-friendly by default, with an explicit human installer mode:
- emits structured JSON status lines prefixed with ``BOOTSTRAP_STATUS:``
- exits 0 on success, non-zero on failure
- prompts only when ``--interactive-confirm`` is supplied; otherwise all input
  remains flag-driven for agent automation

Supported flows:
1. Docker path (preferred if Docker + docker compose are available)
2. Local Python path (uv preferred, pip fallback)
3. Reuse secrets from an existing OpenBiliClaw checkout

Typical agent workflow:

    1. Detect or clone repo into target directory.
    2. Run ``python scripts/agent_bootstrap.py --mode auto`` (add
       ``--reuse-from <path>`` when the user already has a working install).
    3. Parse ``BOOTSTRAP_STATUS`` JSON lines to decide next steps.
    4. If the final status says ``missing_llm_key`` or ``missing_cookie``,
       ask the user for the value and re-run with ``--llm-api-key`` or
       ``--bilibili-cookie``.
    5. Poll the emitted ``Health URL`` to confirm the service is ready.

All secrets accepted via flags are written directly to ``config.toml`` and
``data/bilibili_cookie.json``. Nothing is uploaded off the machine.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Constants

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420
DEFAULT_REPO_URL = "https://github.com/whiteguo233/OpenBiliClaw.git"
DEFAULT_HEALTH_PATH = "/api/health"
HEALTH_TIMEOUT_SECONDS = 90
HEALTH_POLL_INTERVAL = 2.0
LOCAL_NO_PROXY_HOSTS = ("localhost", "127.0.0.1", "::1")
DOCKER_CONTAINER_NAME = "openbiliclaw-backend"
DOCKER_RUNTIME_ROOT = "/app/runtime"
DOCKER_OLLAMA_BASE_URL = "http://ollama:11434/v1"
LOCAL_OLLAMA_BASE_URLS = (
    "http://localhost:11434",
    "http://localhost:11434/v1",
    "http://127.0.0.1:11434",
    "http://127.0.0.1:11434/v1",
)
DEFAULT_BILIBILI_FAVORITE_LIMIT = 300
DEFAULT_BILIBILI_FOLLOW_LIMIT = 100
USER_DATA_ONLY_ENTRIES = {
    "config.toml",
    "config.local.toml",
    "data",
    "logs",
    "openbiliclaw.lock",
}

SUPPORTED_PROVIDERS = (
    "openai",
    "claude",
    "gemini",
    "deepseek",
    "ollama",
    "openrouter",
    "openai_compatible",
)
SUPPORTED_CONNECTION_TYPES = (
    "openai_compatible",
    "anthropic_compatible",
    "gemini_api",
    "ollama",
    "codex_oauth",
)
LEGACY_PROVIDER_CONNECTIONS: dict[str, tuple[str, str]] = {
    "deepseek": ("openai_compatible", "deepseek"),
    "openai": ("openai_compatible", "openai"),
    "openrouter": ("openai_compatible", "openrouter"),
    "openai_compatible": ("openai_compatible", "custom"),
    "claude": ("anthropic_compatible", "anthropic"),
    "gemini": ("gemini_api", ""),
    "ollama": ("ollama", ""),
}
CONNECTION_PRESETS: dict[str, tuple[str, ...]] = {
    "openai_compatible": ("openai", "deepseek", "openrouter", "custom"),
    "anthropic_compatible": ("anthropic", "custom"),
    "gemini_api": (),
    "ollama": (),
    "codex_oauth": (),
}
DEFAULT_CREDENTIAL_ENVS: dict[tuple[str, str], str] = {
    ("openai_compatible", "openai"): "OPENAI_API_KEY",
    ("openai_compatible", "deepseek"): "DEEPSEEK_API_KEY",
    ("openai_compatible", "openrouter"): "OPENROUTER_API_KEY",
    ("anthropic_compatible", "anthropic"): "ANTHROPIC_API_KEY",
    ("gemini_api", ""): "GEMINI_API_KEY",
}
REMOTE_PROVIDERS = (
    "openai",
    "claude",
    "gemini",
    "deepseek",
    "openrouter",
    "openai_compatible",
)

# Providers whose backend has no embeddings endpoint. When a user picks
# one of these as the primary LLM and doesn't explicitly configure
# embedding, we auto-wire local Ollama bge-m3 so the install actually
# pulls the embedding model (otherwise embeddings silently fall back at
# runtime to whatever the registry can find — see registry.py
# build_embedding_service).
PROVIDERS_WITHOUT_EMBED = ("claude", "deepseek", "openrouter")


def ensure_local_no_proxy() -> str:
    """Keep localhost backend checks out of user/global HTTP proxies."""

    parts: list[str] = []
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key, "")
        for item in raw.split(","):
            value = item.strip()
            if value and value not in parts:
                parts.append(value)
    for host in LOCAL_NO_PROXY_HOSTS:
        if host not in parts:
            parts.append(host)
    value = ",".join(parts)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    return value


def resolve_connection_selection(
    *,
    provider: str | None = None,
    connection_type: str | None = None,
    preset: str | None = None,
) -> tuple[str, str]:
    """Resolve canonical Chat type/preset while retaining provider aliases."""
    legacy = LEGACY_PROVIDER_CONNECTIONS.get((provider or "").strip().lower())
    resolved_type = (connection_type or "").strip().lower()
    resolved_preset = (preset or "").strip().lower()
    if legacy is not None:
        if resolved_type and resolved_type != legacy[0]:
            raise ValueError("--provider conflicts with --connection-type")
        if resolved_preset and resolved_preset != legacy[1]:
            raise ValueError("--provider conflicts with --preset")
        resolved_type = resolved_type or legacy[0]
        resolved_preset = resolved_preset or legacy[1]
    if not resolved_type:
        resolved_type, default_preset = LEGACY_PROVIDER_CONNECTIONS["deepseek"]
        resolved_preset = resolved_preset or default_preset
    if resolved_type not in SUPPORTED_CONNECTION_TYPES:
        raise ValueError(f"unknown connection type: {resolved_type}")
    allowed = CONNECTION_PRESETS[resolved_type]
    if allowed:
        if not resolved_preset:
            resolved_preset = "custom" if resolved_type == "openai_compatible" else "anthropic"
        if resolved_preset not in allowed:
            raise ValueError(
                f"preset {resolved_preset!r} is not valid for connection type {resolved_type!r}"
            )
    elif resolved_preset:
        raise ValueError(f"connection type {resolved_type!r} does not use presets")
    return resolved_type, resolved_preset


# Mirror of cli.py's _OPENAI_COMPAT_PRESETS for non-interactive (AI agent
# driven) installs. Keep the model defaults in sync with cli.py — when
# updating one, update the other. Each preset implies provider="openai"
# (the universal Bearer-auth + /v1/chat/completions client).
LLM_PRESETS: dict[str, dict[str, str]] = {
    "kimi": {
        "base_url": "https://api.moonshot.ai/v1",
        "model": "kimi-k2.6",
    },
    "minimax": {
        "base_url": "https://api.minimax.io/v1",
        "model": "MiniMax-M2.7",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
    },
    "yi": {
        "base_url": "https://api.lingyiwanwu.com/v1",
        "model": "yi-medium",
    },
    "self-hosted": {
        "base_url": "http://localhost:8000/v1",
        "model": "",  # user must specify
    },
    "relay": {
        "base_url": "",  # user must specify
        "model": "gpt-5-nano",
    },
    "azure": {
        "base_url": "",  # user must specify (per-deployment URL)
        "model": "",  # deployment name
    },
    "custom": {
        "base_url": "",
        "model": "",
    },
}

HUMAN_LLM_MENU: tuple[tuple[str, str, str], ...] = (
    ("openai_compatible", "OpenAI-compatible", "OpenAI / DeepSeek / OpenRouter / 自定义网关"),
    ("anthropic_compatible", "Anthropic-compatible", "Anthropic / 自定义 Messages 网关"),
    ("gemini_api", "Gemini API", "Google 原生 API"),
    ("ollama", "Ollama", "本地服务"),
    ("codex_oauth", "Codex OAuth", "导入本机登录"),
)

HUMAN_PRESET_MENUS: dict[str, tuple[tuple[str, str], ...]] = {
    "openai_compatible": (
        ("deepseek", "DeepSeek ★默认"),
        ("openai", "OpenAI"),
        ("openrouter", "OpenRouter"),
        ("custom", "自定义 OpenAI-compatible 网关"),
    ),
    "anthropic_compatible": (
        ("anthropic", "Anthropic ★默认"),
        ("custom", "自定义 Anthropic-compatible 网关"),
    ),
}

HUMAN_MODEL_DEFAULTS: dict[tuple[str, str], str] = {
    ("openai_compatible", "deepseek"): "deepseek-v4-flash",
    ("openai_compatible", "openai"): "gpt-5-nano",
    ("openai_compatible", "openrouter"): "openai/gpt-5-nano",
    ("openai_compatible", "custom"): "gpt-5-nano",
    ("anthropic_compatible", "anthropic"): "claude-sonnet-4-6",
    ("gemini_api", ""): "gemini-2.5-flash",
    ("ollama", ""): "qwen2.5:7b",
    ("codex_oauth", ""): "gpt-5-nano",
}


# ---------------------------------------------------------------------------
# Immutable status + exit codes


@dataclass(frozen=True)
class BootstrapResult:
    """Immutable result emitted to the agent."""

    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InitConfirmationAnswers:
    """Explicit user decisions required before auto-init may run."""

    embedding_provider: str
    embedding_model: str
    xhs: bool
    douyin: bool
    youtube: bool
    cookie_mode: str
    bilibili_favorite_limit: int = DEFAULT_BILIBILI_FAVORITE_LIMIT
    bilibili_follow_limit: int = DEFAULT_BILIBILI_FOLLOW_LIMIT
    bilibili_cookie: str = ""


@dataclass(frozen=True)
class HumanInstallAnswers:
    """Full human one-line installer choices collected before bootstrap work."""

    provider: str = ""
    connection_type: str = ""
    preset: str = ""
    llm_api_key: str = ""
    llm_base_url: str | None = None
    llm_model: str | None = None
    embedding_provider: str = "ollama"
    embedding_model: str = "bge-m3"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    xhs: bool = False
    douyin: bool = False
    youtube: bool = False
    cookie_mode: str = "extension"
    bilibili_cookie: str = ""
    bilibili_favorite_limit: int = DEFAULT_BILIBILI_FAVORITE_LIMIT
    bilibili_follow_limit: int = DEFAULT_BILIBILI_FOLLOW_LIMIT

    def __post_init__(self) -> None:
        if self.provider and self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"unknown provider: {self.provider}")
        if self.connection_type:
            connection_type, preset = resolve_connection_selection(
                connection_type=self.connection_type,
                preset=self.preset,
            )
        else:
            connection_type, preset = resolve_connection_selection(provider=self.provider or None)
        object.__setattr__(self, "connection_type", connection_type)
        object.__setattr__(self, "preset", preset)


def emit(result: BootstrapResult) -> None:
    """Emit a machine-parseable status line for the caller agent."""

    payload = {
        "status": result.status,
        "message": result.message,
        "details": result.details,
    }
    print(f"BOOTSTRAP_STATUS: {json.dumps(payload, ensure_ascii=False)}")
    sys.stdout.flush()


def info(message: str) -> None:
    """Human-readable log line that sits above BOOTSTRAP_STATUS events."""

    print(f"[bootstrap] {message}")
    sys.stdout.flush()


def mask_secret_for_prompt(value: str) -> str:
    """Describe whether a secret is present without revealing its value."""

    return "set, press Enter to reuse" if value.strip() else "not set"


def ensure_human_wizard_tty(input_func: Any) -> None:
    """Refuse explicit human prompts when no terminal is attached."""

    if input_func is input and not sys.stdin.isatty():
        raise RuntimeError("interactive confirmation requires a terminal")


def resolve_human_llm_choice(raw: str) -> str | None:
    """Resolve a menu number or alias into a canonical connection type."""

    value = raw.strip().lower()
    if not value:
        return "openai_compatible"
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(HUMAN_LLM_MENU):
            return HUMAN_LLM_MENU[index - 1][0]
        return None
    aliases = {
        "deepseek": "openai_compatible",
        "openai": "openai_compatible",
        "openrouter": "openai_compatible",
        "relay": "openai_compatible",
        "oneapi": "openai_compatible",
        "openai-compatible": "openai_compatible",
        "openai_compatible": "openai_compatible",
        "openai-compat": "openai_compatible",
        "compat": "openai_compatible",
        "claude": "anthropic_compatible",
        "anthropic": "anthropic_compatible",
        "anthropic-compatible": "anthropic_compatible",
        "gemini": "gemini_api",
        "codex": "codex_oauth",
    }
    menu_types = {key for key, _label, _help in HUMAN_LLM_MENU}
    return aliases.get(value, value if value in menu_types else None)


def resolve_human_preset_choice(connection_type: str, raw: str) -> str | None:
    """Resolve the preset only after its connection type is selected."""
    options = HUMAN_PRESET_MENUS.get(connection_type, ())
    if not options:
        return ""
    value = raw.strip().lower()
    if not value:
        return options[0][0]
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(options):
            return options[index - 1][0]
        return None
    aliases = {
        "relay": "custom",
        "gateway": "custom",
        "oneapi": "custom",
        "claude": "anthropic",
    }
    resolved = aliases.get(value, value)
    return resolved if resolved in {preset for preset, _label in options} else None


def _prompt_required(input_func: Any, prompt: str, *, default: str = "") -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = str(input_func(f"{prompt}{suffix}: ")).strip() or default
        if value:
            return value
        print("This value is required.")


def _prompt_optional(input_func: Any, prompt: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return str(input_func(f"{prompt}{suffix}: ")).strip() or default


def _prompt_secret(
    secret_input_func: Any,
    prompt: str,
    *,
    existing: str = "",
    required: bool = True,
) -> str:
    import getpass

    while True:
        suffix = f" ({mask_secret_for_prompt(existing)})" if existing else ""
        try:
            value = str(secret_input_func(f"{prompt}{suffix}: ")).strip()
        except getpass.GetPassWarning as exc:
            raise RuntimeError(f"cannot disable terminal echo for secret prompt: {exc}") from exc
        if value:
            return value
        if existing:
            return ""
        if not required:
            return ""
        print(f"{prompt} is required.")


def read_secret_no_echo(prompt: str) -> str:
    import getpass
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning as exc:
            raise RuntimeError(f"cannot disable terminal echo for secret prompt: {exc}") from exc


def collect_human_llm_config(
    *,
    input_func: Any = input,
    secret_input_func: Any | None = None,
    existing_provider: str = "deepseek",
    existing_api_key: str = "",
    existing_base_url: str = "",
    existing_model: str = "",
) -> HumanInstallAnswers:
    """Collect connection type first, then preset/OAuth and descriptor fields."""

    secret_input_func = secret_input_func or read_secret_no_echo
    print("")
    print("Choose the Chat connection type first.")
    for index, (_connection_type, label, help_text) in enumerate(HUMAN_LLM_MENU, start=1):
        print(f"{index}. {label} ({help_text})")

    connection_type: str | None = None
    while connection_type is None:
        connection_type = resolve_human_llm_choice(
            str(input_func("Connection type [1 OpenAI-compatible]: "))
        )
        if connection_type is None:
            print("Unknown connection type. Please choose a number from the menu.")

    preset = ""
    preset_options = HUMAN_PRESET_MENUS.get(connection_type, ())
    if preset_options:
        print("")
        print(f"{connection_type} presets")
        for index, (_preset, label) in enumerate(preset_options, start=1):
            print(f"{index}. {label}")
        selected_preset: str | None = None
        while selected_preset is None:
            selected_preset = resolve_human_preset_choice(
                connection_type,
                str(input_func(f"Preset [1 {preset_options[0][0]}]: ")),
            )
            if selected_preset is None:
                print("Unknown preset. Please choose a number from the menu.")
        preset = selected_preset

    try:
        existing_type, existing_preset = resolve_connection_selection(
            provider=existing_provider,
        )
    except ValueError:
        existing_type, existing_preset = "", ""
    same_selection = connection_type == existing_type and preset == existing_preset
    model_default = existing_model if same_selection else ""
    model_default = model_default or HUMAN_MODEL_DEFAULTS.get((connection_type, preset), "")
    model = _prompt_required(
        input_func,
        f"{connection_type} chat model",
        default=model_default,
    )

    base_url: str | None = None
    if preset == "custom" and connection_type in {
        "openai_compatible",
        "anthropic_compatible",
    }:
        base_url = _prompt_required(
            input_func,
            f"{connection_type} Base URL",
            default=existing_base_url if same_selection else "",
        )

    api_key = ""
    if connection_type not in {"ollama", "codex_oauth"}:
        api_key = _prompt_secret(
            secret_input_func,
            f"{connection_type} API Key",
            existing=existing_api_key if same_selection else "",
        )
    return HumanInstallAnswers(
        connection_type=connection_type,
        preset=preset,
        llm_api_key=api_key,
        llm_base_url=base_url,
        llm_model=model,
    )


def confirmation_answers_to_bootstrap_args(answers: InitConfirmationAnswers) -> list[str]:
    """Convert interactive answers to the same explicit flags agents pass."""

    args = [
        "--embedding-provider",
        answers.embedding_provider,
        "--embedding-model",
        answers.embedding_model,
        "--yes-xhs" if answers.xhs else "--no-xhs",
        "--yes-douyin" if answers.douyin else "--no-douyin",
        "--yes-youtube" if answers.youtube else "--no-youtube",
        "--bilibili-favorite-limit",
        str(max(0, int(answers.bilibili_favorite_limit))),
        "--bilibili-follow-limit",
        str(max(0, int(answers.bilibili_follow_limit))),
    ]
    if answers.cookie_mode == "manual" and answers.bilibili_cookie:
        args.extend(["--bilibili-cookie", answers.bilibili_cookie])
    return args


def _ask_yes_no(
    input_func: Any,
    prompt: str,
    *,
    default: bool = False,
) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = str(input_func(f"{prompt} [{suffix}]: ")).strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "1", "true", "是", "好", "同意"}


def _ask_non_negative_int(
    input_func: Any,
    prompt: str,
    *,
    default: int,
) -> int:
    raw = str(input_func(f"{prompt} [{default}]: ")).strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def collect_interactive_confirmations(input_func: Any | None = input) -> InitConfirmationAnswers:
    """Ask the user for init decisions in human-run installer flows."""

    if input_func is None or (input_func is input and not sys.stdin.isatty()):
        raise RuntimeError("interactive confirmation requires a terminal")

    print("")
    print("OpenBiliClaw init choices")
    print("Embedding default: local Ollama bge-m3 (free/offline/no extra API key).")
    embedding_choice = str(
        input_func("Embedding provider [ollama] (enter to accept default): ")
    ).strip()
    embedding_provider = embedding_choice or "ollama"
    model_default = "bge-m3" if embedding_provider == "ollama" else ""
    embedding_model = (
        str(input_func(f"Embedding model [{model_default}] (enter to accept default): ")).strip()
        or model_default
    )

    print("")
    print(
        "Bilibili init signal limits default to 300 favorites / 100 follows; "
        "enter 0 to skip one signal."
    )
    bilibili_favorite_limit = _ask_non_negative_int(
        input_func,
        "Max Bilibili favorites to import during init",
        default=DEFAULT_BILIBILI_FAVORITE_LIMIT,
    )
    bilibili_follow_limit = _ask_non_negative_int(
        input_func,
        "Max Bilibili followed creators to import during init",
        default=DEFAULT_BILIBILI_FOLLOW_LIMIT,
    )

    print("")
    print("Optional source data is disabled by default unless you explicitly opt in.")
    xhs = _ask_yes_no(
        input_func,
        "Include Xiaohongshu likes/favorites in the initial profile?",
        default=False,
    )
    douyin = _ask_yes_no(
        input_func,
        "Include Douyin post/favorite/like/follow data in the initial profile?",
        default=False,
    )
    youtube = _ask_yes_no(
        input_func,
        "Include YouTube history/subscriptions/likes in the initial profile?",
        default=False,
    )

    print("")
    print("Bilibili auth default: browser extension sync.")
    cookie_mode_raw = (
        str(input_func("Bilibili cookie source: extension/manual/existing [extension]: "))
        .strip()
        .lower()
    )
    cookie_mode = cookie_mode_raw or "extension"
    bilibili_cookie = ""
    if cookie_mode == "manual":
        bilibili_cookie = str(input_func("Paste Bilibili Cookie header: ")).strip()
    elif cookie_mode not in {"extension", "existing"}:
        cookie_mode = "extension"

    return InitConfirmationAnswers(
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        xhs=xhs,
        douyin=douyin,
        youtube=youtube,
        cookie_mode=cookie_mode,
        bilibili_favorite_limit=bilibili_favorite_limit,
        bilibili_follow_limit=bilibili_follow_limit,
        bilibili_cookie=bilibili_cookie,
    )


def _collect_human_embedding_config(
    input_func: Any,
    secret_input_func: Any,
) -> tuple[str, str, str | None, str | None]:
    print("")
    print("Embedding provider")
    print("1. Local Ollama bge-m3 ★default")
    print("2. Gemini embedding")
    print("3. Disable embedding")
    print("4. Custom OpenAI-compatible embedding")
    print("5. Advanced provider")
    choice = str(input_func("Embedding provider [1 Ollama bge-m3]: ")).strip().lower()

    if choice in {"", "1", "ollama"}:
        return "ollama", "bge-m3", None, None
    if choice in {"2", "gemini"}:
        api_key = _prompt_secret(secret_input_func, "Gemini embedding API Key")
        model = _prompt_optional(
            input_func,
            "Gemini embedding model",
            default="gemini-embedding-001",
        )
        return "gemini", model, None, api_key
    if choice in {"3", "disable", "disabled", "none", "no", "off"}:
        return "", "", None, None
    if choice in {"4", "openai_compatible", "openai-compatible", "compat"}:
        base_url = _prompt_required(input_func, "Embedding OpenAI-compatible Base URL")
        api_key = _prompt_secret(secret_input_func, "Embedding OpenAI-compatible API Key")
        model = _prompt_required(input_func, "Embedding model")
        return "openai_compatible", model, base_url, api_key

    provider = _prompt_required(input_func, "Embedding provider name", default=choice)
    while provider not in SUPPORTED_PROVIDERS:
        print("Unknown embedding provider.")
        provider = _prompt_required(input_func, "Embedding provider name")
    model = _prompt_required(input_func, "Embedding model")
    base_url = _prompt_optional(input_func, "Embedding base URL")
    api_key = _prompt_secret(secret_input_func, "Embedding API Key", required=False)
    return provider, model, base_url or None, api_key or None


def _collect_human_cookie_config(
    input_func: Any,
    secret_input_func: Any,
) -> tuple[str, str]:
    print("")
    print("Bilibili auth default: browser extension sync.")
    raw = (
        str(input_func("Bilibili cookie source: extension/manual/existing [extension]: "))
        .strip()
        .lower()
    )
    cookie_mode = raw or "extension"
    if cookie_mode in {"manual", "paste"}:
        cookie = _prompt_secret(secret_input_func, "Bilibili Cookie")
        return "manual", cookie
    if cookie_mode in {"existing", "reuse"}:
        return "existing", ""
    return "extension", ""


def collect_human_install_wizard(
    *,
    input_func: Any = input,
    secret_input_func: Any | None = None,
    existing_provider: str = "deepseek",
    existing_api_key: str = "",
    existing_base_url: str = "",
    existing_model: str = "",
) -> HumanInstallAnswers:
    """Collect full human one-line installer choices before bootstrap starts."""

    ensure_human_wizard_tty(input_func)
    secret_input_func = secret_input_func or read_secret_no_echo
    llm = collect_human_llm_config(
        input_func=input_func,
        secret_input_func=secret_input_func,
        existing_provider=existing_provider,
        existing_api_key=existing_api_key,
        existing_base_url=existing_base_url,
        existing_model=existing_model,
    )
    embedding_provider, embedding_model, embedding_base_url, embedding_api_key = (
        _collect_human_embedding_config(input_func, secret_input_func)
    )

    print("")
    print(
        "Bilibili init signal limits default to 300 favorites / 100 follows; "
        "enter 0 to skip one signal."
    )
    favorite_limit = _ask_non_negative_int(
        input_func,
        "Max Bilibili favorites to import during init",
        default=DEFAULT_BILIBILI_FAVORITE_LIMIT,
    )
    follow_limit = _ask_non_negative_int(
        input_func,
        "Max Bilibili followed creators to import during init",
        default=DEFAULT_BILIBILI_FOLLOW_LIMIT,
    )

    print("")
    print("Optional source data is disabled by default unless you explicitly opt in.")
    xhs = _ask_yes_no(
        input_func,
        "Include Xiaohongshu likes/favorites in the initial profile?",
        default=False,
    )
    douyin = _ask_yes_no(
        input_func,
        "Include Douyin post/favorite/like/follow data in the initial profile?",
        default=False,
    )
    youtube = _ask_yes_no(
        input_func,
        "Include YouTube history/subscriptions/likes in the initial profile?",
        default=False,
    )
    cookie_mode, bilibili_cookie = _collect_human_cookie_config(input_func, secret_input_func)

    return HumanInstallAnswers(
        provider=llm.provider,
        connection_type=llm.connection_type,
        preset=llm.preset,
        llm_api_key=llm.llm_api_key,
        llm_base_url=llm.llm_base_url,
        llm_model=llm.llm_model,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_base_url=embedding_base_url,
        embedding_api_key=embedding_api_key,
        xhs=xhs,
        douyin=douyin,
        youtube=youtube,
        cookie_mode=cookie_mode,
        bilibili_cookie=bilibili_cookie,
        bilibili_favorite_limit=favorite_limit,
        bilibili_follow_limit=follow_limit,
    )


def apply_confirmation_answers_to_args(
    args: argparse.Namespace,
    answers: InitConfirmationAnswers,
) -> None:
    """Mutate parsed args with interactive choices where flags were omitted."""

    if args.embedding_provider is None:
        args.embedding_provider = answers.embedding_provider
    if args.embedding_model is None:
        args.embedding_model = answers.embedding_model
    if not args.yes_xhs and not args.no_xhs:
        args.yes_xhs = answers.xhs
        args.no_xhs = not answers.xhs
    if not args.yes_douyin and not args.no_douyin:
        args.yes_douyin = answers.douyin
        args.no_douyin = not answers.douyin
    if not args.yes_youtube and not args.no_youtube:
        args.yes_youtube = answers.youtube
        args.no_youtube = not answers.youtube
    if args.bilibili_favorite_limit is None:
        args.bilibili_favorite_limit = answers.bilibili_favorite_limit
    if args.bilibili_follow_limit is None:
        args.bilibili_follow_limit = answers.bilibili_follow_limit
    if answers.cookie_mode == "manual" and answers.bilibili_cookie and not args.bilibili_cookie:
        args.bilibili_cookie = answers.bilibili_cookie
    if answers.cookie_mode == "extension":
        args.wait_for_extension_cookie = True


def apply_human_install_answers_to_args(
    args: argparse.Namespace,
    answers: HumanInstallAnswers,
) -> None:
    """Mutate parsed args with full human installer choices."""

    if args.provider is None and args.connection_type is None:
        args.connection_type = answers.connection_type
        args.preset = answers.preset or None
    if args.llm_api_key is None and answers.llm_api_key:
        args.llm_api_key = answers.llm_api_key
    if args.llm_base_url is None and answers.llm_base_url is not None:
        args.llm_base_url = answers.llm_base_url
    if args.llm_model is None and answers.llm_model is not None:
        args.llm_model = answers.llm_model
    if args.embedding_provider is None:
        args.embedding_provider = answers.embedding_provider
    if args.embedding_model is None:
        args.embedding_model = answers.embedding_model
    if args.embedding_base_url is None and answers.embedding_base_url is not None:
        args.embedding_base_url = answers.embedding_base_url
    if args.embedding_api_key is None and answers.embedding_api_key:
        args.embedding_api_key = answers.embedding_api_key
    if not args.yes_xhs and not args.no_xhs:
        args.yes_xhs = answers.xhs
        args.no_xhs = not answers.xhs
    if not args.yes_douyin and not args.no_douyin:
        args.yes_douyin = answers.douyin
        args.no_douyin = not answers.douyin
    if not args.yes_youtube and not args.no_youtube:
        args.yes_youtube = answers.youtube
        args.no_youtube = not answers.youtube
    if args.bilibili_favorite_limit is None:
        args.bilibili_favorite_limit = answers.bilibili_favorite_limit
    if args.bilibili_follow_limit is None:
        args.bilibili_follow_limit = answers.bilibili_follow_limit
    if answers.cookie_mode == "manual" and answers.bilibili_cookie and not args.bilibili_cookie:
        args.bilibili_cookie = answers.bilibili_cookie
    args.wait_for_extension_cookie = answers.cookie_mode == "extension"


# ---------------------------------------------------------------------------
# CLI argument parsing


def build_arg_parser() -> argparse.ArgumentParser:
    """Return the immutable argument parser."""

    parser = argparse.ArgumentParser(
        description="Automated OpenBiliClaw bootstrap for AI coding agents.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Target project directory (default: current directory).",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "docker", "local"),
        default="auto",
        help="Deployment mode. 'auto' prefers Docker when available.",
    )
    parser.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help="Git repository URL to clone when project-dir is empty.",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Git branch to check out on fresh clones (default: main).",
    )
    parser.add_argument(
        "--reuse-from",
        default=None,
        help="Path to an existing OpenBiliClaw checkout whose secrets (API keys + Bilibili cookie) should be copied into the new install.",
    )
    parser.add_argument(
        "--connection-type",
        choices=SUPPORTED_CONNECTION_TYPES,
        default=None,
        help="Canonical Chat connection type (protocol, local runtime, or OAuth).",
    )
    parser.add_argument(
        "--preset",
        default=None,
        help="Preset within the selected connection type (for example deepseek or openai).",
    )
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=None,
        help="Deprecated provider alias; mapped to --connection-type plus --preset.",
    )
    parser.add_argument(
        "--llm-api-key",
        default=None,
        help="LLM API key for the (current or overridden) provider. Stored in config.toml.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=None,
        help=(
            "Override the chosen provider's base_url. Required for OpenAI-"
            "compatible gateways (Azure / vLLM / LMStudio / OneAPI / 任意 "
            "OpenAI 兼容服务). The 'openai' provider is a protocol family, "
            "not a vendor — point it anywhere that speaks /v1/chat/completions."
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Override the chosen provider's chat/generation model.",
    )
    parser.add_argument(
        "--llm-preset",
        choices=(
            "kimi",
            "minimax",
            "qwen",
            "zhipu",
            "yi",
            "self-hosted",
            "relay",
            "azure",
            "custom",
        ),
        default=None,
        help=(
            "Shortcut for OpenAI-protocol-compatible services. Picks the "
            "preset's canonical Base URL + default model so AI-agent-driven "
            "installs don't have to remember each vendor's endpoint. "
            "Implies --provider=openai. --llm-base-url / --llm-model still "
            "override the preset on a per-field basis. Presets: "
            "kimi (Moonshot), minimax (M2.7), qwen (DashScope), zhipu (GLM), "
            "yi (零一万物), self-hosted (vLLM/LMStudio), relay (中转站/OneAPI), "
            "azure (Azure OpenAI), custom (no preset)."
        ),
    )
    parser.add_argument(
        "--embedding-provider",
        default=None,
        choices=("", *SUPPORTED_PROVIDERS),
        help=(
            "Embedding provider override. Empty string = disable embedding. "
            "Use 'ollama' for local bge-m3, or pick any "
            "supported provider for a dedicated embedding endpoint."
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding model name (e.g. bge-m3, text-embedding-3-small).",
    )
    parser.add_argument(
        "--embedding-base-url",
        default=None,
        help="Custom base_url for the single legacy --embedding-provider alias.",
    )
    parser.add_argument(
        "--embedding-api-key",
        default=None,
        help="Custom API key for the configured embedding provider(s).",
    )
    parser.add_argument(
        "--embedding-endpoint",
        action="append",
        default=None,
        metavar="TYPE[:PRESET]=BASE_URL",
        help=(
            "Ordered embedding endpoint. Repeat to add up to ten providers; "
            "all entries share --embedding-model and route-wide settings."
        ),
    )
    parser.add_argument(
        "--bilibili-cookie",
        default=None,
        help="Bilibili cookie string. Stored in config.toml and data/bilibili_cookie.json.",
    )
    parser.add_argument(
        "--bilibili-favorite-limit",
        type=int,
        default=None,
        help=(
            "Max Bilibili favorite events imported by auto-init. "
            "Default is openbiliclaw init's built-in 300; 0 skips favorites."
        ),
    )
    parser.add_argument(
        "--bilibili-follow-limit",
        type=int,
        default=None,
        help=(
            "Max Bilibili follow events imported by auto-init. "
            "Default is openbiliclaw init's built-in 300; 0 skips follows."
        ),
    )
    xhs_group = parser.add_mutually_exclusive_group()
    xhs_group.add_argument(
        "--yes-xhs",
        action="store_true",
        help=(
            "Explicitly opt in to Xiaohongshu liked/favorite data during auto-init. "
            "AI agents should only pass this after asking the user."
        ),
    )
    xhs_group.add_argument(
        "--no-xhs",
        action="store_true",
        help=(
            "Explicitly skip Xiaohongshu liked/favorite data during auto-init. "
            "Use this when the user says no or has not opted in."
        ),
    )
    douyin_group = parser.add_mutually_exclusive_group()
    douyin_group.add_argument(
        "--yes-douyin",
        action="store_true",
        help=(
            "Explicitly opt in to Douyin post/favorite/like/follow data during auto-init. "
            "AI agents should only pass this after asking the user."
        ),
    )
    douyin_group.add_argument(
        "--no-douyin",
        action="store_true",
        help=(
            "Explicitly skip Douyin data during auto-init. Use this when the user says no "
            "or has not opted in."
        ),
    )
    youtube_group = parser.add_mutually_exclusive_group()
    youtube_group.add_argument(
        "--yes-youtube",
        action="store_true",
        help=(
            "Explicitly opt in to YouTube history/subscription/like data during auto-init. "
            "AI agents should only pass this after asking the user."
        ),
    )
    youtube_group.add_argument(
        "--no-youtube",
        action="store_true",
        help=(
            "Explicitly skip YouTube data during auto-init. Use this when the user says no "
            "or has not opted in."
        ),
    )
    parser.add_argument(
        "--skip-ollama-setup",
        action="store_true",
        help=(
            "When --provider=ollama or --embedding-provider=ollama is "
            "selected, the bootstrap will by default detect, install (via "
            "brew/winget/install.sh), start, and pull the requested model. "
            "Pass this flag to opt out — useful if you manage Ollama "
            "yourself (e.g. inside a container with a custom image)."
        ),
    )
    parser.add_argument(
        "--skip-start",
        action="store_true",
        help="Prepare config and dependencies but do not start the backend.",
    )
    parser.add_argument(
        "--skip-init",
        action="store_true",
        help="Do not run 'openbiliclaw init' after the backend is healthy.",
    )
    parser.add_argument(
        "--interactive-confirm",
        action="store_true",
        help="Ask required init confirmations from the terminal before auto-init.",
    )
    parser.add_argument(
        "--wait-for-extension-cookie",
        action="store_true",
        help="After backend health, wait for the browser extension to sync Bilibili cookie.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Assume dependencies are already installed (local mode only).",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Do not poll /api/health after starting the backend.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="API host to bind on local mode (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="API port (default: 8420).",
    )
    parser.add_argument(
        "--install-cmd",
        default=None,
        help="Override the dependency install command. Default: 'uv sync' when uv is available, otherwise 'pip install -e .'.",
    )
    parser.add_argument(
        "--python",
        default=None,
        help="Path to the Python interpreter to use for the virtual environment. Default: current interpreter.",
    )
    return parser


# ---------------------------------------------------------------------------
# Environment detection


def which(binary: str) -> str | None:
    """Return the absolute path to a binary or None if unavailable."""

    return shutil.which(binary)


def detect_docker() -> bool:
    """Return True when Docker + docker compose V2 are usable."""

    docker = which("docker")
    if docker is None:
        return False
    probe = run_capture([docker, "compose", "version"], check=False)
    return probe.returncode == 0


def detect_uv() -> bool:
    """Return True when `uv` is available on PATH."""

    return which("uv") is not None


# ---------------------------------------------------------------------------
# Ollama auto-install helpers
#
# When the user picks ollama as their LLM and/or embedding provider, the
# install isn't really "done" until ollama itself is installed, the daemon
# is up, and the requested models are pulled. Without these helpers the
# user lands in a "config is fine but nothing works because ollama is
# missing" state, which defeats the one-line install promise.

OLLAMA_HOST = "http://localhost:11434"


def detect_ollama() -> str | None:
    """Return the ollama binary path, or None when not installed."""

    return which("ollama")


def ollama_running(timeout: float = 2.0) -> bool:
    """Probe Ollama's HTTP API. True iff /api/version returns 200."""

    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/version", timeout=timeout) as resp:
            return bool(resp.status == 200)
    except Exception:
        return False


def install_ollama() -> bool:
    """Install Ollama using the platform-native package manager.

    macOS: prefer brew (most devs have it), fall back to printing the
        download URL.
    Windows: prefer winget (ships on Win 10 1803+), fall back to URL.
    Linux: pipe the official install.sh — it auto-detects systemd and
        sets up the service. Needs sudo for the systemd unit; users
        without sudo will see install.sh's own error message.
    """

    if sys.platform == "darwin":
        if which("brew"):
            try:
                run_streaming(["brew", "install", "ollama"], check=False)
                return detect_ollama() is not None
            except RuntimeError:
                pass
        info(
            "Could not auto-install via brew. Download the macOS app from "
            "https://ollama.com/download and re-run the bootstrap."
        )
        return False

    if os.name == "nt":
        if which("winget"):
            try:
                run_streaming(
                    [
                        "winget",
                        "install",
                        "-e",
                        "--id",
                        "Ollama.Ollama",
                        "--accept-source-agreements",
                        "--accept-package-agreements",
                    ],
                    check=False,
                )
                # winget puts ollama under %LOCALAPPDATA%\Programs\Ollama —
                # may not be on PATH in this shell session yet.
                local_app = os.environ.get("LOCALAPPDATA", "")
                if local_app:
                    candidate = Path(local_app) / "Programs" / "Ollama" / "ollama.exe"
                    if candidate.exists():
                        return True
                return detect_ollama() is not None
            except RuntimeError:
                pass
        info(
            "Could not auto-install via winget. Download the Windows "
            "installer from https://ollama.com/download and re-run."
        )
        return False

    # Linux: piped curl | sh. install.sh handles systemd setup itself.
    try:
        result = subprocess.run(
            "curl -fsSL https://ollama.com/install.sh | sh",
            shell=True,
            check=False,
        )
        return result.returncode == 0 and detect_ollama() is not None
    except Exception:
        return False


def start_ollama_serve(wait_seconds: float = 15.0) -> bool:
    """Spawn `ollama serve` in the background; wait for /api/version 200.

    Returns False if the process couldn't be spawned, or if the daemon
    isn't responding within ``wait_seconds``.
    """

    if ollama_running():
        return True

    ollama = detect_ollama()
    if ollama is None:
        return False

    devnull = subprocess.DEVNULL
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        subprocess.Popen(  # noqa: S603 — known binary path from PATH
            [ollama, "serve"],
            creationflags=creationflags,
            stdout=devnull,
            stderr=devnull,
            stdin=devnull,
        )
    else:
        subprocess.Popen(  # noqa: S603 — known binary path from PATH
            [ollama, "serve"],
            start_new_session=True,
            stdout=devnull,
            stderr=devnull,
            stdin=devnull,
        )

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if ollama_running():
            return True
        time.sleep(0.5)
    return False


def ollama_has_model(model: str) -> bool:
    """Return True when the named model is already pulled.

    Matches both the bare name (``bge-m3``) and the tagged form
    (``bge-m3:latest``) so users who pulled with an explicit tag still
    pass the check.
    """

    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    for tag in data.get("models", []):
        name = str(tag.get("name", "")).strip()
        if name == model or name.startswith(f"{model}:"):
            return True
    return False


def ollama_pull(model: str) -> bool:
    """Pull a model via the ollama CLI (streams progress to stdout)."""

    ollama = detect_ollama()
    if ollama is None:
        return False
    try:
        run_streaming([ollama, "pull", model], check=False)
    except RuntimeError:
        return False
    return ollama_has_model(model)


def ensure_ollama_ready(models: list[str]) -> dict[str, Any]:
    """Detect → install → start → pull. Drives all four phases.

    Returns a structured summary so the bootstrap can emit one
    consolidated event. Each individual phase also emits its own event
    (ollama_installed / ollama_serving / ollama_model_pulled / *_failed)
    so an AI agent watching the JSON stream gets fine-grained progress.
    """

    summary: dict[str, Any] = {
        "binary_path": "",
        "installed_now": False,
        "started_now": False,
        "pulled": [],
        "failed_pulls": [],
        "running": False,
    }

    # Phase 1 — detect / install
    binary = detect_ollama()
    if binary is None:
        info(
            "Ollama not detected. Auto-installing now — this can take 1–3 "
            "minutes depending on your network."
        )
        if not install_ollama():
            emit(
                BootstrapResult(
                    "error",
                    "ollama_install_failed",
                    {
                        "platform": sys.platform,
                        "hint": (
                            "Install Ollama manually from "
                            "https://ollama.com/download then re-run the "
                            "bootstrap. The rest of your config is already "
                            "saved — re-running won't lose progress."
                        ),
                    },
                )
            )
            return summary
        binary = detect_ollama()
        summary["installed_now"] = True
        emit(BootstrapResult("ok", "ollama_installed", {"binary": binary or ""}))

    summary["binary_path"] = binary or ""

    # Phase 2 — start the daemon if not already up
    if not ollama_running():
        info("Starting 'ollama serve' in the background...")
        if not start_ollama_serve():
            emit(
                BootstrapResult(
                    "warning",
                    "ollama_serve_failed",
                    {
                        "hint": (
                            "Run 'ollama serve' manually in another terminal, "
                            "then re-run the bootstrap."
                        )
                    },
                )
            )
            return summary
        summary["started_now"] = True
        emit(BootstrapResult("ok", "ollama_serving", {"host": OLLAMA_HOST}))

    summary["running"] = ollama_running()

    # Phase 3 — pull the requested models
    for model in models:
        if not model:
            continue
        if ollama_has_model(model):
            info(f"Ollama model '{model}' already pulled.")
            summary["pulled"].append(model)
            continue
        info(f"Pulling Ollama model '{model}' (first time can take a few minutes)...")
        if ollama_pull(model):
            summary["pulled"].append(model)
            emit(BootstrapResult("ok", "ollama_model_pulled", {"model": model}))
        else:
            summary["failed_pulls"].append(model)
            emit(
                BootstrapResult(
                    "warning",
                    "ollama_pull_failed",
                    {
                        "model": model,
                        "hint": f"Run 'ollama pull {model}' manually and re-check.",
                    },
                )
            )

    return summary


# ---------------------------------------------------------------------------
# Subprocess helpers


@dataclass(frozen=True)
class CommandResult:
    """Immutable result of a subprocess run."""

    returncode: int
    stdout: str
    stderr: str


def run_capture(cmd: list[str], *, check: bool = True, cwd: Path | None = None) -> CommandResult:
    """Run a command and capture its output."""

    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    result = CommandResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {shlex.join(cmd)}\n{result.stderr}"
        )
    return result


SERVICE_CHECK_PROBE = r"""
from __future__ import annotations

import asyncio
import json
import os


async def main() -> None:
    result = {
        "services": {
            "llm": {
                "available": False,
                "provider": "",
                "error": "",
            },
            "embedding": {
                "available": False,
                "provider": "",
                "model": "",
                "skipped": False,
                "error": "",
            },
        }
    }

    cfg = None
    try:
        from openbiliclaw.config import load_config
        from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions, build_chat_adapter
        from openbiliclaw.llm.route import OrderedLLMRoute, RouteConnection

        cfg = load_config()
        primary = cfg.models.chat.connections[0]
        result["services"]["llm"]["provider"] = primary.type
        result["services"]["llm"]["connection_id"] = primary.id
        result["services"]["llm"]["preset"] = primary.preset
        options = AdapterRuntimeOptions(timeout_seconds=30.0, environment=os.environ)
        adapter = build_chat_adapter(primary, options)
        route = OrderedLLMRoute(
            (RouteConnection(connection=primary, adapter=adapter),),
            revision="bootstrap-preflight",
            timeout_seconds=30.0,
        )
        response = await route.complete_connection(
            primary.id,
            [{"role": "user", "content": "Reply with OK only."}],
            temperature=0,
            max_tokens=8,
            ignore_circuit=True,
        )
        if str(getattr(response, "content", "") or "").strip():
            result["services"]["llm"]["available"] = True
        else:
            result["services"]["llm"]["error"] = "empty completion response"
    except Exception:
        result["services"]["llm"]["error"] = "exact_chat_probe_failed"

    try:
        from openbiliclaw.config import load_config
        from openbiliclaw.llm.connection_factory import (
            AdapterRuntimeOptions,
            build_embedding_adapter,
        )
        from openbiliclaw.llm.embedding_route import OrderedEmbeddingRoute

        if cfg is None:
            cfg = load_config()
        embedding_cfg = cfg.models.embedding
        model = str(embedding_cfg.settings.model or "").strip()
        result["services"]["embedding"]["model"] = model
        if not embedding_cfg.enabled:
            result["services"]["embedding"]["available"] = True
            result["services"]["embedding"]["skipped"] = True
        elif not embedding_cfg.providers:
            result["services"]["embedding"]["error"] = (
                "embedding is enabled but its ordered provider list is empty"
            )
        else:
            result["services"]["embedding"]["provider"] = embedding_cfg.providers[0].type
            result["services"]["embedding"]["connection_ids"] = [
                provider.id for provider in embedding_cfg.providers
            ]
            options = AdapterRuntimeOptions(timeout_seconds=30.0, environment=os.environ)
            adapters = tuple(
                build_embedding_adapter(provider, embedding_cfg.settings, options)
                for provider in embedding_cfg.providers
            )
            route = OrderedEmbeddingRoute(
                adapters,
                settings=embedding_cfg.settings,
                revision="bootstrap-preflight",
            )
            probe_failed = False
            for provider in embedding_cfg.providers:
                try:
                    await route.probe_provider(provider.id)
                except Exception:
                    probe_failed = True
            if probe_failed:
                result["services"]["embedding"]["error"] = "exact_embedding_probe_failed"
            else:
                result["services"]["embedding"]["available"] = True
    except Exception:
        result["services"]["embedding"]["error"] = "exact_embedding_probe_failed"

    print(json.dumps(result, ensure_ascii=False))


asyncio.run(main())
"""


def build_service_check_command(mode: str, project_dir: Path) -> list[str]:
    """Build the command that probes LLM + embedding readiness."""

    if mode == "docker":
        return [
            "docker",
            "exec",
            "-i",
            DOCKER_CONTAINER_NAME,
            "python",
            "-c",
            SERVICE_CHECK_PROBE,
        ]

    if detect_uv():
        return ["uv", "run", "python", "-c", SERVICE_CHECK_PROBE]

    if os.name == "nt":
        venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = project_dir / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), "-c", SERVICE_CHECK_PROBE]
    return [sys.executable, "-c", SERVICE_CHECK_PROBE]


def _parse_service_check_output(stdout: str) -> dict[str, Any]:
    """Parse the JSON payload from the service-check probe."""

    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not (text.startswith("{") and text.endswith("}")):
            continue
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    raise ValueError("service check probe did not return JSON")


def _normalize_service_entry(raw: Any) -> dict[str, Any]:
    entry = dict(raw) if isinstance(raw, dict) else {}
    entry["available"] = bool(entry.get("available", False))
    entry["provider"] = str(entry.get("provider", "") or "")
    entry["model"] = str(entry.get("model", "") or "")
    entry["error"] = str(entry.get("error", "") or "")
    if "skipped" in entry:
        entry["skipped"] = bool(entry.get("skipped"))
    return entry


def _service_check_command_failed_result(
    command: list[str], result: CommandResult
) -> dict[str, Any]:
    error = "service_check_process_failed"
    services = {
        "llm": {
            "available": False,
            "provider": "",
            "model": "",
            "error": error,
        },
        "embedding": {
            "available": False,
            "provider": "",
            "model": "",
            "skipped": False,
            "error": error,
        },
    }
    return {
        "available": False,
        "failed": ["llm", "embedding"],
        "services": services,
        "command": shlex.join(command),
        "returncode": result.returncode,
    }


def run_pre_init_service_checks(
    project_dir: Path,
    mode: str,
    *,
    runner: Callable[[list[str]], CommandResult] = run_capture,
) -> dict[str, Any]:
    """Probe required AI services before auto-running init."""

    command = build_service_check_command(mode, project_dir)
    result = runner(command, check=False, cwd=project_dir)
    if result.returncode != 0:
        return _service_check_command_failed_result(command, result)

    try:
        payload = _parse_service_check_output(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        failed = CommandResult(returncode=1, stdout=result.stdout, stderr=str(exc))
        return _service_check_command_failed_result(command, failed)

    raw_services = payload.get("services", {})
    services = {
        "llm": _normalize_service_entry(
            raw_services.get("llm", {}) if isinstance(raw_services, dict) else {}
        ),
        "embedding": _normalize_service_entry(
            raw_services.get("embedding", {}) if isinstance(raw_services, dict) else {}
        ),
    }
    failed_services = [name for name, service in services.items() if not service["available"]]
    return {
        "available": not failed_services,
        "failed": failed_services,
        "services": services,
        "command": shlex.join(command),
        "returncode": result.returncode,
    }


def run_streaming(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    """Run a command, streaming stdout/stderr to the parent process."""

    info(f"$ {shlex.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {shlex.join(cmd)}")
    return proc.returncode


def _init_progress_event(line: str) -> dict[str, str] | None:
    """Return structured progress metadata for high-signal init output lines."""

    text = line.strip()
    if not text:
        return None

    for phase in ("1/4", "2/4", "3/4", "4/4"):
        if text.startswith(phase):
            return {"phase": phase, "kind": "phase", "line": text}

    progress_prefixes = (
        "· ",
        "✓ ",
        "补货阶段",
        "当前池子",
        "阶段完成",
        "初始化摘要",
    )
    if text.startswith(progress_prefixes):
        return {"phase": "", "kind": "progress", "line": text}

    return None


def run_init_streaming(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    """Run init while echoing output and emitting machine-readable progress."""

    info(f"$ {shlex.join(cmd)}")
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    merged_env.setdefault("PYTHONUNBUFFERED", "1")
    start = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 — command is built by this bootstrap script.
        cmd,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
        sys.stdout.flush()
        details = _init_progress_event(line)
        if details is None:
            continue
        emit(
            BootstrapResult(
                "progress",
                "init_progress",
                {
                    **details,
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                },
            )
        )

    returncode = proc.wait()
    if check and returncode != 0:
        raise RuntimeError(f"Command failed ({returncode}): {shlex.join(cmd)}")
    return returncode


# ---------------------------------------------------------------------------
# Repository preparation


def ensure_repo_checkout(project_dir: Path, repo_url: str, branch: str) -> Path:
    """Ensure a working OpenBiliClaw checkout exists at project_dir.

    Rules:
    * If project_dir already contains pyproject.toml + config.example.toml, assume it's already a checkout.
    * Otherwise, clone the repo into project_dir.
    * If project_dir only contains desktop-package user data, clone code into
      the same directory without touching config.toml / data / logs.
    * Refuses to clone into other non-empty directories.
    """

    project_dir = project_dir.expanduser().resolve()
    if (project_dir / "pyproject.toml").exists() and (project_dir / "config.example.toml").exists():
        info(f"Using existing OpenBiliClaw checkout at {project_dir}")
        return project_dir

    project_dir.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in project_dir.iterdir() if entry.name != ".DS_Store"]
    if entries:
        if _is_user_data_only_root(project_dir):
            return _clone_repo_into_user_data_root(project_dir, repo_url, branch)
        raise RuntimeError(
            f"Target directory is not empty and does not look like OpenBiliClaw: {project_dir}"
        )

    git = which("git")
    if git is None:
        raise RuntimeError("git is required to clone OpenBiliClaw but was not found on PATH.")

    info(f"Cloning {repo_url} (branch {branch}) into {project_dir}")
    run_streaming([git, "clone", "--branch", branch, "--depth", "1", repo_url, str(project_dir)])
    return project_dir


def _is_user_data_only_root(path: Path) -> bool:
    """Return True when a directory only contains OpenBiliClaw user data."""
    if not path.exists() or not path.is_dir():
        return False
    entries = [entry for entry in path.iterdir() if entry.name != ".DS_Store"]
    return bool(entries) and all(entry.name in USER_DATA_ONLY_ENTRIES for entry in entries)


def _clone_repo_into_user_data_root(project_dir: Path, repo_url: str, branch: str) -> Path:
    """Clone source code into an existing user-data-only root.

    The desktop package now shares ``~/OpenBiliClaw`` with script / AI installs.
    If the package created that directory first, it contains config/data/logs but
    no source checkout. Clone to a temporary sibling, then move the repo files
    in, leaving user data untouched.
    """
    git = which("git")
    if git is None:
        raise RuntimeError("git is required to clone OpenBiliClaw but was not found on PATH.")

    info(f"Target {project_dir} contains existing user data; cloning source into it")
    with tempfile.TemporaryDirectory(prefix="openbiliclaw-clone-", dir=project_dir.parent) as tmp:
        clone_dir = Path(tmp)
        run_streaming([git, "clone", "--branch", branch, "--depth", "1", repo_url, str(clone_dir)])
        for entry in clone_dir.iterdir():
            destination = project_dir / entry.name
            if destination.exists():
                raise RuntimeError(
                    f"Cannot merge checkout into {project_dir}: destination exists: {destination}"
                )
            shutil.move(str(entry), str(destination))
    return project_dir


# ---------------------------------------------------------------------------
# Config + secret handling


def ensure_config_toml(project_dir: Path) -> Path:
    """Ensure config.toml exists, creating it from the example when missing."""

    config_path = project_dir / "config.toml"
    example_path = project_dir / "config.example.toml"
    if not example_path.exists():
        raise RuntimeError(f"config.example.toml not found in {project_dir}")

    if not config_path.exists():
        info(f"Creating {config_path} from config.example.toml")
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return config_path


def read_simple_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file using the stdlib tomllib."""

    import tomllib

    with path.open("rb") as handle:
        return tomllib.load(handle)


def set_toml_string_value(content: str, section: str, key: str, value: str) -> str:
    """Rewrite ``key = "..."`` under ``[section]`` with the new value.

    This is a minimal line-based editor; it preserves the rest of the file as
    much as possible so operators can keep their own comments. It does not
    handle multi-line strings or inline tables, which is fine because the
    OpenBiliClaw config template uses only single-line string values for the
    fields we need to update.
    """

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{key} = "{escaped}"'
    section_header = f"[{section}]"

    lines = content.splitlines()
    in_section = False
    section_found = False
    insert_at: int | None = None
    updated = False
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section:
                insert_at = index
                break
            in_section = stripped == section_header
            if in_section:
                section_found = True
                insert_at = index + 1
            continue
        if not in_section:
            continue
        insert_at = index + 1
        if stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        lhs = stripped.split("=", 1)[0].strip()
        if lhs == key:
            indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
            lines[index] = f"{indent}{new_line}"
            updated = True
            break

    if not updated:
        trailing_newline = "\n" if content.endswith("\n") else ""
        if section_found:
            lines.insert(insert_at if insert_at is not None else len(lines), new_line)
            return "\n".join(lines) + trailing_newline
        # Append the section if missing
        append_lines = []
        if not content.endswith("\n"):
            append_lines.append("")
        append_lines.append(section_header)
        append_lines.append(new_line)
        return content + "\n".join(append_lines) + "\n"

    trailing_newline = "\n" if content.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline


def update_config_secret(config_path: Path, section: str, key: str, value: str) -> None:
    """Patch a single secret value inside config.toml."""

    original = config_path.read_text(encoding="utf-8")
    updated = set_toml_string_value(original, section, key, value)
    if updated != original:
        config_path.write_text(updated, encoding="utf-8")


def clear_toml_string_value(content: str, section: str, key: str) -> tuple[str, bool]:
    """Reset ``key = "..."`` under ``[section]`` to empty (``key = ""``).

    Returns ``(new_content, did_change)``. We **set to empty** rather than
    deleting the line because the rest of the codebase reads config via
    Pydantic models that expect every field to exist. Setting to empty
    string lets defaults take over (e.g. ``base_url=""`` → OpenAI SDK
    uses its built-in ``https://api.openai.com/v1``).
    """

    new_line = f'{key} = ""'
    section_header = f"[{section}]"
    lines = content.splitlines()
    in_section = False
    changed = False
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == section_header
            continue
        if not in_section:
            continue
        if stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        lhs = stripped.split("=", 1)[0].strip()
        if lhs == key:
            indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
            new_full = f"{indent}{new_line}"
            if new_full != raw_line:
                lines[index] = new_full
                changed = True
            break
    trailing_newline = "\n" if content.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline, changed


def clear_config_value(config_path: Path, section: str, key: str) -> bool:
    """Reset a config field to empty in-place. Returns True if it changed."""

    original = config_path.read_text(encoding="utf-8")
    updated, changed = clear_toml_string_value(original, section, key)
    if changed:
        config_path.write_text(updated, encoding="utf-8")
    return changed


def reuse_config_secrets(project_dir: Path, source_dir: Path) -> dict[str, Any]:
    """Copy API keys + Bilibili cookie from an existing OpenBiliClaw checkout."""

    source_dir = source_dir.expanduser().resolve()
    if not source_dir.exists():
        raise RuntimeError(f"--reuse-from path does not exist: {source_dir}")

    source_config = source_dir / "config.toml"
    summary: dict[str, Any] = {"reused": [], "skipped": [], "source": str(source_dir)}
    if not source_config.exists():
        summary["skipped"].append("config.toml missing in source")
    else:
        source_data = read_simple_toml(source_config)
        source_models = _load_typed_models(source_dir, allow_pending_migration=True)
        target_models = _load_typed_models(project_dir)

        source_is_native = isinstance(source_data.get("models"), dict)
        used_source_identities: set[tuple[str, str, str]] = set()

        def source_identity(source: Any, route: str) -> tuple[str, str, str]:
            if source_is_native:
                return ("native", route, source.id)
            return ("legacy", source.credential.source, source.credential.value)

        def overlay_credentials(
            target_records: tuple[Any, ...],
            source_records: tuple[Any, ...],
            *,
            route: str,
        ) -> tuple[Any, ...]:
            assignments: dict[str, Any] = {}
            for target in target_records:
                exact = next(
                    (
                        source
                        for source in source_records
                        if source_identity(source, route) not in used_source_identities
                        and source.id == target.id
                        and source.type == target.type
                        and source.preset == target.preset
                        and source.credential.source != "none"
                        and source.credential.value.strip()
                    ),
                    None,
                )
                if exact is not None:
                    assignments[target.id] = exact
                    used_source_identities.add(source_identity(exact, route))
            for target in target_records:
                if target.id in assignments:
                    continue
                compatible = [
                    source
                    for source in source_records
                    if source_identity(source, route) not in used_source_identities
                    and source.type == target.type
                    and source.preset == target.preset
                    and source.credential.source != "none"
                    and source.credential.value.strip()
                ]
                if len(compatible) == 1:
                    assignments[target.id] = compatible[0]
                    used_source_identities.add(source_identity(compatible[0], route))

            updated: list[Any] = []
            for target in target_records:
                source = assignments.get(target.id)
                if source is None:
                    updated.append(target)
                    continue
                updated.append(replace(target, credential=source.credential))
                summary["reused"].append(f"models.{route}.{target.id}.credential")
            return tuple(updated)

        chat = overlay_credentials(
            target_models.chat.connections,
            source_models.chat.connections,
            route="chat.connections",
        )
        embedding = overlay_credentials(
            target_models.embedding.providers,
            source_models.embedding.providers,
            route="embedding.providers",
        )
        for route, identity_route, records in (
            ("chat", "chat.connections", source_models.chat.connections),
            ("embedding", "embedding.providers", source_models.embedding.providers),
        ):
            for record in records:
                if (
                    record.credential.source != "none"
                    and record.credential.value.strip()
                    and source_identity(record, identity_route) not in used_source_identities
                ):
                    selection = f"{record.type}:{record.preset}".rstrip(":")
                    summary["skipped"].append(
                        f"models.{route} credential {selection} has no compatible target record"
                    )
        updated_models = replace(
            target_models,
            chat=replace(target_models.chat, connections=chat),
            embedding=replace(target_models.embedding, providers=embedding),
        )
        _persist_typed_models(project_dir, updated_models)

        bilibili_section = source_data.get("bilibili", {})
        cookie_value = str(bilibili_section.get("cookie", "")).strip()
        if cookie_value:
            update_config_secret(project_dir / "config.toml", "bilibili", "cookie", cookie_value)
            summary["reused"].append("bilibili.cookie")

    source_cookie_file = source_dir / "data" / "bilibili_cookie.json"
    if source_cookie_file.exists():
        data_dir = project_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        target_cookie = data_dir / "bilibili_cookie.json"
        target_cookie.write_text(source_cookie_file.read_text(encoding="utf-8"), encoding="utf-8")
        summary["reused"].append("data/bilibili_cookie.json")
    else:
        summary["skipped"].append("data/bilibili_cookie.json missing in source")

    return summary


def persist_cookie_file(project_dir: Path, cookie: str) -> None:
    """Persist the cookie string in the on-disk Bilibili cookie file."""

    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = data_dir / "bilibili_cookie.json"
    cookie_path.write_text(json.dumps({"cookie": cookie}, ensure_ascii=False), encoding="utf-8")


def _ensure_project_model_imports(project_dir: Path) -> None:
    """Expose the checked-out source tree only after project files exist."""
    source_dir = project_dir / "src"
    if source_dir.is_dir() and str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))


def _load_typed_models(
    project_dir: Path,
    *,
    allow_pending_migration: bool = False,
) -> Any:
    """Load native models, migrating a lossless legacy route when necessary."""
    _ensure_project_model_imports(project_dir)
    from openbiliclaw.model_config import (
        default_model_config,
        migrate_legacy_llm,
        parse_model_config,
    )

    raw = read_simple_toml(project_dir / "config.toml")
    models_raw = raw.get("models")
    if isinstance(models_raw, dict):
        return parse_model_config(models_raw)
    llm_raw = raw.get("llm")
    if isinstance(llm_raw, dict):
        migrated = migrate_legacy_llm(llm_raw, os.environ)
        if migrated.report.has_pending_decisions and not allow_pending_migration:
            fields = ", ".join(issue.field for issue in migrated.report.issues)
            raise RuntimeError(
                "legacy model configuration requires explicit migration choices: "
                f"{fields}; run `openbiliclaw models list` first"
            )
        return migrated.models
    return default_model_config()


def _persist_typed_models(project_dir: Path, models: Any) -> None:
    """Validate and serialize one native route while preserving other config."""
    _ensure_project_model_imports(project_dir)
    from openbiliclaw.config import render_model_config_document
    from openbiliclaw.model_config import connection_type_registry, validate_model_config

    issues = validate_model_config(models, connection_type_registry())
    blocking = [issue for issue in issues if issue.severity == "blocking"]
    if blocking:
        detail = "; ".join(f"{issue.path}: {issue.message}" for issue in blocking)
        raise RuntimeError(f"invalid model configuration: {detail}")
    config_path = project_dir / "config.toml"
    original = config_path.read_bytes()
    config_path.write_bytes(render_model_config_document(original, models))


def apply_chat_route_config(
    project_dir: Path,
    *,
    connection_type: str,
    preset: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    credential_ref: str | None,
) -> dict[str, Any]:
    """Edit the stable primary record and retain every ordered fallback."""
    _ensure_project_model_imports(project_dir)
    from openbiliclaw.model_config import (
        ChatConnection,
        CredentialConfig,
        apply_preset_defaults,
        connection_type_registry,
    )

    models = _load_typed_models(project_dir)
    existing = models.chat.connections[0] if models.chat.connections else None
    same_selection = bool(
        existing and existing.type == connection_type and existing.preset == preset
    )
    if connection_type == "codex_oauth":
        credential = CredentialConfig(source="oauth", value=credential_ref or "codex")
    elif api_key is not None:
        credential = CredentialConfig(source="inline", value=api_key)
    elif same_selection and existing is not None:
        credential = existing.credential
    else:
        env_name = DEFAULT_CREDENTIAL_ENVS.get((connection_type, preset), "")
        credential = (
            CredentialConfig(source="env", value=env_name) if env_name else CredentialConfig()
        )

    default_models = {
        ("openai_compatible", "openai"): "gpt-5-nano",
        ("openai_compatible", "deepseek"): "deepseek-v4-flash",
        ("openai_compatible", "openrouter"): "openai/gpt-5-nano",
        ("anthropic_compatible", "anthropic"): "claude-sonnet-4-6",
        ("gemini_api", ""): "gemini-2.5-flash",
        ("ollama", ""): "qwen2.5:7b",
        ("codex_oauth", ""): "gpt-5-nano",
    }
    default_bases = {"ollama": "http://127.0.0.1:11434/v1"}
    connection_id = existing.id if existing is not None else "chat-main"
    if existing is None:
        used_ids = {provider.id for provider in models.embedding.providers}
        suffix = 2
        while connection_id in used_ids:
            connection_id = f"chat-main-{suffix}"
            suffix += 1
    connection = ChatConnection(
        id=connection_id,
        name=existing.name if existing is not None else "Primary Chat",
        type=connection_type,
        preset=preset,
        model=(
            model
            if model is not None
            else existing.model
            if same_selection and existing is not None
            else default_models.get((connection_type, preset), "")
        ),
        base_url=(
            base_url
            if base_url is not None
            else existing.base_url
            if same_selection and existing is not None
            else default_bases.get(connection_type, "")
        ),
        credential=credential,
        api_mode=existing.api_mode if same_selection and existing is not None else "",
        reasoning_effort=(
            existing.reasoning_effort if same_selection and existing is not None else ""
        ),
        http_referer=existing.http_referer if same_selection and existing is not None else "",
        x_title=existing.x_title if same_selection and existing is not None else "",
        num_ctx=existing.num_ctx if same_selection and existing is not None else 0,
    )
    touched = frozenset(
        field_name
        for field_name, value in (("model", model), ("base_url", base_url))
        if value is not None
    )
    if preset:
        definition = connection_type_registry().preset(connection_type, preset)
        connection = apply_preset_defaults(connection, definition, touched)
    fallbacks = models.chat.connections[1:] if existing is not None else ()
    updated = replace(
        models,
        chat=replace(models.chat, connections=(connection, *fallbacks)),
    )
    _persist_typed_models(project_dir, updated)
    return {
        "connection_id": connection.id,
        "connection_type": connection.type,
        "preset": connection.preset,
    }


def should_auto_wire_embedding(
    *, embedding_provider_arg: str | None, effective_provider: str, mode: str
) -> bool:
    """Whether bootstrap should default native ``[models.embedding]`` to local Ollama.

    v0.3.95+: embedding is fully decoupled from the chat provider, so a
    flag-driven install that never passed ``--embedding-*`` — or one whose
    chat provider can't embed (Claude / DeepSeek / OpenRouter) — would
    leave embedding unconfigured and silently disable semantic dedup.

    Returns ``True`` only when embedding is unconfigured AND the user did
    not explicitly disable it (``--embedding-provider ""``) AND we're not
    under Docker (the container can't reach the host's Ollama at
    ``localhost``).
    """
    if mode == "docker":
        return False
    explicitly_disabled = (
        embedding_provider_arg is not None and not (embedding_provider_arg or "").strip()
    )
    if explicitly_disabled:
        return False
    return not effective_provider.strip()


_EMBEDDING_KEY_TARGET_ERROR = (
    "--embedding-api-key requires an existing credential-capable embedding provider"
)


def _embedding_provider_accepts_credential(provider: Any, registry: Any) -> bool:
    return "credential" in registry.definition(provider.type).allowed_fields(
        "embedding", provider.preset
    )


def _validate_key_only_embedding_target(
    project_dir: Path,
    *,
    provider: str | None,
    endpoints: list[str] | None,
    api_key: str | None,
) -> None:
    """Reject an unusable key-only edit before bootstrap mutates configuration."""
    if api_key is None or provider is not None or endpoints:
        return
    _ensure_project_model_imports(project_dir)
    from openbiliclaw.model_config import connection_type_registry

    registry = connection_type_registry()
    providers = _load_typed_models(project_dir).embedding.providers
    if not any(_embedding_provider_accepts_credential(item, registry) for item in providers):
        raise RuntimeError(_EMBEDDING_KEY_TARGET_ERROR)


def apply_embedding_config(
    project_dir: Path,
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    endpoints: list[str] | None = None,
) -> dict[str, Any]:
    """Write ordered native providers that share one embedding model space."""
    _ensure_project_model_imports(project_dir)
    from openbiliclaw.model_config import (
        CredentialConfig,
        EmbeddingProviderConfig,
        connection_type_registry,
    )

    models = _load_typed_models(project_dir)
    current = models.embedding.providers
    target_provider = (provider or "").strip().lower()
    if provider == "" and not endpoints:
        providers: tuple[Any, ...] = ()
        enabled = False
    elif endpoints:
        built: list[Any] = []
        reserved_ids = {item.id for item in (*models.chat.connections, *current)}
        allocated_ids: set[str] = set()

        def allocate_provider_id() -> str:
            candidate = 1
            while (
                f"embedding-{candidate}" in reserved_ids
                or f"embedding-{candidate}" in allocated_ids
            ):
                candidate += 1
            record_id = f"embedding-{candidate}"
            allocated_ids.add(record_id)
            return record_id

        for index, spec in enumerate(endpoints, start=1):
            left, separator, endpoint = spec.partition("=")
            if not separator or not endpoint.strip():
                raise RuntimeError("--embedding-endpoint requires TYPE[:PRESET]=BASE_URL")
            raw_type, preset_separator, raw_preset = left.strip().partition(":")
            mapped = LEGACY_PROVIDER_CONNECTIONS.get(raw_type)
            connection_type = mapped[0] if mapped is not None else raw_type
            preset = raw_preset if preset_separator else (mapped[1] if mapped is not None else "")
            if connection_type == "openai_compatible" and not preset:
                preset = "custom"
            definition = connection_type_registry().definition(connection_type)
            if "embedding" not in definition.capabilities:
                raise RuntimeError(
                    f"connection type {connection_type!r} does not support embedding"
                )
            allowed_presets = connection_type_registry().presets_for(connection_type, "embedding")
            if preset and preset not in allowed_presets:
                raise RuntimeError(
                    f"preset {preset!r} does not support embedding for {connection_type!r}"
                )
            previous = current[index - 1] if index <= len(current) else None
            record_id = previous.id if previous is not None else allocate_provider_id()
            same_selection = bool(
                previous and previous.type == connection_type and previous.preset == preset
            )
            accepts_credential = "credential" in definition.allowed_fields("embedding", preset)
            if not accepts_credential:
                credential = CredentialConfig()
            elif api_key is not None:
                credential = CredentialConfig(source="inline", value=api_key)
            elif same_selection and previous is not None:
                credential = previous.credential
            else:
                env_name = DEFAULT_CREDENTIAL_ENVS.get((connection_type, preset), "")
                credential = (
                    CredentialConfig(source="env", value=env_name)
                    if env_name
                    else CredentialConfig()
                )
            built.append(
                EmbeddingProviderConfig(
                    id=record_id,
                    name=(
                        previous.name
                        if same_selection and previous is not None
                        else f"{definition.label} {index}"
                    ),
                    type=connection_type,
                    preset=preset,
                    base_url=endpoint.strip(),
                    credential=credential,
                )
            )
        providers = tuple(built)
        enabled = True
    elif provider is not None:
        mapped = LEGACY_PROVIDER_CONNECTIONS.get(target_provider)
        if mapped is None:
            raise RuntimeError(f"unknown embedding provider alias: {target_provider}")
        connection_type, preset = mapped
        if "embedding" not in connection_type_registry().definition(connection_type).capabilities:
            raise RuntimeError(f"provider alias {target_provider!r} does not support embedding")
        existing = current[0] if current else None
        if existing is not None:
            record_id = existing.id
        else:
            reserved_ids = {item.id for item in (*models.chat.connections, *current)}
            record_id = "embedding-main"
            suffix = 2
            while record_id in reserved_ids:
                record_id = f"embedding-main-{suffix}"
                suffix += 1
        endpoint = base_url
        same_selection = bool(
            existing and existing.type == connection_type and existing.preset == preset
        )
        if endpoint is None and same_selection and existing is not None:
            endpoint = existing.base_url
        if endpoint is None and connection_type == "ollama":
            endpoint = "http://localhost:11434/v1"
        definition = connection_type_registry().definition(connection_type)
        accepts_credential = "credential" in definition.allowed_fields("embedding", preset)
        if not accepts_credential:
            credential = CredentialConfig()
        elif api_key is not None:
            credential = CredentialConfig(source="inline", value=api_key)
        elif same_selection and existing is not None:
            credential = existing.credential
        else:
            env_name = DEFAULT_CREDENTIAL_ENVS.get((connection_type, preset), "")
            credential = (
                CredentialConfig(source="env", value=env_name) if env_name else CredentialConfig()
            )
        fallbacks = current[1:] if existing is not None else ()
        providers = (
            EmbeddingProviderConfig(
                id=record_id,
                name=(
                    existing.name
                    if same_selection and existing is not None
                    else "Primary Embedding"
                ),
                type=connection_type,
                preset=preset,
                base_url=endpoint or "",
                credential=credential,
            ),
            *fallbacks,
        )
        enabled = True
    elif api_key is not None:
        registry = connection_type_registry()
        updated_providers: list[Any] = []
        credential_updates = 0
        for item in current:
            if _embedding_provider_accepts_credential(item, registry):
                item = replace(
                    item,
                    credential=CredentialConfig(source="inline", value=api_key),
                )
                credential_updates += 1
            updated_providers.append(item)
        if not credential_updates:
            raise RuntimeError(_EMBEDDING_KEY_TARGET_ERROR)
        providers = tuple(updated_providers)
        enabled = models.embedding.enabled
    else:
        providers = current
        enabled = models.embedding.enabled

    updated = replace(
        models,
        embedding=replace(
            models.embedding,
            enabled=enabled,
            settings=replace(
                models.embedding.settings,
                model=model if model is not None else models.embedding.settings.model,
            ),
            providers=providers,
        ),
    )
    _persist_typed_models(project_dir, updated)
    return {
        "written": ["models.embedding"],
        "provider": target_provider,
        "provider_ids": [item.id for item in providers],
    }


def _is_local_ollama_base_url(base_url: str) -> bool:
    normalized = base_url.strip().rstrip("/")
    return not normalized or normalized in LOCAL_OLLAMA_BASE_URLS


def align_docker_runtime_config(project_dir: Path) -> dict[str, Any]:
    """Rewrite host-only config values before copying them into Docker runtime."""
    models = _load_typed_models(project_dir)
    written: list[str] = []
    providers = []
    for item in models.embedding.providers:
        if item.type == "ollama" and _is_local_ollama_base_url(item.base_url):
            providers.append(replace(item, base_url=DOCKER_OLLAMA_BASE_URL))
            written.append(f"models.embedding.providers.{item.id}.base_url")
        else:
            providers.append(item)
    if written:
        _persist_typed_models(
            project_dir,
            replace(
                models,
                embedding=replace(models.embedding, providers=tuple(providers)),
            ),
        )

    return {
        "written": written,
        "embedding_provider": providers[0].type if providers else "",
        "embedding_base_url": providers[0].base_url if providers else "",
    }


def detect_missing_secrets(project_dir: Path) -> dict[str, Any]:
    """Return a structured summary of missing secrets in config.toml."""

    config_path = project_dir / "config.toml"
    data = read_simple_toml(config_path)
    models_raw = data.get("models")
    if isinstance(models_raw, dict):
        models = _load_typed_models(project_dir)
        if not models.chat.connections:
            bilibili_section = data.get("bilibili", {})
            cookie_inline = str(bilibili_section.get("cookie", "") or "").strip()
            cookie_file = project_dir / "data" / "bilibili_cookie.json"
            cookie_on_disk = False
            if cookie_file.exists():
                try:
                    cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
                    cookie_on_disk = bool(str(cookie_data.get("cookie", "")).strip())
                except json.JSONDecodeError:
                    cookie_on_disk = False
            missing = ["models.chat.connections"]
            if not (cookie_inline or cookie_on_disk):
                missing.append("bilibili.cookie")
            return {
                "provider": "",
                "connection_type": "",
                "preset": "",
                "connection_id": "",
                "missing": missing,
                "has_cookie_inline": bool(cookie_inline),
                "has_cookie_file": cookie_on_disk,
            }
        primary = models.chat.connections[0]
        provider = {
            ("anthropic_compatible", "anthropic"): "claude",
            ("gemini_api", ""): "gemini",
            ("ollama", ""): "ollama",
        }.get((primary.type, primary.preset), primary.preset or primary.type)
        if primary.credential.source == "inline":
            credential_ready = bool(primary.credential.value.strip())
        elif primary.credential.source == "env":
            credential_ready = bool(os.environ.get(primary.credential.value, "").strip())
        elif primary.credential.source == "oauth":
            credential_ready = bool(primary.credential.value.strip())
        else:
            credential_ready = primary.type == "ollama"
        missing: list[str] = []
        if not credential_ready:
            missing.append(f"models.chat.connections.{primary.id}.credential")
        bilibili_section = data.get("bilibili", {})
        cookie_inline = str(bilibili_section.get("cookie", "") or "").strip()
        cookie_file = project_dir / "data" / "bilibili_cookie.json"
        cookie_on_disk = False
        if cookie_file.exists():
            try:
                cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
                cookie_on_disk = bool(str(cookie_data.get("cookie", "")).strip())
            except json.JSONDecodeError:
                cookie_on_disk = False
        if not (cookie_inline or cookie_on_disk):
            missing.append("bilibili.cookie")
        return {
            "provider": provider,
            "connection_type": primary.type,
            "preset": primary.preset,
            "connection_id": primary.id,
            "missing": missing,
            "has_cookie_inline": bool(cookie_inline),
            "has_cookie_file": cookie_on_disk,
        }

    # One-cycle read compatibility for old checkouts. Every Task 14 writer
    # upgrades through the typed migration path before changing model fields.
    llm_section = data.get("llm", {})
    provider = str(llm_section.get("default_provider", "") or "").strip() or "deepseek"

    provider_cfg = llm_section.get(provider, {})
    api_key = str(provider_cfg.get("api_key", "") or "").strip()
    bilibili_section = data.get("bilibili", {})
    cookie_inline = str(bilibili_section.get("cookie", "") or "").strip()
    cookie_file = project_dir / "data" / "bilibili_cookie.json"
    cookie_on_disk = False
    if cookie_file.exists():
        try:
            cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
            cookie_on_disk = bool(str(cookie_data.get("cookie", "")).strip())
        except json.JSONDecodeError:
            cookie_on_disk = False

    missing: list[str] = []
    if provider in REMOTE_PROVIDERS and not api_key:
        missing.append(f"llm.{provider}.api_key")
    if provider == "openai_compatible":
        base_url = str(provider_cfg.get("base_url", "") or "").strip()
        if not base_url:
            missing.append("llm.openai_compatible.base_url")
    if not (cookie_inline or cookie_on_disk):
        missing.append("bilibili.cookie")

    return {
        "provider": provider,
        "missing": missing,
        "has_cookie_inline": bool(cookie_inline),
        "has_cookie_file": cookie_on_disk,
    }


def _embedding_choice_from_config(project_dir: Path) -> dict[str, Any]:
    data = read_simple_toml(project_dir / "config.toml")
    if isinstance(data.get("models"), dict):
        embedding = _load_typed_models(project_dir).embedding
        provider = embedding.providers[0].type if embedding.providers else ""
        return {
            "source": "config",
            "provider": provider,
            "providers": [item.type for item in embedding.providers],
            "model": embedding.settings.model,
            "enabled": embedding.enabled,
            "explicit": True,
        }
    raw = data.get("llm", {}).get("embedding", {})
    provider = str(raw.get("provider", "") or "").strip()
    model = str(raw.get("model", "") or "").strip()
    if provider or model:
        return {
            "source": "config",
            "provider": provider,
            "model": model,
            "explicit": True,
        }
    return {
        "source": "missing",
        "provider": provider,
        "model": model,
        "explicit": False,
    }


def detect_init_decisions(
    project_dir: Path,
    args: argparse.Namespace,
    *,
    embedding_touched: bool,
) -> dict[str, Any]:
    """Return user decisions required before non-interactive auto-init.

    ``agent_bootstrap.py`` never prompts. If the AI agent did not pass an
    explicit embedding choice or source opt-in/out, auto-init must pause
    and surface those decisions instead of silently choosing for the user.
    """

    missing: list[str] = []
    if embedding_touched:
        embedding = {
            "source": "flags",
            "provider": (args.embedding_provider or "").strip(),
            "model": (args.embedding_model or "").strip(),
            "explicit": True,
        }
    else:
        embedding = _embedding_choice_from_config(project_dir)
        if not embedding["explicit"]:
            missing.append("embedding")

    if args.yes_xhs:
        xhs = {
            "policy": "enabled",
            "flag": "--yes-xhs",
            "explicit": True,
            "source": "flag",
        }
    elif args.no_xhs or os.environ.get("OPENBILICLAW_NO_XHS", "").strip() == "1":
        xhs = {
            "policy": "disabled",
            "flag": "--no-xhs",
            "explicit": True,
            "source": "env" if not args.no_xhs else "flag",
        }
    else:
        missing.append("xhs")
        xhs = {
            "policy": "pending",
            "flag": "",
            "explicit": False,
            "source": "missing",
        }

    if args.yes_douyin:
        douyin = {
            "policy": "enabled",
            "flag": "--yes-douyin",
            "explicit": True,
            "source": "flag",
        }
    elif args.no_douyin or os.environ.get("OPENBILICLAW_NO_DOUYIN", "").strip() == "1":
        douyin = {
            "policy": "disabled",
            "flag": "--no-douyin",
            "explicit": True,
            "source": "env" if not args.no_douyin else "flag",
        }
    else:
        missing.append("douyin")
        douyin = {
            "policy": "pending",
            "flag": "",
            "explicit": False,
            "source": "missing",
        }

    if args.yes_youtube:
        youtube = {
            "policy": "enabled",
            "flag": "--yes-youtube",
            "explicit": True,
            "source": "flag",
        }
    elif args.no_youtube or os.environ.get("OPENBILICLAW_NO_YOUTUBE", "").strip() == "1":
        youtube = {
            "policy": "disabled",
            "flag": "--no-youtube",
            "explicit": True,
            "source": "env" if not args.no_youtube else "flag",
        }
    else:
        missing.append("youtube")
        youtube = {
            "policy": "pending",
            "flag": "",
            "explicit": False,
            "source": "missing",
        }

    return {
        "missing": missing,
        "embedding": embedding,
        "xhs": xhs,
        "douyin": douyin,
        "youtube": youtube,
    }


def build_init_command(
    mode: str,
    project_dir: Path,
    xhs_flag: str,
    douyin_flag: str,
    youtube_flag: str,
    *,
    bilibili_favorite_limit: int | None = None,
    bilibili_follow_limit: int | None = None,
) -> list[str]:
    """Build the non-interactive init command used after bootstrap health checks."""

    if mode == "docker":
        init_cmd = [
            "docker",
            "exec",
            "-i",
            "openbiliclaw-backend",
            "openbiliclaw",
            "init",
        ]
    elif detect_uv():
        init_cmd = ["uv", "run", "openbiliclaw", "init"]
    else:
        if os.name == "nt":
            venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
        else:
            venv_python = project_dir / ".venv" / "bin" / "python"
        if venv_python.exists():
            init_cmd = [str(venv_python), "-m", "openbiliclaw.cli", "init"]
        else:
            init_cmd = [sys.executable, "-m", "openbiliclaw.cli", "init"]

    if xhs_flag:
        init_cmd.append(xhs_flag)
    if douyin_flag:
        init_cmd.append(douyin_flag)
    if youtube_flag:
        init_cmd.append(youtube_flag)
    if bilibili_favorite_limit is not None:
        init_cmd.extend(
            [
                "--bilibili-favorite-limit",
                str(max(0, int(bilibili_favorite_limit))),
            ]
        )
    if bilibili_follow_limit is not None:
        init_cmd.extend(
            [
                "--bilibili-follow-limit",
                str(max(0, int(bilibili_follow_limit))),
            ]
        )
    return init_cmd


# ---------------------------------------------------------------------------
# Local deployment


def local_install(project_dir: Path, install_cmd: str | None, python_override: str | None) -> None:
    """Install python dependencies using uv (preferred) or pip."""

    if install_cmd:
        run_streaming(shlex.split(install_cmd), cwd=project_dir)
        return

    if detect_uv():
        run_streaming(["uv", "sync"], cwd=project_dir)
        return

    venv_python = python_override or sys.executable
    venv_dir = project_dir / ".venv"
    if not venv_dir.exists():
        run_streaming([venv_python, "-m", "venv", str(venv_dir)])
    pip = venv_dir / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    run_streaming([str(pip), "install", "-e", ".[dev]"], cwd=project_dir)


def local_serve_command(project_dir: Path, host: str, port: int) -> list[str]:
    """Return the command used to start the API server in local mode."""

    if detect_uv():
        return ["uv", "run", "openbiliclaw", "serve-api", "--host", host, "--port", str(port)]

    venv_bin = project_dir / (".venv/Scripts" if os.name == "nt" else ".venv/bin")
    openbiliclaw = venv_bin / "openbiliclaw"
    if openbiliclaw.exists():
        return [str(openbiliclaw), "serve-api", "--host", host, "--port", str(port)]

    python = venv_bin / ("python.exe" if os.name == "nt" else "python")
    return [str(python), "-m", "openbiliclaw.cli", "serve-api", "--host", host, "--port", str(port)]


def _connect_host_for_bind_host(host: str) -> str:
    """Return a concrete local address for checks against a bind address."""
    value = str(host or "").strip().lower()
    if value in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return str(host).strip()


def _health_url(host: str, port: int) -> str:
    return f"http://{_connect_host_for_bind_host(host)}:{port}{DEFAULT_HEALTH_PATH}"


def _probe_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True if a TCP listener answers on host:port."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _probe_is_openbiliclaw(host: str, port: int) -> bool:
    """Confirm the listener on host:port responds to /api/health as OpenBiliClaw."""
    url = _health_url(host, port)
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310
            if not (200 <= response.status < 300):
                return False
            body = response.read().decode("utf-8", errors="replace")
            return "openbiliclaw" in body.lower()
    except Exception:
        return False


def _find_pids_on_port(port: int) -> list[int]:
    """Return PIDs of TCP listeners on the given port.

    On macOS/Linux/WSL2: uses ``lsof -tiTCP:<port> -sTCP:LISTEN``.
    On native Windows: parses ``netstat -ano`` for LISTEN entries on
    the port (lsof is not part of Windows). Returns ``[]`` when no
    suitable tool is available — callers fall back to socket-only
    detection.
    """
    if os.name == "nt":
        netstat = which("netstat")
        if netstat is None:
            return []
        try:
            proc = subprocess.run(
                [netstat, "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            return []
        pids: list[int] = []
        # Format on Windows: "Proto  Local Address  Foreign Address  State  PID"
        # e.g.  "  TCP    127.0.0.1:8420         0.0.0.0:0    LISTENING       12345"
        for line in proc.stdout.splitlines():
            tokens = line.split()
            if len(tokens) < 5:
                continue
            if tokens[0].upper() != "TCP":
                continue
            local = tokens[1]
            state = tokens[3] if len(tokens) >= 4 else ""
            if not local.endswith(f":{port}"):
                continue
            if state.upper() != "LISTENING":
                continue
            try:
                pids.append(int(tokens[-1]))
            except ValueError:
                continue
        return pids

    lsof = which("lsof")
    if lsof is None:
        return []
    try:
        proc = subprocess.run(
            [lsof, "-tiTCP:" + str(port), "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    pids = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _stop_existing_obc_backend(host: str, port: int) -> bool:
    """Try to gracefully stop any OpenBiliClaw backend already on host:port.

    Returns True if the port is free after the stop attempt; False if a
    non-OpenBiliClaw service still holds the port (caller should abort).
    """
    if not _probe_port_open(host, port):
        return True

    if not _probe_is_openbiliclaw(host, port):
        return False

    pids = _find_pids_on_port(port)
    if not pids:
        info(f"port {port} answers as OpenBiliClaw but no PIDs visible via lsof — proceeding")
        return True

    info(f"existing OpenBiliClaw backend on port {port}: pids={pids} — stopping to replace")
    _terminate_pids(pids, force=False)

    # Wait for the port to actually free up
    for _ in range(20):
        if not _probe_port_open(host, port, timeout=0.2):
            return True
        time.sleep(0.3)

    # Last resort: force-kill stragglers
    _terminate_pids(pids, force=True)
    time.sleep(0.5)
    return not _probe_port_open(host, port, timeout=0.2)


def _terminate_pids(pids: list[int], *, force: bool) -> None:
    """Stop the listed PIDs cross-platform.

    On Unix, send SIGTERM (or SIGKILL when ``force`` is True). On
    Windows, where ``os.kill`` semantics differ and SIGTERM doesn't
    map cleanly, shell out to ``taskkill`` (``/T`` walks the process
    tree, ``/F`` is force).
    """
    if os.name == "nt":
        taskkill = which("taskkill") or "taskkill"
        for pid in pids:
            args = [taskkill, "/PID", str(pid), "/T"]
            if force:
                args.append("/F")
            try:
                subprocess.run(args, capture_output=True, timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                continue
        return

    sig = 9 if force else 15
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue
        except PermissionError:
            info(f"  cannot signal pid {pid} (permission)")


def start_local_backend(project_dir: Path, host: str, port: int) -> subprocess.Popen[bytes]:
    """Start the local FastAPI backend as a detached subprocess.

    If something is already on the port:
      - if it's an OpenBiliClaw backend (likely a previous install's
        process), stop it and replace
      - if it's something else, raise so the caller surfaces a clear error
    """
    connect_host = _connect_host_for_bind_host(host)
    if _probe_port_open(connect_host, port):
        freed = _stop_existing_obc_backend(connect_host, port)
        if not freed:
            raise RuntimeError(
                f"port {port} on {connect_host} is in use by a non-OpenBiliClaw service. "
                f"Stop that service or set PORT=<free port> and retry."
            )

    cmd = local_serve_command(project_dir, host, port)
    log_dir = project_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "agent-bootstrap.log").open("ab")
    info(f"Starting local backend: {shlex.join(cmd)} (logs -> {log_dir / 'agent-bootstrap.log'})")
    # Detach the backend so the installer can exit cleanly. The two
    # platforms need different mechanisms: POSIX ``start_new_session``
    # vs Windows ``creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP``
    # (0x00000008 | 0x00000200).
    if os.name == "nt":
        return subprocess.Popen(
            cmd,
            cwd=str(project_dir),
            stdout=log_file,
            stderr=log_file,
            creationflags=0x00000008 | 0x00000200,
        )
    return subprocess.Popen(
        cmd,
        cwd=str(project_dir),
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )


# ---------------------------------------------------------------------------
# Docker deployment


def docker_compose_up(project_dir: Path) -> None:
    docker = which("docker")
    if docker is None:
        raise RuntimeError("docker is not available on PATH")
    run_streaming([docker, "compose", "up", "-d", "--build"], cwd=project_dir)


def build_docker_runtime_sync_commands(project_dir: Path) -> list[list[str]]:
    """Return docker commands that copy confirmed host config into runtime volume."""

    commands = [
        [
            "docker",
            "exec",
            DOCKER_CONTAINER_NAME,
            "mkdir",
            "-p",
            f"{DOCKER_RUNTIME_ROOT}/data",
        ],
        [
            "docker",
            "cp",
            str(project_dir / "config.toml"),
            f"{DOCKER_CONTAINER_NAME}:{DOCKER_RUNTIME_ROOT}/config.toml",
        ],
    ]
    cookie_file = project_dir / "data" / "bilibili_cookie.json"
    if cookie_file.exists():
        commands.append(
            [
                "docker",
                "cp",
                str(cookie_file),
                f"{DOCKER_CONTAINER_NAME}:{DOCKER_RUNTIME_ROOT}/data/bilibili_cookie.json",
            ]
        )
    return commands


def sync_docker_runtime_config(project_dir: Path) -> None:
    """Copy bootstrap-written config into the running Docker runtime volume."""

    for command in build_docker_runtime_sync_commands(project_dir):
        run_streaming(command, cwd=project_dir)


def build_docker_missing_secrets_command() -> list[str]:
    """Return command that inspects secrets inside the backend container."""

    script = r"""
import json
import os
import tomllib
from pathlib import Path

config_path = Path("/app/runtime/config.toml")
cookie_path = Path("/app/runtime/data/bilibili_cookie.json")
data = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
models = data.get("models", {})
chat = models.get("chat", {})
connections = chat.get("connections", [])
primary = connections[0] if connections else {}
connection_id = str(primary.get("id", "") or "").strip()
connection_type = str(primary.get("type", "") or "").strip()
preset = str(primary.get("preset", "") or "").strip()
api_key = str(primary.get("api_key", "") or "").strip()
api_key_env = str(primary.get("api_key_env", "") or "").strip()
credential_ref = str(primary.get("credential_ref", "") or "").strip()
base_url = str(primary.get("base_url", "") or "").strip()
bilibili = data.get("bilibili", {})
cookie_inline = str(bilibili.get("cookie", "") or "").strip()
cookie_on_disk = False
if cookie_path.exists():
    try:
        cookie_on_disk = bool(str(json.loads(cookie_path.read_text(encoding="utf-8")).get("cookie", "")).strip())
    except json.JSONDecodeError:
        cookie_on_disk = False
missing = []
if not primary:
    missing.append("models.chat.connections")
elif connection_type == "codex_oauth":
    if not credential_ref:
        missing.append(f"models.chat.connections.{connection_id}.credential")
elif connection_type != "ollama":
    env_ready = bool(api_key_env and str(os.environ.get(api_key_env, "") or "").strip())
    if not (api_key or env_ready):
        missing.append(f"models.chat.connections.{connection_id}.credential")
if connection_type in {"openai_compatible", "anthropic_compatible", "ollama"} and not base_url:
    missing.append(f"models.chat.connections.{connection_id}.base_url")
if not (cookie_inline or cookie_on_disk):
    missing.append("bilibili.cookie")
print(json.dumps({
    "provider": preset or connection_type,
    "connection_type": connection_type,
    "preset": preset,
    "connection_id": connection_id,
    "missing": missing,
    "has_cookie_inline": bool(cookie_inline),
    "has_cookie_file": cookie_on_disk,
}))
""".strip()
    return ["docker", "exec", DOCKER_CONTAINER_NAME, "python", "-c", script]


def detect_docker_missing_secrets(_project_dir: Path) -> dict[str, Any]:
    """Return missing secrets from the running Docker runtime config."""

    proc = subprocess.run(
        build_docker_missing_secrets_command(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "docker secret detection failed")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Health check


# ── Reused-cookie live validation (init-progress spec Phase 3) ──────────────
# ``--reuse-from`` copies the old install's Bilibili cookie by file existence
# only. B站 cookies expire within weeks; a stale one used to surface only when
# init later failed with empty_history. Once the backend is healthy, its
# /api/init-status prerequisites already run a REAL validate_cookie probe
# (runtime/init_prereqs.py) — consume that instead of adding a second
# validation path.

STALE_COOKIE_MISSING_ENTRY = "bilibili.cookie (stale — reused cookie failed live validation)"


def _init_status_url(host: str, port: int) -> str:
    return _health_url(host, port).replace("/api/health", "/api/init-status")


def fetch_init_status(host: str, port: int, timeout: float = 30.0) -> dict[str, Any] | None:
    """GET /api/init-status; None when unreachable / malformed (indeterminate).

    The timeout is generous because the endpoint runs live provider probes
    (chat + bilibili + embedding) on a not-yet-initialized backend.
    """
    try:
        with urllib.request.urlopen(_init_status_url(host, port), timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _reused_cookie_stale(init_status: dict[str, Any] | None) -> bool | None:
    """True/False from the live probe; None when indeterminate.

    Only an explicit ``failed`` counts as stale — ``checking`` / a missing
    field / an unreachable endpoint must NOT downgrade the install (never
    claim a staleness we didn't observe; the generic install.sh disclaimer
    covers the couldn't-validate case).
    """
    if not isinstance(init_status, dict):
        return None
    prereq = init_status.get("prerequisites")
    if not isinstance(prereq, dict):
        return None
    check = str(prereq.get("bilibili_check", "") or "")
    if check == "ok":
        return False
    if check == "failed":
        return True
    return None


def apply_reused_cookie_validation(
    final_status: dict[str, Any],
    *,
    reuse_summary: dict[str, Any] | None,
    init_status: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fold the live cookie probe into the final status summary.

    When THIS run reused a Bilibili cookie and the backend's live probe says
    it's dead, append a self-describing ``missing`` entry and flag the status
    so ``backend_healthy_label`` downgrades to ``needs_secrets`` instead of
    reporting ``complete`` for an install whose init can only fail.
    """
    reused = reuse_summary.get("reused") if isinstance(reuse_summary, dict) else None
    cookie_reused = any(
        item in ("bilibili.cookie", "data/bilibili_cookie.json") for item in (reused or [])
    )
    if not cookie_reused:
        return final_status
    if _reused_cookie_stale(init_status) is not True:
        return final_status
    updated = dict(final_status)
    missing = list(updated.get("missing") or [])
    if STALE_COOKIE_MISSING_ENTRY not in missing:
        missing.append(STALE_COOKIE_MISSING_ENTRY)
    updated["missing"] = missing
    updated["reused_cookie_stale"] = True
    return updated


def backend_healthy_label(final_status: dict[str, Any]) -> str:
    """Status label for the backend-healthy summary block."""
    if final_status.get("reused_cookie_stale"):
        return "needs_secrets"
    return "complete" if not final_status.get("missing") else "running_with_missing_secrets"


def wait_for_health(host: str, port: int, timeout: float = HEALTH_TIMEOUT_SECONDS) -> bool:
    """Poll /api/health until it returns 200 or timeout expires."""

    url = _health_url(host, port)
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=HEALTH_POLL_INTERVAL) as response:  # noqa: S310
                if 200 <= response.status < 300:
                    return True
                last_error = f"status={response.status}"
        except urllib.error.URLError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(HEALTH_POLL_INTERVAL)
    info(f"health check timed out: {last_error}")
    return False


def wait_for_cookie_sync(
    project_dir: Path,
    *,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 2.0,
    detector: Callable[[Path], dict[str, Any]] = detect_missing_secrets,
) -> bool:
    """Wait until Bilibili cookie arrives via extension sync."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        missing = detector(project_dir).get("missing", [])
        if "bilibili.cookie" not in missing:
            return True
        time.sleep(interval_seconds)
    return False


# ---------------------------------------------------------------------------
# Orchestration


def run(args: argparse.Namespace) -> int:
    ensure_local_no_proxy()
    project_dir = Path(args.project_dir)
    try:
        project_dir = ensure_repo_checkout(project_dir, args.repo_url, args.branch)
    except RuntimeError as exc:
        emit(BootstrapResult("error", str(exc), {"step": "clone"}))
        return 2

    emit(BootstrapResult("ok", "repo_ready", {"project_dir": str(project_dir)}))

    try:
        ensure_config_toml(project_dir)
    except RuntimeError as exc:
        emit(BootstrapResult("error", str(exc), {"step": "config"}))
        return 2

    reuse_summary: dict[str, Any] | None = None

    if args.interactive_confirm:
        try:
            current = detect_missing_secrets(project_dir)
            provider = str(current.get("provider") or "deepseek")
            current_connections = _load_typed_models(project_dir).chat.connections
            primary = current_connections[0] if current_connections else None
            answers = collect_human_install_wizard(
                existing_provider=provider,
                existing_api_key=(
                    primary.credential.value
                    if primary is not None and primary.credential.source == "inline"
                    else ""
                ),
                existing_base_url=primary.base_url if primary is not None else "",
                existing_model=primary.model if primary is not None else "",
            )
            apply_human_install_answers_to_args(args, answers)
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "interactive_confirm"}))
            return 2
        emit(
            BootstrapResult(
                "ok",
                "human_install_choices_set",
                {
                    "connection_type": args.connection_type,
                    "preset": args.preset or "",
                    "llm_model": args.llm_model,
                    "embedding_provider": args.embedding_provider,
                    "embedding_model": args.embedding_model,
                    "xhs": "yes" if args.yes_xhs else "no",
                    "douyin": "yes" if args.yes_douyin else "no",
                    "youtube": "yes" if args.yes_youtube else "no",
                    "cookie_mode": answers.cookie_mode,
                },
            )
        )

    if getattr(args, "llm_preset", None):
        legacy_preset = LLM_PRESETS.get(args.llm_preset, {})
        if args.connection_type and args.connection_type != "openai_compatible":
            emit(BootstrapResult("error", "preset_connection_type_conflict", {}))
            return 2
        args.connection_type = "openai_compatible"
        args.preset = "custom"
        if args.llm_base_url is None and legacy_preset.get("base_url"):
            args.llm_base_url = legacy_preset["base_url"]
        if args.llm_model is None and legacy_preset.get("model"):
            args.llm_model = legacy_preset["model"]

    try:
        _validate_key_only_embedding_target(
            project_dir,
            provider=args.embedding_provider,
            endpoints=args.embedding_endpoint,
            api_key=args.embedding_api_key,
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        emit(BootstrapResult("error", str(exc), {"step": "models.embedding"}))
        return 2

    chat_summary: dict[str, Any] | None = None
    selected_chat: tuple[str, str] | None = None
    current_connections = _load_typed_models(project_dir).chat.connections
    current_primary = current_connections[0] if current_connections else None
    chat_touched = (
        any(
            value is not None
            for value in (
                args.connection_type,
                args.preset,
                args.provider,
                args.llm_api_key,
                args.llm_base_url,
                args.llm_model,
                getattr(args, "llm_preset", None),
            )
        )
        or current_primary is None
    )
    if chat_touched:
        try:
            if args.connection_type or args.preset or args.provider:
                connection_type, preset = resolve_connection_selection(
                    provider=args.provider,
                    connection_type=args.connection_type,
                    preset=args.preset,
                )
            elif current_primary is None:
                connection_type, preset = resolve_connection_selection()
            else:
                connection_type, preset = current_primary.type, current_primary.preset
            selected_chat = (connection_type, preset)
            chat_summary = apply_chat_route_config(
                project_dir,
                connection_type=connection_type,
                preset=preset,
                model=args.llm_model,
                base_url=args.llm_base_url,
                api_key=None if args.reuse_from else args.llm_api_key,
                credential_ref="codex" if connection_type == "codex_oauth" else None,
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            emit(BootstrapResult("error", str(exc), {"step": "models.chat"}))
            return 2

    embedding_summary: dict[str, Any] | None = None
    embedding_touched = (
        args.embedding_provider is not None
        or args.embedding_model is not None
        or args.embedding_base_url is not None
        or args.embedding_api_key is not None
        or args.embedding_endpoint is not None
    )
    if embedding_touched:
        try:
            embedding_summary = apply_embedding_config(
                project_dir,
                provider=args.embedding_provider,
                model=args.embedding_model,
                base_url=args.embedding_base_url,
                api_key=None if args.reuse_from else args.embedding_api_key,
                endpoints=args.embedding_endpoint,
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            emit(BootstrapResult("error", str(exc), {"step": "models.embedding"}))
            return 2
    if args.reuse_from:
        try:
            reuse_summary = reuse_config_secrets(project_dir, Path(args.reuse_from))
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "reuse"}))
            return 2
        emit(BootstrapResult("ok", "secrets_reused", reuse_summary))

        if selected_chat is not None and args.llm_api_key is not None:
            connection_type, preset = selected_chat
            try:
                chat_summary = apply_chat_route_config(
                    project_dir,
                    connection_type=connection_type,
                    preset=preset,
                    model=args.llm_model,
                    base_url=args.llm_base_url,
                    api_key=args.llm_api_key,
                    credential_ref="codex" if connection_type == "codex_oauth" else None,
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                emit(BootstrapResult("error", str(exc), {"step": "models.chat"}))
                return 2
        if embedding_touched and args.embedding_api_key is not None:
            try:
                embedding_summary = apply_embedding_config(
                    project_dir,
                    provider=args.embedding_provider,
                    model=args.embedding_model,
                    base_url=args.embedding_base_url,
                    api_key=args.embedding_api_key,
                    endpoints=args.embedding_endpoint,
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                emit(BootstrapResult("error", str(exc), {"step": "models.embedding"}))
                return 2

    if chat_summary is not None:
        emit(BootstrapResult("ok", "chat_route_set", chat_summary))
    if embedding_summary is not None:
        emit(BootstrapResult("ok", "embedding_set", embedding_summary))

    auto_embedding_to_ollama = False

    if args.bilibili_cookie:
        update_config_secret(
            project_dir / "config.toml", "bilibili", "cookie", args.bilibili_cookie
        )
        persist_cookie_file(project_dir, args.bilibili_cookie)
        emit(BootstrapResult("ok", "cookie_set", {}))

    status = detect_missing_secrets(project_dir)
    emit(BootstrapResult("ok", "config_summary", status))

    mode = args.mode
    if mode == "auto":
        mode = "docker" if detect_docker() else "local"
    emit(BootstrapResult("ok", "mode_selected", {"mode": mode}))
    if mode == "docker":
        docker_config_summary = align_docker_runtime_config(project_dir)
        if docker_config_summary["written"]:
            emit(
                BootstrapResult(
                    "ok",
                    "docker_runtime_config_aligned",
                    docker_config_summary,
                )
            )

    # Keep local semantic dedup available when no native embedding providers
    # were selected. An explicit empty legacy alias still means disabled.
    embedding_route = _load_typed_models(project_dir).embedding
    effective_embedding_provider = (
        embedding_route.providers[0].type if embedding_route.providers else ""
    )
    if should_auto_wire_embedding(
        embedding_provider_arg=args.embedding_provider,
        effective_provider=effective_embedding_provider,
        mode=mode,
    ):
        apply_embedding_config(
            project_dir,
            provider="ollama",
            model="bge-m3",
            base_url=None,
            api_key=None,
            endpoints=None,
        )
        auto_embedding_to_ollama = True
        emit(
            BootstrapResult(
                "ok",
                "embedding_auto_ollama",
                {
                    "provider": "ollama",
                    "model": "bge-m3",
                    "reason": (
                        "embedding was unconfigured; defaulted to local Ollama bge-m3 "
                        "so semantic dedup isn't silently disabled"
                    ),
                },
            )
        )

    # When the user picks ollama for either LLM or embedding, the install
    # isn't really "done" until ollama is installed, the daemon is running,
    # and the requested models are pulled. Without this step the user
    # would land in a "config saved but nothing works" state.
    #
    # Inside Docker we deliberately skip this — the container talks to
    # the *host's* ollama at host.docker.internal, and managing a host
    # service from inside a container would be wrong.
    ollama_models_needed: list[str] = []
    effective_models = _load_typed_models(project_dir)
    if effective_models.chat.connections and effective_models.chat.connections[0].type == "ollama":
        ollama_models_needed.append(effective_models.chat.connections[0].model.strip())
    if any(item.type == "ollama" for item in effective_models.embedding.providers):
        ollama_models_needed.append(effective_models.embedding.settings.model.strip())
    # When we auto-wired Ollama for embedding (Claude / DeepSeek /
    # OpenRouter primary path), make sure bge-m3 is pulled so the
    # embedding service has a working backend at first run.
    if auto_embedding_to_ollama:
        ollama_models_needed.append("bge-m3")
    ollama_models_needed = [m for m in ollama_models_needed if m]
    # Dedupe while preserving order (chat model first, then embedding).
    deduped: list[str] = []
    for model_name in ollama_models_needed:
        if model_name not in deduped:
            deduped.append(model_name)
    ollama_models_needed = deduped
    if ollama_models_needed and not args.skip_ollama_setup and mode != "docker":
        ollama_summary = ensure_ollama_ready(ollama_models_needed)
        emit(BootstrapResult("ok", "ollama_ready", ollama_summary))

    if not args.skip_install and mode == "local":
        try:
            local_install(project_dir, args.install_cmd, args.python)
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "install"}))
            return 3
        emit(BootstrapResult("ok", "dependencies_installed", {}))

    if args.skip_start:
        remaining = detect_missing_secrets(project_dir)
        skipped_label = "complete" if not remaining["missing"] else "needs_secrets"
        init_decisions = detect_init_decisions(
            project_dir,
            args,
            embedding_touched=embedding_touched,
        )
        emit(
            BootstrapResult(
                skipped_label,
                "skipped_start",
                {**remaining, "init_decisions": init_decisions},
            )
        )
        return 0

    if mode == "docker":
        try:
            docker_compose_up(project_dir)
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "docker_up"}))
            return 4
        emit(BootstrapResult("ok", "docker_started", {}))
    else:
        try:
            start_local_backend(project_dir, args.host, args.port)
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "local_start"}))
            return 5
        emit(BootstrapResult("ok", "local_started", {"host": args.host, "port": args.port}))

    if args.skip_health_check:
        final_status = detect_missing_secrets(project_dir)
        final_label = "complete" if not final_status["missing"] else "needs_secrets"
        init_decisions = detect_init_decisions(
            project_dir,
            args,
            embedding_touched=embedding_touched,
        )
        emit(
            BootstrapResult(
                final_label,
                "health_check_skipped",
                {**final_status, "init_decisions": init_decisions},
            )
        )
        return 0

    healthy = wait_for_health(args.host, args.port)
    if healthy:
        status_detector: Callable[[Path], dict[str, Any]] = detect_missing_secrets
        if mode == "docker":
            try:
                sync_docker_runtime_config(project_dir)
            except RuntimeError as exc:
                emit(BootstrapResult("error", str(exc), {"step": "docker_config_sync"}))
                return 4
            status_detector = detect_docker_missing_secrets

        final_status = status_detector(project_dir)
        # Live-validate a reused cookie against the backend's real probe
        # (init-progress spec Phase 3): a stale cookie must surface NOW as
        # needs_secrets, not 30s into init as empty_history.
        if reuse_summary is not None:
            final_status = apply_reused_cookie_validation(
                final_status,
                reuse_summary=reuse_summary,
                init_status=fetch_init_status(args.host, args.port),
            )
            if final_status.get("reused_cookie_stale"):
                emit(BootstrapResult("needs_secrets", "reused_cookie_stale", final_status))
                info(
                    "The Bilibili cookie reused from the previous install failed live "
                    "validation (expired/invalid). Log in to bilibili.com again and let "
                    "the browser extension sync a fresh cookie, or pass --bilibili-cookie."
                )
        if args.wait_for_extension_cookie and final_status["missing"] == ["bilibili.cookie"]:
            emit(
                BootstrapResult(
                    "progress",
                    "waiting_for_extension_cookie",
                    {
                        "timeout_seconds": 300,
                        "hint": "Install the browser extension and log in to bilibili.com.",
                    },
                )
            )
            if wait_for_cookie_sync(project_dir, detector=status_detector):
                final_status = status_detector(project_dir)
                emit(BootstrapResult("ok", "extension_cookie_synced", final_status))
            else:
                emit(
                    BootstrapResult(
                        "needs_secrets",
                        "extension_cookie_wait_timeout",
                        final_status,
                    )
                )

        init_decisions = detect_init_decisions(
            project_dir,
            args,
            embedding_touched=embedding_touched,
        )
        label = backend_healthy_label(final_status)
        if not final_status["missing"] and init_decisions["missing"] and not args.skip_init:
            label = "needs_decisions"
        health_details = {
            "health_url": _health_url(args.host, args.port),
            **final_status,
            "init_decisions": init_decisions,
        }
        emit(
            BootstrapResult(
                label,
                "backend_healthy",
                health_details,
            )
        )

        # Auto-run init when all credentials are present and --skip-init is
        # not set. The user finished giving us their credentials — they
        # expect the system to be in a usable state when we hand control
        # back. init = pull history + generate soul profile + first
        # discovery pass. Without it the user opens the extension and
        # sees nothing.
        if not final_status["missing"] and not args.skip_init:
            if init_decisions["missing"]:
                emit(
                    BootstrapResult(
                        "needs_decisions",
                        "init_decisions_required",
                        health_details,
                    )
                )
                info(
                    "Credentials are present, but init was not run because "
                    "the agent has not supplied explicit choices for: "
                    + ", ".join(init_decisions["missing"])
                )
                return 0

            info("Checking LLM provider and embedding service before init...")
            service_checks = run_pre_init_service_checks(project_dir, mode)
            if not service_checks["available"]:
                emit(
                    BootstrapResult(
                        "service_check_failed",
                        "pre_init_service_check_failed",
                        {**health_details, "service_checks": service_checks},
                    )
                )
                failed = ", ".join(service_checks.get("failed", [])) or "unknown"
                info(
                    "Pre-init AI service check failed for: "
                    f"{failed}. Fix the provider/API key/Ollama/model, then re-run "
                    "the printed bootstrap command. Init has not been run."
                )
                return 0
            emit(
                BootstrapResult(
                    "ok",
                    "pre_init_service_check_passed",
                    {**health_details, "service_checks": service_checks},
                )
            )

            info(
                "All credentials present — running 'openbiliclaw init' to reach usable state... "
                "(this takes 2-5 minutes: real LLM calls + Bilibili history fetches)"
            )
            try:
                xhs_flag = str(init_decisions["xhs"]["flag"])
                douyin_flag = str(init_decisions["douyin"]["flag"])
                youtube_flag = str(init_decisions["youtube"]["flag"])
                init_cmd = build_init_command(
                    mode,
                    project_dir,
                    xhs_flag,
                    douyin_flag,
                    youtube_flag,
                    bilibili_favorite_limit=args.bilibili_favorite_limit,
                    bilibili_follow_limit=args.bilibili_follow_limit,
                )
                init_returncode = run_init_streaming(init_cmd, cwd=project_dir, check=False)
                if init_returncode != 0:
                    emit(
                        BootstrapResult(
                            "warning",
                            "init_failed",
                            {
                                "returncode": init_returncode,
                                "init_command": shlex.join(init_cmd),
                            },
                        )
                    )
                    info(
                        "Init exited with a non-zero status, but the backend is running. "
                        "You can run 'openbiliclaw init' manually later "
                        "(or 'docker exec -it openbiliclaw-backend openbiliclaw init' for Docker)."
                    )
                    return 0
                emit(
                    BootstrapResult(
                        "complete",
                        "init_complete",
                        {**health_details, "init_command": shlex.join(init_cmd)},
                    )
                )
            except Exception as exc:
                emit(BootstrapResult("warning", "init_failed", {"error": str(exc)}))
                info(
                    f"Init failed ({exc}), but the backend is running. "
                    "You can run 'openbiliclaw init' manually later "
                    "(or 'docker exec -it openbiliclaw-backend openbiliclaw init' for Docker)."
                )

        return 0

    final_status = detect_missing_secrets(project_dir)
    init_decisions = detect_init_decisions(
        project_dir,
        args,
        embedding_touched=embedding_touched,
    )
    emit(
        BootstrapResult(
            "error",
            "health_check_failed",
            {
                "health_url": _health_url(args.host, args.port),
                **final_status,
                "init_decisions": init_decisions,
            },
        )
    )
    return 5


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        emit(BootstrapResult("error", "interrupted", {}))
        return 130
    except Exception as exc:  # noqa: BLE001
        emit(BootstrapResult("error", f"unexpected: {exc}", {}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
