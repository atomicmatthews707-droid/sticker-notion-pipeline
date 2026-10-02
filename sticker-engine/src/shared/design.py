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
STYLES_DIR = config.ROOT / "styles"
# Switches a style file can set in its header lines. The first value in each list is the default.
SWITCHES = {
    "cutout": ["floodfill", "rembg", "none"],      # how the background is removed; none keeps the picture as drawn
    "background": ["white", "none"],               # white adds the plain white background the cutout needs
    "qa": ["sticker", "image"],                    # which quality-check rules judge the result
    "guard": ["strict", "basic"],                  # strict also demands a white border; basic only checks size and blankness
    "aspect": ["1:1", "3:4", "4:3", "9:16", "16:9"],
}
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
    text: str = ""


@dataclass
class Rules:
    """What the pipeline does differently for a style: set by header lines like 'cutout: none' in the style file."""
    cutout: str = "floodfill"
    background: str = "white"
    qa: str = "sticker"
    guard: str = "strict"
    aspect: str = "1:1"

    def describe(self) -> str:
        cut = {"floodfill": "background removed", "rembg": "background removed (rembg)", "none": "no cutout (picture kept as drawn)"}[self.cutout]
        bg = "plain white background added to every prompt" if self.background == "white" else "no background phrase added"
        qa = "sticker rules" if self.qa == "sticker" else "full-image rules"
        return f"{cut} · {bg} · quality check: {qa} · {self.aspect}"


def _design_sections() -> dict:
    return parse_markdown(DESIGN_FILE.read_text(encoding="utf-8"))["sections"] if DESIGN_FILE.exists() else {}


def validate_style(style_md: str) -> list[str]:
    """Plain-words problems in a style file's header lines. An empty list means it is fine."""
    problems = []
    for key, value in parse_markdown(style_md or "")["meta"].items():
        if key in SWITCHES and value.lower() not in SWITCHES[key]:
            problems.append(f"'{key}: {value}' is not valid. Use one of: {', '.join(SWITCHES[key])}.")
    return problems


def rules_for(style_md: Optional[str] = None) -> Rules:
    """The switches from a style file (the default DESIGN.md when none is given). Invalid values fall back to defaults."""
    if style_md is None:
        style_md = DESIGN_FILE.read_text(encoding="utf-8") if DESIGN_FILE.exists() else ""
    meta = parse_markdown(style_md)["meta"]
    values = {k: (meta[k].lower() if k in meta and meta[k].lower() in SWITCHES[k] else SWITCHES[k][0]) for k in SWITCHES}
    return Rules(**values)


# ── style presets: files in styles/ ─────────────────────────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def list_presets() -> dict:
    """{file stem: title} for every styles/*.md. Files starting with _ are templates and are not listed."""
    out = {}
    if STYLES_DIR.exists():
        for f in sorted(STYLES_DIR.glob("*.md")):
            if not f.name.startswith("_"):
                out[f.stem] = parse_markdown(f.read_text(encoding="utf-8"))["title"] or f.stem
    return out


def load_preset(stem: str) -> str:
    path = (STYLES_DIR / f"{_slug(stem)}.md").resolve()
    if STYLES_DIR.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"No style called {stem!r}.")
    return path.read_text(encoding="utf-8")


def save_preset(name: str, text: str, overwrite: bool = True) -> str:
    """Write a style file under styles/. The name becomes a safe file name; returns the stem."""
    stem = _slug(name)
    if not stem or stem.startswith("_"):
        raise ValueError("Give the style a name using letters or numbers.")
    path = (STYLES_DIR / f"{stem}.md").resolve()
    if STYLES_DIR.resolve() not in path.parents:
        raise ValueError("That name is not allowed.")
    if path.exists() and not overwrite:
        raise ValueError(f"A style called {stem!r} already exists.")
    STYLES_DIR.mkdir(exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return stem


def direction_for(pack_md: Optional[str] = None, brief: str = "", style: str = "", style_md: Optional[str] = None) -> Direction:
    """The style (a style file's text, else the default DESIGN.md, else config.yaml), then the pack file's sections on top."""
    cfg = config.get("sticker_style", {})
    shop = parse_markdown(style_md)["sections"] if style_md is not None else _design_sections()
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
        text=pick("text"),
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
