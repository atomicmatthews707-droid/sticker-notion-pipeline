"""
etsy_lister.py: Etsy Open API v3 draft-listing publisher. DISABLED unless ETSY_ENABLED=1.

Never run against the live API: the build environment could not reach Etsy and no credentials existed.
Values below come from Etsy's public reference as found on 2026-10-01 and must be re-checked before use:
  - when_made is a date range that changes (currently 2020_2026); it is configurable.
  - who_made is configurable (config listings.etsy_who_made); confirm Etsy's current AI guidance.
  - is_supply, type="download" and taxonomy_id are required for a digital listing.
  - The taxonomy id must come from config/env (getSellerTaxonomyNodes); the engine never guesses one.
  - The AI-disclosure line is always added to the description.
Listings are created as drafts. New shops are limited to a few listings per day (config listings.*).
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.publisher import PublishUnavailable
from src.shared import config
from src.shared.compliance import ensure_disclosure
from src.shared.logger import get_logger
from src.storage import db

logger = get_logger(__name__)
_API = "https://openapi.etsy.com/v3/application"
ETSY_FILE_LIMIT_MB = 20


def is_enabled() -> bool:
    flag = os.getenv("ETSY_ENABLED", "").strip().lower() in ("1", "true", "yes")
    return flag and all(os.getenv(k) for k in ("ETSY_API_KEY", "ETSY_OAUTH_TOKEN", "ETSY_SHOP_ID"))


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['ETSY_OAUTH_TOKEN']}", "x-api-key": os.environ["ETSY_API_KEY"]}


def _taxonomy_id() -> int:
    value = os.getenv("ETSY_TAXONOMY_ID") or config.get("listings.etsy_taxonomy_id")
    if not value:
        raise PublishUnavailable(
            "No Etsy taxonomy id configured. Look up the digital stickers/printables node with "
            "getSellerTaxonomyNodes and set listings.etsy_taxonomy_id (or ETSY_TAXONOMY_ID)."
        )
    return int(value)


def _check_rate_limit() -> None:
    """New shops that list many items at once get flagged: cap listings per day for the first weeks."""
    limit = int(config.get("listings.listing_rate_limit_per_day", 3))
    created = os.getenv("ETSY_SHOP_CREATED_DATE", "")
    if created:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(created).replace(tzinfo=timezone.utc)).days
            if age > int(config.get("listings.new_shop_days", 14)):
                return
        except ValueError:
            pass  # unreadable date: keep the cap, to be safe
    today = db.get_listings_today()
    if today >= limit:
        raise PublishUnavailable(f"New-shop listing cap reached: {today} listed today (limit {limit}).")


def _when_made() -> str:
    """Etsy's date-range value, e.g. 2020_2026. Quote it in YAML: unquoted, YAML reads it as the number 20202026."""
    value = config.get("listings.etsy_when_made", "2020_2026")
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}_\d{4}|\d{4}s|before_\d{4}|made_to_order", value):
        raise PublishUnavailable(f"listings.etsy_when_made {value!r} is not a valid Etsy value; quote it, e.g. \"2020_2026\".")
    return value


def build_payload(listing_data: dict) -> dict:
    return {
        "title": listing_data["title"],
        "description": ensure_disclosure(listing_data["description"]),
        "price": float(listing_data.get("price_usd", 5.0)),
        "quantity": 999,
        "who_made": config.get("listings.etsy_who_made", "i_did"),
        "when_made": _when_made(),
        "is_supply": False,
        "taxonomy_id": _taxonomy_id(),
        "type": "download",
        "tags": ",".join(list(listing_data["tags"])[:13]),  # form-encoded: comma-separated (unverified)
    }


def create_listing(listing_data: dict, zip_path: str, mockup_paths=None) -> str:
    """Create a draft listing, upload the pack and the previews, and return the listing URL."""
    if not is_enabled():
        raise PublishUnavailable("Etsy publishing is disabled (set ETSY_ENABLED=1 and the Etsy credentials).")
    size_mb = Path(zip_path).stat().st_size / 1_000_000
    if size_mb > ETSY_FILE_LIMIT_MB:
        raise PublishUnavailable(f"Pack is {size_mb:.1f} MB; Etsy allows {ETSY_FILE_LIMIT_MB} MB per file.")
    _check_rate_limit()
    shop = os.environ["ETSY_SHOP_ID"]
    payload = build_payload(listing_data)

    resp = httpx.post(f"{_API}/shops/{shop}/listings", headers=_headers(), data=payload, timeout=30)
    resp.raise_for_status()
    listing_id = resp.json()["listing_id"]
    with open(zip_path, "rb") as f:
        httpx.post(f"{_API}/shops/{shop}/listings/{listing_id}/files", headers=_headers(),
                   files={"file": (Path(zip_path).name, f, "application/zip")}, timeout=120).raise_for_status()
    for i, path in enumerate(mockup_paths or [], 1):
        with open(path, "rb") as f:
            httpx.post(f"{_API}/shops/{shop}/listings/{listing_id}/images", headers=_headers(),
                       data={"rank": i}, files={"image": (Path(path).name, f, "image/jpeg")}, timeout=60).raise_for_status()
    url = f"https://www.etsy.com/listing/{listing_id}"
    logger.info("Etsy draft listing created: %s", url)
    return url
