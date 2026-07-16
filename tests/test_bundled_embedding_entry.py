"""Tests for the desktop with-embedding startup orchestration (plan Task 3)."""

from __future__ import annotations

import importlib.util
import tomllib
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
    embedding_enabled = provider == "openai"
    provider_block = (
        """
[[models.embedding.providers]]
id = "remote-embedding"
name = "Remote Embedding"
type = "openai_compatible"
preset = "openai"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
"""
        if embedding_enabled
        else ""
    )
    cfg.write_text(
        f"""[models]
schema_version = 1
[models.chat]
concurrency = 4
timeout_seconds = 300
[[models.chat.connections]]
id = "chat-main"
name = "Primary Chat"
type = "openai_compatible"
preset = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
[models.embedding]
enabled = {str(embedding_enabled).lower()}
[models.embedding.settings]
model = "{"text-embedding-3-small" if embedding_enabled else "bge-m3"}"
output_dimensionality = {1536 if embedding_enabled else 1024}
similarity_threshold = 0.82
multimodal_enabled = false
{provider_block}
[general]
language = "zh"
""",
        encoding="utf-8",
    )
    return cfg


def test_configure_ollama_embedding_updates_native_route_and_keeps_chat(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)

    assert (
        entry._configure_ollama_embedding(
            cfg,
            provider_id="ollama-packaged",
            name="Bundled Ollama",
            base_url="http://127.0.0.1:11435/v1",
            model="bge-m3",
        )
        is True
    )

    raw = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert raw["models"]["chat"]["connections"][0]["id"] == "chat-main"
    assert raw["models"]["embedding"]["settings"]["model"] == "bge-m3"
    assert raw["models"]["embedding"]["providers"] == [
        {
            "id": "ollama-packaged",
            "name": "Bundled Ollama",
            "type": "ollama",
            "base_url": "http://127.0.0.1:11435/v1",
        }
    ]
    assert raw["general"] == {"language": "zh"}


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


# --- Task 3: desktop boot contract — adoption recording + watchdog arming ---


def test_seed_bundled_success_records_daemon_and_arms_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) the seeding success path leaves a managed record and an armed watchdog."""
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = _write_config(tmp_path)
    resources = _bundled_with_seed(tmp_path)
    models = tmp_path / "eff-models"
    monkeypatch.setenv("OLLAMA_MODELS", str(models))

    monkeypatch.setattr(ollama_supervisor, "_managed_daemon", None)
    armed: list[str] = []
    monkeypatch.setattr(
        ollama_supervisor, "start_ollama_watchdog", lambda *a, **k: armed.append("armed")
    )
    # The private port already answers => start_managed_ollama_at takes the
    # adoption branch (no spawn), which must still record + arm.
    monkeypatch.setattr(ollama_supervisor, "_ollama_is_running", lambda *a, **k: True)

    assert entry._seed_bundled_embedding_model(resources, cfg) is True
    record = ollama_supervisor._managed_daemon
    assert record is not None
    assert record.base_url == "http://127.0.0.1:11435"
    assert record.models_dir == str(models)
    assert armed == ["armed"]


def test_seed_bundled_adoption_records_proc_none_and_managed_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) the adoption branch records proc=None and is_managed_endpoint is True."""
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = _write_config(tmp_path)
    resources = _bundled_with_seed(tmp_path)
    models = tmp_path / "eff-models"
    monkeypatch.setenv("OLLAMA_MODELS", str(models))

    monkeypatch.setattr(ollama_supervisor, "_managed_daemon", None)
    monkeypatch.setattr(ollama_supervisor, "start_ollama_watchdog", lambda *a, **k: None)
    monkeypatch.setattr(ollama_supervisor, "_ollama_is_running", lambda *a, **k: True)

    assert entry._seed_bundled_embedding_model(resources, cfg) is True
    record = ollama_supervisor._managed_daemon
    assert record is not None
    assert record.proc is None  # adopted force-quit orphan: recorded, not signalable
    assert ollama_supervisor.is_managed_endpoint("http://127.0.0.1:11435") is True


def test_watchdog_relaunches_dead_adopted_private_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) after an adopted daemon dies, the watchdog relaunches it with the
    recorded models dir on the recorded private host."""
    sup = ollama_supervisor
    models_dir = str(tmp_path / "eff-models")
    monkeypatch.setattr(
        sup, "_managed_daemon", sup._ManagedDaemon(None, "http://127.0.0.1:11435", models_dir)
    )
    monkeypatch.setattr(sup, "_watchdog_failures", 0)
    monkeypatch.setattr(sup, "_watchdog_gave_up", False)
    monkeypatch.setattr(sup, "_restart_in_progress", False)
    monkeypatch.setattr(sup, "_watchdog_sleep", lambda s: None)
    monkeypatch.setattr(sup, "start_ollama_watchdog", lambda *a, **k: None)
    monkeypatch.setattr(sup, "_watchdog_probe", lambda url: False)  # daemon died

    # Restart flow: refusal probe dead, start guard dead, post-spawn healthy.
    probes = iter([False, False, True])
    monkeypatch.setattr(sup, "_ollama_is_running", lambda *a, **k: next(probes))

    spawns: list[dict[str, object]] = []

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.pid = 555
            spawns.append(kwargs)

        def poll(self) -> None:
            return None

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    sup._watchdog_tick()

    assert len(spawns) == 1
    env = spawns[0]["env"]
    assert isinstance(env, dict)
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert env["OLLAMA_MODELS"] == models_dir
    record = sup._managed_daemon
    assert record is not None
    assert record.proc is not None  # fresh daemon is now owned, not adopted
    assert record.base_url == "http://127.0.0.1:11435"
