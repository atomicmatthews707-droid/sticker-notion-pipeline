"""
main.py — Sticker Engine orchestrator + FastAPI server.

Two modes:
  - Loop mode (default): runs the pipeline in a background thread indefinitely.
  - Tick mode (DISABLE_LOOP=1): one pipeline cycle per POST /tick call.
    Use this on Cloud Run (stateless) with Cloud Scheduler hitting /tick.

The loop:
  budget check
  → every 6h: refresh trends from scouts → rank → queue top niches
  → take highest-scored QUEUED niche
  → GENERATING → FILTERING → PACKAGING → LISTING → PUBLISHED
  Any stage raising NotImplementedError marks the niche FAILED so we can see
  which stub is next in the DB.

HANDOFF Phase 0 fix: _price_for_size normalizes dict keys to int before sorting
(YAML may load numeric keys as strings, breaking sorted() ordering).
"""

import os
import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI
from src.storage.db import init_db, get_niche_to_process, update_niche_status, queue_niches
from src.shared.logger import get_logger

logger = get_logger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
DISABLE_LOOP = os.getenv("DISABLE_LOOP", "").strip() in ("1", "true", "yes")
LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL_SECONDS", "300"))
TREND_REFRESH_HOURS = 6


# ── Pipeline stage imports (lazy: each stage only imported when needed) ─────────
def _run_pipeline_for_niche(niche_id: int, niche_name: str) -> None:
    """
    Run one full niche through all pipeline stages sequentially.
    Each stage transition updates the DB. NotImplementedError → FAILED.
    Any other exception also marks FAILED with the traceback message.
    This function runs in a thread, not in the async loop.
    """
    import traceback

    stages = [
        ("GENERATING", _stage_generate),
        ("FILTERING", _stage_filter),
        ("PACKAGING", _stage_package),
        ("LISTING", _stage_list),
    ]

    for status, fn in stages:
        try:
            update_niche_status(niche_id, status)
            fn(niche_id, niche_name)
        except NotImplementedError as e:
            update_niche_status(niche_id, "FAILED", error_msg=f"NotImplemented: {e}")
            logger.error(f"Niche {niche_name!r} FAILED at {status}: NotImplemented — {e}")
            return
        except Exception as e:
            tb = traceback.format_exc()
            update_niche_status(niche_id, "FAILED", error_msg=f"{type(e).__name__}: {e}\n{tb[:500]}")
            logger.error(f"Niche {niche_name!r} FAILED at {status}: {e}")
            return

    update_niche_status(niche_id, "PUBLISHED")
    logger.info(f"Niche {niche_name!r} → PUBLISHED ✓")


# ── Pipeline stage functions ────────────────────────────────────────────────────

def _stage_generate(niche_id: int, niche_name: str) -> None:
    """GENERATING: build prompts → generate images → style guard check."""
    from src.generator.prompt_builder import build_prompts
    from src.generator.image_gen import generate_images
    from src.generator.style_guard import check as style_check
    from src.storage.db import save_image_record

    prompts = build_prompts(niche_name)
    if not prompts:
        raise RuntimeError("prompt_builder returned no prompts")

    results = generate_images(prompts, niche_id)

    # Style guard: reject blurry/blank images before they waste QA budget
    kept = []
    for r in results:
        ok, reason = style_check(r["image_path"])
        if ok:
            kept.append(r)
        else:
            logger.info(f"Style guard rejected {r['image_path']}: {reason}")
            save_image_record(niche_id, r["prompt"], r["image_path"], kept=False, qa_reason=reason)

    if not kept:
        raise RuntimeError("Style guard rejected all generated images")

    logger.info(f"GENERATING done: {len(kept)}/{len(results)} images passed style guard")


def _stage_filter(niche_id: int, niche_name: str) -> None:
    """FILTERING: vision QA → dedupe."""
    from src.storage.db import get_images_for_niche
    from src.quality.auto_filter import filter_batch
    from src.quality.deduper import dedupe
    from src.quality.bg_remover import remove_background

    images = get_images_for_niche(niche_id, kept=None)  # all style-guard-passed images
    image_paths = [img["image_path"] for img in images]

    # Auto-filter: vision model QA scores each image
    kept_paths = filter_batch(image_paths, niche_name)

    if len(kept_paths) < 10:
        raise RuntimeError(f"Too few images survived QA: {len(kept_paths)} (need ≥10 for a pack)")

    # Dedupe: phash Hamming distance < 8 = duplicate
    unique_paths = dedupe(kept_paths)

    # Background removal: rembg runs on white-background outputs from gemini-3.1-flash-image
    final_paths = []
    for p in unique_paths:
        try:
            nobg = remove_background(p)
            final_paths.append(nobg)
        except Exception as e:
            logger.warning(f"bg_remover failed on {p}: {e} — using original")
            final_paths.append(p)

    logger.info(f"FILTERING done: {len(final_paths)} unique images after QA + dedupe + bg removal")


