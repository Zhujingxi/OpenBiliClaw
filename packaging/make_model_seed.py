#!/usr/bin/env python3
"""Produce a portable Ollama model "seed" directory from a local Ollama store.

Bundled-embedding-model plan Task 1. Given a local Ollama root that already has
the model pulled (``ollama pull bge-m3``), this copies the exact blobs +
manifest into a self-contained ``seed`` directory that ``runtime.embedding_seed``
(desktop) or ``docker/seed-bge-m3.sh`` (docker) can drop into any Ollama store
so the daemon recognises the model without a network pull.

Layout produced::

    <out>/
      seed.manifest.json                       # {model, tag, manifest, blobs[]}
      manifests/registry.ollama.ai/library/<model>/<tag>
      blobs/sha256-<hex>                        # one per config + layer blob

Ollama stores blobs content-addressed as ``sha256-<hex>`` (dash) on disk while
the manifest references ``sha256:<hex>`` (colon); this script bridges the two
and verifies every blob's bytes hash to its digest before copying (a corrupt
source store must never be baked into a release).

Usage::

    python packaging/make_model_seed.py --model bge-m3 --out packaging/model-seed
    # optionally pin against the Task 0 allowlist:
    python packaging/make_model_seed.py --model bge-m3 --out ... \
        --expect-digest sha256:0c4c... --expect-digest sha256:daec...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

_REGISTRY_REL = "manifests/registry.ollama.ai/library"


def _digest_to_filename(digest: str) -> str:
    """``sha256:<hex>`` (manifest form) -> ``sha256-<hex>`` (on-disk form)."""
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


def _verify_blob(blob_path: Path, digest: str) -> None:
    """Raise if ``blob_path`` bytes do not hash to ``digest``."""
    _algo, _, expected_hex = digest.partition(":")
    actual = _sha256_file(blob_path)
    if actual != expected_hex:
        raise ValueError(f"blob content mismatch for {digest}: file hashes to sha256:{actual}")


def _manifest_digests(manifest: dict) -> list[dict]:
    """All blobs a manifest references: the config blob + every layer."""
    out: list[dict] = []
    config = manifest.get("config") or {}
    if config.get("digest"):
        out.append({"digest": config["digest"], "size": int(config.get("size", 0))})
    for layer in manifest.get("layers") or []:
        if layer.get("digest"):
            out.append({"digest": layer["digest"], "size": int(layer.get("size", 0))})
    return out


def make_seed(
    ollama_root: Path,
    model: str,
    tag: str,
    out_dir: Path,
    expect_digests: set[str] | None = None,
) -> dict:
    """Build the seed directory; return the ``seed.manifest.json`` dict.

    Raises on: missing manifest/blob, blob hash mismatch, or drift from
    ``expect_digests`` (the Task 0 allowlist) when provided.
    """
    models_dir = ollama_root / "models"
    manifest_src = models_dir / _REGISTRY_REL / model / tag
    if not manifest_src.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_src}")
    manifest = json.loads(manifest_src.read_text())

    blobs = _manifest_digests(manifest)
    if not blobs:
        raise ValueError(f"manifest references no blobs: {manifest_src}")

    if expect_digests is not None:
        got = {b["digest"] for b in blobs}
        if got != expect_digests:
            raise ValueError(
                "digest allowlist drift: manifest references "
                f"{sorted(got)} but expected {sorted(expect_digests)}"
            )

    # Fresh output tree.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "blobs").mkdir(parents=True)
    manifest_dst = out_dir / _REGISTRY_REL / model / tag
    manifest_dst.parent.mkdir(parents=True)

    seed_blobs: list[dict] = []
    for blob in blobs:
        digest = blob["digest"]
        fname = _digest_to_filename(digest)
        src = models_dir / "blobs" / fname
        if not src.is_file():
            raise FileNotFoundError(f"blob missing in source store: {src}")
        _verify_blob(src, digest)  # never bake a corrupt source blob
        shutil.copy2(src, out_dir / "blobs" / fname)
        seed_blobs.append({"digest": digest, "size": src.stat().st_size, "file": f"blobs/{fname}"})

    shutil.copy2(manifest_src, manifest_dst)

    seed_manifest = {
        "model": model,
        "tag": tag,
        "manifest": str(Path(_REGISTRY_REL) / model / tag),
        "blobs": seed_blobs,
    }
    (out_dir / "seed.manifest.json").write_text(
        json.dumps(seed_manifest, indent=2, sort_keys=True) + "\n"
    )
    return seed_manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a portable Ollama model seed dir.")
    ap.add_argument("--ollama-root", default=str(Path.home() / ".ollama"))
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--expect-digest",
        action="append",
        default=None,
        help="Pin against the Task 0 allowlist (repeatable). Drift => failure.",
    )
    args = ap.parse_args(argv)

    try:
        seed = make_seed(
            Path(args.ollama_root).expanduser(),
            args.model,
            args.tag,
            Path(args.out),
            set(args.expect_digest) if args.expect_digest else None,
        )
    except Exception as exc:  # noqa: BLE001 — CLI boundary, report and non-zero exit
        print(f"make_model_seed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    total = sum(b["size"] for b in seed["blobs"])
    print(
        f"seeded {seed['model']}:{seed['tag']} -> {args.out} "
        f"({len(seed['blobs'])} blobs, {total / 1024 / 1024:.1f} MiB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
