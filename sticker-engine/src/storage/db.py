"""SQLite storage. All pipeline helpers are synchronous (the loop runs in a thread).

Image `kept` semantics: NULL = generated, awaiting QA; 1 = kept; 0 = rejected.
Niche statuses: QUEUED -> GENERATING -> FILTERING -> PACKAGING -> LISTING -> PUBLISHED, or READY
(the pack and publish kit exist but no marketplace published it), FAILED, DEPRIORITIZED.
In-flight statuses are resumed after a crash.
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, Optional


class Status(Enum):
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    FILTERING = "FILTERING"
    PACKAGING = "PACKAGING"
    LISTING = "LISTING"
    REVIEW = "REVIEW"  # generated and filtered, not packaged: waiting for you to look at the stickers
    READY = "READY"  # packaged and listing copy written; waiting for a marketplace to publish
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    DEPRIORITIZED = "DEPRIORITIZED"


IN_FLIGHT = ("GENERATING", "FILTERING", "PACKAGING", "LISTING")
MAX_FAILED_RETRIES = 2  # a niche that failed this many times is not re-queued by trend refresh
_IMAGE_FIELDS = {"image_path", "qa_score", "qa_reason", "kept"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # same format as CURRENT_TIMESTAMP


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _db_path() -> str:
    url = os.getenv("DB_URL", "sqlite+aiosqlite:///dev.db")
    if not url.startswith("sqlite"):
        raise RuntimeError(
            f"DB_URL={url!r} is not supported: the engine is SQLite-only today. "
            "Use SQLite on a persistent volume (Fly/Railway/Docker), not Cloud Run's ephemeral disk."
        )
    return url.split(":///", 1)[1]


def _conn() -> sqlite3.Connection:
    """New connection; caller must close it. WAL + busy timeout allow worker threads."""
    c = sqlite3.connect(_db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS niches (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, status TEXT, score REAL, source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        error_msg TEXT, brief TEXT, style TEXT, target_count INTEGER, subjects TEXT, pack_md TEXT, options TEXT)""",
    """CREATE TABLE IF NOT EXISTS images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, niche_id INTEGER, prompt TEXT, image_path TEXT,
        qa_score REAL, qa_reason TEXT, kept BOOLEAN, variant INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS packs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, niche_id INTEGER, zip_path TEXT, etsy_url TEXT,
        gumroad_url TEXT, spend_usd REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS spend_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT, tokens_in INTEGER, tokens_out INTEGER,
        images_count INTEGER, cost_usd REAL, niche_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS idea_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT, style_key TEXT, idea TEXT, liked INTEGER, ai_score REAL,
        ai_chosen INTEGER, brief TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(style_key, idea))""",
]


_NICHE_REQUEST_COLUMNS = {"brief": "TEXT", "style": "TEXT", "target_count": "INTEGER", "subjects": "TEXT", "pack_md": "TEXT", "options": "TEXT"}


def init_db_sync() -> None:
    """Idempotent table creation, plus adding the request columns to databases made by older versions."""
    conn = _conn()
    try:
        for stmt in _SCHEMA:
            conn.execute(stmt)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(niches)")}
        for column, kind in _NICHE_REQUEST_COLUMNS.items():
            if column not in have:
                conn.execute(f"ALTER TABLE niches ADD COLUMN {column} {kind}")
        have_img = {r["name"] for r in conn.execute("PRAGMA table_info(images)")}
        if "variant" not in have_img:
            conn.execute("ALTER TABLE images ADD COLUMN variant INTEGER DEFAULT 0")
        # One row per (pack, prompt, variation). Older databases had a narrower index.
        conn.execute("DROP INDEX IF EXISTS idx_images_niche_prompt")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_images_niche_prompt_variant ON images(niche_id, prompt, variant)")
        conn.commit()
    finally:
        conn.close()


async def init_db() -> None:
    await asyncio.to_thread(init_db_sync)


# ── Niches ──────────────────────────────────────────────────────────────────────

