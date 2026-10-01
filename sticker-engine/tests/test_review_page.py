import io

from PIL import Image

from src.packaging import review_page
from src.storage import db
from tests.helpers import save_stickers


def seed(tmp_path):
    nid = db.queue_request("Autumn <b>Cozy</b>", brief="for planners")
    paths = save_stickers(tmp_path, 3)
    db.save_image_record(nid, "Steaming mug, kawaii style", paths[0], kept=True, qa_score=8, qa_reason="clean")
    db.save_image_record(nid, "Baked pie, kawaii style", paths[1], kept=False, qa_score=4, qa_reason="fused lattice")
    db.save_image_record(nid, "Blocked thing, kawaii style", "", kept=False, qa_reason="blocked: safety")
    db.log_spend("m", cost_usd=0.42, niche_id=nid)
    return nid


def test_collect_counts_and_embeds_images(tmp_path):
    d = review_page.collect_review(seed(tmp_path))
    assert (d["generated"], d["kept"], d["rejected"], d["accepted_pct"]) == (3, 1, 2, 33)
    assert d["images"][0]["uri"].startswith("data:image/png;base64,") and d["images"][2]["uri"] is None
    assert d["cost"] == 0.42


def test_embedded_stickers_are_downsized_and_keep_transparency(tmp_path):
    import base64

    d = review_page.collect_review(seed(tmp_path))
    img = Image.open(io.BytesIO(base64.b64decode(d["images"][0]["uri"].split(",", 1)[1])))
    assert max(img.size) == review_page.STICKER_PX and img.mode == "RGBA" and img.getpixel((2, 2))[3] == 0


def test_page_shows_verdicts_backgrounds_and_escapes_text(tmp_path):
    nid = seed(tmp_path)
    html = review_page.render_review([review_page.collect_review(nid)])
    assert html.startswith("<!doctype html>") and "fused lattice" in html and "blocked: safety" in html
    for bg in ("White", "Dark", "Colour", "Transparent"):
        assert f">{bg}</button>" in html
    assert "Autumn &lt;b&gt;Cozy&lt;/b&gt;" in html and "<b>Cozy</b>" not in html
    assert "No image (blocked or missing)" in html


def test_fragment_mode_has_no_document_wrapper(tmp_path):
    html = review_page.render_review([review_page.collect_review(seed(tmp_path))], standalone=False)
    assert not html.startswith("<!doctype") and "<title>Sticker Pack Review</title>" in html


def test_write_review_creates_a_single_self_contained_file(tmp_path):
    nid = seed(tmp_path)
    path = review_page.write_review(nid)
    text = path.read_text()
    assert path.name == "review.html" and "src=\"http" not in text and text.count("data:image/png;base64,") >= 1


def test_recut_never_touches_the_original_files(tmp_path):
    nid = seed(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.glob("s*.png")}
    review_page.collect_review(nid, recut=True)
    assert {p: p.read_bytes() for p in tmp_path.glob("s*.png")} == before
    assert not list(tmp_path.glob("*_nobg.png"))


def test_pipeline_refreshes_the_review_after_a_run(monkeypatch):
    from src import main

    for name in ("generate", "filter", "package", "list"):
        monkeypatch.setattr(main, f"_stage_{name}", lambda i, n: None)
    nid = db.queue_request("x")
    main._run_pipeline_for_niche(nid, "x", None)
    assert (review_page.config.output_dir() / f"niche_{nid}" / "review.html").exists()


def test_a_broken_review_never_breaks_the_pipeline(monkeypatch):
    from src import main

    for name in ("generate", "filter", "package", "list"):
        monkeypatch.setattr(main, f"_stage_{name}", lambda i, n: None)
    monkeypatch.setattr("src.packaging.review_page.write_review", lambda i: (_ for _ in ()).throw(OSError("disk full")))
    assert main._run_pipeline_for_niche(db.queue_request("y"), "y", None) == "published"
