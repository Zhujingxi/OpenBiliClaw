"""Seed a bundled Ollama embedding model into an Ollama store, offline.

Bundled-embedding-model plan Task 2. The ``with-embedding`` desktop variant
ships a seed directory produced by ``packaging/make_model_seed.py``; at startup
(before any ``ollama serve``) we copy those content-addressed blobs + manifest
into the effective model directory so the private managed Ollama recognises the
model without a network pull.

Contract (see docs/plans/2026-07-07-bundled-embedding-model-spec.md §Seeding):

- ``blobs/`` and ``manifests/`` are SHARED across models — never rename whole
  dirs. Copy each blob to a unique ``.tmp-*`` sibling, verify its sha256, then
  atomically ``os.replace`` it into ``sha256-<hex>``.
- Write the manifest LAST as the commit marker: no manifest => not installed.
- Idempotent: a complete model (manifest + all blobs present, right size) is
  ``already_present`` and untouched.
- Any integrity failure cleans up only our own ``.tmp-*`` and returns
  ``failed`` so the caller falls back to the network pull; we never touch or
  delete blobs belonging to other models.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

_REGISTRY_REL = Path("manifests/registry.ollama.ai/library")

SeedStatus = Literal["already_present", "seeded", "failed"]


@dataclass(frozen=True)
class SeedResult:
    status: SeedStatus
    detail: str = ""
    seeded_blobs: int = 0

    @property
    def ok(self) -> bool:
        return self.status in ("already_present", "seeded")


def _digest_to_filename(digest: str) -> str:
    algo, _, hexdigest = digest.partition(":")
    if not hexdigest:
        raise ValueError(f"malformed digest: {digest!r}")
    return f"{algo}-{hexdigest}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def _dir_lock(lock_path: Path, timeout: float = 60.0) -> Iterator[None]:
    """Best-effort cross-platform lock via atomic ``mkdir`` with a stale sweep."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_path.mkdir()
            break
        except FileExistsError:
            # Reclaim an obviously-stale lock (crashed seeder) after 10 min.
            with contextlib.suppress(OSError):
                if time.time() - lock_path.stat().st_mtime > 600:
                    lock_path.rmdir()
                    continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"could not acquire seed lock: {lock_path}") from None
            time.sleep(0.2)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.rmdir()


