"""
main.py — Sticker Engine orchestrator and FastAPI server.

Two modes:
  - Loop mode (default): runs pipeline cycles in a background thread.
  - Tick mode (DISABLE_LOOP=1): one cycle per POST /tick (Cloud Scheduler / n8n).

A cycle: budget check -> refresh trends (every few hours) -> take a niche -> run its stages
GENERATING -> FILTERING -> PACKAGING -> LISTING -> PUBLISHED.

State lives in the database, so a crash resumes the same niche at the stage it was in. Stages are
idempotent. A stage that is not built raises NotImplementedError and the niche is marked FAILED, never
faked as published. When no marketplace publishes a finished pack, the niche becomes READY: the pack and a
publish kit (listing text, files, checklist) exist and are waiting for you to upload them.

Control endpoints (/tick, /digest/send) require ENGINE_API_TOKEN (Authorization: Bearer <token> or
X-Engine-Token). Set ALLOW_UNAUTHENTICATED=1 only for local development.
"""

import asyncio
import hmac
import json
import os
import threading
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

from src.shared import config
from src.publisher import PublishUnavailable
from src.shared.gemini_client import BudgetExceeded
from src.shared.logger import get_logger
from src.storage import db

logger = get_logger(__name__)

DISABLE_LOOP = os.getenv("DISABLE_LOOP", "").strip().lower() in ("1", "true", "yes")
LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL_SECONDS") or config.get("loop_interval_seconds", 300))
TREND_REFRESH_HOURS = float(config.get("trend_scout.refresh_interval_hours", 6))
STAGE_ORDER = ["GENERATING", "FILTERING", "PACKAGING", "LISTING"]

_cycle_lock = threading.Lock()  # one pipeline cycle at a time per process


class AwaitingPublish(Exception):
    """The pack is finished but no marketplace published it. Not a failure."""


# ── Stages ──────────────────────────────────────────────────────────────────────

def _prompts_file(niche_id: int):
    return config.output_dir() / f"niche_{niche_id}" / "prompts.json"


def _load_or_build_prompts(niche_id: int, niche_name: str) -> list[str]:
    """Prompts are generated once and saved, so a resumed run asks for the same images."""
    path = _prompts_file(niche_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    from src.generator.prompt_builder import build_prompts

    prompts = build_prompts(niche_name)
    if not prompts:
        raise RuntimeError("prompt_builder returned no prompts")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prompts, indent=2), encoding="utf-8")
    return prompts


def _stage_generate(niche_id: int, niche_name: str) -> None:
    """GENERATING: prompts -> images on disk and in the DB -> local style checks."""
    from src.generator.image_gen import generate_images
    from src.generator.style_guard import check as style_check

    prompts = _load_or_build_prompts(niche_id, niche_name)
    generate_images(prompts, niche_id)

    pending = db.get_pending_images(niche_id)
    for img in pending:
        ok, reason = style_check(img["image_path"])
        if not ok:
            logger.info("Style guard rejected %s: %s", img["image_path"], reason)
            db.update_image(img["id"], kept=False, qa_reason=f"style: {reason}")
    remaining = len(db.get_pending_images(niche_id))
    if remaining == 0:
        raise RuntimeError("Style guard rejected every generated image")
    logger.info("GENERATING done: %d/%d images passed the style guard", remaining, len(pending))


