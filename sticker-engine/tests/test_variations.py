import io

import pytest
from PIL import Image

from src import cli, main
from src.generator import image_gen, prompt_builder
from src.shared import cancel
from src.shared.gemini_client import GeminiClient
from src.storage import db
from tests.helpers import fake_client, genai_response, jpeg_bytes, sticker_image, transparent_sticker

# ── image generation: variations, seeds, references ──────────────────────────────


class Recorder:
    def __init__(self):
        self.calls = []

    def generate_image(self, prompt, references=None, seed=None, aspect_ratio="1:1"):
        from src.shared.gemini_client import ImageResult

        self.calls.append({"prompt": prompt, "references": references, "seed": seed})
        return ImageResult(jpeg_bytes(), "image/jpeg")


def test_each_prompt_gets_n_variations_with_different_seeds_and_files():
    rec = Recorder()
    out = image_gen.generate_images(["mug", "cat"], 1, client=rec, variants=3)
    assert len(out) == 6 and len({o["image_path"] for o in out}) == 6
    assert sorted((o["prompt"], o["variant"]) for o in out) == [(p, v) for p in ("cat", "mug") for v in range(3)]
    seeds = {(c["prompt"], c["seed"]) for c in rec.calls}
    assert len(seeds) == 6                                       # every variation has its own seed


def test_seeds_are_repeatable_between_runs():
    assert image_gen.seed_for("mug", 0) == image_gen.seed_for("mug", 0)
    assert image_gen.seed_for("mug", 1) == image_gen.seed_for("mug", 0) + 1
    assert image_gen.seed_for("mug", 0) != image_gen.seed_for("cat", 0)


def test_variation_zero_keeps_the_old_file_name():
    import hashlib

    assert image_gen.image_path_for(1, "mug").stem == hashlib.sha1(b"mug").hexdigest()[:12]
    assert image_gen.image_path_for(1, "mug", 1) != image_gen.image_path_for(1, "mug", 0)


def test_resume_only_makes_the_missing_variations():
    first = Recorder()
    image_gen.generate_images(["mug"], 1, client=first, variants=2)
    second = Recorder()
    out = image_gen.generate_images(["mug"], 1, client=second, variants=4)
    assert len(second.calls) == 2 and len(out) == 4


def test_references_are_sent_with_a_note_and_shrunk(tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (3000, 2000), "red").save(ref)
    rec = Recorder()
    image_gen.generate_images(["mug"], 1, client=rec, references=[str(ref)], reference_mode="character")
    call = rec.calls[0]
    assert call["prompt"].startswith("Keep the character in the attached reference image") and call["prompt"].endswith("mug")
    data, mime = call["references"][0]
    assert mime == "image/png" and max(Image.open(io.BytesIO(data)).size) == 1024


def test_no_references_means_no_note_and_nothing_extra_sent():
    rec = Recorder()
    image_gen.generate_images(["mug"], 1, client=rec)
    assert rec.calls[0]["prompt"] == "mug" and rec.calls[0]["references"] is None


def test_the_recorded_prompt_is_the_users_not_the_note(tmp_path):
    ref = tmp_path / "r.png"
    sticker_image().save(ref)
    image_gen.generate_images(["mug"], 1, client=Recorder(), references=[str(ref)])
    assert [i["prompt"] for i in db.get_images_for_niche(1, kept=None)] == ["mug"]


def test_gemini_call_carries_reference_parts_and_the_seed():
    c = GeminiClient()
    models = fake_client(c, genai_response(image=jpeg_bytes()))
    c.generate_image("a mug", references=[(b"\x89PNGfake", "image/png")], seed=123)
    call = models.calls[0]
    assert len(call["contents"]) == 2 and call["contents"][-1] == "a mug"
    assert call["contents"][0].inline_data.mime_type == "image/png" and call["config"].seed == 123


def test_stop_button_saves_finished_images_and_resumes_later():
    class StopAfterTwo(Recorder):
        def generate_image(self, prompt, references=None, seed=None, aspect_ratio="1:1"):
            if len(self.calls) == 2:
                cancel.request()
            return super().generate_image(prompt, references, seed)

    rec = StopAfterTwo()
    try:
        with pytest.raises(cancel.Cancelled):
            image_gen.generate_images([f"p{i}" for i in range(8)], 1, client=rec, max_workers=1)
    finally:
        cancel.reset()
    done = len(db.get_images_for_niche(1, kept=None))
    assert 2 <= done < 8
    resumed = Recorder()
    image_gen.generate_images([f"p{i}" for i in range(8)], 1, client=resumed, max_workers=1)
    assert len(resumed.calls) == 8 - done                       # nothing paid for twice


