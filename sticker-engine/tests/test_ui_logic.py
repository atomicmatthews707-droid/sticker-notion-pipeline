import subprocess
import time
from pathlib import Path

import pytest

from src import main
from src.shared import cancel, config
from src.storage import db
from src.ui import gallery, jobs, opener, settings
from src.ui.prompts import parse_prompts
from tests.helpers import transparent_sticker

# ── reading your text ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("a grumpy pumpkin on a skateboard", ["a grumpy pumpkin on a skateboard"]),
    ("- mug\n- sweater\n- candle", ["mug", "sweater", "candle"]),
    ("1. mug\n2) sweater", ["mug", "sweater"]),
    ("mug\nsweater\ncandle", ["mug", "sweater", "candle"]),                               # short lines: one each
    ("A long first prompt about a pumpkin.\n\nA second prompt about a ghost.", ["A long first prompt about a pumpkin.", "A second prompt about a ghost."]),
    ("- steaming mug\n  with a cinnamon stick\n- sweater", ["steaming mug with a cinnamon stick", "sweater"]),
    ("# Autumn\n\n- mug\n- sweater", ["mug", "sweater"]),
])
def test_smart_reading(text, expected):
    assert parse_prompts(text).prompts == expected


def test_a_hard_wrapped_long_prompt_stays_one_prompt():
    long_line = "x" * 100
    text = f"{long_line} and then some more\n{long_line} and so on and on\n{long_line} until the end"
    assert len(parse_prompts(text).prompts) == 1


def test_modes_override_the_guess():
    text = "mug\nsweater\ncandle"
    assert len(parse_prompts(text, "single").prompts) == 1
    assert parse_prompts(text, "single").prompts[0] == "mug sweater candle"
    assert parse_prompts("- a thing\n- another", "lines").prompts == ["a thing", "another"]


def test_pack_markdown_files_work_in_the_text_box():
    md = "# Autumn Cozy\ncount: 3\n\n## Brief\nCozy things\n\n## Subjects\n- mug\n- sweater\n- candle\n\n## Style\nwatercolour"
    p = parse_prompts(md)
    assert p.name == "Autumn Cozy" and p.prompts == ["mug", "sweater", "candle"] and "Subjects" in p.how


def test_pack_file_without_subjects_uses_its_brief_as_the_one_prompt():
    assert parse_prompts("# Cozy\n## Brief\nCozy autumn objects for planners.\n## Style\nink").prompts == ["Cozy autumn objects for planners."]


def test_comments_and_empty_input():
    assert parse_prompts("<!-- note -->\n- mug").prompts == ["mug"]
    assert parse_prompts("   ").prompts == [] and parse_prompts("<!-- only a comment -->").prompts == []


def test_names():
    assert parse_prompts("# My Pack\n- a\n- b").name == "My Pack"
    assert parse_prompts("A grumpy pumpkin wearing a tiny cowboy hat riding").name == "A grumpy pumpkin wearing a tiny"
    assert parse_prompts("").name == ""


# ── the review area never hides an image ────────────────────────────────────────

def seed_images(tmp_path, spec):
    """spec: list of (prompt, variant, score, kept, reason, has_file)."""
    nid = db.queue_request("t", options={"variants": 2})
    for i, (prompt, variant, score, kept, reason, has_file) in enumerate(spec):
        path = ""
        if has_file:
            f = tmp_path / f"{i}.png"
            transparent_sticker(i).save(f)
            path = str(f)
        db.save_image_record(nid, prompt, path, kept=kept, qa_score=score, qa_reason=reason, variant=variant)
    return nid


def test_every_generated_image_is_listed_whatever_its_score(tmp_path):
    nid = seed_images(tmp_path, [("mug", 0, 9, True, "great", True), ("mug", 1, 3, False, "fused", True),
                                 ("cat", 0, None, None, "", True), ("dog", 0, None, False, "blocked: safety", False)])
    items = gallery.gallery_items(nid)
    assert len(items) == 4
    assert [i["state"] for i in items] == ["passed", "low", "waiting", "blocked"]
    assert len(gallery.filter_items(items, "all")) == 4                           # 'all' is the default and hides nothing


