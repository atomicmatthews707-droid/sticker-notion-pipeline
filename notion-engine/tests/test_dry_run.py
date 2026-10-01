import csv

import run
from engine.spec_generator import generate_spec


def test_dry_run_spec():
    spec = generate_spec("Test Niche", dry_run=True)
    assert spec["template_name"] == "Test Niche Template"
    assert len(spec["databases"]) == 1


def test_slugify_is_path_safe():
    assert run.slugify("Freelance Invoice Tracker") == "freelance_invoice_tracker"
    assert run.slugify("../../etc/passwd") == "etc_passwd"
    assert run.slugify("!!!") == "niche"


def test_dry_run_writes_outputs_without_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["run.py", "--dry-run", "--niche", "Habit, Tracker"])
    assert run.main() == 0
    out = tmp_path / "output" / "habit_tracker"
    assert {p.name for p in out.iterdir()} == {"spec.json", "listing.md", "cover_prompts.txt"}
    rows = list(csv.DictReader(open(tmp_path / "output" / "catalog.csv", encoding="utf-8")))
    assert rows[0]["Niche"] == "Habit, Tracker" and rows[0]["Status"] == "Success"  # comma survives


def test_failed_niche_does_not_abort_batch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake(niche, dry_run):
        calls.append(niche)
        if niche == "bad":
            raise RuntimeError("boom")
        return {"slug": niche, "root_url": "u", "skipped": ["x"]}

    monkeypatch.setattr(run, "process_niche", fake)
    niches = tmp_path / "n.txt"
    niches.write_text("bad\ngood\n")
    monkeypatch.setattr("sys.argv", ["run.py", "--niches-file", str(niches)])
    assert run.main() == 1  # non-zero exit reports the failure
    assert calls == ["bad", "good"]
    rows = list(csv.DictReader(open(tmp_path / "output" / "catalog.csv", encoding="utf-8")))
    assert [(r["Niche"], r["Status"]) for r in rows] == [("bad", "Failed"), ("good", "Partial")]
    assert "boom" in rows[0]["Error"]


def test_no_niche_is_an_error(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run.py", "--dry-run"])
    assert run.main() == 1
