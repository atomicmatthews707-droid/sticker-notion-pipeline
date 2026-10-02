"""The keep list: stickers you dragged into the Keep & build panel. Saved per run so a refresh does not lose it."""

import json
from pathlib import Path

from src.shared import config


def _file(niche_id: int) -> Path:
    return config.output_dir() / f"niche_{niche_id}" / "keep.json"


def load(niche_id: int) -> list[int]:
    try:
        data = json.loads(_file(niche_id).read_text(encoding="utf-8"))
        return [int(i) for i in data] if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def save(niche_id: int, ids: list[int]) -> None:
    f = _file(niche_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(list(dict.fromkeys(int(i) for i in ids))), encoding="utf-8")


def add(niche_id: int, image_id: int) -> list[int]:
    ids = load(niche_id)
    if int(image_id) not in ids:
        ids.append(int(image_id))
        save(niche_id, ids)
    return ids


def remove(niche_id: int, image_id: int) -> list[int]:
    ids = [i for i in load(niche_id) if i != int(image_id)]
    save(niche_id, ids)
    return ids


def clear(niche_id: int) -> None:
    save(niche_id, [])
