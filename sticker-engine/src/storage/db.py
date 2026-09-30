import os
import aiosqlite
from enum import Enum

# AI Handoff: Using enum for statuses to ensure strict adherence to pipeline stages
class Status(Enum):
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    FILTERING = "FILTERING"
    PACKAGING = "PACKAGING"
    LISTING = "LISTING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    DEPRIORITIZED = "DEPRIORITIZED"

async def get_db_conn():
    # AI Handoff: Defaulting to local dev.db for SQLite if DB_URL is not set
    db_url = os.getenv("DB_URL", "sqlite+aiosqlite:///dev.db")
    db_path = db_url.replace("sqlite+aiosqlite:///", "")
    return await aiosqlite.connect(db_path)

async def init_db():
    # AI Handoff: Idempotent table creation. Schema matched to HANDOFF spec.
    conn = await get_db_conn()
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS niches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            status TEXT,
            score REAL,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            error_msg TEXT
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            niche_id INTEGER,
            prompt TEXT,
            image_path TEXT,
            qa_score INTEGER,
            qa_reason TEXT,
            kept BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            niche_id INTEGER,
            zip_path TEXT,
            etsy_url TEXT,
            gumroad_url TEXT,
            spend_usd REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS spend_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER,
            images_count INTEGER,
            cost_usd REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.commit()
    await conn.close()


# ── Synchronous helpers ─────────────────────────────────────────────────────────
# The pipeline loop runs in a regular thread (not async), so we use sqlite3
# directly here alongside the async aiosqlite init.
# HANDOFF: main.py runs _run_pipeline_for_niche in a thread via loop.run_in_executor,
# meaning we can't use await inside — hence sync sqlite3 for all pipeline helpers.

import sqlite3
from datetime import datetime, date


def _db_path() -> str:
    db_url = os.getenv("DB_URL", "sqlite+aiosqlite:///dev.db")
    return db_url.replace("sqlite+aiosqlite:///", "")


def _conn() -> sqlite3.Connection:
    """Open a new synchronous SQLite connection. Caller must close it."""
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row  # access columns by name
    return c


def queue_niches(names: list[str]) -> None:
    """
    Insert niches as QUEUED if they don't already exist in any active state.
    Idempotent: ignores duplicates silently.
    """
    conn = _conn()
    try:
        for name in names:
            # Don't re-queue a niche that's in flight or recently published
            existing = conn.execute(
                "SELECT id FROM niches WHERE name = ? AND status NOT IN ('FAILED', 'DEPRIORITIZED')",
                (name,),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO niches (name, status, score, source) VALUES (?, 'QUEUED', 0.0, 'seed')",
                    (name,),
                )
        conn.commit()
    finally:
        conn.close()


def get_niche_to_process() -> dict | None:
    """Return the highest-scored QUEUED niche, or None if queue is empty."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, name, score FROM niches WHERE status = 'QUEUED' ORDER BY score DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_niche_status(niche_id: int, status: str, error_msg: str = "") -> None:
    """Update a niche's pipeline status and timestamp."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE niches SET status = ?, updated_at = ?, error_msg = ? WHERE id = ?",
            (status, datetime.utcnow().isoformat(), error_msg, niche_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_image_record(
    niche_id: int,
    prompt: str,
    image_path: str,
    kept: bool = True,
    qa_score: float = 0.0,
    qa_reason: str = "",
) -> None:
    """Insert an image record after generation or QA."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO images (niche_id, prompt, image_path, qa_score, qa_reason, kept) VALUES (?,?,?,?,?,?)",
            (niche_id, prompt, image_path, qa_score, qa_reason, kept),
        )
        conn.commit()
    finally:
        conn.close()


def get_images_for_niche(niche_id: int, kept: bool | None = True) -> list[dict]:
    """
    Fetch image records for a niche.
    kept=True → only kept images; kept=False → only rejected; kept=None → all.
    """
    conn = _conn()
    try:
        if kept is None:
            rows = conn.execute(
                "SELECT * FROM images WHERE niche_id = ?", (niche_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM images WHERE niche_id = ? AND kept = ?", (niche_id, kept)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_pack_record(niche_id: int, zip_path: str) -> int:
    """Insert a pack record after bundling. Returns the new pack id."""
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO packs (niche_id, zip_path) VALUES (?, ?)",
            (niche_id, zip_path),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_pack_for_niche(niche_id: int) -> dict | None:
    """Fetch the most recent pack record for a niche."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM packs WHERE niche_id = ? ORDER BY id DESC LIMIT 1",
            (niche_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_pack_urls(niche_id: int, etsy_url: str, gumroad_url: str) -> None:
    """Set the published URLs on the most recent pack for this niche."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE packs SET etsy_url = ?, gumroad_url = ? WHERE niche_id = ? AND id = (SELECT MAX(id) FROM packs WHERE niche_id = ?)",
            (etsy_url, gumroad_url, niche_id, niche_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_today_spend() -> float:
    """Sum today's spend from spend_log. Returns 0.0 if table is empty."""
    conn = _conn()
    try:
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM spend_log WHERE date(created_at) = ?",
            (today,),
        ).fetchone()
        return float(row[0]) if row else 0.0
    finally:
        conn.close()


def get_listings_today() -> int:
    """Count packs published today (for new-shop rate limiting)."""
    conn = _conn()
    try:
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT COUNT(*) FROM packs WHERE etsy_url != '' AND date(created_at) = ?",
            (today,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def log_spend(model: str, tokens_in: int = 0, tokens_out: int = 0, images_count: int = 0, cost_usd: float = 0.0) -> None:
    """Record a spend event for the daily digest and budget tracking."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO spend_log (model, tokens_in, tokens_out, images_count, cost_usd) VALUES (?,?,?,?,?)",
            (model, tokens_in, tokens_out, images_count, cost_usd),
        )
        conn.commit()
    finally:
        conn.close()
