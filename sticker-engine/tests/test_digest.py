from src import digest
from src.storage import db


def seed_day():
    db.queue_niches([("alpha", 3.0, "etsy"), ("beta", 2.0, "etsy"), ("gamma <script>", 1.0, "seed")])
    ids = {n: i for n, i in ((r["name"], r["id"]) for r in [db.get_niche_to_process()])}
    conn = db._conn()
    nid = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM niches")}
    conn.close()
    db.update_niche_status(nid["alpha"], "READY", "Publish kit ready: /k/alpha")
    db.update_niche_status(nid["beta"], "FAILED", "NotImplemented: nothing")
    a = db.save_image_record(nid["alpha"], "p1", "/x.png", kept=True, qa_score=8, qa_reason="good")
    b = db.save_image_record(nid["alpha"], "p2", "/y.png", kept=False, qa_score=3, qa_reason="garbled text")
    db.log_spend("gemini-3.1-flash-image", images_count=1, cost_usd=0.067, niche_id=nid["alpha"])
    db.log_spend("gemini-3.8-flash", tokens_in=1000, tokens_out=200, cost_usd=0.0015)
    return ids, a, b


def test_digest_data_aggregates_the_day():
    seed_day()
    d = db.digest_data()
    assert d["niches_discovered"] == 3 and d["images_generated"] == 2
    assert (d["qa_scored"], d["qa_kept"]) == (2, 1)
    assert [r["name"] for r in d["ready"]] == ["alpha"] and d["failed"][0]["name"] == "beta"
    assert d["sample_rejects"][0]["qa_reason"] == "garbled text"
    assert d["spend_total"] == 0.0685 or abs(d["spend_total"] - 0.0685) < 1e-9
    assert [m["model"] for m in d["spend_by_model"]] == ["gemini-3.1-flash-image", "gemini-3.8-flash"]
    assert [q["name"] for q in d["queue"]] == ["gamma <script>"]


def test_other_days_are_excluded():
    seed_day()
    assert db.digest_data("2020-01-01")["images_generated"] == 0


def test_html_reports_the_facts_and_escapes_names():
    seed_day()
    html = digest.render_digest()
    assert "Ready for you to publish" in html and "Publish kit ready: /k/alpha" in html
    assert "50% accepted" in html and "garbled text" in html and "$0.07 of $10.00" in html
    assert "&lt;script&gt;" in html and "<script>" not in html


def test_empty_day_renders():
    html = digest.render_digest()
    assert "Nothing published automatically today" in html and "Empty." in html


def test_send_digest_saves_file_and_is_honest_without_smtp(monkeypatch):
    result = digest.send_digest()
    assert result["emailed"] is False and open(result["path"]).read().startswith("<!doctype html>")


def test_send_digest_emails_when_smtp_is_configured(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, u, p):
            sent["login"] = (u, p)

        def send_message(self, msg):
            sent["to"], sent["html"] = msg["To"], msg.get_body(("html",)).get_content()

    monkeypatch.setattr(digest.smtplib, "SMTP", FakeSMTP)
    for k, v in {"SMTP_HOST": "mail.example.com", "SMTP_PORT": "2525", "SMTP_USER": "u", "SMTP_PASS": "p", "DIGEST_EMAIL_TO": "me@example.com"}.items():
        monkeypatch.setenv(k, v)
    result = digest.send_digest()
    assert result["emailed"] is True
    assert sent["host"] == "mail.example.com" and sent["port"] == 2525 and sent["tls"] and sent["login"] == ("u", "p")
    assert sent["to"] == "me@example.com" and "Sticker Engine digest" in sent["html"]
