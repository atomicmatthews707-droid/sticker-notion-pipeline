"""
reddit_scanner.py — Reddit trend signal scout via PRAW.

Scans weekly top posts in planner/digital sticker communities for trending topics.
Degrades to empty list if REDDIT_* creds are missing — the orchestrator is not blocked.

HANDOFF: subreddits to watch — r/PlannerAddicts, r/DigitalPlanning, r/GoodNotes, r/etsysellers
"""

import os
import logging
from typing import Optional

from src.trend_scout import NicheSignal

logger = logging.getLogger(__name__)

_TARGET_SUBREDDITS = [
    "PlannerAddicts",
    "DigitalPlanning",
    "GoodNotes",
    "etsysellers",
]

# Keyword fragments that indicate a trending niche (extracted from post titles)
_NICHE_KEYWORDS = [
    "sticker", "pack", "planner", "goodnotes", "notability", "digital",
    "kawaii", "aesthetic", "theme", "template", "printable",
]


def scan() -> list[NicheSignal]:
    """Return Reddit-sourced trend signals. Empty list if creds missing or PRAW fails."""
    client_id = os.getenv("REDDIT_CLIENT_ID", "")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
    user_agent = os.getenv("REDDIT_USER_AGENT", "StickerEngineBot/1.0")

    if not client_id or not client_secret:
        logger.info("Reddit creds not set, skipping Reddit scout")
        return []

    try:
        import praw
    except ImportError:
        logger.warning("praw not installed, skipping Reddit scout")
        return []

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )
        return _fetch_signals(reddit)
    except Exception as e:
        # Don't crash the orchestrator — Reddit API can be flaky
        logger.warning(f"Reddit scout failed: {e}")
        return []


def _fetch_signals(reddit) -> list[NicheSignal]:
    signals: list[NicheSignal] = []

    for subreddit_name in _TARGET_SUBREDDITS:
        try:
            subreddit = reddit.subreddit(subreddit_name)
            # Weekly top posts: captures short-term trends better than month/all-time
            for post in subreddit.top(time_filter="week", limit=25):
                title_lower = post.title.lower()

                # Only posts that mention sticker/planner/digital keywords are relevant
                if not any(kw in title_lower for kw in _NICHE_KEYWORDS):
                    continue

                # Score proxy: upvote ratio * score gives us engagement quality × volume
                engagement = post.score * post.upvote_ratio

                # Extract a 2-4 word niche hint from the post title
                niche_words = [
                    w for w in post.title.split()
                    if w.lower() not in {"a", "the", "is", "i", "my", "for", "in", "on", "of", "and"}
                ]
                niche_hint = " ".join(niche_words[:3]).lower().strip("?!.,")

                if niche_hint:
                    signals.append(NicheSignal(
                        name=niche_hint,
                        source="reddit",
                        score=engagement,
                        metadata={
                            "subreddit": subreddit_name,
                            "post_title": post.title,
                            "url": f"https://reddit.com{post.permalink}",
                        },
                    ))

        except Exception as e:
            logger.warning(f"Reddit scout error on r/{subreddit_name}: {e}")
            continue

    return signals
