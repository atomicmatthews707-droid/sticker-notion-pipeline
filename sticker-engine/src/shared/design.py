"""
Markdown art direction. Two kinds of file, both plain markdown you can edit freely:

  DESIGN.md        the shop-wide look (style, palette, things to avoid, listing voice)
  packs/<name>.md  one pack: what it is, exact subjects if you want them, and any overrides

Sections are recognised by their heading (Style, Composition, Palette, Avoid, Voice, Notes, Brief, Subjects).
Anything else in the file is ignored, so notes to yourself are safe. <!-- comments --> are removed.
With the default DESIGN.md the prompts are identical to what the engine produced before this existed.
"""

import re
from dataclasses import dataclass
from typing import Optional

from src.shared import config

DESIGN_FILE = config.ROOT / "DESIGN.md"
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_META = re.compile(r"^([A-Za-z_ ]+?):\s*(.+)$")


def parse_markdown(text: str) -> dict:
    """{'title': H1 text, 'meta': {key: value} from lines before the first ## heading, 'sections': {heading: body}}."""
    text = _COMMENT.sub("", text or "")
    title, meta, sections, current, buf = "", {}, {}, None, []

    def flush():
        if current is not None:
            sections[current] = "\n".join(buf).strip()

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            current, buf = line[3:].strip().lower(), []
        elif line.startswith("# ") and not title and current is None:
            title = line[2:].strip()
        elif current is None:
            m = _META.match(line.strip())
            if m:
                meta[m.group(1).strip().lower().replace(" ", "_")] = m.group(2).strip()
        else:
            buf.append(line)
    flush()
    return {"title": title, "meta": meta, "sections": sections}


def bullets(text: str) -> list[str]:
    """Items of a markdown list (-, * or 1.). Plain lines count too, so a simple list of lines works."""
    items = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip()
        if line:
            items.append(line)
    return items


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


@dataclass
class Direction:
    style: str = ""
    composition: str = ""
    palette: str = ""
    avoid: str = ""
    voice: str = ""
    notes: str = ""
    brief: str = ""


def _design_sections() -> dict:
    return parse_markdown(DESIGN_FILE.read_text(encoding="utf-8"))["sections"] if DESIGN_FILE.exists() else {}


def direction_for(pack_md: Optional[str] = None, brief: str = "", style: str = "") -> Direction:
    """Shop-wide DESIGN.md (falling back to config.yaml), then the pack file's own sections on top."""
    cfg = config.get("sticker_style", {})
    shop = _design_sections()
    pack = parse_markdown(pack_md or "")["sections"]

    def pick(key: str, fallback: str = "") -> str:
        return _one_line(pack.get(key) or shop.get(key) or fallback)

    def both(key: str) -> str:  # these add up: the pack's rules come on top of the shop's
        return _one_line(" ".join(x for x in (shop.get(key, ""), pack.get(key, "")) if x))

    return Direction(
        style=_one_line(style) or pick("style", cfg.get("aesthetic", "")),
        composition=pick("composition", cfg.get("composition", "")),
        palette=pick("palette"),
        avoid=both("avoid"),
        voice=pick("voice"),
        notes=both("notes"),
        brief=_one_line(brief) or _one_line(pack.get("brief", "")),
    )


def request_from_markdown(md: str) -> dict:
    """Fields for db.queue_request from a pack file. Raises ValueError with a plain message if it is unusable."""
    doc = parse_markdown(md)
    if not doc["title"]:
        raise ValueError("The pack file needs a title: a first line like '# Autumn Cozy Vibes'.")
    count = doc["meta"].get("count")
    if count is not None and not (count.isdigit() and 1 <= int(count) <= 60):
        raise ValueError(f"count must be a number from 1 to 60, not {count!r}.")
    subjects = bullets(doc["sections"].get("subjects", "")) or None
    return {
        "name": doc["title"],
        "brief": _one_line(doc["sections"].get("brief", "")),
        "style": "",
        "count": int(count) if count else (len(subjects) if subjects else None),
        "subjects": subjects,
        "pack_md": md,
    }
