import pytest

from src import cli
from src.generator import prompt_builder
from src.shared import design
from src.storage import db

DEFAULT_PROMPT = ("Cozy mug, cute kawaii flat vector illustration, plain flat pure white background, "
                  "no shadow, no border, centered subject, thick outline, minimal detail")

PACK = """# Autumn Cozy Vibes
count: 3

<!-- a note to myself -->
## Brief
Cozy autumn objects
for planners.

## Subjects
- steaming mug of cocoa
- chunky knit sweater
1. pumpkin candle

## Style
soft watercolour, hand painted

## Palette
dusty orange and cream

## Avoid
lettering, gradients

## Voice
warm and brief

## Notes
Keep it homely.
## Something else
ignored
"""


class FakeLLM:
    def __init__(self, data):
        self.data, self.asked = data, []

    def generate_json(self, prompt, system=None, **kw):
        self.asked.append(prompt)
        return self.data


# ── parsing ──────────────────────────────────────────────────────────────────────

def test_parse_markdown_title_meta_sections_and_comments():
    doc = design.parse_markdown(PACK)
    assert doc["title"] == "Autumn Cozy Vibes" and doc["meta"] == {"count": "3"}
    assert doc["sections"]["brief"] == "Cozy autumn objects\nfor planners."
    assert "note to myself" not in str(doc)
    assert doc["sections"]["something else"] == "ignored"


def test_bullets_accept_dashes_numbers_and_plain_lines():
    assert design.bullets("- a\n* b\n3. c\nplain one\n\n") == ["a", "b", "c", "plain one"]


def test_request_from_markdown():
    r = design.request_from_markdown(PACK)
    assert r["name"] == "Autumn Cozy Vibes" and r["count"] == 3 and r["brief"] == "Cozy autumn objects for planners."
    assert r["subjects"] == ["steaming mug of cocoa", "chunky knit sweater", "pumpkin candle"] and r["pack_md"] == PACK


def test_count_defaults_to_the_number_of_listed_subjects():
    assert design.request_from_markdown("# P\n## Subjects\n- a thing\n- another thing")["count"] == 2


@pytest.mark.parametrize("md,message", [("no title here", "needs a title"), ("# P\ncount: lots", "count must be")])
def test_unusable_files_explain_themselves(md, message):
    with pytest.raises(ValueError, match=message):
        design.request_from_markdown(md)


# ── the default design changes nothing ───────────────────────────────────────────

def test_default_design_md_reproduces_the_original_prompt_exactly():
    assert prompt_builder.compose_prompt("Cozy mug") == DEFAULT_PROMPT


def test_design_md_in_the_repo_matches_config_yaml():
    d = design.direction_for()
    cfg = design.config.get("sticker_style")
    assert (d.style, d.composition) == (cfg["aesthetic"], cfg["composition"])
    assert not (d.palette or d.avoid or d.voice or d.notes)   # the shipped file adds nothing


def test_missing_design_file_falls_back_to_config(tmp_path, monkeypatch):
    monkeypatch.setattr(design, "DESIGN_FILE", tmp_path / "nope.md")
    assert prompt_builder.compose_prompt("Cozy mug") == DEFAULT_PROMPT


# ── editing changes the result ───────────────────────────────────────────────────

def test_editing_design_md_changes_every_prompt(tmp_path, monkeypatch):
    f = tmp_path / "DESIGN.md"
    f.write_text("# Shop\n## Style\nbold sticker-book ink drawing\n## Palette\nteal and coral\n## Avoid\nlettering\n")
    monkeypatch.setattr(design, "DESIGN_FILE", f)
    assert prompt_builder.compose_prompt("Cozy mug") == (
        "Cozy mug, bold sticker-book ink drawing, teal and coral, plain flat pure white background, "
        "no shadow, no border, centered subject, thick outline, minimal detail. Avoid: lettering")


def test_pack_file_overrides_style_and_adds_to_avoid(tmp_path, monkeypatch):
    f = tmp_path / "DESIGN.md"
    f.write_text("# Shop\n## Style\nink drawing\n## Avoid\nwords\n")
    monkeypatch.setattr(design, "DESIGN_FILE", f)
    d = design.direction_for(PACK)
    assert d.style == "soft watercolour, hand painted" and d.avoid == "words lettering, gradients"
    assert d.palette == "dusty orange and cream" and d.voice == "warm and brief"


def test_the_white_background_cannot_be_edited_away(tmp_path, monkeypatch):
    f = tmp_path / "DESIGN.md"
    f.write_text("# Shop\n## Composition\non a black background\n")
    monkeypatch.setattr(design, "DESIGN_FILE", f)
    assert "plain flat pure white background" in prompt_builder.compose_prompt("x")


def test_exact_subjects_skip_the_ai_entirely():
    llm = FakeLLM({"subjects": ["should not be used"]})
    prompts = prompt_builder.build_prompts("autumn", client=llm, pack_md=PACK, subjects=["steaming mug of cocoa", "chunky knit sweater"])
    assert llm.asked == [] and len(prompts) == 2 and prompts[0].startswith("steaming mug of cocoa, soft watercolour")
    assert prompts[0].endswith("Avoid: lettering, gradients")


def test_brainstorming_is_guided_by_the_brief_and_notes():
    llm = FakeLLM({"subjects": ["Sun", "Moon", "Teacup"]})
    prompt_builder.build_prompts("autumn", client=llm, pack_md=PACK, count=3)
    asked = llm.asked[0]
    assert "Cozy autumn objects for planners." in asked and "Keep it homely." in asked and "lettering, gradients" in asked


