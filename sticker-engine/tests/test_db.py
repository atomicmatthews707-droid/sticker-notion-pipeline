import sqlite3
from types import SimpleNamespace

import pytest

from src.storage import db


def test_queue_niches_accepts_names_tuples_and_signals():
    n = db.queue_niches(["cats", ("dogs", 3.5, "etsy"), SimpleNamespace(name="frogs", score=9.0, source="reddit")])
    assert n == 3
    best = db.get_niche_to_process()
    assert (best["name"], best["status"]) == ("frogs", "QUEUED")  # highest score first, and scores are kept


def test_queue_is_idempotent_and_case_insensitive():
    assert db.queue_niches(["Cats"]) == 1
    assert db.queue_niches(["cats", "  CATS "]) == 0


def test_failed_niche_requeues_once_then_is_dropped():
    db.queue_niches(["x"])
    nid = db.get_niche_to_process()["id"]
    db.update_niche_status(nid, "FAILED", "boom")
    assert db.queue_niches(["x"]) == 1  # first failure: allowed another try
    nid2 = db.get_niche_to_process()["id"]
    db.update_niche_status(nid2, "FAILED", "boom")
    assert db.queue_niches(["x"]) == 0  # failed twice: stop burning budget on it


def test_in_flight_niche_is_resumed_before_new_ones():
    db.queue_niches([("fresh", 99.0, "etsy"), ("crashed", 1.0, "etsy")])
    crashed = next(r for r in [db.get_niche_to_process()] if r["name"] == "fresh")
    conn = db._conn()
    cid = conn.execute("SELECT id FROM niches WHERE name='crashed'").fetchone()[0]
    conn.close()
    db.update_niche_status(cid, "FILTERING")
    picked = db.get_niche_to_process()
    assert (picked["name"], picked["status"]) == ("crashed", "FILTERING")
    assert crashed["name"] == "fresh"


def test_recently_published():
    db.queue_niches(["a"])
    nid = db.get_niche_to_process()["id"]
    assert not db.recently_published("a")
    db.update_niche_status(nid, "PUBLISHED")
    assert db.recently_published("A")
    assert not db.recently_published("a", days=0)


def test_image_records_are_unique_per_prompt_and_updatable():
    a = db.save_image_record(1, "p1", "/x/1.png")
    b = db.save_image_record(1, "p1", "/x/1b.png")  # same prompt: updates, never duplicates
    assert a == b and len(db.get_images_for_niche(1, kept=None)) == 1
    assert [i["id"] for i in db.get_pending_images(1)] == [a]
    db.update_image(a, kept=True, qa_score=8.5, qa_reason="nice")
    assert db.get_pending_images(1) == []
    assert db.get_images_for_niche(1, kept=True)[0]["qa_score"] == 8.5
    db.update_image(a, kept=False)
    assert db.get_images_for_niche(1, kept=True) == [] and len(db.get_images_for_niche(1, kept=False)) == 1
    with pytest.raises(ValueError):
        db.update_image(a, bogus=1)


def test_blocked_record_without_path_is_not_pending():
    db.save_image_record(1, "p", "", kept=False, qa_reason="blocked")
    assert db.get_pending_images(1) == []


def test_spend_counts_only_today_utc_and_per_niche():
    db.log_spend("m", cost_usd=1.25, niche_id=7)
    db.log_spend("m", cost_usd=0.75, niche_id=8)
    conn = db._conn()
    conn.execute("INSERT INTO spend_log (model, cost_usd, created_at) VALUES ('m', 50, '2020-01-01 00:00:00')")
    conn.commit()
    conn.close()
    assert db.get_today_spend() == pytest.approx(2.0)
    assert db.get_niche_spend(7) == pytest.approx(1.25)


def test_pack_urls_and_listing_count():
    pid = db.save_pack_record(5, "/p.zip")
    assert pid and db.get_listings_today() == 0
    db.log_spend("m", cost_usd=3.0, niche_id=5)
    db.update_pack_urls(5, "https://etsy/1", "")
    pack = db.get_pack_for_niche(5)
    assert pack["etsy_url"] == "https://etsy/1" and pack["spend_usd"] == pytest.approx(3.0)
    assert db.get_listings_today() == 1


def test_only_sqlite_is_supported(monkeypatch):
    monkeypatch.setenv("DB_URL", "postgresql://u:p@h/db")
    with pytest.raises(RuntimeError, match="SQLite-only"):
        db.get_today_spend()


def test_schema_is_idempotent():
    db.init_db_sync()
    db.init_db_sync()
    conn = sqlite3.connect(db._db_path())
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"niches", "images", "packs", "spend_log"} <= tables