def _stage_filter(niche_id: int, niche_name: str) -> None:
    """
    FILTERING: cut out the background -> vision QA on the cutout -> dedupe.
    The cutout comes first so QA judges the finished sticker, including any damage the cutout caused.
    """
    from src.quality.auto_filter import filter_batch
    from src.quality.bg_remover import remove_background
    from src.quality.deduper import dedupe

    for img in db.get_pending_images(niche_id):
        try:
            out = remove_background(img["image_path"])
        except Exception as e:
            logger.warning("Background removal failed for %s: %s", img["image_path"], e)
            db.update_image(img["id"], kept=False, qa_reason=f"background removal failed: {e}"[:200])
            continue
        if out != img["image_path"]:
            db.update_image(img["id"], image_path=out)

    pending = db.get_pending_images(niche_id)
    if pending:
        filter_batch(pending, niche_name)  # writes qa_score / qa_reason / kept itself

    kept = db.get_images_for_niche(niche_id, kept=True)
    unique_ids = {i["id"] for i in dedupe(kept)}
    for img in kept:
        if img["id"] not in unique_ids:
            db.update_image(img["id"], kept=False, qa_reason="duplicate")

    final = len(db.get_images_for_niche(niche_id, kept=True))
    minimum = config.int_setting("MIN_PACK_IMAGES", "packaging.min_images", 10)
    if final < minimum:
        raise RuntimeError(f"Too few images survived filtering: {final} (need at least {minimum})")
    logger.info("FILTERING done: %d images ready to package", final)


def _pack_dir(niche_id: int):
    return config.output_dir() / f"niche_{niche_id}" / "pack"


def _stage_package(niche_id: int, niche_name: str) -> None:
    """PACKAGING: sheet layout -> mockups -> zip bundle (with the Goodnotes PDF)."""
    from src.packaging.bundler import bundle
    from src.packaging.mockup_gen import create_mockups
    from src.packaging.sheet_layout import create_sheet

    existing = db.get_pack_for_niche(niche_id)
    if existing and existing.get("zip_path") and os.path.exists(existing["zip_path"]):
        return  # packaged before a crash; the kept images cannot change after filtering
    out_dir = _pack_dir(niche_id)
    image_paths = [i["image_path"] for i in db.get_images_for_niche(niche_id, kept=True)]
    sheet_path, _preview = create_sheet(image_paths, out_dir)
    mockups = create_mockups(sheet_path, image_paths, niche_name, out_dir)
    zip_path = bundle(niche_name, image_paths, sheet_path, mockups, out_dir)
    db.save_pack_record(niche_id, zip_path)
    logger.info("PACKAGING done: %s (%d stickers)", zip_path, len(image_paths))


def _sample_subjects(niche_id: int) -> list[str]:
    path = _prompts_file(niche_id)
    if not path.exists():
        return []
    return [p.split(",")[0] for p in json.loads(path.read_text(encoding="utf-8"))][:8]


