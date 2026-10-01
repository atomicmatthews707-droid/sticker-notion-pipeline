"""Banned-term list (brands, characters, celebrities, weapons, drugs). Whole-word, case-insensitive."""

import re
from functools import lru_cache

from src.shared.config import ROOT

_PATH = ROOT / "config" / "banned_words.txt"


@lru_cache(maxsize=1)
def banned_terms() -> tuple:
    if not _PATH.exists():
        return ()
    lines = _PATH.read_text(encoding="utf-8").splitlines()
    return tuple(ln.strip().lower() for ln in lines if ln.strip() and not ln.startswith("#"))


@lru_cache(maxsize=1)
def _pattern():
    terms = banned_terms()
    if not terms:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.IGNORECASE)


def is_banned(text: str) -> bool:
    """True if the text contains a banned term as a whole word ('weed' matches, 'seaweed' does not)."""
    pattern = _pattern()
    return bool(pattern and pattern.search(text))
