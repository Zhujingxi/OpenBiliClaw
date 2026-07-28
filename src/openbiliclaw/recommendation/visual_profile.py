"""User visual profile — cluster liked/disliked covers into mean centroids.

Builds a multi-peak visual taste profile from the user's recommendation
feedback (like/dislike/save). Cover image vectors (same multimodal embedding
space) are greedily agglomerated into clusters; each cluster's centroid is the
*mean* of its member vectors (unlike ``discovery.engine._normalize_topic_keys``
which uses a canonical member as the label — here we want a numeric centroid
because consumers compare candidate cover vectors against it by cosine, and a
mean captures the shared visual style of a multi-member taste cluster).

Pure functions + a small dataclass. No I/O: the caller fetches feedback rows
and persists centroids. This keeps the clustering testable without a DB.

Why mean centroids, not a single global centroid: a user's taste is multi-peaked
— they may like both "dark game screenshots" and "bright food close-ups", which
a single mean would average into an incoherent grey. Keeping the top-k clusters
by membership preserves each peak; the consumer takes the *max* cosine against
all centroids (top-1 cluster), so a candidate matching any peak scores well.
"""

from __future__ import annotations

from dataclasses import dataclass

from openbiliclaw.llm.embedding import cosine_similarity

# Default cap on clusters kept per polarity (by membership, desc). A handful of
# peaks captures taste variety without flooding the hot path with cosine calls.
# CALIBRATION PROVENANCE: PROVISIONAL — not tuned against a real multimodal
# model's cover-vector distribution. Reopen after choosing a production model
# (CLAUDE.md pitfall rule 3).
DEFAULT_MAX_CLUSTERS = 5
# Minimum members for a cluster to survive (singletons are noise: one liked
# cover does not establish a taste peak).
DEFAULT_MIN_CLUSTER_MEMBERS = 2
# Cosine at/above which a new vector joins an existing cluster (same-modal
# image↔image). CALIBRATION PROVENANCE: MEASURED 2026-07-27 against dashscope
# qwen3-vl-embedding (dim=1024) — 452 real Bilibili covers, 101,926 pairs:
#   p50=0.219  p75=0.295  p90=0.365  p95=0.409  p99=0.497  p100=0.929
# The original 0.80 inherited the same "same-modal cosine runs high" intuition
# that made the cover/keyframe bonuses inert, and is provably unreachable
# here: p99 is 0.497, so 0.80 meant NO two real covers ever joined a cluster —
# every liked cover became a singleton and was pruned by min_members, leaving
# zero centroids and a silently no-op feature. 0.50 sits just above p99, so
# only genuinely visually-similar covers (the rare tail) merge into a taste
# peak; unrelated covers stay separate rather than polluting a centroid.
# Reopen after any embedding provider/model swap (CLAUDE.md pitfall rule 3):
# rerun scripts/calibrate_visual_thresholds.py --report and re-derive from the
# fresh p99.
DEFAULT_CLUSTER_THRESHOLD = 0.50


@dataclass(frozen=True)
class VisualCluster:
    """One cluster of liked (or disliked) cover vectors."""

    centroid: tuple[float, ...]
    member_count: int


def build_centroids(
    vectors: list[list[float]],
    *,
    threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    max_clusters: int = DEFAULT_MAX_CLUSTERS,
    min_members: int = DEFAULT_MIN_CLUSTER_MEMBERS,
) -> list[VisualCluster]:
    """Greedy agglomerative cluster of cover vectors → mean centroids.

    Mirrors the loop shape of ``discovery.engine._normalize_topic_keys`` but
    keeps a running *mean* centroid per cluster (not a canonical member) and
    prunes small clusters + keeps only the top-``max_clusters`` by membership.

    Args:
        vectors: non-empty cover image vectors (same embedding space). Empty
            or all-zero vectors are skipped (a zero vector is a provider
            failure — caching/using it would poison the profile, per pitfall
            rule 2).
        threshold: cosine at/above which a vector joins the nearest cluster.
        max_clusters: keep at most this many clusters (by membership, desc).
        min_members: drop clusters with fewer members (singleton = noise).

    Returns:
        Centroids sorted by membership desc; empty if no usable vectors.
    """
    # Step 1: filter zero / empty vectors (never feed a failed embed into a
    # centroid — it would pull the mean toward the origin and corrupt cosine).
    usable: list[list[float]] = []
    for vec in vectors:
        if not vec or all(abs(x) < 1e-12 for x in vec):
            continue
        usable.append(list(vec))
    if not usable:
        return []

    # Step 2: greedy agglomerative clustering with a running mean centroid.
    centroids: list[list[float]] = []
    sums: list[list[float]] = []  # running sum per cluster for O(1) mean update
    counts: list[int] = []

    for vec in usable:
        best_idx: int | None = None
        best_sim = 0.0
        for idx, centroid in enumerate(centroids):
            sim = cosine_similarity(vec, centroid)
            if sim > best_sim:
                best_sim = sim
                best_idx = idx

        if best_idx is not None and best_sim >= threshold:
            for i, x in enumerate(vec):
                sums[best_idx][i] += x
            counts[best_idx] += 1
            centroids[best_idx] = [s / counts[best_idx] for s in sums[best_idx]]
        else:
            centroids.append(list(vec))
            sums.append(list(vec))
            counts.append(1)

    # Step 3: prune small clusters, keep top-k by membership.
    kept = [
        (counts[i], centroids[i])
        for i in range(len(centroids))
        if counts[i] >= min_members
    ]
    kept.sort(key=lambda item: item[0], reverse=True)
    kept = kept[:max(0, int(max_clusters))]

    return [
        VisualCluster(centroid=tuple(centroid), member_count=count)
        for count, centroid in kept
    ]


def best_centroid_similarity(
    candidate_vec: list[float],
    centroids: list[VisualCluster],
) -> float:
    """Max cosine of a candidate cover against the centroids (0 if none)."""
    if not candidate_vec or not centroids:
        return 0.0
    best = 0.0
    for cluster in centroids:
        sim = cosine_similarity(candidate_vec, list(cluster.centroid))
        if sim > best:
            best = sim
    return best
