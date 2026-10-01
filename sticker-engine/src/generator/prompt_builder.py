from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from src.shared import config
from src.shared.banned import is_banned
from src.shared.design import Direction, Rules, direction_for, rules_for
from src.shared.gemini_client import GeminiClient
from src.shared.logger import get_logger

logger = get_logger(__name__)
_PROMPT_FILE = config.ROOT / "config" / "prompts" / "prompt_builder.md"
SIMILARITY_LIMIT = 0.85


MAX_USER_PROMPT = 2000


def compose_prompt(subject: str, direction: Optional[Direction] = None, style: str = "", apply_style: bool = True,
                   rules: Optional[Rules] = None) -> str:
    """
    Final image prompt: [subject] + [style directives]: style, palette, text rules, the white background (when the
    style asks for a cutout), composition, then what to avoid. With apply_style off, your prompt as written
    (plus the white background if the style needs one).
    """
    rules = rules or rules_for()
    background = config.get("sticker_style", {}).get("background", "") if rules.background == "white" else ""
    if not apply_style:
        return ", ".join(p for p in (subject, background) if p)
    d = direction or direction_for(style=style)
    parts = [subject, style.strip() or d.style, d.palette, d.text, background, d.composition]
    prompt = ", ".join(p for p in parts if p)
    return f"{prompt}. Avoid: {d.avoid}" if d.avoid else prompt


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


def user_prompts(prompts: list, limit: Optional[int] = None) -> list[str]:
    """
    Prompts you wrote yourself: kept as written (no trimming to 120 characters, no near-duplicate removal).
    Only empty ones are dropped, and banned terms are refused so a listing can never end up with them.
    """
    cleaned = []
    for p in prompts:
        p = " ".join(str(p).split())
        if not p:
            continue
        if len(p) > MAX_USER_PROMPT:
            raise ValueError(f"A prompt is {len(p)} characters; the limit is {MAX_USER_PROMPT}.")
        if is_banned(p):
            raise ValueError(f"A prompt contains a banned term: {p[:60]!r}")
        cleaned.append(p)
    return cleaned[:limit] if limit else cleaned


def build_prompts(niche: str, client: Optional[GeminiClient] = None, brief: str = "", style: str = "",
                  count: Optional[int] = None, subjects: Optional[list] = None, niche_id: Optional[int] = None,
                  pack_md: str = "", verbatim: bool = False, apply_style: bool = True,
                  style_md: Optional[str] = None) -> list[str]:
    """
    Image prompts for a pack. With an exact subjects list the AI is not asked to brainstorm; otherwise the text
    model proposes `count` distinct subjects, guided by the brief and the art direction. Banned terms are refused.
    """
    count = count or config.int_setting("SUBJECTS_PER_NICHE", "generator.subjects_per_niche", 40)
    direction = direction_for(pack_md, brief=brief, style=style, style_md=style_md)
    rules = rules_for(style_md)
    for text in (niche, direction.brief, direction.style, direction.palette, *(subjects or [])):
        if text and is_banned(str(text)):
            raise ValueError(f"The request contains a banned term: {text!r}")

    if subjects and verbatim:
        return [compose_prompt(p, direction, apply_style=apply_style, rules=rules) for p in user_prompts(subjects, limit=count)]
    if subjects:
        chosen = clean_subjects(subjects, limit=count)
    else:
        client = client or GeminiClient(niche_id=niche_id)
        system = Path(_PROMPT_FILE).read_text(encoding="utf-8")
        ask = f"Niche: {niche}\n"
        if direction.brief:
            ask += f"What the customer wants (follow this closely): {direction.brief}\n"
        if direction.notes:
            ask += f"Shop notes: {direction.notes}\n"
        if direction.avoid:
            ask += f"Never propose subjects involving: {direction.avoid}\n"
        ask += f"Generate {count} distinct sticker subjects."
        data = client.generate_json(ask, system=system)
        chosen = clean_subjects(data.get("subjects", []) if isinstance(data, dict) else data, limit=count)
    return [compose_prompt(s, direction, rules=rules) for s in chosen]
