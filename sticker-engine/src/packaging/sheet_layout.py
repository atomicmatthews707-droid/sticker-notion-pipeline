from pathlib import Path

from PIL import Image

from src.packaging.common import fit, grid_shape, load_sticker
from src.shared import config


def create_sheet(image_paths: list[str], out_dir: Path) -> tuple[str, str]:
    """
    Lay all stickers on one transparent RGBA sheet (default 3000x3000) and write a white-background
    JPG preview. Returns (sheet_png, preview_jpg).
    """
    size = int(config.get("packaging.sheet_px", 3000))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stickers = [load_sticker(p) for p in image_paths]
    cols, rows = grid_shape(len(stickers))
    cell = min(size // cols, size // rows)
    pad = int(cell * 0.06)
    left = (size - cell * cols) // 2
    top = (size - cell * rows) // 2

    sheet = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    for i, sticker in enumerate(stickers):
        art = fit(sticker, cell - 2 * pad)
        in_last_row = i // cols == rows - 1
        row_count = len(stickers) - (rows - 1) * cols if in_last_row else cols
        row_shift = (cols - row_count) * cell // 2      # centre an incomplete last row
        x = left + row_shift + (i % cols) * cell + (cell - art.width) // 2
        y = top + (i // cols) * cell + (cell - art.height) // 2
        sheet.alpha_composite(art, (x, y))

    sheet_path, preview_path = out_dir / "sticker_sheet.png", out_dir / "sticker_sheet_preview.jpg"
    sheet.save(sheet_path, "PNG", optimize=True)
    preview = Image.new("RGB", sheet.size, "white")
    preview.paste(sheet, mask=sheet.getchannel("A"))
    preview.save(preview_path, "JPEG", quality=90)
    return str(sheet_path), str(preview_path)
