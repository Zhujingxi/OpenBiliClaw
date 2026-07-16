"""Contract tests for standalone scripts' native model composition helper."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.config import Config
from openbiliclaw.model_config import compute_model_revision

if TYPE_CHECKING:
    import pytest


def test_script_model_helper_delegates_to_native_runtime_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    sys.modules.pop("_model_runtime", None)
    helper = importlib.import_module("_model_runtime")
    config = Config()
    memory = object()
    sentinel = object()
    captured: dict[str, object] = {}

    def _build(models: object, revision: str, **kwargs: object) -> object:
        captured.update(models=models, revision=revision, **kwargs)
        return sentinel

    monkeypatch.setattr(helper, "build_runtime_model_bundle", _build)

    assert helper.build_script_model_bundle(config, memory) is sentinel
    assert captured["models"] is config.models
    assert captured["revision"] == compute_model_revision(config.models)
    assert captured["memory"] is memory
    assert captured["usage_sink"] is None
    gate = captured["concurrency_gate"]
    assert gate.total_concurrency == config.models.chat.concurrency