def test_banned_terms_in_a_pack_file_are_refused_before_any_spend():
    llm = FakeLLM({"subjects": ["x"]})
    with pytest.raises(ValueError, match="banned"):
        prompt_builder.build_prompts("autumn", client=llm, pack_md="# P\n## Palette\npokemon colours\n")
    assert llm.asked == []


def test_qa_rubric_is_unchanged_by_default_and_extended_by_the_design(tmp_path, monkeypatch):
    from src.quality import auto_filter

    img = [{"niche_id": 1}]
    assert auto_filter._direction_rules(img) == ""
    nid = db.queue_request("autumn", pack_md=PACK)
    rules = auto_filter._direction_rules([{"niche_id": nid}])
    assert "lettering, gradients" in rules and "dusty orange and cream" in rules and "soft watercolour" in rules and "Keep it homely." in rules


def test_listing_voice_comes_from_the_markdown():
    from src.publisher import listing_writer

    class LLM:
        prompts = []

        def generate_json(self, prompt, system=None, **kw):
            self.prompts.append(prompt)
            return {"title": "T" * 20, "description": "d" * 200, "tags": [f"tag{i}" for i in range(13)],
                    "gumroad_title": "G", "gumroad_description": "GD"}

    llm = LLM()
    listing_writer.write_listing("autumn", ["a"] * 3, client=llm, pack_md=PACK)
    assert "Write in this voice: warm and brief" in llm.prompts[0]


# ── requests: database, endpoint, command line ───────────────────────────────────

def test_request_is_stored_with_its_markdown_and_jumps_the_queue():
    db.queue_niches([("scouted", 50.0, "etsy")])
    nid = db.queue_request(**design.request_from_markdown(PACK))
    niche = db.get_niche(nid)
    assert niche["pack_md"] == PACK and niche["target_count"] == 3 and niche["subjects"][0] == "steaming mug of cocoa"
    assert db.get_niche_to_process()["id"] == nid


def test_the_same_pack_cannot_be_queued_twice_while_active():
    db.queue_request("Cozy mugs")
    with pytest.raises(ValueError, match="already queued"):
        db.queue_request("cozy MUGS")


def test_old_databases_gain_the_new_columns(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE niches (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, status TEXT, score REAL, source TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, error_msg TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{path}")
    db.init_db_sync()
    assert db.queue_request("x", pack_md="# x")


def test_small_requested_packs_need_only_most_of_what_was_asked():
    from src import main

    assert main._minimum_pack_size(db.queue_request("a", count=5)) == 3
    assert main._minimum_pack_size(db.queue_request("b", count=40)) == 10      # the configured minimum still caps it
    assert main._minimum_pack_size(db.queue_request("c")) == 10


def test_endpoint_accepts_a_whole_markdown_file(monkeypatch):
    from fastapi.testclient import TestClient

    from src import main

    monkeypatch.setattr(main, "DISABLE_LOOP", True)
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED", "1")
    with TestClient(main.app) as c:
        ok = c.post("/niches", json={"markdown": PACK})
        assert ok.status_code == 200 and db.get_niche(ok.json()["id"])["pack_md"] == PACK
        assert c.post("/niches", json={"markdown": PACK}).status_code == 409
        assert c.post("/niches", json={"markdown": "no title"}).status_code == 422
        assert c.post("/niches", json={"name": "pokemon pack"}).status_code == 422
        assert c.post("/niches", json={"name": "ok", "count": 500}).status_code == 422


def test_endpoint_needs_the_token(monkeypatch):
    from fastapi.testclient import TestClient

    from src import main

    monkeypatch.setattr(main, "DISABLE_LOOP", True)
    with TestClient(main.app) as c:
        assert c.post("/niches", json={"name": "x"}).status_code == 503


def test_cli_shows_the_cost_and_spends_nothing_without_yes(tmp_path, capsys, monkeypatch):
    pack = tmp_path / "p.md"
    pack.write_text(PACK)
    monkeypatch.setattr("src.main._run_pipeline_for_niche", lambda *a: pytest.fail("must not run"))
    assert cli.main(["make", str(pack)]) == 0
    out = capsys.readouterr().out
    assert "stickers: 3" in out and "estimated cost: up to $0.26" in out and "Nothing was generated" in out
    assert db.get_niche_to_process() is None                 # not even queued


def test_cli_with_yes_runs_the_pack_and_reports(tmp_path, capsys, monkeypatch):
    pack = tmp_path / "p.md"
    pack.write_text(PACK)
    ran = []
    monkeypatch.setattr("src.main._run_pipeline_for_niche", lambda i, n, s: ran.append((i, n)) or "ready")
    assert cli.main(["make", str(pack), "--yes"]) == 0
    assert ran[0][1] == "Autumn Cozy Vibes" and "Result: ready" in capsys.readouterr().out


def test_cli_refuses_when_the_daily_cap_is_spent(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("BUDGET_DAILY_LIMIT_USD", "0.5")
    db.log_spend("m", cost_usd=0.5)
    assert cli.main(["make", "Cozy mugs", "--count", "2", "--yes"]) == 1
    assert "budget already reached" in capsys.readouterr().out


def test_cost_estimate_is_just_above_what_was_measured():
    measured_5 = 0.417                         # the live 5-sticker run
    assert measured_5 <= cli.estimate_usd(5) <= measured_5 * 1.1
    assert cli.estimate_usd(40) == 3.33
