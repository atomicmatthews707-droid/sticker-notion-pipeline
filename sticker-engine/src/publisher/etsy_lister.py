"""
etsy_lister.py — Etsy Open API v3 publisher.

HANDOFF compliance requirements (verified 2026-09-30):
  - type: "download" is REQUIRED on digital listings or Etsy demands a shipping profile
  - who_made: "collective" — Etsy's guidance for AI-generated items with disclosure
    (do NOT use "i_did" without disclosure — policy violation risk)
  - when_made: "2020_2024" — current decade value for recently made digital goods
  - taxonomy_id: must be fetched from getSellerTaxonomyNodes at runtime (v1 hardcoded 6883, which was never verified)
  - AI disclosure line must be present in description before publishing
  - New-shop rate cap: max listings_rate_limit_per_day for first new_shop_days days

OAuth2 PKCE flow: Etsy requires browser-based auth. The token must be obtained
manually and stored in ETSY_OAUTH_TOKEN. See README for the one-time auth flow.

AI Handoff: Etsy taxonomy ID is fetched and cached on first call because the
node number can change. Do NOT hardcode it.
"""

import os
from datetime import datetime
from typing import Optional

import httpx

from src.shared.logger import get_logger

logger = get_logger(__name__)

_ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
_AI_DISCLOSURE = "These stickers were designed with the help of AI image tools and hand-selected for this pack."

# Cache taxonomy ID to avoid fetching it on every listing
_taxonomy_id_cache: Optional[int] = None


def _get_auth_headers() -> dict:
    token = os.getenv("ETSY_OAUTH_TOKEN", "")
    api_key = os.getenv("ETSY_API_KEY", "")
    if not token:
        raise RuntimeError("ETSY_OAUTH_TOKEN not set — run the OAuth flow first (see README)")
    return {
        "Authorization": f"Bearer {token}",
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }


def _get_taxonomy_id() -> int:
    """
    Fetch the taxonomy node ID for digital stickers/printables from Etsy.
    Cached after first successful fetch.
    Falls back to a well-known value (6883 = Digital Prints) if fetch fails,
    but logs a warning so the user can verify.
    """
    global _taxonomy_id_cache
    if _taxonomy_id_cache is not None:
        return _taxonomy_id_cache

    # Store in config file if available
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.yaml")
    if os.path.exists(config_path):
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        stored = cfg.get("listings", {}).get("etsy_taxonomy_id")
        if stored:
            _taxonomy_id_cache = int(stored)
            return _taxonomy_id_cache

    try:
        resp = httpx.get(
            f"{_ETSY_API_BASE}/seller-taxonomy/nodes",
            headers=_get_auth_headers(),
            timeout=15.0,
        )
        resp.raise_for_status()
        nodes = resp.json().get("results", [])
        # Search for "Digital Prints" or "Stickers" under Craft Supplies & Tools
        for node in nodes:
            name = node.get("name", "").lower()
            if "digital" in name and ("print" in name or "sticker" in name):
                _taxonomy_id_cache = node["id"]
                logger.info(f"Found Etsy taxonomy node: {node['name']} (id={node['id']})")
                return _taxonomy_id_cache
        # Walk children if top-level didn't match
        for node in nodes:
            for child in node.get("children", []):
                name = child.get("name", "").lower()
                if "digital" in name or "sticker" in name or "printable" in name:
                    _taxonomy_id_cache = child["id"]
                    logger.info(f"Found Etsy taxonomy node (child): {child['name']} (id={child['id']})")
                    return _taxonomy_id_cache
    except Exception as e:
        logger.warning(f"Could not fetch Etsy taxonomy nodes: {e}")

    # Fallback to 6883 — warn loudly
    logger.warning(
        "Using fallback Etsy taxonomy_id=6883 (Digital Prints). "
        "Verify this is correct in your Etsy Seller dashboard."
    )
    _taxonomy_id_cache = 6883
    return _taxonomy_id_cache


def _check_listing_rate_limit(shop_id: str) -> None:
    """
    HANDOFF: New shops that list dozens of items on day one get flagged.
    Cap at listing_rate_limit_per_day for the first new_shop_days days.
    """
    import yaml
    from src.storage.db import get_listings_today

    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.yaml")
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        rate_limit = cfg.get("listings", {}).get("listing_rate_limit_per_day", 3)
        new_shop_days = cfg.get("listings", {}).get("new_shop_days", 14)
    except Exception:
        rate_limit = 3
        new_shop_days = 14

    # Check if shop is still in the new-shop grace period
    # We use an env var for shop creation date rather than querying Etsy
    shop_created_str = os.getenv("ETSY_SHOP_CREATED_DATE", "")
    if shop_created_str:
        try:
            shop_created = datetime.fromisoformat(shop_created_str)
            if (datetime.utcnow() - shop_created).days > new_shop_days:
                return  # Past grace period, no rate limit
        except ValueError:
            pass  # Bad date format — apply rate limit to be safe

    today_count = get_listings_today()
    if today_count >= rate_limit:
        raise RuntimeError(
            f"New-shop listing rate cap: already listed {today_count} today "
            f"(limit={rate_limit}). Try again tomorrow."
        )


