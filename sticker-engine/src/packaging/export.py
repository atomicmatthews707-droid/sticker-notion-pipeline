"""
Export a finished run as files you can hand to someone.

  goodnotes  A4 sticker pages as a PDF with transparent stickers (the format Goodnotes imports; the default)
  png        a zip of full-size transparent PNG stickers
  jpeg       a zip of full-size JPEG stickers on white

Goodnotes' own .goodnotes file type is private and cannot be written reliably, so the PDF is used instead.
Only stickers that passed (or were not checked) are exported; low-scoring and duplicate ones are left out.
"""

import io
import re
import zipfile
from pathlib import Path
from typing import Optional

from PIL import Image

from src.shared import config
from src.ui import gallery

FORMATS = {
    "goodnotes": "Goodnotes (PDF sticker pages)",
    "png": "PNG (transparent, full size)",
    "jpeg": "JPEG (white background, full size)",
}
DEFAULT_FORMAT = "goodnotes"
EXPORTABLE = ("passed", "unchecked")
PDF_MAX_PX = 1600            # a sticker on a Goodnotes page is small; full 4K would make a huge file for nothing
JPEG_QUALITY = 95


class NothingToExport(Exception):
    pass


def _slug(text: str, fallback: str = "stickers") -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or fallback


def exportable_paths(niche_id: int) -> list[str]:
    items = gallery.gallery_items(niche_id)
    return [i["path"] for i in items if i["state"] in EXPORTABLE and i["path"]]


def _on_white(path: str) -> Image.Image:
    img = Image.open(path).convert("RGBA")
    backdrop = Image.new("RGB", img.size, (255, 255, 255))
    backdrop.paste(img, mask=img.split()[3])
    return backdrop


def export_run(niche_id: int, fmt: str = DEFAULT_FORMAT, name: str = "stickers", out_dir: Optional[Path] = None) -> Path:
    """Write the export next to the run's other files and return its path."""
    if fmt not in FORMATS:
        raise ValueError(f"Unknown export format {fmt!r}.")
    paths = exportable_paths(niche_id)
    if not paths:
        raise NothingToExport("There are no passed or unchecked stickers in this run to export.")
    out_dir = out_dir or config.output_dir() / f"niche_{niche_id}" / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _slug(name)

    if fmt == "goodnotes":
        from src.packaging.bundler import make_goodnotes_pdf

        small_dir = out_dir / "_small"
        small_dir.mkdir(exist_ok=True)
        smalls = []
        for i, p in enumerate(paths, 1):
            img = Image.open(p).convert("RGBA")
            img.thumbnail((PDF_MAX_PX, PDF_MAX_PX), Image.LANCZOS)
            target = small_dir / f"{i:03d}.png"
            img.save(target, "PNG")
            smalls.append(str(target))
        target = out_dir / f"{base}_goodnotes.pdf"
        make_goodnotes_pdf(smalls, target)
        for f in small_dir.iterdir():
            f.unlink()
        small_dir.rmdir()
        return target

    target = out_dir / f"{base}_{fmt}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as z:      # PNG/JPEG are already compressed
        for i, p in enumerate(paths, 1):
            buf = io.BytesIO()
            if fmt == "png":
                Image.open(p).convert("RGBA").save(buf, "PNG")
                z.writestr(f"{base}_{i:02d}.png", buf.getvalue())
            else:
                _on_white(p).save(buf, "JPEG", quality=JPEG_QUALITY, subsampling=0)
                z.writestr(f"{base}_{i:02d}.jpg", buf.getvalue())
    return target
