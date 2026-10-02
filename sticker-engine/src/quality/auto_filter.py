"""Autonomous quality gate: a vision model scores every generated sticker against the QA rubric."""

import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from PIL import Image

from src.shared import cancel, config
from src.shared.gemini_client import BudgetExceeded, GeminiClient, GeminiError
from src.shared.logger import get_logger
from src.storage import db

logger = get_logger(__name__)
_RUBRIC = config.ROOT / "config" / "prompts" / "qa_rubric.md"
_RUBRIC_IMAGE = config.ROOT / "config" / "prompts" / "qa_rubric_image.md"


VIEW_GREY = (118, 124, 136)  # neutral backdrop: exposes haze, fringes and see-through areas that white hides


REVIEW_MAX_PX = 1280


def render_for_review(path: str, sticker: bool = True) -> bytes:
    """The picture as the reviewer sees it. Stickers sit on grey so cutout damage is visible; full-frame images on white."""
    img = Image.open(path).convert("RGBA")
    if max(img.size) > REVIEW_MAX_PX:   # judging does not need 4K, and every extra pixel is billed as tokens
        img.thumbnail((REVIEW_MAX_PX, REVIEW_MAX_PX), Image.LANCZOS)
    backdrop = Image.new("RGBA", img.size, (VIEW_GREY if sticker else (255, 255, 255)) + (255,))
    backdrop.alpha_composite(img)
    out = io.BytesIO()
    backdrop.convert("RGB").save(out, "PNG")
    return out.getvalue()


def _style_md(images: list[dict]) -> Optional[str]:
    niche = db.get_niche(images[0]["niche_id"]) if images else None
    return ((niche or {}).get("options") or {}).get("style_md")


def _direction_rules(images: list[dict]) -> str:
    """Extra rules from the chosen style and the pack file. Empty (so the rubric is unchanged) when there are none."""
    from src.shared.design import direction_for

    niche = db.get_niche(images[0]["niche_id"]) if images else None
    d = direction_for((niche or {}).get("pack_md"), style_md=_style_md(images))
    lines = []
    if d.avoid:
        lines.append(f"- Score 3 or lower if the sticker shows any of: {d.avoid}")
    if d.palette:
        lines.append(f"- Deduct 1 point if the colours clearly depart from this palette: {d.palette}")
    if d.style and d.style != config.get("sticker_style", {}).get("aesthetic"):
        lines.append(f"- The intended style is: {d.style}")
    if d.notes:
        lines.append(f"- Shop notes: {d.notes}")
    return "\n\nThis shop's art direction. Enforce it too:\n" + "\n".join(lines) if lines else ""


def _parse_score(data) -> tuple[float, str]:
    """Pull (score 0-10, reason) from the model's JSON, or raise ValueError."""
    if not isinstance(data, dict) or "score" not in data:
        raise ValueError(f"missing score in {data!r}")
    score = float(data["score"])
    if not 0 <= score <= 10:
        raise ValueError(f"score {score} outside 0-10")
    return score, str(data.get("reason", ""))[:300]


def filter_batch(images: list[dict], niche: str, client: Optional[GeminiClient] = None) -> dict:
    """
    Score each pending image and write qa_score / qa_reason / kept to the DB. kept is decided here from
    config qa.min_score, not by the model. Returns {"scored", "kept", "rejected"}.
    Raises BudgetExceeded after saving partial results, and RuntimeError if API errors left images unjudged
    (those stay pending and are retried on the next run).
    """
    client = client or GeminiClient(niche_id=images[0].get("niche_id") if images else None)
    from src.shared.design import rules_for

    sticker = rules_for(_style_md(images)).qa == "sticker"
    system = (_RUBRIC if sticker else _RUBRIC_IMAGE).read_text(encoding="utf-8") + _direction_rules(images)
    min_score = float(config.get("qa.min_score", 7))
    stats = {"scored": 0, "kept": 0, "rejected": 0}
    api_errors = 0
    budget_hit = False
    stopped = False

    def judge(img: dict) -> None:
        cancel.check()
        data = client.generate_vision_json(
            f"Niche: {niche}\nScore this sticker.", render_for_review(img["image_path"], sticker), "image/png", system=system
        )
        score, reason = _parse_score(data)
        threshold = min_score + (float(config.get("qa.fallback_score_bonus", 1)) if client.vision_fallback_used else 0)
        db.update_image(img["id"], qa_score=score, qa_reason=reason, kept=score >= threshold)
        stats["scored"] += 1
        stats["kept" if score >= threshold else "rejected"] += 1

    workers = int(config.get("qa.max_workers", 3))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(judge, img): img for img in images}
        for fut in as_completed(futures):
            img = futures[fut]
            try:
                fut.result()
            except BudgetExceeded:
                budget_hit = True
            except cancel.Cancelled:
                stopped = True
            except (ValueError, GeminiError) as e:
                # The model could not give a usable verdict. Do not ship what was not judged.
                db.update_image(img["id"], qa_score=0, qa_reason=f"qa unusable: {e}"[:300], kept=False)
                stats["scored"] += 1
                stats["rejected"] += 1
            except Exception as e:
                api_errors += 1
                logger.warning("QA call failed for %s: %s", img["image_path"], e)

    if stats["scored"]:
        rate = stats["kept"] / stats["scored"]
        logger.info("QA: %d scored, %d kept (%.0f%% accepted)", stats["scored"], stats["kept"], rate * 100)
        if stats["scored"] >= 10 and (rate > 0.9 or rate < 0.2):
            logger.warning("QA acceptance rate %.0f%% is outside the healthy range; review the rubric.", rate * 100)
    if stopped:
        raise cancel.Cancelled("Stopped by the user; judged images are saved.")
    if budget_hit:
        raise BudgetExceeded("Daily budget reached during QA; judged images are saved, the rest resume later.")
    if api_errors:
        raise RuntimeError(f"QA could not judge {api_errors} images (API errors); they stay pending for the next run.")
    return stats
