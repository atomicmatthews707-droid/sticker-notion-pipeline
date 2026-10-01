import importlib
import shutil

import pytest
from fastapi.testclient import TestClient

from src import main
from src.shared.gemini_client import BudgetExceeded
from src.storage import db
from tests.helpers import sticker_image, varied_image


def new_niche(name="cats", score=1.0):
    db.queue_niches([(name, score, "etsy")])
    return db.get_niche_to_process()["id"]


def status_of(niche_id):
    conn = db._conn()
    row = conn.execute("SELECT status, error_msg FROM niches WHERE id=?", (niche_id,)).fetchone()
    conn.close()
    return row["status"], row["error_msg"]


@pytest.fixture
def stages(monkeypatch):
    """Replace every stage with a recorder; individual tests override one to fail."""
    calls = []
    for name in ("generate", "filter", "package", "list"):
        monkeypatch.setattr(main, f"_stage_{name}", lambda i, n, name=name: calls.append(name))
    return calls


# ── stage runner ─────────────────────────────────────────────────────────────────

def test_full_run_publishes(stages):
    nid = new_niche()
    assert main._run_pipeline_for_niche(nid, "cats") == "published"
    assert stages == ["generate", "filter", "package", "list"] and status_of(nid)[0] == "PUBLISHED"


@pytest.mark.parametrize("start,expected", [
    ("GENERATING", ["generate", "filter", "package", "list"]),
    ("FILTERING", ["filter", "package", "list"]),
    ("PACKAGING", ["package", "list"]),
    ("LISTING", ["list"]),
    ("QUEUED", ["generate", "filter", "package", "list"]),
    (None, ["generate", "filter", "package", "list"]),
])
def test_resume_starts_at_the_recorded_stage(stages, start, expected):
    main._run_pipeline_for_niche(new_niche(), "cats", start)
    assert stages == expected


def test_unbuilt_stage_fails_loudly_never_published(monkeypatch, stages):
    def unbuilt(i, n):
        raise NotImplementedError("sheet_layout is not built")

    monkeypatch.setattr(main, "_stage_package", unbuilt)
    nid = new_niche()
    assert main._run_pipeline_for_niche(nid, "cats") == "failed"
    status, msg = status_of(nid)
    assert status == "FAILED" and "NotImplemented: sheet_layout" in msg


def test_unexpected_error_marks_failed_with_context(monkeypatch, stages):
    def broken(i, n):
        raise ValueError("bad data")

    monkeypatch.setattr(main, "_stage_filter", broken)
    nid = new_niche()
    assert main._run_pipeline_for_niche(nid, "cats") == "failed"
    assert "ValueError: bad data" in status_of(nid)[1]
    assert stages == ["generate"]


def test_budget_pause_is_not_a_failure_and_resumes_in_place(monkeypatch, stages):
    def over(i, n):
        raise BudgetExceeded("cap")

    monkeypatch.setattr(main, "_stage_filter", over)
    nid = new_niche()
    assert main._run_pipeline_for_niche(nid, "cats") == "paused"
    assert status_of(nid)[0] == "FILTERING"
    assert db.get_niche_to_process()["status"] == "FILTERING"  # picked up again first


# ── cycle ────────────────────────────────────────────────────────────────────────

@pytest.fixture
def cycle(monkeypatch):
    monkeypatch.setattr(main, "_maybe_refresh_trends", lambda: None)
    ran = []
    monkeypatch.setattr(main, "_run_pipeline_for_niche", lambda i, n, s=None: ran.append((n, s)) or "published")
    return ran


def test_cycle_resumes_a_crashed_niche_at_its_stage(cycle):
    nid = new_niche("crashed")
    db.update_niche_status(nid, "PACKAGING")
    result = main._run_cycle()
    assert result == {"status": "published", "niche": "crashed", "resumed": True}
    assert cycle == [("crashed", "PACKAGING")]


def test_cycle_idle_when_nothing_is_queued(cycle):
    assert main._run_cycle() == {"status": "idle"} and cycle == []


