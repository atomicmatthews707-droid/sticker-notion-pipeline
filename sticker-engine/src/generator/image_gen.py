"""Image generation: resumable, budget-aware, saves real PNG files and records them in the DB."""

import hashlib
import io
import itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from PIL import Image

from src.shared import cancel, config
from src.shared.gemini_client import BudgetExceeded, GeminiClient, ImageBlocked
from src.shared.logger import get_logger
from src.storage import db

logger = get_logger(__name__)
MAX_WORKERS_CAP = 4


REFERENCE_NOTES = {
    "style": "Use the attached reference image(s) as a style guide only: line weight, colours and shapes. Do not copy their subjects.",
    "subject": "Redraw the subject shown in the attached reference image(s) as a sticker.",
    "character": "Keep the character in the attached reference image(s) consistent: the same face, proportions and colours.",
}


def raw_dir(niche_id: int) -> Path:
    return config.output_dir() / f"niche_{niche_id}" / "raw"


def image_path_for(niche_id: int, prompt: str, variant: int = 0) -> Path:
    """Stable file name per prompt and variation, so a resumed run finds the same file."""
    key = prompt if variant == 0 else f"{prompt}#v{variant}"
    return raw_dir(niche_id) / f"{hashlib.sha1(key.encode()).hexdigest()[:12]}.png"


def seed_for(prompt: str, variant: int) -> int:
    """Repeatable per prompt, different for every variation."""
    return int(hashlib.sha1(prompt.encode()).hexdigest()[:7], 16) + variant


def load_references(paths: list[str], max_px: int = 1024) -> list[tuple[bytes, str]]:
    """Reference images as (PNG bytes, mime), shrunk so they add little to the request."""
    out = []
    for p in paths:
        with Image.open(p) as im:
            im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
            im.thumbnail((max_px, max_px), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            out.append((buf.getvalue(), "image/png"))
    return out


def to_png(data: bytes) -> Image.Image:
    """The image model returns JPEG bytes; decode whatever came back and normalise to RGB."""
    return Image.open(io.BytesIO(data)).convert("RGB")


def generate_images(
    prompts: list[str],
    niche_id: int,
    client: Optional[GeminiClient] = None,
    max_workers: Optional[int] = None,
    variants: int = 1,
    references: Optional[list[str]] = None,
    reference_mode: str = "style",
    aspect_ratio: str = "1:1",
) -> list[dict]:
    """
    Generate `variants` images (different seeds) for each prompt, skipping any that already exist (resume).
    Blocked prompts are recorded as rejected so they are never retried. Raises BudgetExceeded or Cancelled after
    saving what was finished, and RuntimeError if every attempted image failed.
    Returns [{id, prompt, variant, image_path}] for every usable image of these prompts.
    """
    client = client or GeminiClient(niche_id=niche_id)
    workers = min(int(max_workers or config.get("generator.max_workers", 2)), MAX_WORKERS_CAP)
    known = {(r["prompt"], r["variant"] or 0): r for r in db.get_images_for_niche(niche_id, kept=None)}
    refs = load_references(references or [])
    note = REFERENCE_NOTES.get(reference_mode, REFERENCE_NOTES["style"]) if refs else ""

    wanted = [(p, v) for p in prompts for v in range(max(1, variants))]
    todo = [(p, v) for p, v in wanted
            if (p, v) not in known or (known[(p, v)]["image_path"] and not Path(known[(p, v)]["image_path"]).exists())]
    raw_dir(niche_id).mkdir(parents=True, exist_ok=True)
    logger.info("Niche %s: %d images wanted, %d already done, %d to generate", niche_id, len(wanted), len(wanted) - len(todo), len(todo))

    def work(prompt: str, variant: int) -> None:
        cancel.check()
        sent = f"{note}\n\n{prompt}" if note else prompt
        result = client.generate_image(sent, references=refs or None, seed=seed_for(prompt, variant), aspect_ratio=aspect_ratio)
        path = image_path_for(niche_id, prompt, variant)
        to_png(result.data).save(path, "PNG")
        db.save_image_record(niche_id, prompt, str(path), variant=variant)
        logger.info("Generated image %d of %d (%s, variation %d)", next(counter), len(wanted), prompt[:40], variant + 1)

    counter = itertools.count(len(wanted) - len(todo) + 1)  # next() is atomic, so worker threads cannot clash
    failures, budget_hit, stopped = 0, False, False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, p, v): (p, v) for p, v in todo}
        for fut in as_completed(futures):
            prompt, variant = futures[fut]
            try:
                fut.result()
            except cancel.Cancelled:
                stopped = True
            except ImageBlocked as e:
                logger.info("Blocked, skipping %r: %s", prompt[:60], e)
                db.save_image_record(niche_id, prompt, "", kept=False, qa_reason=f"blocked: {e}"[:200], variant=variant)
            except BudgetExceeded:
                budget_hit = True
            except Exception as e:  # one bad image must not lose the batch
                failures += 1
                logger.warning("Image failed for %r: %s", prompt[:60], e)

    if stopped:
        raise cancel.Cancelled("Stopped by the user; finished images are saved.")
    if budget_hit:
        raise BudgetExceeded("Daily budget reached during image generation; progress is saved and will resume.")
    if todo and failures == len(todo):
        raise RuntimeError(f"Every image generation attempt failed ({failures}).")

    by_key = {(r["prompt"], r["variant"] or 0): r for r in db.get_images_for_niche(niche_id, kept=None)}
    return [
        {"id": by_key[k]["id"], "prompt": k[0], "variant": k[1], "image_path": by_key[k]["image_path"]}
        for k in wanted
        if k in by_key and by_key[k]["image_path"] and Path(by_key[k]["image_path"]).exists()
    ]
