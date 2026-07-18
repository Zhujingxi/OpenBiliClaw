"""Delight threshold policy — the bar a candidate must clear to earn delight copy.

This module owns *only* the eligibility threshold. It does **not** compute
``delight_score`` itself: the runtime score is produced by
``RecommendationEngine.precompute_delight_scores()``, which reuses the
``relevance_score`` Evo already wrote into ``content_cache`` (a deliberate
decision to avoid a second LLM call per candidate). Catalogue signals such as
``rating_score`` / ``rating_count`` / ``source_rank`` influence that score by
entering the shared evaluator prompt via
``discovery.engine._prompt_visible_content_fields`` when non-zero, so the LLM
weighs them in context rather than through a separate weighted formula here.

A standalone embedding-based multi-signal ``DelightScorer`` lived here until
v0.3.174; it had no production call sites and was removed. If delight ever
needs its own scorer again, wire it into ``precompute_delight_scores`` rather
than reviving a parallel scoring path.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Delight threshold:
# Runtime delight scoring reuses Evo's candidate relevance result as the
# delight score. The 0.75 default is the line where an item becomes eligible
# for proactive delight copy; lower scores are stored only as non-eligible
# progress markers so the background sweep does not retry them endlessly.
# Conservative users keep a higher 0.80 bar.
DEFAULT_DELIGHT_THRESHOLD: float = 0.75
CONSERVATIVE_DELIGHT_THRESHOLD: float = 0.80
_LOW_EXPLORATION_OPENNESS: float = 0.3


def effective_delight_threshold(
    exploration_openness: float,
    *,
    threshold: float = DEFAULT_DELIGHT_THRESHOLD,
) -> float:
    """Return the delight threshold adjusted for conservative users."""
    if exploration_openness < _LOW_EXPLORATION_OPENNESS:
        return max(threshold, CONSERVATIVE_DELIGHT_THRESHOLD)
    return threshold