def test_a_duplicate_is_labelled_as_one_not_as_a_low_score(tmp_path):
    items = gallery.gallery_items(seed_images(tmp_path, [("mug", 0, 9, True, "great", True), ("mug", 1, 9, False, "duplicate", True)]))
    assert [i["state"] for i in items] == ["passed", "duplicate"]
    assert len(gallery.filter_items(items, "duplicate")) == 1 and len(gallery.filter_items(items, "all")) == 2


def test_the_best_variation_gets_a_star_but_the_others_stay(tmp_path):
    nid = seed_images(tmp_path, [("mug", 0, 6, False, "meh", True), ("mug", 1, 9, True, "great", True), ("mug", 2, 8, True, "good", True)])
    items = gallery.gallery_items(nid)
    assert [i["best"] for i in items] == [False, True, False]
    assert len(items) == 3 and all(i["path"] for i in items)


def test_no_star_when_a_prompt_has_one_variation(tmp_path):
    items = gallery.gallery_items(seed_images(tmp_path, [("mug", 0, 9, True, "great", True)]))
    assert not items[0]["best"]


def test_skipped_quality_check_is_labelled_unchecked_not_passed(tmp_path):
    items = gallery.gallery_items(seed_images(tmp_path, [("mug", 0, None, True, "AI quality check skipped", True)]))
    assert items[0]["state"] == "unchecked"


def test_cutout_and_original_paths(tmp_path):
    nid = db.queue_request("t")
    orig, cut = tmp_path / "a.png", tmp_path / "a_nobg.png"
    transparent_sticker(1).save(orig)
    transparent_sticker(2).save(cut)
    db.save_image_record(nid, "mug", str(cut))
    (item,) = gallery.gallery_items(nid)
    assert item["cutout"] == str(cut) and item["original"] == str(orig) and item["path"] == str(cut)


def test_expected_count_is_prompts_times_variations(tmp_path):
    nid = db.queue_request("t", options={"variants": 3})
    f = config.output_dir() / f"niche_{nid}" / "prompts.json"
    f.parent.mkdir(parents=True)
    f.write_text('["a", "b"]')
    assert gallery.expected_images(nid) == 6


def test_progress_counts(tmp_path):
    nid = seed_images(tmp_path, [("mug", 0, 9, True, "ok", True), ("cat", 0, None, None, "", True)])
    assert gallery.progress(nid, 4) == {"made": 2, "judged": 1, "expected": 4, "total": 2}


# ── open with ────────────────────────────────────────────────────────────────────

def media(tmp_path, monkeypatch, name="a.png"):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    f = tmp_path / name
    f.write_bytes(b"x")
    return f


def test_files_outside_the_output_folder_are_refused(tmp_path, monkeypatch):
    media(tmp_path, monkeypatch)
    with pytest.raises(opener.NotAllowed, match="outside"):
        opener.check_path("/etc/passwd")
    with pytest.raises(opener.NotAllowed):
        opener.check_path(str(tmp_path / ".." / "etc" / "passwd"))


def test_programs_and_unknown_types_are_never_opened(tmp_path, monkeypatch):
    for name in ("evil.exe", "run.sh", "x.bat"):
        f = media(tmp_path, monkeypatch, name)
        with pytest.raises(opener.NotAllowed, match="not opened"):
            opener.check_path(str(f))


def test_a_missing_file_is_a_clear_message(tmp_path, monkeypatch):
    media(tmp_path, monkeypatch)
    with pytest.raises(opener.NotAllowed, match="no longer exists"):
        opener.check_path(str(tmp_path / "gone.png"))


@pytest.mark.parametrize("platform,first", [("win32", "Open"), ("darwin", "Open"), ("linux", "Open")])
def test_every_platform_has_open_and_show_in_folder(platform, first):
    names = [a["name"] for a in opener.builtin_actions(platform)]
    assert names[0] == first and any("folder" in n.lower() or "finder" in n.lower() for n in names)


