import io
import time
import zipfile

import pytest
from PIL import Image

from src import main
from src.generator import ideas as idea_writer
from src.generator.prompt_builder import compose_prompt
from src.packaging import export
from src.quality.auto_filter import render_for_review
from src.shared import config, design
from src.shared import gemini_client as gc
from src.storage import db
from src.ui import jobs
from tests.helpers import transparent_sticker


# ── picture size and quality wording ─────────────────────────────────────────────

def test_default_size_is_4k_and_costs_more(monkeypatch):
    monkeypatch.delenv("GEMINI_IMAGE_SIZE")
    assert gc.image_size() == "4K"
    assert gc.image_price("gemini-3.1-flash-image") > gc.image_price("gemini-3.1-flash-image", "1K") == 0.067


def test_size_can_be_lowered_and_junk_is_ignored(monkeypatch):
    monkeypatch.setenv("GEMINI_IMAGE_SIZE", "2k")
    assert gc.image_size() == "2K"
    monkeypatch.setenv("GEMINI_IMAGE_SIZE", "8K")
    assert gc.image_size() == "4K"


def test_the_size_is_sent_to_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_IMAGE_SIZE", "4K")
    seen = {}

    class Models:
        def generate_content(self, model, contents, config):
            seen["size"] = config.image_config.image_size
            raise RuntimeError("stop")

    client = gc.GeminiClient.__new__(gc.GeminiClient)
    client.client = type("C", (), {"models": Models()})()
    client.image_model, client.niche_id = "gemini-3.1-flash-image", None
    with pytest.raises(RuntimeError):
        client.generate_image("x")
    assert seen["size"] == "4K"


def test_every_prompt_asks_for_clean_sharp_lines(monkeypatch):
    monkeypatch.delenv("IMAGE_QUALITY_PHRASE")
    out = compose_prompt("a mug", design.direction_for(), rules=design.rules_for())
    assert "razor-sharp" in out and "clean" in out
    assert "razor-sharp" in compose_prompt("my words", apply_style=False)


def test_judging_downsizes_huge_pictures(tmp_path):
    p = tmp_path / "big.png"
    Image.new("RGBA", (4096, 4096), (200, 0, 0, 255)).save(p)
    assert max(Image.open(io.BytesIO(render_for_review(str(p)))).size) == 1280


# ── ideas: written, scored and ranked before any image ───────────────────────────

class FakeText:
    def __init__(self, ideas_by_call):
        self.calls, self.by_call = [], ideas_by_call

    def generate_json(self, ask, system=None):
        self.calls.append(ask)
        return {"ideas": self.by_call[len(self.calls) - 1]}


def idea(text, funny=5, original=5, readable=5, on_brief=5, weakness="meh"):
    return {"text": text, "weakness": weakness, "scores": {"funny": funny, "original": original, "readable": readable, "on_brief": on_brief}}


def test_best_ideas_are_kept_and_ranked_by_computed_score():
    client = FakeText([[idea("ghost wearing a fitted sheet joke", 4, 4, 4, 4), idea("skeleton waiting for a date", 9, 8, 7, 8),
                        idea("pumpkin tax season pun", 6, 6, 6, 6), idea("witch parking permit sign", 7, 7, 7, 7)]])
    result = idea_writer.write_ideas("halloween humour", 2, client=client)
    assert [i.text for i in result.chosen(0)] == ["skeleton waiting for a date", "witch parking permit sign"]
    assert result.packs[0][0].score == 8.0 and not result.packs[0][-1].chosen
    assert "Write 5 candidate ideas" in client.calls[0]          # more than the 2 wanted


def test_unscored_banned_and_repeated_ideas_are_dropped():
    bad = {"text": "an idea with no scores at all"}
    client = FakeText([[bad, idea("ghost wearing a fitted sheet joke"), idea("ghost wearing a fitted sheet joke!"),
                        idea("short")]])
    result = idea_writer.write_ideas("halloween", 5, client=client)
    assert [i.text for i in result.packs[0]] == ["ghost wearing a fitted sheet joke"]
    assert any("only 1 usable" in w for w in result.warnings)


def test_self_flattery_is_called_out():
    client = FakeText([[idea(f"idea number {n} about spooky things", 9, 9, 9, 9) for n in range(6)]])
    result = idea_writer.write_ideas("halloween", 3, client=client)
    assert any("not believable" in w for w in result.warnings)


