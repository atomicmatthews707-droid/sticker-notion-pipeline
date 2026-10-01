"""Central config: config/config.yaml, with a few env overrides. Cached per process."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def load_config() -> dict:
    path = Path(os.getenv("CONFIG_PATH", ROOT / "config" / "config.yaml"))
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def reload_config() -> None:
    load_config.cache_clear()


def get(path: str, default: Any = None) -> Any:
    """Dotted lookup, e.g. get("generator.max_workers", 2)."""
    node: Any = load_config()
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def output_dir() -> Path:
    """Where generated images and packs go. Gitignored; set OUTPUT_DIR for a mounted volume."""
    return Path(os.getenv("OUTPUT_DIR", ROOT / "output"))


def daily_budget_usd() -> float:
    return float(os.getenv("BUDGET_DAILY_LIMIT_USD") or get("budget.daily_limit_usd", 10.0))
