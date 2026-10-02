"""Optimize: rewrite a rough brief into a sharper one with one cheap text call. The user sees it and can undo it."""

from pathlib import Path
from typing import Optional

from src.shared import config
from src.shared.banned import is_banned
from src.shared.design import direction_for
from src.shared.gemini_client import GeminiClient

_PROMPT_FILE = config.ROOT / "config" / "prompts" / "optimize.md"
MAX_LEN = 700


def optimize_brief(brief: str, style_md: Optional[str] = None, client: Optional[GeminiClient] = None,
                   niche_id: Optional[int] = None) -> str:
    brief = (brief or "").strip()
    if not brief:
        raise ValueError("Write something to optimize first.")
    if is_banned(brief):
        raise ValueError("The text contains a banned term.")
    client = client or GeminiClient(niche_id=niche_id)
    direction = direction_for(style_md=style_md)
    ask = f"Brief to improve: {brief}\nStyle the stickers will be drawn in: {direction.style}\n"
    if direction.avoid:
        ask += f"Avoid: {direction.avoid}\n"
    data = client.generate_json(ask, system=Path(_PROMPT_FILE).read_text(encoding="utf-8"))
    better = " ".join(str((data or {}).get("prompt", "") if isinstance(data, dict) else "").split())
    if len(better) < 10:
        raise ValueError("The AI did not return a usable prompt. Your text was left unchanged.")
    if is_banned(better):
        raise ValueError("The improved text contained a banned term, so it was discarded.")
    return better[:MAX_LEN]
