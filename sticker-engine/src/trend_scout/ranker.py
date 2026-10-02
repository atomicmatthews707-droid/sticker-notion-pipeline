"""
ranker.py — Aggregates trend signals, z-scores per source, and ranks niches.

HANDOFF spec:
  - Z-score each source independently (different sources have different scales)
  - Weighted sum per config.ranker_weights
  - Reject banned/trademarked terms (config/banned_words.txt)
  - Reject anything published in the last 30 days (DB check)
  - Return top N signals

Why per-source z-scoring: Etsy favorites are in the thousands; Google Trends values
are 0-100 (or 5000 for breakouts). A flat sum would be dominated by Etsy. Z-scoring
normalizes each source to its own mean/std before weighting.
"""

import logging
import statistics
from typing import Callable, Optional

from src.shared import config
from src.shared.banned import is_banned
from src.trend_scout import NicheSignal

logger = logging.getLogger(__name__)

# Default weights if config is missing; they match config/config.yaml.
_DEFAULT_WEIGHTS = {
    "etsy": 1.5,
    "reddit": 1.0,
    "google_trends": 0.8,
    "pinterest": 0.5,
    "sales": 2.0,
}


def _z_score_source(signals: list[NicheSignal]) -> list[tuple[NicheSignal, float]]:
    """
    Z-score the raw scores within a single source.
    If std dev is 0 (all same score), assign 0.0 to all — avoids division by zero.
    Returns list of (signal, z_score) tuples.
    """
    if not signals:
        return []
    scores = [s.score for s in signals]
    mean = statistics.mean(scores)
    std = statistics.stdev(scores) if len(scores) > 1 else 0.0

    result = []
    for sig in signals:
        z = (sig.score - mean) / std if std > 0 else 0.0
        result.append((sig, z))
    return result


def rank(
    signals: list[NicheSignal],
    weights: Optional[dict] = None,
    top_n: Optional[int] = None,
    recent_check: Optional[Callable[[str], bool]] = None,
) -> list[NicheSignal]:
    """
    Aggregate signals, z-score per source, weighted-sum, reject banned and recently published niches,
    return the top N. recent_check(name) -> True skips a niche; main passes db.recently_published.
    """
    if not signals:
        return []

    w = weights or config.get("ranker_weights") or _DEFAULT_WEIGHTS
    top_n = top_n or int(config.get("trend_scout.top_n", 5))

    by_source: dict[str, list[NicheSignal]] = {}
    for sig in signals:
        by_source.setdefault(sig.source, []).append(sig)

    # Several signals can point at the same niche: sum their weighted z-scores.
    niche_scores: dict[str, float] = {}
    niche_meta: dict[str, NicheSignal] = {}
    for source, source_signals in by_source.items():
        source_weight = w.get(source, 1.0)
        for sig, z in _z_score_source(source_signals):
            name = sig.name.strip().lower()
            if not name:
                continue
            niche_scores[name] = niche_scores.get(name, 0.0) + z * source_weight
            if name not in niche_meta or sig.score > niche_meta[name].score:
                niche_meta[name] = sig

    filtered: list[tuple[str, float]] = []
    for name, score in niche_scores.items():
        if is_banned(name):
            logger.debug("Ranker rejected banned niche: %r", name)
            continue
        if recent_check and recent_check(name):
            logger.debug("Ranker skipped recently published niche: %r", name)
            continue
        filtered.append((name, score))

    filtered.sort(key=lambda x: x[1], reverse=True)
    return [
        NicheSignal(name=niche_meta[name].name, source=niche_meta[name].source, score=combined, metadata=niche_meta[name].metadata)
        for name, combined in filtered[:top_n]
    ]