def queue_niches(items: Iterable) -> int:
    """
    Queue niches as QUEUED. Each item is a name, or a (name, score, source) tuple, or any object with
    .name/.score/.source (a NicheSignal). Skips names already active or published, and names that have
    already failed MAX_FAILED_RETRIES times. Returns the number inserted.
    """
    conn = _conn()
    inserted = 0
    try:
        for item in items:
            if isinstance(item, str):
                name, score, source = item, 0.0, "seed"
            elif isinstance(item, tuple):
                name, score, source = (list(item) + [0.0, "seed"])[:3]
            else:
                name, score, source = item.name, item.score, item.source
            name = name.strip()
            if not name:
                continue
            active = conn.execute(
                "SELECT 1 FROM niches WHERE lower(name) = lower(?) AND status NOT IN ('FAILED', 'DEPRIORITIZED')",
                (name,),
            ).fetchone()
            failures = conn.execute(
                "SELECT COUNT(*) FROM niches WHERE lower(name) = lower(?) AND status = 'FAILED'", (name,)
            ).fetchone()[0]
            if active or failures >= MAX_FAILED_RETRIES:
                continue
            conn.execute(
                "INSERT INTO niches (name, status, score, source, updated_at) VALUES (?, 'QUEUED', ?, ?, ?)",
                (name, float(score), source, _now()),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


REQUEST_PRIORITY = 1000.0  # requested packs jump ahead of anything the scouts queued


def queue_request(name: str, brief: str = "", style: str = "", count: Optional[int] = None,
                  subjects: Optional[list] = None, pack_md: str = "", options: Optional[dict] = None) -> int:
    """
    Queue a pack the user asked for, ahead of scouted niches. brief: free text about what is wanted;
    style: replaces the default art style for this pack; count: number of stickers; subjects: an exact list,
    which skips AI brainstorming. Raises ValueError if a pack with this name is already queued or running.
    """
    name = name.strip()
    if not name:
        raise ValueError("A pack needs a name.")
    marks = ",".join("?" * len(IN_FLIGHT))
    conn = _conn()
    try:
        busy = conn.execute(
            f"SELECT 1 FROM niches WHERE lower(name) = lower(?) AND status IN ('QUEUED', {marks})", (name, *IN_FLIGHT)
        ).fetchone()
        if busy:
            raise ValueError(f"A pack called {name!r} is already queued or in progress.")
        cur = conn.execute(
            "INSERT INTO niches (name, status, score, source, updated_at, brief, style, target_count, subjects, pack_md, options) "
            "VALUES (?, 'QUEUED', ?, 'request', ?, ?, ?, ?, ?, ?, ?)",
            (name, REQUEST_PRIORITY, _now(), brief.strip(), style.strip(), count, json.dumps(subjects) if subjects else None,
             pack_md, json.dumps(options) if options else None),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_niche(niche_id: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM niches WHERE id = ?", (niche_id,)).fetchone()
        if not row:
            return None
        niche = dict(row)
        niche["subjects"] = json.loads(niche["subjects"]) if niche.get("subjects") else None
        niche["options"] = json.loads(niche["options"]) if niche.get("options") else {}
        return niche
    finally:
        conn.close()


def update_niche_options(niche_id: int, **changes) -> None:
    """Merge changes into a pack's saved run options."""
    niche = get_niche(niche_id)
    if not niche:
        raise ValueError(f"No pack {niche_id}")
    merged = {**niche["options"], **changes}
    conn = _conn()
    try:
        conn.execute("UPDATE niches SET options = ? WHERE id = ?", (json.dumps(merged), niche_id))
        conn.commit()
    finally:
        conn.close()


def list_niches(limit: int = 50) -> list[dict]:
    conn = _conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, status, source, target_count, updated_at FROM niches ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def get_niche_to_process() -> Optional[dict]:
    """A niche that was in flight when the process died (resumed first), else the best QUEUED one."""
    conn = _conn()
    try:
        marks = ",".join("?" * len(IN_FLIGHT))
        row = conn.execute(
            f"SELECT id, name, score, status FROM niches WHERE status IN ({marks}) ORDER BY updated_at ASC LIMIT 1",
            IN_FLIGHT,
        ).fetchone() or conn.execute(
            "SELECT id, name, score, status FROM niches WHERE status = 'QUEUED' ORDER BY score DESC, id ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_niche_status(niche_id: int, status: str, error_msg: str = "") -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE niches SET status = ?, updated_at = ?, error_msg = ? WHERE id = ?",
            (status, _now(), error_msg, niche_id),
        )
        conn.commit()
    finally:
        conn.close()


def recently_published(name: str, days: int = 30) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        return conn.execute(
            "SELECT 1 FROM niches WHERE lower(name) = lower(?) AND status = 'PUBLISHED' AND updated_at > ?",
            (name, cutoff),
        ).fetchone() is not None
    finally:
        conn.close()


# ── Images ──────────────────────────────────────────────────────────────────────

def save_image_record(
    niche_id: int,
    prompt: str,
    image_path: str,
    kept: Optional[bool] = None,
    qa_score: Optional[float] = None,
    qa_reason: str = "",
    variant: int = 0,
) -> int:
    """Insert an image (or update the existing row for the same pack, prompt and variation). Returns its id."""
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO images (niche_id, prompt, image_path, qa_score, qa_reason, kept, variant) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(niche_id, prompt, variant) DO UPDATE SET image_path = excluded.image_path,
               qa_score = excluded.qa_score, qa_reason = excluded.qa_reason, kept = excluded.kept""",
            (niche_id, prompt, image_path, qa_score, qa_reason, None if kept is None else int(kept), variant),
        )
        conn.commit()
        return conn.execute(
            "SELECT id FROM images WHERE niche_id = ? AND prompt = ? AND variant = ?", (niche_id, prompt, variant)
        ).fetchone()[0]
    finally:
        conn.close()


def update_image(image_id: int, **fields) -> None:
    """Update image_path, qa_score, qa_reason and/or kept (True/False/None)."""
    bad = set(fields) - _IMAGE_FIELDS
    if bad:
        raise ValueError(f"Unknown image fields: {sorted(bad)}")
    if "kept" in fields and fields["kept"] is not None:
        fields["kept"] = int(fields["kept"])
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn = _conn()
    try:
        conn.execute(f"UPDATE images SET {sets} WHERE id = ?", (*fields.values(), image_id))
        conn.commit()
    finally:
        conn.close()


def get_images_for_niche(niche_id: int, kept: Optional[bool] = True) -> list[dict]:
    """kept=True: kept only; False: rejected only; None: every image regardless of state."""
    conn = _conn()
    try:
        if kept is None:
            rows = conn.execute("SELECT * FROM images WHERE niche_id = ? ORDER BY id", (niche_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM images WHERE niche_id = ? AND kept = ? ORDER BY id", (niche_id, int(kept))
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_images(niche_id: int) -> list[dict]:
    """Images that exist but have not been accepted or rejected yet (kept IS NULL)."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM images WHERE niche_id = ? AND kept IS NULL AND image_path != '' ORDER BY id", (niche_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Packs ───────────────────────────────────────────────────────────────────────

def save_pack_record(niche_id: int, zip_path: str) -> int:
    """Record the pack for a niche; re-packaging after a resume updates the same row."""
    conn = _conn()
    try:
        existing = conn.execute("SELECT id FROM packs WHERE niche_id = ? ORDER BY id DESC LIMIT 1", (niche_id,)).fetchone()
        if existing:
            conn.execute("UPDATE packs SET zip_path = ? WHERE id = ?", (zip_path, existing[0]))
            conn.commit()
            return existing[0]
        cur = conn.execute("INSERT INTO packs (niche_id, zip_path) VALUES (?, ?)", (niche_id, zip_path))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_pack_for_niche(niche_id: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM packs WHERE niche_id = ? ORDER BY id DESC LIMIT 1", (niche_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_pack_urls(niche_id: int, etsy_url: str, gumroad_url: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE packs SET etsy_url = ?, gumroad_url = ?, spend_usd = ? "
            "WHERE niche_id = ? AND id = (SELECT MAX(id) FROM packs WHERE niche_id = ?)",
            (etsy_url, gumroad_url, get_niche_spend(niche_id), niche_id, niche_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_listings_today() -> int:
    """Packs listed on Etsy today (UTC), for the new-shop rate cap."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM packs WHERE etsy_url IS NOT NULL AND etsy_url != '' AND date(created_at) = ?",
            (_today(),),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


# ── Spend ───────────────────────────────────────────────────────────────────────

def log_spend(
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    images_count: int = 0,
    cost_usd: float = 0.0,
    niche_id: Optional[int] = None,
) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO spend_log (model, tokens_in, tokens_out, images_count, cost_usd, niche_id) VALUES (?,?,?,?,?,?)",
            (model, tokens_in, tokens_out, images_count, cost_usd, niche_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_today_spend() -> float:
    """Sum of today's (UTC) spend. Survives restarts, so the daily cap cannot be reset by crashing."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM spend_log WHERE date(created_at) = ?", (_today(),)
        ).fetchone()
        return float(row[0])
    finally:
        conn.close()


def get_total_spend() -> float:
    """Everything this database has logged, across all days."""
    conn = _conn()
    try:
        return float(conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM spend_log").fetchone()[0])
    finally:
        conn.close()


def save_idea_feedback(style_key: str, rows: list[dict], brief: str = "") -> int:
    """
    Remember which ideas you marked as the best (liked) and which you saw and passed over, per style.
    rows: {"idea", "liked", "ai_score", "ai_chosen"}. The same idea in the same style is updated, never duplicated.
    """
    conn = _conn()
    try:
        for r in rows:
            conn.execute(
                """INSERT INTO idea_feedback (style_key, idea, liked, ai_score, ai_chosen, brief) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(style_key, idea) DO UPDATE SET liked = excluded.liked, ai_score = excluded.ai_score,
                   ai_chosen = excluded.ai_chosen, brief = excluded.brief, updated_at = CURRENT_TIMESTAMP""",
                (style_key, r["idea"], 1 if r["liked"] else 0, r.get("ai_score"), 1 if r.get("ai_chosen") else 0, brief),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_idea_feedback(style_key: str, liked: bool, limit: int = 8) -> list[str]:
    """Most recent ideas you did (liked=True) or did not pick, for one style."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT idea FROM idea_feedback WHERE style_key = ? AND liked = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
            (style_key, 1 if liked else 0, limit),
        ).fetchall()
        return [r["idea"] for r in rows]
    finally:
        conn.close()


def count_idea_feedback(style_key: str) -> dict:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(liked), 0) AS yes, COUNT(*) AS total FROM idea_feedback WHERE style_key = ?", (style_key,)
        ).fetchone()
        return {"liked": int(row["yes"]), "passed": int(row["total"]) - int(row["yes"])}
    finally:
        conn.close()


def get_niche_spend(niche_id: int) -> float:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM spend_log WHERE niche_id = ?", (niche_id,)
        ).fetchone()
        return float(row[0])
    finally:
        conn.close()


# ── Digest ──────────────────────────────────────────────────────────────────────

def digest_data(day: Optional[str] = None) -> dict:
    """Everything the daily digest reports, for one UTC day (default today)."""
    day = day or _today()
    conn = _conn()
    try:
        def rows(sql, *args):
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

        scored = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(kept), 0) FROM images WHERE qa_score IS NOT NULL AND date(created_at) = ?", (day,)
        ).fetchone()
        return {
            "day": day,
            "niches_discovered": conn.execute("SELECT COUNT(*) FROM niches WHERE date(created_at) = ?", (day,)).fetchone()[0],
            "images_generated": conn.execute("SELECT COUNT(*) FROM images WHERE date(created_at) = ?", (day,)).fetchone()[0],
            "qa_scored": scored[0],
            "qa_kept": int(scored[1]),
            "sample_rejects": rows(
                "SELECT i.qa_reason, i.image_path, n.name AS niche FROM images i JOIN niches n ON n.id = i.niche_id "
                "WHERE i.kept = 0 AND date(i.created_at) = ? ORDER BY i.id DESC LIMIT 5", day),
            "published": rows(
                "SELECT n.name, p.etsy_url, p.gumroad_url, p.spend_usd FROM niches n JOIN packs p ON p.niche_id = n.id "
                "WHERE n.status = 'PUBLISHED' AND date(n.updated_at) = ?", day),
            "ready": rows("SELECT name, error_msg AS note, updated_at FROM niches WHERE status = 'READY' ORDER BY updated_at DESC"),
            "failed": rows(
                "SELECT name, substr(error_msg, 1, 240) AS error FROM niches WHERE status = 'FAILED' AND date(updated_at) = ?", day),
            "spend_total": get_today_spend(),
            "spend_by_model": rows(
                "SELECT model, ROUND(SUM(cost_usd), 4) AS cost, SUM(images_count) AS images, SUM(tokens_in) AS tokens_in, "
                "SUM(tokens_out) AS tokens_out FROM spend_log WHERE date(created_at) = ? GROUP BY model ORDER BY cost DESC", day),
            "queue": rows("SELECT name, ROUND(score, 2) AS score FROM niches WHERE status = 'QUEUED' ORDER BY score DESC, id LIMIT 5"),
        }
    finally:
        conn.close()