def _stage_package(niche_id: int, niche_name: str) -> None:
    """PACKAGING: sheet layout → mockup → zip bundle."""
    from src.storage.db import get_images_for_niche, save_pack_record
    from src.packaging.sheet_layout import create_sheet
    from src.packaging.mockup_gen import create_mockup
    from src.packaging.bundler import bundle

    images = get_images_for_niche(niche_id, kept=True)
    image_paths = [img["image_path"] for img in images]

    sheet_path, preview_path = create_sheet(image_paths, niche_name)
    mockup_path = create_mockup(sheet_path, niche_name)
    zip_path = bundle(niche_name, image_paths, sheet_path, mockup_path, niche_id)

    save_pack_record(niche_id, zip_path)
    logger.info(f"PACKAGING done: {zip_path}")


def _stage_list(niche_id: int, niche_name: str) -> None:
    """LISTING: write copy → publish to Etsy + Gumroad → update pack record with URLs."""
    from src.storage.db import get_pack_for_niche, get_images_for_niche, update_pack_urls
    from src.publisher.listing_writer import write_listing
    from src.publisher.etsy_lister import create_listing as etsy_create
    from src.publisher.gumroad_lister import create_product as gumroad_create

    pack = get_pack_for_niche(niche_id)
    if not pack:
        raise RuntimeError("No pack record found for this niche")

    images = get_images_for_niche(niche_id, kept=True)
    image_paths = [img["image_path"] for img in images]

    listing_data = write_listing(niche_name, image_paths)

    # Publish to Etsy (may be rate-limited on new shops — etsy_lister handles the cap)
    etsy_url = ""
    gumroad_url = ""
    try:
        etsy_url = etsy_create(listing_data, pack["zip_path"], "")
    except Exception as e:
        logger.warning(f"Etsy publish failed: {e} — continuing to Gumroad")

    try:
        gumroad_url = gumroad_create(listing_data, pack["zip_path"])
    except Exception as e:
        logger.warning(f"Gumroad publish failed: {e}")

    if not etsy_url and not gumroad_url:
        raise RuntimeError("Both Etsy and Gumroad publish failed")

    update_pack_urls(niche_id, etsy_url, gumroad_url)
    logger.info(f"LISTING done — Etsy: {etsy_url}  Gumroad: {gumroad_url}")


# ── Phase 0 fix ─────────────────────────────────────────────────────────────────
def _price_for_size(sizes_dict: dict) -> dict:
    """
    HANDOFF Phase 0: YAML may load numeric keys as strings or ints depending on
    version/quoting. Normalize all keys to int before sorting so price tiers
    are always ordered correctly (e.g. {10: ..., 25: ..., 50: ...}).
    """
    return dict(sorted({int(k): v for k, v in sizes_dict.items()}.items()))


# ── Trend refresh ───────────────────────────────────────────────────────────────
_last_trend_refresh: datetime = datetime.min


def _maybe_refresh_trends() -> None:
    """Refresh trends every TREND_REFRESH_HOURS. Falls back to niche_seeds.yaml if scouts fail."""
    global _last_trend_refresh

    if (datetime.utcnow() - _last_trend_refresh).total_seconds() < TREND_REFRESH_HOURS * 3600:
        return

    logger.info("Refreshing trends from scouts...")
    try:
        from src.trend_scout.etsy_scraper import scan as etsy_scan
        from src.trend_scout.reddit_scanner import scan as reddit_scan
        from src.trend_scout.google_trends import scan as trends_scan
        from src.trend_scout.pinterest_scout import scan as pinterest_scan
        from src.trend_scout.ranker import rank

        signals = []
        for scanner_name, scanner in [
            ("etsy", etsy_scan),
            ("reddit", reddit_scan),
            ("google_trends", trends_scan),
            ("pinterest", pinterest_scan),
        ]:
            try:
                got = scanner()
                signals.extend(got)
                logger.info(f"  {scanner_name}: {len(got)} signals")
            except Exception as e:
                logger.warning(f"  {scanner_name} scout error: {e}")

        if signals:
            top_niches = rank(signals)
            queue_niches([n.name for n in top_niches])
            logger.info(f"Queued {len(top_niches)} niches from scouts")
        else:
            _load_seed_niches()

    except Exception as e:
        logger.warning(f"Trend refresh failed: {e} — loading seeds")
        _load_seed_niches()

    _last_trend_refresh = datetime.utcnow()