def _load_seed_manifest(seed_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((seed_dir / "seed.manifest.json").read_text())
    if not data.get("blobs") or not data.get("manifest") or not data.get("model"):
        raise ValueError("seed.manifest.json missing model/manifest/blobs")
    return data


def _model_already_present(target_models: Path, seed: dict[str, Any]) -> bool:
    manifest_dst = target_models / seed["manifest"]
    if not manifest_dst.is_file():
        return False
    for blob in seed["blobs"]:
        dst = target_models / "blobs" / _digest_to_filename(blob["digest"])
        if not dst.is_file() or dst.stat().st_size != int(blob["size"]):
            return False
    return True


def seed_embedding_model(seed_dir: Path, target_models_dir: Path) -> SeedResult:
    """Install the seed's blobs + manifest into ``target_models_dir`` (the dir
    Ollama treats as ``$OLLAMA_MODELS``, i.e. it contains ``blobs/`` and
    ``manifests/``). Idempotent, atomic per blob, manifest committed last.
    """
    seed_dir = Path(seed_dir)
    target_models = Path(target_models_dir)
    try:
        seed = _load_seed_manifest(seed_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return SeedResult("failed", f"unreadable seed manifest: {exc}")

    try:
        (target_models / "blobs").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return SeedResult("failed", f"target not writable: {exc}")

    lock_path = target_models / ".obc-seed.lock"
    try:
        with _dir_lock(lock_path):
            if _model_already_present(target_models, seed):
                return SeedResult("already_present", f"{seed['model']}:{seed['tag']} present")

            blobs_dir = target_models / "blobs"
            our_tmp: list[Path] = []
            copied = 0
            try:
                for blob in seed["blobs"]:
                    digest = blob["digest"]
                    fname = _digest_to_filename(digest)
                    src = seed_dir / blob["file"]
                    dst = blobs_dir / fname
                    if dst.is_file() and dst.stat().st_size == int(blob["size"]):
                        continue  # content-addressed => already good, reuse
                    if not src.is_file():
                        return SeedResult("failed", f"seed blob missing: {src}")
                    tmp = blobs_dir / f".tmp-{fname}-{os.getpid()}"
                    our_tmp.append(tmp)
                    shutil.copy2(src, tmp)
                    _algo, _, expected_hex = digest.partition(":")
                    if _sha256_file(tmp) != expected_hex:
                        return SeedResult("failed", f"blob hash mismatch: {digest}")
                    os.replace(tmp, dst)  # atomic commit of this blob
                    our_tmp.pop()
                    copied += 1

                # Manifest LAST — this is the "model installed" commit marker.
                manifest_dst = target_models / seed["manifest"]
                manifest_dst.parent.mkdir(parents=True, exist_ok=True)
                manifest_src = seed_dir / seed["manifest"]
                mtmp = manifest_dst.parent / f".tmp-{manifest_dst.name}-{os.getpid()}"
                our_tmp.append(mtmp)
                shutil.copy2(manifest_src, mtmp)
                os.replace(mtmp, manifest_dst)
                our_tmp.pop()
            finally:
                for tmp in our_tmp:  # only ever our own uniquely-named temps
                    with contextlib.suppress(OSError):
                        tmp.unlink()

            return SeedResult("seeded", f"{seed['model']}:{seed['tag']} seeded", copied)
    except (OSError, TimeoutError) as exc:
        return SeedResult("failed", f"{type(exc).__name__}: {exc}")


# ─────────────────────────── effective model dir ───────────────────────────


def _is_usable_ascii_dir(path: Path) -> bool:
    """True if ``path`` is pure-ASCII and we can create + write inside it.

    Ollama's llama-server cannot load models from non-ASCII paths (the CJK
    username bug); and a bundled seed target must be writable by the current
    user without admin.
    """
    if not str(path).isascii():
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            with contextlib.suppress(OSError):
                path.chmod(0o700)
        probe = path / ".obc-write-probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def effective_embedding_models_dir(
    *,
    user_ollama_models: str | None,
    platform: str,
    home: Path,
    programdata: str | None = None,
    uid: int | None = None,
    for_bundled: bool = True,
) -> Path | None:
    """The single directory used as seed target, private daemon ``OLLAMA_MODELS``,
    diagnostics root, and repair target for the ``with-embedding`` private mode.

    Priority (spec §Effective dir): valid ASCII+writable user ``OLLAMA_MODELS`` >
    ``%PROGRAMDATA%\\OpenBiliClaw\\ollama-models`` (win) > user-writable ASCII
    fallback (``/var/tmp/openbiliclaw-<uid>/ollama-models`` posix, ``C:\\OpenBiliClaw
    \\ollama-models`` win). ``~/.ollama`` is intentionally NOT a candidate here —
    it may be non-ASCII (CJK home) and is not private. Returns ``None`` (=> caller
    falls back to the network pull, i.e. lean behaviour) when nothing qualifies.
    """
    candidates: list[Path] = []
    if user_ollama_models:
        candidates.append(Path(user_ollama_models).expanduser())

    if platform.startswith("win"):
        base = programdata or os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        candidates.append(Path(base) / "OpenBiliClaw" / "ollama-models")
        candidates.append(Path(r"C:\OpenBiliClaw") / "ollama-models")
    else:
        effective_uid = uid if uid is not None else os.getuid()
        candidates.append(Path("/var/tmp") / f"openbiliclaw-{effective_uid}" / "ollama-models")

    for cand in candidates:
        if _is_usable_ascii_dir(cand):
            return cand

    if not for_bundled:
        # Lean path uses Ollama's default ~/.ollama; this function is only the
        # bundled/private selector, so signal "no private dir" to the caller.
        return None
    return None
