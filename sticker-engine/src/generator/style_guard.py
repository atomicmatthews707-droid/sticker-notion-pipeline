import numpy as np
from PIL import Image

MIN_SIZE = 512
WHITE_LEVEL = 235          # grey level at or above which a pixel counts as white background
BORDER_WHITE_RATIO = 0.90  # share of the outer frame that must be white
MIN_CONTENT_RATIO = 0.01   # share of non-white pixels needed to count as a real sticker


def _on_white(img: Image.Image) -> Image.Image:
    """Composite any transparency over white so alpha cannot fool the checks."""
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


def check(image_path: str, strict: bool = True) -> tuple[bool, str]:
    """
    Fast local checks, no API calls: size, aspect, blank image. strict (sticker styles) also demands a plain white
    background around the subject; styles that fill the whole frame (photographs) use strict=False.
    """
    try:
        with Image.open(image_path) as img:
            img.load()
            width, height = img.size
            if width < MIN_SIZE or height < MIN_SIZE:
                return False, f"Resolution {width}x{height} < {MIN_SIZE}x{MIN_SIZE}"
            if not 0.8 <= width / height <= 1.2:
                return False, "Aspect ratio not square-ish"
            small = _on_white(img)
            small.thumbnail((256, 256))
    except Exception as e:
        return False, f"Unreadable image: {e}"

    grey = np.asarray(small.convert("L"), dtype=np.uint8)
    if float(np.std(grey)) <= 5:
        return False, "Blank image"
    if not strict:
        return True, "ok"
    frame = max(2, int(min(grey.shape) * 0.03))
    border = np.concatenate([grey[:frame].ravel(), grey[-frame:].ravel(), grey[:, :frame].ravel(), grey[:, -frame:].ravel()])
    if float(np.mean(border >= WHITE_LEVEL)) < BORDER_WHITE_RATIO:
        return False, "Background not white enough"
    if float(np.mean(grey < WHITE_LEVEL)) < MIN_CONTENT_RATIO:
        return False, "Blank image"
    return True, "ok"
