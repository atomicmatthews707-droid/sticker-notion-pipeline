"""What the review area shows. Every generated image is listed: a score is a label, never a reason to hide one."""

import json
from pathlib import Path
from typing import Optional

from src.shared import config
from src.storage import db


def original_path(path: str) -> str:
    return path.replace("_nobg.png", ".png") if path else ""


def gallery_items(niche_id: int) -> list[dict]:
    """One entry per image generated for this pack, in the order they were made."""
    rows = db.get_images_for_niche(niche_id, kept=None)
    groups: dict = {}
    for r in rows:
        if r["qa_score"] is not None:
            groups.setdefault(r["prompt"], []).append(r)
    best_ids = set()
    for rs in groups.values():
        if len(rs) > 1:                                  # only meaningful when a prompt has several variations
            best_ids.add(max(rs, key=lambda r: (r["qa_score"], -(r["variant"] or 0)))["id"])

    items = []
    for r in rows:
        path = r["image_path"] or ""
        has_file = bool(path) and Path(path).exists()
        reason = r["qa_reason"] or ""
        if not has_file:
            state = "blocked" if reason.startswith("blocked") else "missing"
        elif r["kept"] == 1 and reason == "AI quality check skipped":
            state = "unchecked"
        elif r["kept"] == 1:
            state = "passed"
        elif r["kept"] == 0 and reason.startswith("duplicate"):
            state = "duplicate"
        elif r["kept"] == 0:
            state = "low"
        else:
            state = "waiting"
        items.append({
            "id": r["id"], "prompt": r["prompt"], "variant": r["variant"] or 0, "score": r["qa_score"], "reason": reason,
            "state": state, "best": r["id"] in best_ids, "path": path if has_file else "",
            "original": original_path(path) if has_file and Path(original_path(path)).exists() else "",
            "cutout": path if has_file and path.endswith("_nobg.png") else "",
        })
    return items


def expected_images(niche_id: int) -> Optional[int]:
    """How many images this pack was asked to make: prompts x variations."""
    f = config.output_dir() / f"niche_{niche_id}" / "prompts.json"
    if not f.exists():
        return None
    return len(json.loads(f.read_text(encoding="utf-8"))) * ((db.get_niche(niche_id) or {}).get("options", {}).get("variants", 1) or 1)


def filter_items(items: list[dict], show: str) -> list[dict]:
    """show: all | passed | low | unchecked | duplicate. 'all' is the default and never hides anything."""
    if show == "all":
        return items
    return [i for i in items if i["state"] == show]


def progress(niche_id: int, expected: Optional[int]) -> dict:
    items = gallery_items(niche_id)
    made = sum(1 for i in items if i["path"])
    judged = sum(1 for i in items if i["state"] in ("passed", "low"))
    return {"made": made, "judged": judged, "expected": expected, "total": len(items)}
