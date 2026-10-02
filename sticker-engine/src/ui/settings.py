"""Your choices, remembered between runs in ui_settings.json next to the project (never committed)."""

import json
from pathlib import Path

from src.shared import config

FILE = config.ROOT / "ui_settings.json"
DEFAULTS = {
    "daily_cap": None,          # dollars; None means the value from config.yaml
    "open_with": [],            # extra programs for the right-click menu, as 'Name = program {file}' lines
    "background": "white",
    "mode": "smart",
    "apply_style": True,
    "qa": True,
    "build_pack": False,
    "count": 10,
    "variants": 1,
    "reference_mode": "style",
    "style_preset": "clipart-kawaii",
    "batches": 1,
    "write_ideas": True,
    "image_size": "4K",
    "export_format": "goodnotes",
}


def load(path: Path | None = None) -> dict:
    path = path or FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save(values: dict, path: Path | None = None) -> None:
    path = path or FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: values[k] for k in DEFAULTS if k in values}, indent=2), encoding="utf-8")
    tmp.replace(path)
