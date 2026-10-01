"""Image generation: resumable, budget-aware, saves real PNG files and records them in the DB."""

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from PIL import Image

from src.shared import config
from src.shared.gemini_client import BudgetExceeded, GeminiClient, ImageBlocked
from src.shared.logger import get_logger
from src.storage import db

logger = get_logger(__name__)
MAX_WORKERS_CAP = 4


def raw_dir(niche_id: int) -> Path:
    return config.output_dir() / f"niche_{niche_id}" / "raw"


def image_path_for(niche_id: int, prompt: str) -> Path:
    """Stable file name per prompt, so a resumed run finds the same file."""
    return raw_dir(niche_id) / f"{hashlib.sha1(prompt.encode()).hexdigest()[:12]}.png"


def to_png(data: bytes) -> Image.Image:
    """The image model returns JPEG bytes; decode whatever came back and normalise to RGB."""
    return Image.open(io.BytesIO(data)).convert("RGB")


def generate_images(
    prompts: list[str],
    niche_id: int,
    client: Optional[GeminiClient] = None,
    max_workers: Optional[int] = None,
) -> list[dict]:
    """
    Generate one image per prompt, skipping prompts that already have a record (resume).
    Blocked prompts are recorded as rejected so they are never retried. Raises BudgetExceeded after
    saving what was finished, and RuntimeError if every attempted prompt failed.
    Returns [{id, prompt, image_path}] for every usable image of this niche from these prompts.
    """
    client = client or GeminiClient(niche_id=niche_id)
    workers = min(int(max_workers or config.get("generator.max_workers", 2)), MAX_WORKERS_CAP)
    known = {r["prompt"]: r for r in db.get_images_for_niche(niche_id, kept=None)}

    todo = [p for p in prompts if p not in known or (known[p]["image_path"] and not Path(known[p]["image_path"]).exists())]
    raw_dir(niche_id).mkdir(parents=True, exist_ok=True)
    logger.info("Niche %s: %d prompts, %d already done, %d to generate", niche_id, len(prompts), len(prompts) - len(todo), len(todo))

    def work(prompt: str) -> None:
        result = client.generate_image(prompt)
        path = image_path_for(niche_id, prompt)
        to_png(result.data).save(path, "PNG")
        db.save_image_record(niche_id, prompt, str(path))

    failures, budget_hit = 0, False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, p): p for p in todo}
        for fut in as_completed(futures):
            prompt = futures[fut]
            try:
                fut.result()
            except ImageBlocked as e:
                logger.info("Blocked, skipping %r: %s", prompt[:60], e)
                db.save_image_record(niche_id, prompt, "", kept=False, qa_reason=f"blocked: {e}"[:200])
            except BudgetExceeded:
                budget_hit = True
            except Exception as e:  # one bad prompt must not lose the batch
                failures += 1
                logger.warning("Image failed for %r: %s", prompt[:60], e)

    if budget_hit:
        raise BudgetExceeded("Daily budget reached during image generation; progress is saved and will resume.")
    if todo and failures == len(todo):
        raise RuntimeError(f"Every image generation attempt failed ({failures}).")

    by_prompt = {r["prompt"]: r for r in db.get_images_for_niche(niche_id, kept=None)}
    return [
        {"id": by_prompt[p]["id"], "prompt": p, "image_path": by_prompt[p]["image_path"]}
        for p in prompts
        if p in by_prompt and by_prompt[p]["image_path"] and Path(by_prompt[p]["image_path"]).exists()
    ]
