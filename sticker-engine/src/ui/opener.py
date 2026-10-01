"""Open files in other programs on this computer. Only files inside the output folder can be opened."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

from src.shared import config

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".zip", ".html", ".md", ".txt"}


class NotAllowed(Exception):
    pass


def check_path(path: str, roots: list | None = None) -> Path:
    """Refuse anything that is not an existing media file inside the output folder (or the given roots)."""
    p = Path(path).resolve()
    allowed = [Path(r).resolve() for r in (roots or [config.output_dir()])]
    if not any(p == r or r in p.parents for r in allowed):
        raise NotAllowed("That file is outside the output folder.")
    if p.suffix.lower() not in ALLOWED_SUFFIXES:
        raise NotAllowed(f"Files of type {p.suffix or '(none)'} are not opened from here.")
    if not p.is_file():
        raise NotAllowed("That file no longer exists.")
    return p


def builtin_actions(platform: str | None = None) -> list[dict]:
    """The menu entries every computer gets. kind: default | choose | reveal | command."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        return [{"name": "Open", "kind": "default"}, {"name": "Open with… (choose a program)", "kind": "choose"},
                {"name": "Paint", "kind": "command", "command": ["mspaint", "{file}"]},
                {"name": "Show in folder", "kind": "reveal"}]
    if platform == "darwin":
        return [{"name": "Open", "kind": "default"}, {"name": "Preview", "kind": "command", "command": ["open", "-a", "Preview", "{file}"]},
                {"name": "Show in Finder", "kind": "reveal"}]
    return [{"name": "Open", "kind": "default"}, {"name": "Show in folder", "kind": "reveal"}]


def parse_custom_app(line: str, platform: str | None = None) -> dict:
    """'Photoshop = "C:\\Program Files\\Adobe\\Photoshop.exe" {file}' becomes a menu entry. {file} is added if missing."""
    name, sep, cmd = line.partition("=")
    if not sep or not name.strip() or not cmd.strip():
        raise ValueError("Use the form:  Name = path-to-program {file}")
    parts = shlex.split(cmd.strip(), posix=not (platform or sys.platform).startswith("win"))
    parts = [x.strip('"') for x in parts]
    if "{file}" not in parts:
        parts.append("{file}")
    return {"name": name.strip(), "kind": "command", "command": parts}


def build_command(action: dict, path: Path, platform: str | None = None) -> list[str] | None:
    """The command for an action, or None when it is done in-process (Windows 'Open')."""
    platform = platform or sys.platform
    kind = action["kind"]
    if kind == "command":
        return [str(path) if part == "{file}" else part for part in action["command"]]
    if kind == "choose":
        return ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(path)]
    if kind == "reveal":
        if platform.startswith("win"):
            return ["explorer", f"/select,{path}"]
        if platform == "darwin":
            return ["open", "-R", str(path)]
        return ["xdg-open", str(path.parent)]
    if kind == "default":
        if platform.startswith("win"):
            return None
        return ["open", str(path)] if platform == "darwin" else ["xdg-open", str(path)]
    raise ValueError(f"Unknown action kind {kind!r}")


def run_action(action: dict, path: str, roots: list | None = None, platform: str | None = None) -> str:
    """Do a menu action on a file. Returns a short message for the screen. Never uses a shell."""
    p = check_path(path, roots)
    cmd = build_command(action, p, platform)
    try:
        if cmd is None:
            os.startfile(str(p))  # noqa: S606  (Windows only; only reached for files inside the output folder)
        else:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, AttributeError) as e:
        return f"Could not open {p.name}: {e}"
    return f"{action['name']}: {p.name}"
