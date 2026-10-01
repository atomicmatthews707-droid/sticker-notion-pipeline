import imagehash
from PIL import Image

from src.shared.logger import get_logger

logger = get_logger(__name__)
HAMMING_LIMIT = 8  # distance below this means "the same picture"


def _path(item) -> str:
    return item["image_path"] if isinstance(item, dict) else item


def _hash(path: str):
    """Hash on a white background, so a transparent copy and its white-background original match."""
    with Image.open(path) as img:
        if img.mode in ("RGBA", "LA") or "transparency" in img.info:
            rgba = img.convert("RGBA")
            flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba)
            return imagehash.phash(flat.convert("RGB"))
        return imagehash.phash(img.convert("RGB"))


def _score(item) -> float:
    return float(item.get("qa_score") or 0) if isinstance(item, dict) else 0.0


def dedupe(items: list) -> list:
    """
    Drop near-duplicate images (perceptual hash distance < 8). Items are file paths or DB records with
    image_path/qa_score; within each group of duplicates the highest qa_score wins (ties keep the earlier).
    Unreadable images are dropped. Returns the survivors in their original order.
    """
    hashed = []
    for index, item in enumerate(items):
        try:
            hashed.append((index, _hash(_path(item))))
        except Exception as e:
            logger.warning("Dedupe skipped unreadable image %s: %s", _path(item), e)

    survivors: list[tuple[int, object]] = []
    for index, h in sorted(hashed, key=lambda t: (-_score(items[t[0]]), t[0])):
        if all(h - kept_hash >= HAMMING_LIMIT for _, kept_hash in survivors):
            survivors.append((index, h))
    return [items[i] for i in sorted(i for i, _ in survivors)]