def test_cycle_stops_when_budget_is_spent(cycle, monkeypatch):
    new_niche()
    monkeypatch.setenv("BUDGET_DAILY_LIMIT_USD", "1")
    db.log_spend("m", cost_usd=1.0)
    assert main._run_cycle() == {"status": "budget_exhausted"} and cycle == []


def test_only_one_cycle_runs_at_a_time(cycle):
    new_niche()
    assert main._cycle_lock.acquire(blocking=False)
    try:
        assert main._run_cycle() == {"status": "busy"} and cycle == []
    finally:
        main._cycle_lock.release()


# ── stages with real DB state ────────────────────────────────────────────────────

def make_images(niche_id, tmp_path, n):
    ids = []
    for i in range(n):
        img = varied_image(i)
        p = tmp_path / f"{niche_id}_{i}.png"
        img.save(p)
        ids.append(db.save_image_record(niche_id, f"prompt {i}", str(p)))
    return ids


def test_generate_stage_saves_prompts_once_and_style_rejects(monkeypatch, tmp_path):
    nid = new_niche()
    built = []
    monkeypatch.setattr("src.generator.prompt_builder.build_prompts", lambda niche: built.append(niche) or ["p0", "p1"])
    good = tmp_path / "good.png"
    sticker_image().save(good)
    bad = tmp_path / "bad.png"
    sticker_image(bg=(100, 200, 255)).save(bad)

    def fake_generate(prompts, niche_id):
        db.save_image_record(niche_id, "p0", str(good))
        db.save_image_record(niche_id, "p1", str(bad))
        return []

    monkeypatch.setattr("src.generator.image_gen.generate_images", fake_generate)
    main._stage_generate(nid, "cats")
    main._stage_generate(nid, "cats")  # a resumed run
    assert built == ["cats"]  # prompts are generated once, then reused
    assert len(db.get_pending_images(nid)) == 1
    assert "style:" in db.get_images_for_niche(nid, kept=False)[0]["qa_reason"]


def test_generate_stage_fails_when_style_guard_rejects_everything(monkeypatch, tmp_path):
    nid = new_niche()
    bad = tmp_path / "bad.png"
    sticker_image(bg=(100, 200, 255)).save(bad)
    monkeypatch.setattr("src.generator.prompt_builder.build_prompts", lambda niche: ["p"])
    monkeypatch.setattr("src.generator.image_gen.generate_images", lambda p, i: db.save_image_record(i, "p", str(bad)))
    with pytest.raises(RuntimeError, match="rejected every"):
        main._stage_generate(nid, "cats")


def test_real_qa_filter_is_honestly_unbuilt():
    nid = new_niche()
    db.save_image_record(nid, "p", "/tmp/x.png")
    with pytest.raises(NotImplementedError):
        main._stage_filter(nid, "cats")


def test_filter_stage_dedupes_removes_backgrounds_and_enforces_minimum(monkeypatch, tmp_path):
    nid = new_niche()
    ids = make_images(nid, tmp_path, 12)
    dup = tmp_path / "dup.png"
    varied_image(0).save(dup)  # identical to image 0
    dup_id = db.save_image_record(nid, "dup", str(dup))
    monkeypatch.setattr("src.quality.auto_filter.filter_batch", lambda imgs, niche: [db.update_image(i["id"], kept=True, qa_score=8) for i in imgs])

    def fake_remove(path):
        if "_nobg" in path:
            return path
        if path.endswith(f"{nid}_3.png"):
            raise RuntimeError("model crashed")
        out = path.replace(".png", "_nobg.png")
        shutil.copy(path, out)
        return out

    monkeypatch.setattr("src.quality.bg_remover.remove_background", fake_remove)
    monkeypatch.setattr(main.config, "get", lambda k, d=None: 10 if k == "packaging.min_images" else d)
    main._stage_filter(nid, "cats")

    kept = db.get_images_for_niche(nid, kept=True)
    assert all(i["image_path"].endswith("_nobg.png") for i in kept)
    reasons = {i["id"]: i["qa_reason"] for i in db.get_images_for_niche(nid, kept=False)}
    assert reasons[ids[3]].startswith("background removal failed")  # never ships a white-background sticker
    assert "duplicate" in (reasons.get(dup_id) or reasons.get(ids[0]) or "")
    before = {i["id"]: i["image_path"] for i in kept}
    main._stage_filter(nid, "cats")  # a resumed run changes nothing
    assert {i["id"]: i["image_path"] for i in db.get_images_for_niche(nid, kept=True)} == before


