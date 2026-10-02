import time

import pytest

from src import main
from src.generator import optimize
from src.packaging import export
from src.shared import config
from src.storage import db
from src.ui import jobs, keep
from src.ui.app import dropped_ids
from tests.helpers import transparent_sticker


# ── Optimize ────────────────────────────────────────────────────────────────────

class FakeText:
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    def generate_json(self, ask, system=None):
        self.asked.append(ask)
        return self.reply


def test_optimize_returns_the_improved_prompt_and_sends_style_context():
    client = FakeText({"prompt": "  Dry, sarcastic   Halloween stickers for adults, short bold words.  "})
    out = optimize.optimize_brief("halloween funny", client=client)
    assert out == "Dry, sarcastic Halloween stickers for adults, short bold words."
    assert client.asked[0].startswith("Brief to improve: halloween funny") and "Style the stickers" in client.asked[0]


@pytest.mark.parametrize("reply", [{"prompt": "short"}, {}, {"prompt": ""}, ["nope"]])
def test_optimize_refuses_to_replace_text_with_junk(reply):
    with pytest.raises(ValueError, match="did not return"):
        optimize.optimize_brief("halloween funny", client=FakeText(reply))


def test_optimize_needs_text_and_is_capped():
    with pytest.raises(ValueError):
        optimize.optimize_brief("   ", client=FakeText({}))
    assert len(optimize.optimize_brief("x y z", client=FakeText({"prompt": "word " * 400}))) <= optimize.MAX_LEN


# ── the keep list ───────────────────────────────────────────────────────────────

def test_keep_list_adds_once_removes_and_survives_restart():
    assert keep.load(7) == []
    keep.add(7, 3), keep.add(7, 5), keep.add(7, 3)
    assert keep.load(7) == [3, 5]
    assert keep.remove(7, 3) == [5]
    keep.clear(7)
    assert keep.load(7) == []


def test_a_damaged_keep_file_is_ignored():
    f = config.output_dir() / "niche_9" / "keep.json"
    f.parent.mkdir(parents=True)
    f.write_text("{broken")
    assert keep.load(9) == []


def test_drop_events_are_read_safely():
    assert dropped_ids("12") == [12]
    assert dropped_ids(["4", "x", None, "7"]) == [4, 7]
    assert dropped_ids("<script>") == []


# ── exporting and building only what you kept ───────────────────────────────────

@pytest.fixture
def run_with_images():
    nid = db.queue_request("Spooky", subjects=["a", "b", "c", "d"], count=4, options={"variants": 1})
    folder = config.output_dir() / f"niche_{nid}" / "raw"
    folder.mkdir(parents=True)
    ids = []
    for i, (kept, reason) in enumerate([(True, "ok"), (True, "ok"), (False, "score 3"), (True, "ok")]):
        path = folder / f"{i}.png"
        transparent_sticker(i, size=800).save(path)
        db.save_image_record(nid, f"sticker {i}", str(path), kept=kept, qa_score=8.0 if kept else 3.0, qa_reason=reason)
    ids = [r["id"] for r in db.get_images_for_niche(nid, kept=None)]
    return nid, ids


def test_export_uses_exactly_the_kept_stickers_in_the_order_you_chose(run_with_images):
    nid, ids = run_with_images
    chosen = [ids[3], ids[2]]                               # includes a low-scoring one: you picked it on purpose
    paths = export.exportable_paths(nid, chosen)
    assert [p.rsplit("/", 1)[1] for p in paths] == ["3.png", "2.png"]
    assert len(export.exportable_paths(nid)) == 3           # no keep list: every sticker that passed


def test_export_ignores_ids_from_other_runs(run_with_images):
    nid, ids = run_with_images
    assert export.exportable_paths(nid, [99999]) == []
    with pytest.raises(export.NothingToExport):
        export.export_run(nid, "png", "x", image_ids=[99999])


def test_pack_is_built_from_the_kept_stickers_only(run_with_images):
    nid, ids = run_with_images
    assert len(main._chosen_images(nid)) == 3
    db.update_niche_options(nid, only_image_ids=[ids[0], ids[2]])
    assert [i["id"] for i in main._chosen_images(nid)] == [ids[0], ids[2]]
    assert main._options(nid)["only_image_ids"] == [ids[0], ids[2]]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "key-for-tests")
    for name in ("generate", "filter", "package", "list"):
        monkeypatch.setattr(main, f"_stage_{name}", lambda i, n: None)
    return jobs.JobRunner()


def test_build_refuses_ids_that_are_not_in_the_run(runner, run_with_images):
    nid, ids = run_with_images
    with pytest.raises(jobs.CannotStart, match="Keep panel"):
        runner.build_pack(nid, [424242])
    runner.build_pack(nid, [ids[0], 424242])
    deadline = time.time() + 5
    while runner.running and time.time() < deadline:
        time.sleep(0.02)
    assert db.get_niche(nid)["options"]["only_image_ids"] == [ids[0]]


# ── the header tallies ──────────────────────────────────────────────────────────

def test_header_stats_count_this_runs_packs_stickers_and_cost(runner):
    assert runner.stats() == {"packs": 0, "images": 0, "spent": 0}
    runner.start_packs(name="Halloween", packs=[["a", "b"], ["c"]], variants=1, references=[], reference_mode="style",
                       apply_style=True, qa=False, build_pack=False)
    deadline = time.time() + 5
    while runner.running and time.time() < deadline:
        time.sleep(0.02)
    assert runner.stats()["packs"] == 2
    nid = runner.session_niches[0]
    p = config.output_dir() / "x.png"
    transparent_sticker(1, size=300).save(p)
    db.save_image_record(nid, "a", str(p), kept=True)
    db.log_spend("m", 0, 0, 1, 0.24, nid)
    s = runner.stats()
    assert s["images"] == 1 and s["spent"] == pytest.approx(0.24)
