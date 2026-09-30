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

import os
import logging
import statistics
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from src.trend_scout import NicheSignal

logger = logging.getLogger(__name__)

# Default weights if config not available — match config/config.yaml
_DEFAULT_WEIGHTS = {
    "etsy": 1.5,
    "reddit": 1.0,
    "google_trends": 0.8,
    "pinterest": 0.5,
    "sales": 2.0,
}

# Path to banned words list relative to project root
_BANNED_WORDS_PATH = Path(__file__).parent.parent.parent / "config" / "banned_words.txt"


def _load_banned_words() -> set[str]:
    """Load banned words from config file. Returns empty set if file missing."""
    if not _BANNED_WORDS_PATH.exists():
        logger.warning(f"banned_words.txt not found at {_BANNED_WORDS_PATH}")
        return set()
    lines = _BANNED_WORDS_PATH.read_text(encoding="utf-8").splitlines()
    return {line.strip().lower() for line in lines if line.strip() and not line.startswith("#")}


def _is_banned(name: str, banned_words: set[str]) -> bool:
    """True if any banned word appears as a substring of the niche name."""
    name_lower = name.lower()
    return any(bw in name_lower for bw in banned_words)


def _recently_published(name: str, db_conn=None) -> bool:
    """
    True if this niche was published within the last 30 days.
    db_conn is optional — if not provided (e.g. during tests), skip this check.
    """
    if db_conn is None:
        return False
    # Avoid re-publishing niches that just shipped — give them time to accumulate sales
    cutoff = datetime.utcnow() - timedelta(days=30)
    try:
        # Synchronous check — ranker runs outside the async loop
        import sqlite3
        row = db_conn.execute(
            "SELECT updated_at FROM niches WHERE name = ? AND status = 'PUBLISHED' AND updated_at > ?",
            (name, cutoff.isoformat()),
        ).fetchone()
        return row is not None
    except Exception as e:
        logger.warning(f"DB check for recent niche failed: {e}")
        return False


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
    top_n: int = 5,
    db_conn=None,
) -> list[NicheSignal]:
    """
    Aggregate signals, z-score per source, weighted-sum, reject banned/recent,
    return top N unique niche names.
    """
    if not signals:
        return []

    banned = _load_banned_words()
    w = weights or _DEFAULT_WEIGHTS

    # Group by source for per-source z-scoring
    by_source: dict[str, list[NicheSignal]] = {}
    for sig in signals:
        by_source.setdefault(sig.source, []).append(sig)

    # Compute weighted z-scores per niche name
    # Multiple signals can point at the same niche name — sum their weighted z-scores
    niche_scores: dict[str, float] = {}
    niche_meta: dict[str, NicheSignal] = {}  # keep one representative signal per name

    for source, source_signals in by_source.items():
        source_weight = w.get(source, 1.0)
        for sig, z in _z_score_source(source_signals):
            name = sig.name.strip().lower()
            if not name:
                continue
            niche_scores[name] = niche_scores.get(name, 0.0) + z * source_weight
            # Keep the highest-scoring signal as the representative for this name
            if name not in niche_meta or sig.score > niche_meta[name].score:
                niche_meta[name] = sig

    # Filter: banned words and recently published
    filtered: list[tuple[str, float]] = []
    for name, score in niche_scores.items():
        if _is_banned(name, banned):
            logger.debug(f"Ranker rejected banned niche: '{name}'")
            continue
        if _recently_published(name, db_conn):
            logger.debug(f"Ranker skipped recently published niche: '{name}'")
            continue
        filtered.append((name, score))

    # Sort descending, take top N, reconstruct NicheSignal objects with updated score
    filtered.sort(key=lambda x: x[1], reverse=True)
    results: list[NicheSignal] = []
    for name, combined_score in filtered[:top_n]:
        base = niche_meta[name]
        results.append(NicheSignal(
            name=base.name,
            source=base.source,
            score=combined_score,
            metadata=base.metadata,
        ))

    return results