def _enforce_ai_disclosure(description: str) -> str:
    """
    HANDOFF compliance: description MUST contain the AI disclosure line.
    Add it at the top if missing.
    """
    if _AI_DISCLOSURE.lower() not in description.lower():
        logger.warning("AI disclosure missing from description — prepending it")
        return f"{_AI_DISCLOSURE}\n\n{description}"
    return description


def create_listing(listing_data: dict, zip_path: str, mockup_path: str) -> str:
    """
    Create a draft Etsy listing for a sticker pack.
    Returns the listing URL on success.

    listing_data keys: title, description, tags (list of 13), ...
    """
    shop_id = os.getenv("ETSY_SHOP_ID", "")
    if not shop_id:
        raise RuntimeError("ETSY_SHOP_ID not set")

    # Rate limit check for new shops
    _check_listing_rate_limit(shop_id)

    # Enforce AI disclosure
    description = _enforce_ai_disclosure(listing_data.get("description", ""))

    # Enforce tag count (Etsy allows max 13)
    tags = listing_data.get("tags", [])[:13]

    taxonomy_id = _get_taxonomy_id()

    payload = {
        "title": listing_data.get("title", "Sticker Pack"),
        "description": description,
        "price": listing_data.get("price_usd", 8.00),
        "quantity": 999,  # digital goods don't deplete
        "who_made": "collective",   # AI-generated with human curation — "i_did" requires disclosure + risk
        "when_made": "2020_2024",   # current decade value for recently made digital items
        "taxonomy_id": taxonomy_id,
        "type": "download",         # REQUIRED: prevents Etsy from demanding a shipping profile
        "tags": tags,
        "is_digital": True,
        "state": "draft",           # publish as draft; review before activating
    }

    headers = _get_auth_headers()

    try:
        resp = httpx.post(
            f"{_ETSY_API_BASE}/shops/{shop_id}/listings",
            headers=headers,
            json=payload,
            timeout=20.0,
        )
        resp.raise_for_status()
        listing = resp.json()
        listing_id = listing["listing_id"]
        listing_url = f"https://www.etsy.com/listing/{listing_id}"

        # Upload the zip as the digital file
        if zip_path and os.path.exists(zip_path):
            _upload_digital_file(shop_id, listing_id, zip_path, headers)

        # Upload mockup as the primary listing image
        if mockup_path and os.path.exists(mockup_path):
            _upload_listing_image(shop_id, listing_id, mockup_path, headers)

        logger.info(f"Etsy listing created (draft): {listing_url}")
        return listing_url

    except httpx.HTTPStatusError as e:
        logger.error(f"Etsy create_listing HTTP error: {e.response.status_code} — {e.response.text}")
        raise


def _upload_digital_file(shop_id: str, listing_id: int, zip_path: str, headers: dict) -> None:
    """Upload the zip file as the downloadable digital product."""
    with open(zip_path, "rb") as f:
        files = {"file": (os.path.basename(zip_path), f, "application/zip")}
        # Remove Content-Type from headers — httpx sets it correctly for multipart
        upload_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
        resp = httpx.post(
            f"{_ETSY_API_BASE}/shops/{shop_id}/listings/{listing_id}/files",
            headers=upload_headers,
            files=files,
            timeout=60.0,
        )
        resp.raise_for_status()
    logger.info(f"Digital file uploaded to listing {listing_id}")


def _upload_listing_image(shop_id: str, listing_id: int, image_path: str, headers: dict) -> None:
    """Upload the mockup/preview as the listing photo."""
    with open(image_path, "rb") as f:
        ext = os.path.splitext(image_path)[1].lower()
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        files = {"image": (os.path.basename(image_path), f, mime)}
        upload_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
        resp = httpx.post(
            f"{_ETSY_API_BASE}/shops/{shop_id}/listings/{listing_id}/images",
            headers=upload_headers,
            files=files,
            timeout=60.0,
        )
        resp.raise_for_status()
    logger.info(f"Listing image uploaded to listing {listing_id}")
