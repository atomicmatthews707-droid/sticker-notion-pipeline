"""Shared image helpers for packaging."""

import math
from typing import Optional

from PIL import Image, ImageFont


def load_sticker(path: str) -> Image.Image:
    """Open a sticker as RGBA and trim transparent margins so layout can size the artwork itself."""
    img = Image.open(path).convert("RGBA")
    bbox = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return img.crop(bbox) if bbox else img


def fit(img: Image.Image, box: int) -> Image.Image:
    """Scale down (never up) so the image fits inside a box x box square."""
    scale = min(box / img.width, box / img.height, 1.0)
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def grid_shape(n: int) -> tuple[int, int]:
    """(columns, rows) for n items, as square as possible."""
    cols = max(1, math.ceil(math.sqrt(n)))
    return cols, math.ceil(n / cols)


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)  # Pillow's bundled font; no system fonts needed


def wrap_text(draw, text: str, fnt, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=fnt) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + [current] if current else lines


def average_color(images: list[Image.Image]) -> Optional[tuple[int, int, int]]:
    """Mean colour of the opaque pixels across stickers, used to tint mockup backgrounds."""
    import numpy as np

    chunks = [np.asarray(img.resize((32, 32)).convert("RGBA")).reshape(-1, 4) for img in images]
    if not chunks:
        return None
    pixels = np.concatenate(chunks)
    opaque = pixels[pixels[:, 3] > 200]
    return tuple(int(v) for v in opaque[:, :3].mean(axis=0)) if len(opaque) else None
