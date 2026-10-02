"""Turn what you paste into the text box into a list of prompts. No AI is involved: your words are the prompts."""

import re
from dataclasses import dataclass

from src.shared.design import bullets, parse_markdown

_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
MAX_LINE_FOR_LIST = 100   # longer lines are probably one wrapped prompt, not a list (the screen shows what was detected)


@dataclass
class Parsed:
    name: str
    prompts: list
    how: str            # a plain-words description of what was detected, shown under the text box


def _items(lines: list[str]) -> list[str]:
    """List items (- * + or 1.) with their indented continuation lines folded in."""
    out: list[str] = []
    for line in lines:
        m = _ITEM.match(line)
        if m:
            out.append(m.group(1).strip())
        elif out and line.strip() and line[:1] in " \t":
            out[-1] = f"{out[-1]} {line.strip()}"
    return [x for x in out if x]


def _name(title: str, prompts: list[str]) -> str:
    if title:
        return title[:80]
    if prompts:
        words = re.sub(r"[^\w\s'-]", "", prompts[0]).split()[:6]
        if words:
            return " ".join(words).capitalize()
    return "Sticker test"


def parse_prompts(text: str, mode: str = "smart") -> Parsed:
    """
    mode "smart": a ## Subjects list, else list items, else paragraphs separated by blank lines, else one prompt
    per short line, else the whole text as one prompt. mode "single": everything is one prompt.
    mode "lines": every non-empty line is its own prompt.
    """
    text = _COMMENT.sub("", text or "").strip()
    if not text:
        return Parsed("", [], "Nothing yet. Paste a prompt or a list of prompts.")
    doc = parse_markdown(text)
    body_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    title = doc["title"]

    if mode == "single":
        prompt = " ".join(" ".join(body_lines).split())
        return Parsed(_name(title, [prompt]), [prompt] if prompt else [], "Using your whole text as one prompt.")
    if mode == "lines":
        prompts = [(_ITEM.match(ln).group(1) if _ITEM.match(ln) else ln).strip() for ln in body_lines if ln.strip()]
        return Parsed(_name(title, prompts), prompts, f"One prompt per line: {len(prompts)} found.")

    subjects = bullets(doc["sections"].get("subjects", ""))
    if subjects:
        return Parsed(_name(title, subjects), subjects, f"Read the Subjects list from your file: {len(subjects)} prompts.")

    if doc["sections"]:                       # a pack file without a Subjects list: its Brief is the prompt
        base = doc["sections"].get("brief") or " ".join(body_lines)
        prompt = " ".join(base.split())
        return Parsed(_name(title, [prompt]), [prompt] if prompt else [], "Using the Brief from your file as one prompt.")

    items = _items(body_lines)
    if items:
        return Parsed(_name(title, items), items, f"Read your list: {len(items)} prompts.")

    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", "\n".join(body_lines)) if p.strip()]
    if len(paragraphs) > 1:
        return Parsed(_name(title, paragraphs), paragraphs, f"Paragraphs separated by blank lines: {len(paragraphs)} prompts.")

    lines = [ln.strip() for ln in body_lines if ln.strip()]
    if len(lines) > 1 and all(len(ln) <= MAX_LINE_FOR_LIST for ln in lines):
        return Parsed(_name(title, lines), lines, f"One prompt per line: {len(lines)} found.")

    prompt = " ".join(" ".join(lines).split())
    return Parsed(_name(title, [prompt]), [prompt] if prompt else [], "One prompt.")