def test_filter_stage_raises_when_too_few_survive(monkeypatch, tmp_path):
    nid = new_niche()
    make_images(nid, tmp_path, 3)
    monkeypatch.setattr("src.quality.auto_filter.filter_batch", lambda imgs, niche: [db.update_image(i["id"], kept=True) for i in imgs])
    monkeypatch.setattr("src.quality.bg_remover.remove_background", lambda p: p.replace(".png", "_nobg.png"))
    with pytest.raises(RuntimeError, match="Too few"):
        main._stage_filter(nid, "cats")


def test_list_stage_never_publishes_twice_after_a_crash(monkeypatch):
    nid = new_niche()
    db.save_pack_record(nid, "/p.zip")
    db.update_pack_urls(nid, "https://etsy/1", "")
    called = []
    monkeypatch.setattr("src.publisher.listing_writer.write_listing", lambda *a: called.append("w"))
    main._stage_list(nid, "cats")
    assert called == []


def test_list_stage_needs_a_pack():
    with pytest.raises(RuntimeError, match="No pack"):
        main._stage_list(new_niche(), "cats")


@pytest.mark.parametrize("module,func,args", [
    ("src.quality.auto_filter", "filter_batch", ([], "n")),
    ("src.packaging.sheet_layout", "create_sheet", ([], "n")),
    ("src.packaging.mockup_gen", "create_mockup", ("s", "n")),
    ("src.packaging.bundler", "bundle", ("n", [], "s", "m", 1)),
    ("src.publisher.listing_writer", "write_listing", ("n", [])),
    ("src.publisher.gumroad_lister", "create_product", ({}, "z")),
    ("src.digest", "send_digest", ()),
])
def test_unbuilt_modules_raise_instead_of_faking_success(module, func, args):
    with pytest.raises(NotImplementedError):
        getattr(importlib.import_module(module), func)(*args)


# ── API ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "DISABLE_LOOP", True)
    monkeypatch.setattr(main, "_run_cycle", lambda: {"status": "idle"})
    with TestClient(main.app) as c:
        yield c


def test_health_is_open(client):
    assert client.get("/health").json()["status"] == "ok"


def test_control_endpoints_fail_closed_without_a_token(client):
    assert client.post("/tick").status_code == 503
    assert client.post("/digest/send").status_code == 503


def test_unauthenticated_mode_is_an_explicit_opt_in(client, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED", "1")
    assert client.post("/tick").json() == {"status": "idle"}


def test_token_is_enforced(client, monkeypatch):
    monkeypatch.setenv("ENGINE_API_TOKEN", "s3cret")
    assert client.post("/tick").status_code == 401
    assert client.post("/tick", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/tick", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.post("/tick", headers={"X-Engine-Token": "s3cret"}).status_code == 200


def test_digest_reports_unbuilt_instead_of_claiming_sent(client, monkeypatch):
    monkeypatch.setenv("ENGINE_API_TOKEN", "t")
    r = client.post("/digest/send", headers={"Authorization": "Bearer t"})
    assert r.status_code == 501 and "not built" in r.json()["detail"]


def test_tick_in_loop_mode_does_not_start_a_second_runner(monkeypatch):
    monkeypatch.setattr(main, "DISABLE_LOOP", False)
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setattr(main, "_background_loop", lambda: None)  # the real loop would run forever
    with TestClient(main.app) as c:
        assert c.post("/tick").json() == {"status": "loop_running"}
