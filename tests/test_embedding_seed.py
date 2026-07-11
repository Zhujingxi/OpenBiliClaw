"""Tests for the bundled-embedding seed core (plan Task 1 + Task 2)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from openbiliclaw.runtime.embedding_seed import (
    SeedResult,
    effective_embedding_models_dir,
    seed_embedding_model,
)

# packaging/ is not an importable package (invoked as a script) — load by path.
_MAKE_SEED_PATH = Path(__file__).resolve().parents[1] / "packaging" / "make_model_seed.py"
_spec = importlib.util.spec_from_file_location("make_model_seed", _MAKE_SEED_PATH)
assert _spec and _spec.loader
make_model_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_model_seed)


def _put_blob(blobs_dir: Path, content: bytes) -> tuple[str, int]:
    hx = hashlib.sha256(content).hexdigest()
    (blobs_dir / f"sha256-{hx}").write_bytes(content)
    return f"sha256:{hx}", len(content)


def _build_fake_ollama(root: Path, *, model: str = "bge-m3", tag: str = "latest") -> set[str]:
    """A minimal but real content-addressed Ollama store: config + model + license."""
    blobs = root / "models" / "blobs"
    blobs.mkdir(parents=True)
    cfg_d, cfg_s = _put_blob(blobs, b'{"model_format":"gguf"}')
    model_d, model_s = _put_blob(blobs, b"FAKE-GGUF-WEIGHTS" * 64)
    lic_d, lic_s = _put_blob(blobs, b"MIT License\n\nPermission is hereby granted")
    manifest = {
        "schemaVersion": 2,
        "config": {"digest": cfg_d, "size": cfg_s},
        "layers": [
            {"mediaType": "application/vnd.ollama.image.model", "digest": model_d, "size": model_s},
            {"mediaType": "application/vnd.ollama.image.license", "digest": lic_d, "size": lic_s},
        ],
    }
    mpath = root / "models" / "manifests/registry.ollama.ai/library" / model / tag
    mpath.parent.mkdir(parents=True)
    mpath.write_text(json.dumps(manifest))
    return {cfg_d, model_d, lic_d}


# ─────────────────────────── Task 1: make_model_seed ───────────────────────────


def test_make_seed_produces_portable_dir(tmp_path: Path) -> None:
    src = tmp_path / "ollama"
    digests = _build_fake_ollama(src)
    out = tmp_path / "seed"
    seed = make_model_seed.make_seed(src, "bge-m3", "latest", out)

    assert seed["model"] == "bge-m3"
    assert {b["digest"] for b in seed["blobs"]} == digests
    assert (out / "seed.manifest.json").is_file()
    assert (out / "manifests/registry.ollama.ai/library/bge-m3/latest").is_file()
    for blob in seed["blobs"]:
        f = out / blob["file"]
        assert f.is_file() and f.stat().st_size == blob["size"]


def test_make_seed_rejects_corrupt_source_blob(tmp_path: Path) -> None:
    src = tmp_path / "ollama"
    _build_fake_ollama(src)
    # Corrupt one blob so its bytes no longer hash to its filename digest.
    a_blob = next((src / "models" / "blobs").glob("sha256-*"))
    a_blob.write_bytes(a_blob.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="content mismatch"):
        make_model_seed.make_seed(src, "bge-m3", "latest", tmp_path / "seed")


def test_make_seed_allowlist_drift_fails(tmp_path: Path) -> None:
    src = tmp_path / "ollama"
    _build_fake_ollama(src)
    with pytest.raises(ValueError, match="allowlist drift"):
        make_model_seed.make_seed(
            src, "bge-m3", "latest", tmp_path / "seed", expect_digests={"sha256:deadbeef"}
        )


# ─────────────────────────── Task 2: seed_embedding_model ───────────────────────────


def _make_seed(tmp_path: Path) -> Path:
    src = tmp_path / "ollama"
    _build_fake_ollama(src)
    out = tmp_path / "seed"
    make_model_seed.make_seed(src, "bge-m3", "latest", out)
    return out


def test_seed_into_fresh_target(tmp_path: Path) -> None:
    seed = _make_seed(tmp_path)
    target = tmp_path / "target"
    res = seed_embedding_model(seed, target)
    assert res.status == "seeded" and res.ok
    assert (target / "manifests/registry.ollama.ai/library/bge-m3/latest").is_file()
    seed_manifest = json.loads((seed / "seed.manifest.json").read_text())
    for blob in seed_manifest["blobs"]:
        fname = blob["digest"].replace("sha256:", "sha256-")
        assert (target / "blobs" / fname).stat().st_size == blob["size"]
    # No temp/lock residue.
    assert not list((target / "blobs").glob(".tmp-*"))
    assert not (target / ".obc-seed.lock").exists()


def test_seed_idempotent(tmp_path: Path) -> None:
    seed = _make_seed(tmp_path)
    target = tmp_path / "target"
    assert seed_embedding_model(seed, target).status == "seeded"
    assert seed_embedding_model(seed, target).status == "already_present"


def test_seed_bad_blob_fails_without_committing_manifest(tmp_path: Path) -> None:
    seed = _make_seed(tmp_path)
    # Corrupt a blob inside the seed AFTER production (size preserved so the
    # copy proceeds, but the sha256 verify must catch it).
    big = max((seed / "blobs").glob("sha256-*"), key=lambda p: p.stat().st_size)
    data = bytearray(big.read_bytes())
    data[0] ^= 0xFF
    big.write_bytes(bytes(data))

    target = tmp_path / "target"
    res = seed_embedding_model(seed, target)
    assert res.status == "failed"
    # Manifest is the commit marker — it must NOT exist on failure.
    assert not (target / "manifests/registry.ollama.ai/library/bge-m3/latest").exists()
    assert not list((target / "blobs").glob(".tmp-*"))


def test_seed_never_touches_other_models_blobs(tmp_path: Path) -> None:
    seed = _make_seed(tmp_path)
    target = tmp_path / "target"
    (target / "blobs").mkdir(parents=True)
    other = target / "blobs" / "sha256-deadbeefcafe"
    other.write_bytes(b"someone-elses-model")
    seed_embedding_model(seed, target)
    assert other.read_bytes() == b"someone-elses-model"


# ─────────────────────────── effective_embedding_models_dir ───────────────────────────


def test_effective_dir_prefers_ascii_writable_user_env(tmp_path: Path) -> None:
    want = tmp_path / "user-models"
    got = effective_embedding_models_dir(
        user_ollama_models=str(want), platform="darwin", home=tmp_path, uid=501
    )
    assert got == want and got.is_dir()


def test_effective_dir_skips_non_ascii_user_env(tmp_path: Path) -> None:
    cjk = tmp_path / "用户模型"
    got = effective_embedding_models_dir(
        user_ollama_models=str(cjk), platform="linux", home=tmp_path, uid=1000
    )
    # Non-ASCII env rejected -> falls through to the /var/tmp ASCII fallback.
    assert got is not None and str(got).isascii()
    assert got == Path("/var/tmp/openbiliclaw-1000/ollama-models")


def test_effective_dir_windows_programdata(tmp_path: Path) -> None:
    pd = tmp_path / "ProgramData"
    got = effective_embedding_models_dir(
        user_ollama_models=None, platform="win32", home=tmp_path, programdata=str(pd)
    )
    assert got == pd / "OpenBiliClaw" / "ollama-models" and got.is_dir()


def test_seed_result_ok_flag() -> None:
    assert SeedResult("seeded").ok
    assert SeedResult("already_present").ok
    assert not SeedResult("failed", "x").ok
