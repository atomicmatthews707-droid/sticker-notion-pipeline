from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from src.shared import config
from src.shared.banned import is_banned
from src.shared.gemini_client import GeminiClient
from src.shared.logger import get_logger

logger = get_logger(__name__)
_PROMPT_FILE = config.ROOT / "config" / "prompts" / "prompt_builder.md"
SIMILARITY_LIMIT = 0.85


def compose_prompt(subject: str) -> str:
    """Final image prompt: subject, then the shared style, background and composition from config."""
    style = config.get("sticker_style", {})
    parts = [subject, style.get("aesthetic"), style.get("background"), style.get("composition")]
    return ", ".join(p for p in parts if p)


def clean_subjects(raw: list, limit: Optional[int] = None) -> list[str]:
    """Strip, drop banned terms and near-duplicates (ratio > 0.85), keep order."""
    kept: list[str] = []
    for item in raw:
        subject = str(item).strip().strip(".")
        if not 2 <= len(subject) <= 120:
            continue
        if is_banned(subject):
            logger.info("Dropped banned subject: %r", subject)
            continue
        if any(SequenceMatcher(None, subject.lower(), k.lower()).ratio() > SIMILARITY_LIMIT for k in kept):
            continue
        kept.append(subject)
    return kept[:limit] if limit else kept


def build_prompts(niche: str, client: Optional[GeminiClient] = None) -> list[str]:
    """Ask the text model for distinct sticker subjects for a niche and turn them into image prompts."""
    count = int(config.get("generator.subjects_per_niche", 40))
    if is_banned(niche):
        raise ValueError(f"Niche {niche!r} contains a banned term")
    client = client or GeminiClient()
    system = Path(_PROMPT_FILE).read_text(encoding="utf-8")
    data = client.generate_json(f"Niche: {niche}\nGenerate {count} distinct sticker subjects.", system=system)
    raw = data.get("subjects", []) if isinstance(data, dict) else data
    subjects = clean_subjects(raw, limit=count)
    return [compose_prompt(s) for s in subjects]