def test_windows_gets_the_native_choose_a_program_dialog(tmp_path, monkeypatch):
    f = media(tmp_path, monkeypatch)
    action = next(a for a in opener.builtin_actions("win32") if a["kind"] == "choose")
    assert opener.build_command(action, f, "win32") == ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(f)]


def test_commands_per_platform(tmp_path, monkeypatch):
    f = media(tmp_path, monkeypatch)
    reveal = {"kind": "reveal", "name": "r"}
    default = {"kind": "default", "name": "d"}
    assert opener.build_command(reveal, f, "darwin") == ["open", "-R", str(f)]
    assert opener.build_command(reveal, f, "linux") == ["xdg-open", str(tmp_path)]
    assert opener.build_command(reveal, f, "win32") == ["explorer", f"/select,{f}"]
    assert opener.build_command(default, f, "linux") == ["xdg-open", str(f)]
    assert opener.build_command(default, f, "darwin") == ["open", str(f)]
    assert opener.build_command(default, f, "win32") is None                      # handled with os.startfile


def test_custom_programs():
    app = opener.parse_custom_app(r'Photoshop = "C:\Program Files\Adobe\Photoshop.exe" {file}', "win32")
    assert app["name"] == "Photoshop" and app["command"] == [r"C:\Program Files\Adobe\Photoshop.exe", "{file}"]
    assert opener.parse_custom_app("GIMP = gimp", "linux")["command"] == ["gimp", "{file}"]      # {file} added if missing
    with pytest.raises(ValueError, match="Name = path"):
        opener.parse_custom_app("just words")


def test_actions_run_without_a_shell_and_report_what_happened(tmp_path, monkeypatch):
    f = media(tmp_path, monkeypatch, "my pic (1).png")                              # spaces and brackets are fine
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    msg = opener.run_action({"name": "GIMP", "kind": "command", "command": ["gimp", "{file}"]}, str(f))
    assert calls[0][0] == ["gimp", str(f.resolve())] and "shell" not in calls[0][1] and "GIMP" in msg


def test_a_missing_program_gives_a_message_not_a_crash(tmp_path, monkeypatch):
    f = media(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise FileNotFoundError("no such program")

    monkeypatch.setattr(subprocess, "Popen", boom)
    assert "Could not open" in opener.run_action({"name": "X", "kind": "command", "command": ["nope", "{file}"]}, str(f))


# ── settings ─────────────────────────────────────────────────────────────────────

def test_settings_round_trip_and_ignore_junk(tmp_path):
    path = tmp_path / "s.json"
    assert settings.load(path)["variants"] == 1
    settings.save({"variants": 4, "background": "dark", "evil": 1}, path)
    loaded = settings.load(path)
    assert loaded["variants"] == 4 and loaded["background"] == "dark" and "evil" not in loaded and loaded["count"] == 1


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert settings.load(path) == settings.DEFAULTS


# ── starting a run ───────────────────────────────────────────────────────────────

@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "key-for-tests")
    for name in ("generate", "filter", "package", "list"):
        monkeypatch.setattr(main, f"_stage_{name}", lambda i, n: None)
    return jobs.JobRunner()


def start(runner, **over):
    args = dict(name="Pumpkins", prompts=["a", "b", "c"], count=3, variants=2, references=[], reference_mode="style",
                apply_style=True, qa=True, build_pack=False)
    return runner.start(**{**args, **over})


def wait(runner, seconds=5):
    end = time.time() + seconds
    while runner.running and time.time() < end:
        time.sleep(0.02)


def test_image_total_is_exactly_prompts_times_variations():
    assert jobs.image_total(3, 3, 2) == 6
    assert jobs.image_total(10, 4, 3) == 12            # count caps the prompts used
    assert jobs.image_total(2, 10, 5) == 10            # count cannot invent prompts
    assert jobs.image_total(1, 1, 1) == 1