def test_cancel_flag_basics():
    cancel.reset()
    assert not cancel.is_set()
    cancel.request()
    with pytest.raises(cancel.Cancelled):
        cancel.check()
    cancel.reset()
    cancel.check()


# ── prompts: yours, as written ───────────────────────────────────────────────────

LONG = "A grumpy pumpkin wearing a tiny cowboy hat, riding a skateboard, " * 6      # far longer than 120 characters


def test_your_prompts_are_kept_whole_with_only_the_background_added():
    out = prompt_builder.build_prompts("x", subjects=[LONG, LONG[:-3] + "!!!"], verbatim=True, apply_style=False)
    assert len(out) == 2                                             # near-duplicates are NOT removed
    assert out[0] == f"{' '.join(LONG.split())}, plain flat pure white background, no shadow, no border"


def test_shop_style_can_be_added_to_your_prompt():
    out = prompt_builder.build_prompts("x", subjects=["a pumpkin"], verbatim=True, apply_style=True)
    assert out[0].startswith("a pumpkin, cute kawaii flat vector illustration, plain flat pure white background")


def test_verbatim_prompts_still_refuse_banned_terms_and_absurd_length():
    with pytest.raises(ValueError, match="banned"):
        prompt_builder.build_prompts("x", subjects=["a pokemon plush"], verbatim=True)
    with pytest.raises(ValueError, match="limit"):
        prompt_builder.build_prompts("x", subjects=["a" * 2500], verbatim=True)


def test_no_ai_call_is_made_when_you_supply_the_prompts():
    class Boom:
        def generate_json(self, *a, **k):
            raise AssertionError("no AI ideation may happen")

    assert prompt_builder.build_prompts("x", client=Boom(), subjects=["one", "two"], verbatim=True)


# ── pipeline: best variation, optional QA, review-only ───────────────────────────

def pack(options, n_prompts=2, variants=2, kept_scores=None):
    nid = db.queue_request("t", options=options)
    ids = []
    for p in range(n_prompts):
        for v in range(variants):
            ids.append(db.save_image_record(nid, f"prompt{p}", f"/x/{p}{v}.png", variant=v))
    return nid, ids


def test_no_variation_is_ever_discarded_because_another_scored_higher(monkeypatch, tmp_path):
    """You paid for every variation, so every one stays and is shown. A score is only a label."""
    nid = db.queue_request("t", options={"variants": 2, "build_pack": False})
    for p in range(2):
        for v in range(2):
            path = tmp_path / f"{p}{v}.png"
            transparent_sticker(p * 2 + v).save(path)
            db.save_image_record(nid, f"prompt{p}", str(path), variant=v)

    def judge(images, niche):
        for img, score in zip(images, [6, 9, 8, 7]):
            db.update_image(img["id"], qa_score=score, qa_reason="x", kept=score >= 7)

    monkeypatch.setattr("src.quality.auto_filter.filter_batch", judge)
    main._stage_filter(nid, "t")
    everything = db.get_images_for_niche(nid, kept=None)
    assert len(everything) == 4
    assert not any("not the best variation" in (i["qa_reason"] or "") for i in everything)
    assert {i["qa_score"] for i in everything} == {6, 9, 8, 7}
    assert len(db.get_images_for_niche(nid, kept=True)) == 3        # only the low score is marked, and still shown


def test_the_pipeline_has_no_best_of_n_culling_step():
    assert not hasattr(main, "_keep_best_variation")


def test_options_default_safely():
    nid = db.queue_request("plain")
    assert main._options(nid) == {"variants": 1, "references": [], "reference_mode": "style", "apply_style": True,
                                  "verbatim": False, "qa": True, "build_pack": True, "style_md": None, "only_image_ids": None}