def test_later_packs_avoid_earlier_ones():
    client = FakeText([[idea("skeleton waiting for a date night")], [idea("vampire reading the terms of service")]])
    result = idea_writer.write_ideas("halloween", 1, packs=2, client=client)
    assert "skeleton waiting for a date night" in client.calls[1] and "pack 2 of 2" in client.calls[1]
    assert [len(p) for p in result.packs] == [1, 1]


def test_empty_brief_is_refused():
    with pytest.raises(ValueError):
        idea_writer.write_ideas("  ", 3, client=FakeText([]))


# ── several packs, one after another ────────────────────────────────────────────

@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "key-for-tests")
    for name in ("generate", "filter", "package", "list"):
        monkeypatch.setattr(main, f"_stage_{name}", lambda i, n: None)
    return jobs.JobRunner()


def wait(runner, seconds=5):
    end = time.time() + seconds
    while runner.running and time.time() < end:
        time.sleep(0.02)


def start_packs(runner, packs, **over):
    args = dict(name="Halloween", packs=packs, variants=3, references=[], reference_mode="style", apply_style=True,
                qa=False, build_pack=False)
    return runner.start_packs(**{**args, **over})


def test_three_packs_of_ten_with_three_versions_is_ninety_images(runner):
    first = start_packs(runner, [[f"s{p}-{i}" for i in range(10)] for p in range(3)])
    assert "exactly 90 images in 3 packs" in runner.log[0]
    wait(runner)
    niches = [db.get_niche(n["id"]) for n in db.list_niches(10) if n["source"] == "request"]
    assert len(niches) == 3 and all(len(n["subjects"]) == 10 and n["options"]["variants"] == 3 for n in niches)
    assert first in [n["id"] for n in niches] and runner.outcome == "review"
    assert len({tuple(n["subjects"]) for n in niches}) == 3        # each pack has its own stickers


def test_a_failed_pack_stops_the_queue(runner, monkeypatch):
    def boom(niche_id, name):
        raise RuntimeError("no more credit")

    monkeypatch.setattr(main, "_stage_generate", boom)
    start_packs(runner, [["a"], ["b"], ["c"]])
    wait(runner)
    assert len([n for n in db.list_niches(10) if n["source"] == "request"]) == 1
    assert "Skipped the 2 remaining packs" in " ".join(runner.log)


def test_total_limit_applies_to_all_packs(runner):
    with pytest.raises(jobs.CannotStart, match="limit is 100"):
        start_packs(runner, [["x"] * 20] * 3, variants=2)
    assert db.get_today_spend() == 0


# ── exporting ───────────────────────────────────────────────────────────────────

@pytest.fixture
def finished_run(tmp_path):
    nid = db.queue_request("Spooky", subjects=["a", "b", "c"], count=3, options={"variants": 1})
    folder = config.output_dir() / f"niche_{nid}" / "raw"
    folder.mkdir(parents=True)
    for i, (kept, reason) in enumerate([(True, "ok"), (True, "AI quality check skipped"), (False, "score 3")]):
        path = folder / f"{i}.png"
        transparent_sticker(i, size=1500).save(path)
        db.save_image_record(nid, f"sticker {i}", str(path), kept=kept, qa_score=8.0, qa_reason=reason)
    return nid


def test_goodnotes_is_the_default_and_makes_a_pdf(finished_run):
    assert export.DEFAULT_FORMAT == "goodnotes" and list(export.FORMATS)[0] == "goodnotes"
    out = export.export_run(finished_run, name="Spooky Pack!")
    assert out.suffix == ".pdf" and out.read_bytes()[:4] == b"%PDF"
    assert not (out.parent / "_small").exists()


def test_png_and_jpeg_zips_hold_only_stickers_that_passed(finished_run):
    for fmt, ext in (("png", ".png"), ("jpeg", ".jpg")):
        out = export.export_run(finished_run, fmt, "Spooky")
        names = zipfile.ZipFile(out).namelist()
        assert names == [f"spooky_01{ext}", f"spooky_02{ext}"]          # the low-scoring one is left out
    z = zipfile.ZipFile(export.export_run(finished_run, "jpeg", "Spooky"))
    jpg = Image.open(io.BytesIO(z.read("spooky_01.jpg")))
    assert jpg.format == "JPEG" and jpg.size == (1500, 1500) and jpg.getpixel((2, 2)) == (255, 255, 255)


def test_nothing_to_export_and_unknown_format(finished_run):
    empty = db.queue_request("Empty", subjects=["x"], count=1)
    with pytest.raises(export.NothingToExport):
        export.export_run(empty)
    with pytest.raises(ValueError):
        export.export_run(finished_run, "goodnotes-file")
