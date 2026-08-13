"""Narrow atomic persistence for the UI-managed model configuration."""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.core.config import ModelSettings

_TABLE = re.compile(r"^\s*\[([^]]+)]\s*(?:#.*)?$")


def render_model_section(settings: ModelSettings) -> str:
    """Render only ``[model]`` and its owned nested tables as TOML."""

    lines = [
        "[model]",
        f"provider = {json.dumps(settings.provider)}",
        f"model_name = {json.dumps(settings.model_name)}",
    ]
    if settings.protocol is not None:
        lines.append(f"protocol = {json.dumps(settings.protocol)}")
    if settings.endpoint is not None:
        lines.append(f"endpoint = {json.dumps(settings.endpoint)}")
    if settings.secret_ref is not None:
        lines.append(f"secret_ref = {json.dumps(settings.secret_ref)}")
    lines.extend(
        (
            "",
            "[model.options]",
            f"disable_thinking = {str(settings.options.disable_thinking).lower()}",
        )
    )
    if settings.capabilities is not None:
        capabilities = settings.capabilities
        lines.extend(
            (
                "",
                "[model.capabilities]",
                f"tools = {str(capabilities.tools).lower()}",
                f"structured_output = {str(capabilities.structured_output).lower()}",
                f"vision = {str(capabilities.vision).lower()}",
                f"context_tokens = {capabilities.context_tokens}",
                f"streaming = {str(capabilities.streaming).lower()}",
                f"reasoning = {str(capabilities.reasoning).lower()}",
            )
        )
    return "\n".join(lines) + "\n"


def replace_model_section(document: str, settings: ModelSettings) -> str:
    """Replace model-owned tables while preserving every other byte range."""

    lines = document.splitlines(keepends=True)
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = _TABLE.match(line.rstrip("\r\n"))
        if match is None:
            continue
        table = match.group(1).strip()
        if start is None:
            if table == "model":
                start = index
        elif table != "model" and not table.startswith("model."):
            end = index
            break
    rendered = render_model_section(settings)
    if start is None:
        separator = "" if not document or document.endswith("\n\n") else "\n"
        return f"{document}{separator}{rendered}"
    return "".join(lines[:start]) + rendered + "\n" + "".join(lines[end:])


def write_model_settings(path: Path, settings: ModelSettings) -> None:
    """Atomically update model tables in an existing configuration file."""

    document = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = replace_model_section(document, settings)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, path.stat().st_mode if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
