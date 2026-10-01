"""
google_trends.py — Google Trends trend signal scout via pytrends.

Rising queries around seed terms signal emerging niches before they peak on Etsy.
pytrends is unofficial and rate-limited — sleep aggressively, degrade gracefully.

HANDOFF: use pytrends, sleep between calls, degrade on failure.
"""

import time
import logging

from src.trend_scout import NicheSignal

logger = logging.getLogger(__name__)

_SEED_TERMS = [
    "sticker pack",
    "goodnotes stickers",
    "digital planner stickers",
    "kawaii stickers",
    "aesthetic stickers",
]

# pytrends GEO: empty string = worldwide, which is fine for a global Etsy market
_GEO = ""
_TIMEFRAME = "now 7-d"  # rising queries in the past 7 days


def scan() -> list[NicheSignal]:
    """Return Google Trends rising queries as trend signals."""
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.warning("pytrends not installed, skipping Google Trends scout")
        return []

    try:
        # Aggressive sleep here: pytrends hits an unofficial endpoint and
        # Google bans IPs that hammer it. 2s between requests is minimum safe.
        pt = TrendReq(hl="en-US", tz=360, timeout=(10, 25))  # no retries=: breaks on urllib3 2.x
        return _fetch_signals(pt)
    except Exception as e:
        logger.warning(f"Google Trends scout failed: {e}")
        return []


def _fetch_signals(pt) -> list[NicheSignal]:
    signals: list[NicheSignal] = []

    for seed in _SEED_TERMS:
        try:
            # Build payload one seed at a time — multi-term payloads are less reliable
            pt.build_payload([seed], cat=0, timeframe=_TIMEFRAME, geo=_GEO, gprop="")
            # rising_queries returns a dict of DataFrames: {'top': df, 'rising': df}
            related = pt.related_queries()
            rising_df = related.get(seed, {}).get("rising")

            if rising_df is None or rising_df.empty:
                continue

            for _, row in rising_df.iterrows():
                query = str(row.get("query", "")).lower().strip()
                value = float(row.get("value", 0))  # Breakout = >5000; otherwise 0-100

                if not query:
                    continue

                # "Breakout" means >5000% rise — treat as very high signal
                score = 5000.0 if value > 4999 else value

                signals.append(NicheSignal(
                    name=query,
                    source="google_trends",
                    score=score,
                    metadata={"seed_term": seed, "raw_value": value},
                ))

            # Mandatory sleep between seed queries — pytrends rate limit
            time.sleep(2.5)

        except Exception as e:
            logger.warning(f"Google Trends error for seed '{seed}': {e}")
            time.sleep(5)  # back off longer after an error
            continue

    return signals
