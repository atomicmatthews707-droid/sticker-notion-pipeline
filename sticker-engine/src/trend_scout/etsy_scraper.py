"""
etsy_scraper.py — Etsy trend signal scout.

Strategy (in priority order):
  1. Etsy Open API v3 findAllListingsActive (more stable, uses ETSY_API_KEY).
  2. HTTP scraping via httpx + selectolax if no API key (rate-limited, proxy-aware).

Both paths return NicheSignal objects for the ranker to aggregate.
Any persistent error degrades to empty list — the orchestrator must not crash here.
"""

import os
import time
import random
import logging
from typing import Optional

import httpx
from selectolax.parser import HTMLParser

from src.trend_scout import NicheSignal

logger = logging.getLogger(__name__)

# Rotate these to reduce fingerprinting on fallback scrape path.
# These are generic desktop UAs — not spoofing a specific mobile device.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 Chrome/125.0 Safari/537.36",
]

_ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
_SEARCH_QUERIES = [
    "sticker pack",
    "digital stickers planners",
    "kawaii sticker pack",
    "goodnotes stickers",
    "cute planner stickers",
]


def scan() -> list[NicheSignal]:
    """Return raw Etsy trend signals. Ranker aggregates and z-scores."""
    api_key = os.getenv("ETSY_API_KEY", "")
    proxy_url = os.getenv("SCRAPER_PROXY_URL", "")

    if api_key:
        return _scan_via_api(api_key)
    else:
        logger.warning("ETSY_API_KEY not set — falling back to HTML scrape (fragile)")
        return _scan_via_scrape(proxy_url)


def _scan_via_api(api_key: str) -> list[NicheSignal]:
    """
    Use Etsy Open API v3 findAllListingsActive. Preferred path: structured data,
    no HTML parsing, less likely to break on layout changes.
    https://developers.etsy.com/documentation/reference#operation/findAllListingsActive
    """
    signals: list[NicheSignal] = []
    headers = {"x-api-key": api_key}

    for query in _SEARCH_QUERIES:
        try:
            # Rate-limit: 1 req / 2 sec per HANDOFF constraint
            time.sleep(2)
            resp = httpx.get(
                f"{_ETSY_API_BASE}/listings/active",
                headers=headers,
                params={
                    "keywords": query,
                    "limit": 25,
                    "includes": ["Images", "MainImage"],
                    "sort_on": "score",
                    "sort_order": "desc",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

            for listing in data.get("results", []):
                title = listing.get("title", "")
                # Use num_favorers + reviews as a popularity proxy
                favorites = listing.get("num_favorers", 0)
                reviews = listing.get("num_views", 0)
                score = favorites * 1.5 + reviews * 0.5  # weighted raw signal

                # Extract niche keywords from tags and title (first 3 words of title)
                tags = listing.get("tags", [])
                niche_hint = " ".join(title.split()[:4]).lower()

                signals.append(NicheSignal(
                    name=niche_hint,
                    source="etsy",
                    score=score,
                    metadata={"title": title, "tags": tags, "query": query},
                ))

        except httpx.HTTPError as e:
            logger.warning(f"Etsy API error for query '{query}': {e}")
            continue

    return signals


def _scan_via_scrape(proxy_url: str) -> list[NicheSignal]:
    """
    Fallback: scrape Etsy search HTML. Fragile by nature — Etsy changes layouts.
    Rate-limited to 1 req/2 sec. Proxy-aware via SCRAPER_PROXY_URL.
    """
    signals: list[NicheSignal] = []
    proxies = {"http://": proxy_url, "https://": proxy_url} if proxy_url else None

    for query in _SEARCH_QUERIES:
        try:
            time.sleep(2)
            ua = random.choice(_USER_AGENTS)
            encoded_query = query.replace(" ", "+")
            url = f"https://www.etsy.com/search?q={encoded_query}&type=digital&explicit=1"

            resp = httpx.get(
                url,
                headers={"User-Agent": ua},
                proxies=proxies,
                timeout=15.0,
                follow_redirects=True,
            )
            if resp.status_code == 429:
                logger.warning(f"Etsy rate-limited on scrape for '{query}', skipping")
                continue
            resp.raise_for_status()

            tree = HTMLParser(resp.text)
            # Etsy listing cards use h3 for titles — selector may need updates if Etsy redesigns
            for card in tree.css("div.v2-listing-card"):
                title_node = card.css_first("h3")
                title = title_node.text(strip=True) if title_node else ""
                if not title:
                    continue

                # Favorites often shown in aria-label or a separate span
                fav_node = card.css_first("[data-favoritecount]")
                fav_count = int(fav_node.attrs.get("data-favoritecount", 0)) if fav_node else 0

                niche_hint = " ".join(title.split()[:4]).lower()
                signals.append(NicheSignal(
                    name=niche_hint,
                    source="etsy",
                    score=float(fav_count),
                    metadata={"title": title, "query": query},
                ))

        except Exception as e:
            logger.warning(f"Etsy scrape error for query '{query}': {e}")
            continue

    return signals
