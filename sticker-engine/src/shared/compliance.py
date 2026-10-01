"""Marketplace compliance helpers shared by the listing writer and the publishers."""

AI_DISCLOSURE = "These stickers were designed with the help of AI image tools and hand-selected for this pack."


def has_disclosure(description: str) -> bool:
    return AI_DISCLOSURE.lower() in description.lower()


def ensure_disclosure(description: str) -> str:
    """Put the AI-disclosure line at the top of a description if it is missing."""
    return description if has_disclosure(description) else f"{AI_DISCLOSURE}\n\n{description}"