def test_a_run_says_exactly_how_many_images_it_will_make(runner):
    nid = start(runner)
    wait(runner)
    assert "exactly 6 images (3 prompts x 2 variations)" in runner.log[0]
    assert runner.outcome == "review" and "Every image is below" in " ".join(runner.log)
    niche = db.get_niche(nid)
    assert niche["subjects"] == ["a", "b", "c"] and niche["options"]["verbatim"] is True and niche["options"]["variants"] == 2


def test_count_limits_how_many_prompts_are_used(runner):
    nid = start(runner, count=2)
    wait(runner)
    assert db.get_niche(nid)["subjects"] == ["a", "b"] and "exactly 4 images" in runner.log[0]


@pytest.mark.parametrize("over,message", [
    ({"prompts": []}, "at least one prompt"),
    ({"count": 0}, "at least 1"),
    ({"prompts": ["x"] * 61, "count": 61}, "limit is 60"),
    ({"prompts": ["x"] * 50, "count": 50, "variants": 3}, "limit is 100"),
])
def test_refusals_happen_before_anything_is_spent(runner, over, message):
    with pytest.raises(jobs.CannotStart, match=message):
        start(runner, **over)
    assert db.get_niche_to_process() is None and db.get_today_spend() == 0


def test_missing_api_key_is_explained(runner, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(jobs.CannotStart, match="No Gemini API key"):
        start(runner)


def test_spent_cap_blocks_a_new_run(runner, monkeypatch):
    monkeypatch.setenv("BUDGET_DAILY_LIMIT_USD", "0.1")
    db.log_spend("m", cost_usd=0.1)
    with pytest.raises(jobs.CannotStart, match="spending cap"):
        start(runner)


def test_only_one_run_at_a_time(runner, monkeypatch):
    import threading

    gate = threading.Event()
    monkeypatch.setattr(main, "_stage_generate", lambda i, n: gate.wait(3))
    start(runner)
    try:
        with pytest.raises(jobs.CannotStart, match="already in progress"):
            start(runner, name="Other")
    finally:
        gate.set()
        wait(runner)


def test_stop_button_stops_and_says_nothing_is_lost(runner, monkeypatch):
    import threading

    started = threading.Event()

    def slow(i, n):
        started.set()
        for _ in range(100):
            cancel.check()
            time.sleep(0.02)

    monkeypatch.setattr(main, "_stage_generate", slow)
    start(runner)
    assert started.wait(2)
    runner.stop()
    wait(runner)
    cancel.reset()
    text = " ".join(runner.log)
    assert runner.outcome == "cancelled" and "Nothing already made is lost" in text and "Everything made so far is kept" in text


def test_a_failed_run_reports_the_reason(runner, monkeypatch):
    def broken(i, n):
        raise RuntimeError("Gemini said no")

    monkeypatch.setattr(main, "_stage_generate", broken)
    start(runner)
    wait(runner)
    assert runner.outcome == "failed" and "Gemini said no" in " ".join(runner.log)


def test_two_runs_with_the_same_name_do_not_collide(runner):
    start(runner)
    wait(runner)
    nid2 = start(runner)
    wait(runner)
    assert db.get_niche(nid2)["name"] == "Pumpkins" or "(2)" in db.get_niche(nid2)["name"]


def test_log_lines_arrive_once(runner):
    start(runner)
    wait(runner)
    first = runner.new_log_lines()
    assert first and runner.new_log_lines() == []


def test_build_pack_from_a_reviewed_run(runner, monkeypatch):
    ran = []
    for name in ("package", "list"):
        monkeypatch.setattr(main, f"_stage_{name}", lambda i, n, name=name: ran.append(name))
    nid = start(runner)
    wait(runner)
    with pytest.raises(jobs.CannotStart, match="no passed or unchecked"):
        runner.build_pack(nid)
    db.save_image_record(nid, "a", "/x.png", kept=True)
    runner.build_pack(nid)
    wait(runner)
    assert ran == ["package", "list"] and db.get_niche(nid)["options"]["build_pack"] is True


def test_nothing_under_the_ui_modules_calls_the_text_ai():
    src = "".join(p.read_text() for p in Path(jobs.__file__).parent.glob("*.py"))
    assert "generate_json" not in src and "generate_text" not in src and "build_prompts(" not in src
