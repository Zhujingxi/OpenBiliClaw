#!/bin/sh
# Seed a bundled Ollama model into an Ollama store, offline — the docker-side
# counterpart of src/openbiliclaw/runtime/embedding_seed.py (same contract, no
# Python: the ollama/ollama image has neither OpenBiliClaw nor jq). The seed dir
# is self-contained (only this model's blobs), so no JSON parsing is needed —
# copy every blob, verifying its sha256 equals its content-addressed filename,
# then write the manifest LAST as the "installed" commit marker.
#
# Usage: seed-bge-m3.sh [SEED_DIR] [TARGET_MODELS_DIR]
set -eu

SEED_DIR="${1:-/opt/bge-m3-seed}"
TARGET="${2:-${OLLAMA_MODELS:-/root/.ollama}/models}"
MANIFEST_REL="manifests/registry.ollama.ai/library/bge-m3/latest"

# Idempotent: manifest present => model already installed.
if [ -f "$TARGET/$MANIFEST_REL" ]; then
  echo "[seed] bge-m3 already present in $TARGET, skipping"
  exit 0
fi
if [ ! -d "$SEED_DIR/blobs" ]; then
  echo "[seed] ERROR: no seed dir at $SEED_DIR" >&2
  exit 1
fi

mkdir -p "$TARGET/blobs"
found=0
for src in "$SEED_DIR"/blobs/sha256-*; do
  [ -e "$src" ] || break
  found=1
  fname=$(basename "$src")
  want=${fname#sha256-}
  dst="$TARGET/blobs/$fname"
  # Content-addressed: an existing blob of this name is already correct.
  [ -f "$dst" ] && continue
  got=$(sha256sum "$src" | cut -d' ' -f1)
  if [ "$got" != "$want" ]; then
    echo "[seed] ERROR: hash mismatch for $fname (got sha256:$got)" >&2
    exit 1
  fi
  tmp="$TARGET/blobs/.tmp-$fname.$$"
  cp "$src" "$tmp"
  mv "$tmp" "$dst"   # atomic within the same filesystem
done
if [ "$found" = 0 ]; then
  echo "[seed] ERROR: no blobs found under $SEED_DIR/blobs" >&2
  exit 1
fi

# Manifest LAST — commit marker. Write via temp+mv so a crash never leaves a
# half-written manifest that makes ollama think the model is installed.
mkdir -p "$(dirname "$TARGET/$MANIFEST_REL")"
mtmp="$TARGET/$MANIFEST_REL.tmp.$$"
cp "$SEED_DIR/$MANIFEST_REL" "$mtmp"
mv "$mtmp" "$TARGET/$MANIFEST_REL"
echo "[seed] bge-m3 seeded into $TARGET"
