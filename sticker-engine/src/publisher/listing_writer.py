"""Listing copy for Etsy and Gumroad, validated before anything is published."""

import re
from pathlib import Path
from typing import Optional

from src.shared import config
from src.shared.banned import is_banned
from src.shared.compliance import ensure_disclosure
from src.shared.gemini_client import GeminiClient, GeminiError
from src.shared.logger import get_logger

logger = get_logger(__name__)
_PROMPT = config.ROOT / "config" / "prompts" / "listing_writer.md"
ETSY_TITLE_MAX, ETSY_TAG_MAX, ETSY_TAGS = 140, 20, 13


def price_for_count(count: int) -> float:
    """Price tier for a pack size: the highest configured tier not above the count, else the lowest."""
    tiers = {int(k): float(v) for k, v in (config.get("pricing.tiers") or {10: 5.0}).items()}
    eligible = [k for k in tiers if k <= count]
    return tiers[max(eligible)] if eligible else tiers[min(tiers)]


def normalize_tags(tags) -> list[str]:
    """Lowercase, strip punctuation, drop duplicates and tags over Etsy's 20-character limit."""
    out: list[str] = []
    for tag in tags if isinstance(tags, list) else []:
        clean = re.sub(r"[^a-z0-9 ]", "", str(tag).lower()).strip()
        clean = re.sub(r"\s+", " ", clean)
        if clean and len(clean) <= ETSY_TAG_MAX and clean not in out:
            out.append(clean)
    return out[:ETSY_TAGS]


def validate(data) -> list[str]:
    """Problems that must be fixed before this copy can be used."""
    if not isinstance(data, dict):
        return ["Reply must be a JSON object."]
    errors = []
    title = str(data.get("title", "")).strip()
    if not title or len(title) > ETSY_TITLE_MAX:
        errors.append(f"title must be 1-{ETSY_TITLE_MAX} characters (it is {len(title)}).")
    if len(str(data.get("description", ""))) < 150:
        errors.append("description is missing or too short.")
    if len(normalize_tags(data.get("tags"))) < ETSY_TAGS:
        errors.append(f"need {ETSY_TAGS} distinct tags, each at most {ETSY_TAG_MAX} characters, letters and numbers only.")
    for key in ("gumroad_title", "gumroad_description"):
        if not str(data.get(key, "")).strip():
            errors.append(f"{key} is missing.")
    blob = " ".join(str(data.get(k, "")) for k in ("title", "description", "gumroad_title", "gumroad_description")) + " " + " ".join(map(str, data.get("tags") or []))
    if is_banned(blob):
        errors.append("the copy mentions a banned brand, character, celebrity, weapon or drug term; remove it.")
    return errors


def write_listing(niche: str, image_paths: list[str], sample_subjects: Optional[list[str]] = None,
                  client: Optional[GeminiClient] = None, max_attempts: int = 3) -> dict:
    """Generate and validate listing copy. The AI-disclosure line is enforced in code, not trusted to the model."""
    client = client or GeminiClient()
    count = len(image_paths)
    system = Path(_PROMPT).read_text(encoding="utf-8")
    prompt = (f"Niche: {niche}\nNumber of stickers in the pack: {count}\n"
              f"Sample sticker subjects: {', '.join((sample_subjects or [])[:8]) or 'n/a'}")

    feedback, errors = "", ["no attempt made"]
    for _ in range(max_attempts):
        data = client.generate_json(prompt + feedback, system=system)
        if isinstance(data, dict):
            data["tags"] = normalize_tags(data.get("tags"))
        errors = validate(data)
        if not errors:
            data["description"] = ensure_disclosure(str(data["description"]).strip())
            data["gumroad_description"] = str(data["gumroad_description"]).strip()
            data["title"] = str(data["title"]).strip()
            data["price_usd"] = price_for_count(count)
            data["sticker_count"] = count
            return data
        feedback = "\n\nYour previous reply had these problems. Fix them all and return the full JSON again:\n- " + "\n- ".join(errors)
    raise GeminiError(f"Could not get valid listing copy after {max_attempts} attempts: {'; '.join(errors)}")
