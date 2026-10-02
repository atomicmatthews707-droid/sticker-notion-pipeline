"""
Turn the model's white-background images into transparent PNGs.

Default method is a flood fill from the border (never deletes interior content). rembg is optional:
it handles see-through loops better but was found to hollow out white areas of stickers, so use it only
with the vision QA gate judging the result.
"""

import io
import os
from typing import Optional

from PIL import Image

from src.shared import config
from src.shared.logger import get_logger

logger = get_logger(__name__)
# rembg only: pinned on purpose: rembg's default model changed to one whose weights are non-commercial only.
# isnet-general-use is Apache-2.0, which is safe for selling the stickers.
DEFAULT_MODEL = "isnet-general-use"
WHITE_LEVEL = 236   # all channels at or above this count as white background
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
    Default cutout: clear only the near-white pixels that connect to the image border, then trim 1px and
    feather the edge. Anything enclosed by the sticker's outline is kept, so white fills (a blanket, a ghost,
    a bear's belly) can never be hollowed out. Trade-off: white enclosed by a loop that should be
    see-through (a mug handle) stays white. Needs no model download.
    """
    import numpy as np
    from PIL import ImageFilter
    from scipy import ndimage

    rgb = Image.open(image_path).convert("RGB")
    arr = np.asarray(rgb)
    near_white = arr.min(axis=2) >= WHITE_LEVEL
    labels, _ = ndimage.label(near_white)
    border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    background = np.isin(labels, border[border != 0])
    background = ndimage.binary_dilation(background, iterations=1)  # drop the light anti-aliased fringe
    alpha = Image.fromarray(np.where(background, 0, 255).astype("uint8"))
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.8))               # soft, not jagged, edge
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    out.save(out_path, "PNG")


def remove_background(image_path: str, method: Optional[str] = None) -> str:
    """Write <name>_nobg.png next to the input and return its path. Never overwrites the original."""
    if os.path.splitext(image_path)[0].endswith("_nobg"):
        return image_path
    method = method or config.get("bg_remover.method", "floodfill")
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
