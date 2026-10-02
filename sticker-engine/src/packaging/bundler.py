"""Build the downloadable pack: PNGs, sheet, Goodnotes PDF, previews, README and LICENSE in one zip."""

import io
import zipfile
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from src.packaging.common import load_sticker
from src.shared import config
from src.shared.compliance import AI_DISCLOSURE
from src.shared.logger import get_logger

logger = get_logger(__name__)

LICENSE_TEXT = """LICENSE

You may use these stickers for personal use and in your own small-business products (for example planners
and digital journals you sell), as part of a larger design.

You may NOT resell, share, redistribute or give away the sticker files themselves, alone or in a bundle,
or upload them to any marketplace or file-sharing site.

All rights remain with the seller.
"""


def _readme(niche: str, count: int) -> str:
    return f"""{niche.upper()}  |  DIGITAL STICKER PACK

{AI_DISCLOSURE}

WHAT IS INCLUDED
- stickers/      {count} individual transparent PNG files (use these for the best quality)
- sticker_sheet.png        all stickers on one transparent sheet
- goodnotes_stickers.pdf   the stickers laid out for importing into Goodnotes
- previews/                listing preview images
- LICENSE.txt              how you may use the files

HOW TO USE IN GOODNOTES
1. Open Goodnotes and a notebook page.
2. Tap the + button, choose Image, and pick a PNG from the stickers folder. Or open the PDF, select a sticker
   with the lasso tool, copy it, and paste it into your notebook.
3. Drag, resize and rotate it to fit your page.

Most other note apps (Notability, Noteshelf, GoodNotes alternatives) accept the same PNG files.
This is a digital product. Nothing is shipped.
"""


def make_goodnotes_pdf(image_paths: list[str], pdf_path: Path) -> None:
    """A4 pages of stickers on a grid. Transparency is preserved so stickers can be lassoed and copied."""
    page_w, page_h = A4
    cols, rows = 3, 4
    margin = 36
    cell_w, cell_h = (page_w - 2 * margin) / cols, (page_h - 2 * margin) / rows
    pdf = canvas.Canvas(str(pdf_path), pagesize=A4)
    per_page = cols * rows
    for i, path in enumerate(image_paths):
        slot = i % per_page
        if i and slot == 0:
            pdf.showPage()
        buf = io.BytesIO()
        load_sticker(path).save(buf, "PNG")
        buf.seek(0)
        x = margin + (slot % cols) * cell_w + 8
        y = page_h - margin - (slot // cols + 1) * cell_h + 8
        pdf.drawImage(ImageReader(buf), x, y, cell_w - 16, cell_h - 16, mask="auto", preserveAspectRatio=True, anchor="c")
    pdf.save()


def bundle(niche: str, image_paths: list[str], sheet_path: str, mockup_paths: list[str], out_dir: Path) -> str:
    """Write goodnotes_stickers.pdf and the zip into out_dir; returns the zip path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "goodnotes_stickers.pdf"
    make_goodnotes_pdf(image_paths, pdf_path)

    zip_path = out_dir / "sticker_pack.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", _readme(niche, len(image_paths)))
        z.writestr("LICENSE.txt", LICENSE_TEXT)
        for i, p in enumerate(image_paths, 1):
            z.write(p, f"stickers/sticker_{i:02d}.png")
        z.write(sheet_path, "sticker_sheet.png")
        z.write(pdf_path, "goodnotes_stickers.pdf")
        for p in mockup_paths:
            z.write(p, f"previews/{Path(p).name}")

    size_mb = zip_path.stat().st_size / 1_000_000
    limit = float(config.get("packaging.max_zip_mb", 19))
    if size_mb > limit:
        logger.warning("Pack is %.1f MB, over the %.0f MB single-file limit Etsy allows.", size_mb, limit)
    return str(zip_path)