def _stage_list(niche_id: int, niche_name: str) -> None:
    """LISTING: write copy, build the publish kit, publish where a marketplace is enabled."""
    from src.publisher import etsy_lister, gumroad_lister
    from src.publisher.kit import write_kit
    from src.publisher.listing_writer import write_listing

    pack = db.get_pack_for_niche(niche_id)
    if not pack:
        raise RuntimeError("No pack record found for this niche")
    if pack.get("etsy_url") or pack.get("gumroad_url"):
        return  # already listed before a crash; never publish twice

    out_dir = _pack_dir(niche_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    listing_file = out_dir / "listing.json"
    if listing_file.exists():
        listing = json.loads(listing_file.read_text(encoding="utf-8"))  # reuse: copy is generated (and paid for) once
    else:
        image_paths = [i["image_path"] for i in db.get_images_for_niche(niche_id, kept=True)]
        listing = write_listing(niche_name, image_paths, sample_subjects=_sample_subjects(niche_id))
        listing_file.write_text(json.dumps(listing, indent=2), encoding="utf-8")

    mockups = sorted(str(p) for p in out_dir.glob("mockup_*.jpg"))
    kit_dir = write_kit(niche_name, listing, pack["zip_path"], mockups, out_dir)

    etsy_url = gumroad_url = ""
    if etsy_lister.is_enabled():
        try:
            etsy_url = etsy_lister.create_listing(listing, pack["zip_path"], mockups)
        except PublishUnavailable as e:
            logger.warning("Etsy skipped: %s", e)
        except Exception as e:
            logger.warning("Etsy publish failed: %s", e)
    if gumroad_lister.is_enabled():
        try:
            gumroad_url = gumroad_lister.create_product(listing, pack["zip_path"], mockups)
        except Exception as e:
            logger.warning("Gumroad publish failed: %s", e)

    if not etsy_url and not gumroad_url:
        raise AwaitingPublish(f"Publish kit ready: {kit_dir}")
    db.update_pack_urls(niche_id, etsy_url, gumroad_url)
    logger.info("LISTING done. Etsy: %s Gumroad: %s", etsy_url, gumroad_url)


def _run_pipeline_for_niche(niche_id: int, niche_name: str, start_status: Optional[str] = None) -> str:
    """
    Run a niche through its stages, starting at start_status when resuming a crashed run.
    Returns "published", "ready" (finished, awaiting a manual upload), "failed" or "paused" (budget reached).
    """
    stages = [
        ("GENERATING", _stage_generate),
        ("FILTERING", _stage_filter),
        ("PACKAGING", _stage_package),
        ("LISTING", _stage_list),
    ]
    first = STAGE_ORDER.index(start_status) if start_status in STAGE_ORDER else 0

    for status, fn in stages[first:]:
        try:
            db.update_niche_status(niche_id, status)
            fn(niche_id, niche_name)
        except BudgetExceeded as e:
            logger.warning("Niche %r paused at %s: %s", niche_name, status, e)
            return "paused"
        except AwaitingPublish as e:
            db.update_niche_status(niche_id, "READY", error_msg=str(e))
            logger.info("Niche %r READY: %s", niche_name, e)
            return "ready"
        except NotImplementedError as e:
            db.update_niche_status(niche_id, "FAILED", error_msg=f"NotImplemented: {e}")
            logger.error("Niche %r FAILED at %s: not implemented: %s", niche_name, status, e)
            return "failed"
        except Exception as e:
            tb = traceback.format_exc()
            db.update_niche_status(niche_id, "FAILED", error_msg=f"{type(e).__name__}: {e}\n{tb[-500:]}")
            logger.error("Niche %r FAILED at %s: %s", niche_name, status, e)
            return "failed"

    db.update_niche_status(niche_id, "PUBLISHED")
    logger.info("Niche %r PUBLISHED", niche_name)
    return "published"


# ── Prices, trends, budget ──────────────────────────────────────────────────────

def _price_for_size(sizes_dict: dict) -> dict:
    """Normalise price-tier keys to int (YAML may load them as strings) and sort them."""
    return dict(sorted({int(k): v for k, v in sizes_dict.items()}.items()))


_last_trend_refresh: Optional[datetime] = None


def _maybe_refresh_trends() -> None:
    """Refresh trend signals every TREND_REFRESH_HOURS; fall back to the seed list if scouts find nothing."""
    global _last_trend_refresh
    now = datetime.now(timezone.utc)
    if _last_trend_refresh and (now - _last_trend_refresh).total_seconds() < TREND_REFRESH_HOURS * 3600:
        return

    logger.info("Refreshing trends from scouts")
    try:
        from src.trend_scout.etsy_scraper import scan as etsy_scan
        from src.trend_scout.google_trends import scan as trends_scan
        from src.trend_scout.pinterest_scout import scan as pinterest_scan
        from src.trend_scout.ranker import rank
        from src.trend_scout.reddit_scanner import scan as reddit_scan

        signals = []
        for name, scanner in [("etsy", etsy_scan), ("reddit", reddit_scan), ("google_trends", trends_scan), ("pinterest", pinterest_scan)]:
            try:
                got = scanner()
                signals.extend(got)
                logger.info("  %s: %d signals", name, len(got))
            except Exception as e:
                logger.warning("  %s scout error: %s", name, e)

        if signals:
            top = rank(signals, recent_check=db.recently_published)
            queued = db.queue_niches(top)
            logger.info("Queued %d of %d ranked niches", queued, len(top))
        else:
            _load_seed_niches()
    except Exception as e:
        logger.warning("Trend refresh failed: %s; loading seeds", e)
        _load_seed_niches()

    _last_trend_refresh = now


def _load_seed_niches() -> None:
    import yaml

    seeds_path = config.ROOT / "config" / "niche_seeds.yaml"
    try:
        seeds = (yaml.safe_load(seeds_path.read_text(encoding="utf-8")) or {}).get("seeds", [])
        logger.info("Loaded %d seed niches (%d new)", len(seeds), db.queue_niches(seeds))
    except Exception as e:
        logger.error("Failed to load seed niches: %s", e)


def _check_budget() -> bool:
    spent, limit = db.get_today_spend(), config.daily_budget_usd()
    if spent >= limit:
        logger.warning("Daily budget cap hit: $%.2f of $%.2f", spent, limit)
        return False
    return True


# ── Cycle and loop ──────────────────────────────────────────────────────────────

def _run_cycle() -> dict:
    """One full cycle. Safe to call from the loop and from /tick; only one runs at a time."""
    if not _cycle_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        if not _check_budget():
            return {"status": "budget_exhausted"}
        _maybe_refresh_trends()
        niche = db.get_niche_to_process()
        if not niche:
            return {"status": "idle"}
        resumed = niche["status"] in STAGE_ORDER
        logger.info("%s niche %r (id=%s)", "Resuming" if resumed else "Processing", niche["name"], niche["id"])
        outcome = _run_pipeline_for_niche(niche["id"], niche["name"], niche["status"])
        return {"status": outcome, "niche": niche["name"], "resumed": resumed}
    finally:
        _cycle_lock.release()


def _background_loop() -> None:
    logger.info("Sticker Engine loop started")
    while True:
        try:
            result = _run_cycle()
            if result["status"] == "budget_exhausted":
                now = datetime.now(timezone.utc)
                reset = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
                time.sleep((reset - now).total_seconds())
                continue
        except Exception as e:
            logger.error("Loop error: %s", e, exc_info=True)
        time.sleep(LOOP_INTERVAL)


# ── API ─────────────────────────────────────────────────────────────────────────

def require_token(request: Request) -> None:
    """Guard for endpoints that spend money. Fails closed when no token is configured."""
    expected = os.getenv("ENGINE_API_TOKEN", "")
    if not expected:
        if os.getenv("ALLOW_UNAUTHENTICATED", "").strip().lower() in ("1", "true", "yes"):
            return
        raise HTTPException(503, "ENGINE_API_TOKEN is not set; refusing unauthenticated control requests")
    auth = request.headers.get("authorization", "")
    supplied = auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-engine-token", "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Invalid or missing token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    if not DISABLE_LOOP:
        threading.Thread(target=_background_loop, daemon=True, name="pipeline-loop").start()
        logger.info("Pipeline loop started in background thread")
    else:
        logger.info("DISABLE_LOOP=1: tick mode only")
    yield


app = FastAPI(title="Sticker Engine", lifespan=lifespan)


@app.post("/tick", dependencies=[Depends(require_token)])
async def tick():
    """Run one pipeline cycle (tick mode). In loop mode the background loop already does this."""
    if not DISABLE_LOOP:
        return {"status": "loop_running"}
    return await asyncio.get_running_loop().run_in_executor(None, _run_cycle)


@app.post("/digest/send", dependencies=[Depends(require_token)])
async def send_digest_endpoint():
    """Build today's digest, save it, and email it if SMTP is configured. Reports honestly whether it was emailed."""
    from src.digest import send_digest

    result = await asyncio.get_running_loop().run_in_executor(None, send_digest)
    return {"status": "emailed" if result["emailed"] else "saved_not_emailed", **result}


@app.get("/health")
async def health():
    return {"status": "ok", "loop": not DISABLE_LOOP}