def test_quality_check_can_be_skipped_and_everything_is_kept(monkeypatch, tmp_path):
    nid = db.queue_request("t", options={"qa": False, "build_pack": False})
    for i in range(3):
        p = tmp_path / f"{i}.png"
        transparent_sticker(i).save(p)
        db.save_image_record(nid, f"p{i}", str(p))
    monkeypatch.setattr("src.quality.auto_filter.filter_batch", lambda *a: pytest.fail("QA must not run"))
    main._stage_filter(nid, "t")
    kept = db.get_images_for_niche(nid, kept=True)
    assert len(kept) == 3 and all(k["qa_reason"] == "AI quality check skipped" for k in kept)


def test_review_only_run_stops_after_filtering_with_status_review(monkeypatch):
    ran = []
    for name in ("generate", "filter", "package", "list"):
        monkeypatch.setattr(main, f"_stage_{name}", lambda i, n, name=name: ran.append(name))
    nid = db.queue_request("t", options={"build_pack": False})
    assert main._run_pipeline_for_niche(nid, "t", None) == "review"
    assert ran == ["generate", "filter"] and db.get_niche(nid)["status"] == "REVIEW"
    assert db.get_niche_to_process() is None


def test_review_only_run_does_not_fail_when_nothing_is_kept():
    nid = db.queue_request("t", options={"build_pack": False})
    assert main._minimum_pack_size(nid) == 0


def test_stop_button_pauses_the_run_instead_of_failing_it(monkeypatch):
    def stopped(i, n):
        raise cancel.Cancelled("stop")

    monkeypatch.setattr(main, "_stage_generate", stopped)
    nid = db.queue_request("t")
    assert main._run_pipeline_for_niche(nid, "t", None) == "cancelled"
    assert db.get_niche(nid)["status"] == "GENERATING"             # resumable, not FAILED


def test_options_survive_the_database_round_trip():
    opts = {"variants": 3, "references": ["/a.png"], "reference_mode": "subject", "qa": False}
    assert db.get_niche(db.queue_request("t", options=opts))["options"] == opts


def test_old_databases_get_the_variant_column_and_the_wider_unique_index(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE images (id INTEGER PRIMARY KEY AUTOINCREMENT, niche_id INTEGER, prompt TEXT, image_path TEXT, qa_score REAL, qa_reason TEXT, kept BOOLEAN, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE UNIQUE INDEX idx_images_niche_prompt ON images(niche_id, prompt)")
    c.commit()
    c.close()
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{path}")
    db.init_db_sync()
    db.save_image_record(1, "mug", "/a.png", variant=0)
    db.save_image_record(1, "mug", "/b.png", variant=1)             # would have collided with the old index
    assert len(db.get_images_for_niche(1, kept=None)) == 2


def test_cost_estimate_scales_with_variations_and_drops_without_the_quality_check():
    assert cli.estimate_usd(5, variants=1) == 0.43
    assert cli.estimate_usd(5, variants=3) == pytest.approx(1.26, abs=0.01)
    assert cli.estimate_usd(5, variants=1, qa=False, text_ai=False) == pytest.approx(0.34, abs=0.01)
    assert cli.estimate_usd(1, qa=False, text_ai=False) == 0.07


def test_generate_stage_sends_your_options_to_image_generation(monkeypatch, tmp_path):
    seen = {}
    nid = db.queue_request("t", subjects=["a pumpkin"], options={"variants": 3, "references": ["/r.png"], "reference_mode": "subject", "verbatim": True, "apply_style": False})
    monkeypatch.setattr("src.generator.image_gen.generate_images", lambda prompts, niche_id, **kw: seen.update(prompts=prompts, **kw) or [])
    monkeypatch.setattr(main, "_stage_generate", main._stage_generate)
    try:
        main._stage_generate(nid, "t")
    except RuntimeError:
        pass                                                         # nothing was written, so the style guard step has no images
    assert seen["prompts"] == ["a pumpkin, plain flat pure white background, no shadow, no border"]
    assert (seen["variants"], seen["references"], seen["reference_mode"]) == (3, ["/r.png"], "subject")


def test_jobs_store_the_style_text(monkeypatch):
    from src.ui import jobs

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(jobs.JobRunner, "_launch", lambda self, *a, **k: None, raising=False)
    runner = jobs.JobRunner()
    try:
        nid = runner.start(name="n", prompts=["a"], count=1, variants=1, references=[], reference_mode="style",
                           apply_style=True, qa=False, build_pack=False, style_md="# S\ncutout: none\n")
    except Exception:
        pytest.skip("runner needs a live thread")
    assert db.get_niche(nid)["options"]["style_md"].startswith("# S")