def _load_seed_niches() -> None:
    """Fallback: queue niche seeds from config/niche_seeds.yaml."""
    import yaml
    seeds_path = os.path.join(os.path.dirname(__file__), "..", "config", "niche_seeds.yaml")
    try:
        with open(seeds_path) as f:
            data = yaml.safe_load(f)
        seeds = data.get("seeds", [])
        queue_niches(seeds)
        logger.info(f"Loaded {len(seeds)} seed niches from niche_seeds.yaml")
    except Exception as e:
        logger.error(f"Failed to load seed niches: {e}")


# ── Budget check ────────────────────────────────────────────────────────────────
def _check_budget() -> bool:
    """True if we're under the daily spend limit. Logs and returns False if over."""
    from src.storage.db import get_today_spend
    from src.shared.gemini_client import GeminiClient

    limit = float(os.getenv("BUDGET_DAILY_LIMIT_USD", "10.0"))
    spent = get_today_spend()
    if spent >= limit:
        logger.warning(f"Daily budget cap hit: ${spent:.2f} / ${limit:.2f}. Pausing until tomorrow.")
        return False
    return True


# ── Background loop ─────────────────────────────────────────────────────────────
def _background_loop() -> None:
    """Runs in a daemon thread. One pipeline cycle per iteration."""
    logger.info("Sticker Engine loop started")
    while True:
        try:
            if not _check_budget():
                # Budget exhausted — sleep until midnight UTC
                now = datetime.utcnow()
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0)
                sleep_secs = (tomorrow - now).total_seconds()
                logger.info(f"Sleeping until budget resets in {sleep_secs/3600:.1f}h")
                time.sleep(sleep_secs)
                continue

            _maybe_refresh_trends()

            niche = get_niche_to_process()
            if not niche:
                logger.info("No queued niches — sleeping")
                time.sleep(LOOP_INTERVAL)
                continue

            logger.info(f"Processing niche: {niche['name']!r} (id={niche['id']})")
            _run_pipeline_for_niche(niche["id"], niche["name"])

        except Exception as e:
            logger.error(f"Loop error: {e}", exc_info=True)

        time.sleep(LOOP_INTERVAL)


# ── FastAPI app ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background loop on app startup (unless DISABLE_LOOP=1)."""
    await init_db()
    if not DISABLE_LOOP:
        thread = threading.Thread(target=_background_loop, daemon=True, name="pipeline-loop")
        thread.start()
        logger.info("Pipeline loop started in background thread")
    else:
        logger.info("DISABLE_LOOP=1 — tick mode only")
    yield


app = FastAPI(title="Sticker Engine", lifespan=lifespan)


@app.post("/tick")
async def tick():
    """
    Run one pipeline cycle synchronously (for Cloud Run / n8n).
    Returns immediately in loop mode (loop is already running in background).
    """
    if DISABLE_LOOP:
        # Run one cycle in a thread so we don't block the event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _tick_once)
    return {"status": "ok", "mode": "tick" if DISABLE_LOOP else "loop"}


def _tick_once() -> None:
    """One full pipeline cycle for tick mode."""
    if not _check_budget():
        logger.warning("Budget exhausted, tick skipped")
        return
    _maybe_refresh_trends()
    niche = get_niche_to_process()
    if niche:
        _run_pipeline_for_niche(niche["id"], niche["name"])
    else:
        logger.info("No queued niches on this tick")


@app.post("/digest/send")
async def send_digest_endpoint():
    """Trigger the nightly digest email."""
    from src.digest import send_digest
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_digest)
    return {"status": "sent"}


@app.get("/health")
async def health():
    return {"status": "ok", "loop": not DISABLE_LOOP}
