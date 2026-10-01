"""
Writes and ranks sticker ideas BEFORE any image is paid for.

One cheap text call writes more candidate ideas than needed and critiques each one; the code (not the model) averages
the scores, ranks the ideas and keeps the top N. The scores are the model's opinion of its own work, so they are a
filter, not proof that a sticker will sell. Ideas that are repeats of each other are dropped.
"""

import math
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from src.shared import config
from src.shared.banned import is_banned
from src.shared.design import direction_for
from src.shared.gemini_client import GeminiClient
from src.shared.logger import get_logger

logger = get_logger(__name__)

_PROMPT_FILE = config.ROOT / "config" / "prompts" / "idea_writer.md"
CRITERIA = ("funny", "original", "readable", "on_brief")
MAX_CANDIDATES = 40
SIMILARITY_LIMIT = 0.8
INFLATED_SHARE = 0.5      # if more than half the ideas score 8+ on average, the model is flattering itself


@dataclass
class Idea:
    text: str
    score: float                      # mean of the four criteria, 1-10, computed here
    scores: dict = field(default_factory=dict)
    weakness: str = ""
    chosen: bool = False
    pack: int = 1


@dataclass
class IdeaSet:
    packs: list = field(default_factory=list)       # list[list[Idea]]: every candidate, best first, chosen ones flagged
    warnings: list = field(default_factory=list)

    def chosen(self, pack: int) -> list:
        return [i for i in self.packs[pack] if i.chosen]


def candidates_wanted(count: int) -> int:
    """Write more than needed so there is something to throw away."""
    return min(MAX_CANDIDATES, max(count + 3, math.ceil(count * 1.6)))


def _clean(raw, taken: list[str]) -> list[Idea]:
    ideas: list[Idea] = []
    items = raw.get("ideas", []) if isinstance(raw, dict) else raw
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text", "")).split())
        if not 8 <= len(text) <= 500 or is_banned(text):
            continue
        scores = item.get("scores") or {}
        try:
            values = [min(10.0, max(1.0, float(scores[c]))) for c in CRITERIA]
        except (KeyError, TypeError, ValueError):
            continue                                   # an idea the model did not score is not usable
        if any(SequenceMatcher(None, text.lower(), t.lower()).ratio() > SIMILARITY_LIMIT for t in taken + [i.text for i in ideas]):
            continue
        ideas.append(Idea(text=text, score=round(sum(values) / len(values), 2),
                          scores=dict(zip(CRITERIA, values)), weakness=str(item.get("weakness", "")).strip()))
    return ideas


def write_ideas(brief: str, count: int, packs: int = 1, style_md: Optional[str] = None,
                client: Optional[GeminiClient] = None, niche_id: Optional[int] = None) -> IdeaSet:
    """For each pack: write candidates, score them, keep the best `count`. Later packs avoid what earlier ones used."""
    if not brief.strip():
        raise ValueError("Write a brief first.")
    if is_banned(brief):
        raise ValueError("The brief contains a banned term.")
    client = client or GeminiClient(niche_id=niche_id)
    direction = direction_for(style_md=style_md)
    system = Path(_PROMPT_FILE).read_text(encoding="utf-8")
    result = IdeaSet()
    used: list[str] = []
    for n in range(1, max(1, packs) + 1):
        ask = f"Brief: {brief.strip()}\nStyle the stickers will be drawn in: {direction.style}\n"
        if direction.avoid:
            ask += f"Avoid: {direction.avoid}\n"
        if used:
            ask += "Already used in other packs (do not repeat or rephrase any of these):\n" + "\n".join(f"- {u}" for u in used) + "\n"
        if packs > 1:
            ask += f"This is pack {n} of {packs}: take a clearly different angle on the brief from the other packs.\n"
        ask += f"Write {candidates_wanted(count)} candidate ideas."
        ideas = _clean(client.generate_json(ask, system=system), used)
        ideas.sort(key=lambda i: -i.score)             # stable: ties keep the model's own order
        for rank, idea in enumerate(ideas):
            idea.chosen, idea.pack = rank < count, n
        chosen = [i for i in ideas if i.chosen]
        if len(chosen) < count:
            result.warnings.append(f"Pack {n}: only {len(chosen)} usable ideas came back for the {count} you asked for. Press Write ideas again, or add your own.")
        if ideas and sum(i.score >= 8 for i in ideas) / len(ideas) > INFLATED_SHARE:
            result.warnings.append(f"Pack {n}: the AI rated most of its own ideas 8 or higher, which is not believable. Treat the scores as a loose guide and read the ideas yourself.")
        used += [i.text for i in chosen]
        result.packs.append(ideas)
        logger.info("Pack %d: wrote %d candidate ideas, kept %d.", n, len(ideas), len(chosen))
    return result
