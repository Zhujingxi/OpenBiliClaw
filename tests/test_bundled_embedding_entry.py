"""Tests for the desktop with-embedding startup orchestration (plan Task 3)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.runtime import ollama_supervisor

if TYPE_CHECKING:
    import pytest

# packaging/entry.py is a script, not an importable package member.
_ENTRY_PATH = Path(__file__).resolve().parents[1] / "packaging" / "entry.py"
_spec = importlib.util.spec_from_file_location("obc_entry", _ENTRY_PATH)
assert _spec and _spec.loader
entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(entry)

_MAKE_SEED_PATH = Path(__file__).resolve().parents[1] / "packaging" / "make_model_seed.py"
_ms_spec = importlib.util.spec_from_file_location("make_model_seed", _MAKE_SEED_PATH)
assert _ms_spec and _ms_spec.loader
make_model_seed = importlib.util.module_from_spec(_ms_spec)
_ms_spec.loader.exec_module(make_model_seed)


def _fake_ollama_store(root: Path) -> None:
    import hashlib
    import json

    blobs = root / "models" / "blobs"
    blobs.mkdir(parents=True)

    def put(content: bytes) -> tuple[str, int]:
        hx = hashlib.sha256(content).hexdigest()
        (blobs / f"sha256-{hx}").write_bytes(content)
        return f"sha256:{hx}", len(content)

    cfg_d, cfg_s = put(b'{"model_format":"gguf"}')
    model_d, model_s = put(b"WEIGHTS" * 32)
    manifest = {
        "schemaVersion": 2,
        "config": {"digest": cfg_d, "size": cfg_s},
        "layers": [{"digest": model_d, "size": model_s}],
    }
    mpath = root / "models" / "manifests/registry.ollama.ai/library/bge-m3/latest"
    mpath.parent.mkdir(parents=True)
    mpath.write_text(json.dumps(manifest))


def _bundled_with_seed(tmp_path: Path) -> Path:
    """A bundled_resources dir containing a real bge-m3-seed produced by Task 1."""
    src = tmp_path / "ollama-src"
    _fake_ollama_store(src)
    resources = tmp_path / "resources"
    resources.mkdir()
    make_model_seed.make_seed(src, "bge-m3", "latest", resources / "bge-m3-seed")
    return resources


def _write_config(tmp_path: Path, *, provider: str = "") -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[llm.embedding]\n"
        f'provider = "{provider}"\n'
        'base_url = ""\n'
        'model = "bge-m3"\n',
        encoding="utf-8",
    )
    return cfg


def test_set_embedding_base_url_edits_only_that_line(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    entry._set_embedding_base_url(cfg, "http://127.0.0.1:11435/v1")
    text = cfg.read_text()
    assert 'base_url = "http://127.0.0.1:11435/v1"' in text
    assert 'model = "bge-m3"' in text  # untouched


def test_seed_bundled_lean_variant_is_noop(tmp_path: Path) -> None:
    # No bge-m3-seed dir => lean build => no-op False.
    resources = tmp_path / "resources"
    resources.mkdir()
    assert entry._seed_bundled_embedding_model(resources, tmp_path / "config.toml") is False


def test_seed_bundled_success_starts_private_daemon_and_points_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = _write_config(tmp_path)
    resources = _bundled_with_seed(tmp_path)
    models = tmp_path / "eff-models"
    monkeypatch.setenv("OLLAMA_MODELS", str(models))  # ASCII + writable => effective dir

    started: dict[str, str] = {}

    def _fake_start(models_dir: str, host: str) -> bool:
        started["dir"] = models_dir
        started["host"] = host
        return True

    monkeypatch.setattr(ollama_supervisor, "start_managed_ollama_at", _fake_start)

    ok = entry._seed_bundled_embedding_model(resources, cfg)
    assert ok is True
    # model seeded into the effective dir, and the private daemon was started on it
    assert (models / "manifests/registry.ollama.ai/library/bge-m3/latest").is_file()
    assert started["dir"] == str(models)
    assert started["host"] == "127.0.0.1:11435"
    # config now points embedding at the private endpoint
    assert 'base_url = "http://127.0.0.1:11435/v1"' in cfg.read_text()


def test_seed_bundled_respects_remote_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = _write_config(tmp_path, provider="openai")
    resources = _bundled_with_seed(tmp_path)
    # User chose a remote embedding provider — do not hijack it.
    assert entry._seed_bundled_embedding_model(resources, cfg) is False


def test_seed_bundled_daemon_failure_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = _write_config(tmp_path)
    resources = _bundled_with_seed(tmp_path)
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "eff"))
    monkeypatch.setattr(ollama_supervisor, "start_managed_ollama_at", lambda d, h: False)
    # Seeding succeeds but the private daemon won't start => fall back (False).
    assert entry._seed_bundled_embedding_model(resources, cfg) is False
