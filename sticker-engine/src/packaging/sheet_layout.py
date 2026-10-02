from pathlib import Path

from PIL import Image

from src.packaging.common import grid_shape, load_sticker
from src.shared import config

GAP = 1.12  # cell size relative to the largest sticker


def plan_layout(sizes: list[tuple[int, int]], canvas: int) -> tuple[float, list[tuple[int, int]]]:
    """
    Scale and top-left positions for stickers of the given (width, height) on a canvas x canvas sheet.
    Stickers keep their natural size and are clustered at the centre; only a pack too big for the sheet is
    scaled down, and every sticker gets the same scale. An incomplete last row is centred.
    """
    n = len(sizes)
    cols, rows = grid_shape(n)
    natural_cell = round(max(max(w, h) for w, h in sizes) * GAP)
    scale = min(1.0, canvas / (cols * natural_cell), canvas / (rows * natural_cell))
    cell = natural_cell * scale
    left, top = (canvas - cols * cell) / 2, (canvas - rows * cell) / 2
    positions = []
    for i, (w, h) in enumerate(sizes):
        row, col = divmod(i, cols)
        in_row = n - (rows - 1) * cols if row == rows - 1 else cols
        x = left + (cols - in_row) * cell / 2 + col * cell + (cell - w * scale) / 2
        y = top + row * cell + (cell - h * scale) / 2
        positions.append((round(x), round(y)))
    return scale, positions


def create_sheet(image_paths: list[str], out_dir: Path) -> tuple[str, str]:
    """
    Lay all stickers on one transparent RGBA sheet (default 3000x3000) and write a white-background
    JPG preview. Returns (sheet_png, preview_jpg).
    """
    size = int(config.get("packaging.sheet_px", 3000))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stickers = [load_sticker(p) for p in image_paths]
    scale, positions = plan_layout([s.size for s in stickers], size)

    sheet = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    for sticker, pos in zip(stickers, positions):
        art = sticker if scale == 1.0 else sticker.resize((max(1, round(sticker.width * scale)), max(1, round(sticker.height * scale))), Image.LANCZOS)
        sheet.alpha_composite(art, pos)

    sheet_path, preview_path = out_dir / "sticker_sheet.png", out_dir / "sticker_sheet_preview.jpg"
    sheet.save(sheet_path, "PNG", optimize=True)
    preview = Image.new("RGB", sheet.size, "white")
    preview.paste(sheet, mask=sheet.getchannel("A"))
    preview.save(preview_path, "JPEG", quality=90)
    return str(sheet_path), str(preview_path)
