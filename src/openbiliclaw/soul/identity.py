"""Natural-key identity helpers for the settles/injection contract (Phase 1).

Insight hypotheses have no stored id (収編不迁移 — spec §invariant 5), so the
settles contract references them by a content hash. ``insight_hash8`` computes
that key deterministically:

    SHA-256 over «NFC-normalized + stripped + whitespace-collapsed» UTF-8 bytes,
    hex, first 8 chars (r3/R2-8).

``build_hash8_map`` builds the per-round injection map with collision handling:
a hex8 collision within the injected list escalates the colliding entries to
hex16; a persisting hex16 collision drops that item (logged at WARNING) so an
ambiguous ref can never settle the wrong hypothesis.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import defaultdict

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def _canonicalize(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text).strip()
    return _WHITESPACE.sub(" ", normalized)


def insight_full_hash(text: str) -> str:
    """Full SHA-256 hex of the canonicalized text (used for hex8/hex16 slices)."""
    return hashlib.sha256(_canonicalize(text).encode("utf-8")).hexdigest()


def insight_hash8(text: str) -> str:
    """Return the 8-char content hash of an insight hypothesis."""
    return insight_full_hash(text)[:8]


def build_hash8_map(texts: list[str]) -> dict[str, str]:
    """Map hash-key -> canonical source text for a round's injected insights.

    Collision policy (spec §invariant 5): hex8 collision within this list →
    escalate the colliding group to hex16; still colliding → drop with WARNING.
    Returns ``{key: original_text}``; keys are hex8 unless escalated to hex16.
    """
    full: dict[str, str] = {}
    by_hex8: dict[str, list[str]] = defaultdict(list)
    for text in texts:
        digest = insight_full_hash(text)
        full[text] = digest
        by_hex8[digest[:8]].append(text)

    result: dict[str, str] = {}
    for hex8, group in by_hex8.items():
        if len(group) == 1:
            result[hex8] = group[0]
            continue
        # hex8 collision → escalate this group to hex16.
        by_hex16: dict[str, list[str]] = defaultdict(list)
        for text in group:
            by_hex16[full[text][:16]].append(text)
        for hex16, sub in by_hex16.items():
            if len(sub) == 1:
                result[hex16] = sub[0]
            else:
                logger.warning(
                    "insight hash16 collision for %d hypotheses; dropping from "
                    "settles injection (ambiguous ref): %s",
                    len(sub),
                    [t[:40] for t in sub],
                )
    return result
