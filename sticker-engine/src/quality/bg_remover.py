"""Turn the model's white-background images into transparent PNGs."""

import io
import os
from typing import Optional

from PIL import Image, ImageDraw

from src.shared import config
from src.shared.logger import get_logger

logger = get_logger(__name__)
# Pinned on purpose: rembg's default model changed to one whose weights are non-commercial only.
# isnet-general-use is Apache-2.0, which is safe for selling the stickers.
DEFAULT_MODEL = "isnet-general-use"
OPAQUE_FROM = 245  # rembg leaves the subject at alpha 250-254; snap it to fully opaque
_session = None


def _get_session(model: str):
    global _session
    if _session is None:
        import rembg

        _session = rembg.new_session(model)
    return _session


def _harden_alpha(png_bytes: bytes) -> bytes:
    """Make near-opaque pixels fully opaque; soft edge pixels below the threshold are left alone."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    lut = [255 if v >= OPAQUE_FROM else v for v in range(256)]
    img.putalpha(img.getchannel("A").point(lut))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _output_path(image_path: str) -> str:
    stem, _ = os.path.splitext(image_path)
    return stem + "_nobg.png"


def _floodfill(image_path: str, out_path: str) -> None:
    """
    No-ML fallback: clear the white that touches the border. Lower quality than rembg: white areas
    enclosed by the subject (a mug handle, a ring) are NOT cleared. Use only when rembg is unavailable.
    """
    img = Image.open(image_path).convert("RGBA")
    w, h = img.size
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if img.getpixel(seed)[:3] != (0, 0, 0) and sum(img.getpixel(seed)[:3]) > 3 * 225:
            ImageDraw.floodfill(img, seed, (255, 255, 255, 0), thresh=28)
    img.save(out_path, "PNG")


def remove_background(image_path: str, method: Optional[str] = None) -> str:
    """Write <name>_nobg.png next to the input and return its path. Never overwrites the original."""
    if os.path.splitext(image_path)[0].endswith("_nobg"):
        return image_path
    method = method or config.get("bg_remover.method", "rembg")
    out_path = _output_path(image_path)
    if method == "floodfill":
        _floodfill(image_path, out_path)
        return out_path

    import rembg

    session = _get_session(config.get("bg_remover.model", DEFAULT_MODEL))
    with open(image_path, "rb") as f:
        result = rembg.remove(f.read(), session=session)
    with open(out_path, "wb") as f:
        f.write(_harden_alpha(result))
    return out_path
