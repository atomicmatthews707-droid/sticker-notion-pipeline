"""Daily digest: what the engine did, what it spent, and what needs you. Saved as HTML and emailed if SMTP is set."""

import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from jinja2 import Environment, select_autoescape

from src.shared import config
from src.shared.config import daily_budget_usd
from src.shared.logger import get_logger
from src.storage import db

logger = get_logger(__name__)

_TEMPLATE = """<!doctype html><html><body style="margin:0;background:#f3f4f8;font-family:Arial,Helvetica,sans-serif;color:#1c1f2b">
<div style="max-width:680px;margin:0 auto;padding:24px 16px">
<h1 style="font-size:22px;margin:0 0 4px">Sticker Engine digest</h1>
<p style="margin:0 0 20px;color:#5b6075">{{ d.day }} (UTC)</p>
<table role="presentation" width="100%" cellspacing="8" style="margin-bottom:8px"><tr>
{% for label, value in tiles %}<td style="background:#fff;border-radius:8px;padding:12px;text-align:center">
<div style="font-size:22px;font-weight:bold">{{ value }}</div><div style="font-size:12px;color:#5b6075">{{ label }}</div></td>{% endfor %}
</tr></table>

{% if d.ready %}<h2 style="font-size:16px">Ready for you to publish</h2>
<p style="margin:0 0 8px;color:#5b6075">Packs are built and the listing copy is written. Upload them from the publish kit.</p>
<ul>{% for r in d.ready %}<li><b>{{ r.name }}</b><br><span style="color:#5b6075;font-size:13px">{{ r.note }}</span></li>{% endfor %}</ul>{% endif %}

<h2 style="font-size:16px">Published today</h2>
{% if d.published %}<ul>{% for p in d.published %}<li><b>{{ p.name }}</b>
{% if p.etsy_url %}<a href="{{ p.etsy_url }}">Etsy</a> {% endif %}{% if p.gumroad_url %}<a href="{{ p.gumroad_url }}">Gumroad</a>{% endif %}
{% if p.spend_usd %}<span style="color:#5b6075">(cost ${{ '%.2f' % p.spend_usd }})</span>{% endif %}</li>{% endfor %}</ul>
{% else %}<p style="color:#5b6075">Nothing published automatically today.</p>{% endif %}

<h2 style="font-size:16px">Quality filter</h2>
<p>{{ d.qa_scored }} stickers scored, {{ d.qa_kept }} kept{% if d.qa_scored %} ({{ '%.0f' % (100 * d.qa_kept / d.qa_scored) }}% accepted; healthy is 50-70%){% endif %}.</p>
{% if d.sample_rejects %}<p style="margin-bottom:4px">Sample rejects:</p><ul>{% for r in d.sample_rejects %}<li><b>{{ r.niche }}</b>: {{ r.qa_reason }}</li>{% endfor %}</ul>{% endif %}

<h2 style="font-size:16px">Spend</h2>
<p>${{ '%.2f' % d.spend_total }} of ${{ '%.2f' % budget }} daily cap.</p>
{% if d.spend_by_model %}<table width="100%" cellpadding="6" style="border-collapse:collapse;font-size:13px">
<tr style="text-align:left;color:#5b6075"><th>Model</th><th>Cost</th><th>Images</th><th>Tokens in/out</th></tr>
{% for m in d.spend_by_model %}<tr style="border-top:1px solid #dde"><td>{{ m.model }}</td><td>${{ m.cost }}</td><td>{{ m.images or 0 }}</td><td>{{ m.tokens_in or 0 }} / {{ m.tokens_out or 0 }}</td></tr>{% endfor %}
</table>{% endif %}

<h2 style="font-size:16px">Tomorrow's queue</h2>
{% if d.queue %}<ol>{% for q in d.queue %}<li>{{ q.name }} <span style="color:#5b6075">(score {{ q.score }})</span></li>{% endfor %}</ol>
{% else %}<p style="color:#5b6075">Empty. The next trend refresh will fill it.</p>{% endif %}

<h2 style="font-size:16px">Errors</h2>
{% if d.failed %}<ul>{% for f in d.failed %}<li><b>{{ f.name }}</b>: {{ f.error }}</li>{% endfor %}</ul>
{% else %}<p style="color:#5b6075">None.</p>{% endif %}
</div></body></html>"""

_env = Environment(autoescape=select_autoescape(default=True))


def render_digest(data: Optional[dict] = None) -> str:
    d = data or db.digest_data()
    tiles = [
        ("niches found", d["niches_discovered"]),
        ("stickers made", d["images_generated"]),
        ("ready to publish", len(d["ready"])),
        ("published", len(d["published"])),
        ("failed", len(d["failed"])),
    ]
    return _env.from_string(_TEMPLATE).render(d=d, tiles=tiles, budget=daily_budget_usd())


def _smtp_configured() -> bool:
    return all(os.getenv(k) for k in ("SMTP_HOST", "DIGEST_EMAIL_TO"))


def _send_email(html: str, subject: str) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, os.environ["DIGEST_EMAIL_TO"]
    msg["From"] = os.getenv("DIGEST_EMAIL_FROM") or os.getenv("SMTP_USER") or os.environ["DIGEST_EMAIL_TO"]
    msg.set_content("Open this email in an HTML-capable client to see the digest.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT") or 587), timeout=30) as smtp:
        smtp.starttls()
        if os.getenv("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASS", ""))
        smtp.send_message(msg)


def send_digest() -> dict:
    """Render and save today's digest; email it when SMTP is configured. Never claims a send that did not happen."""
    data = db.digest_data()
    html = render_digest(data)
    folder = config.output_dir() / "digests"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{data['day']}.html"
    path.write_text(html, encoding="utf-8")
    emailed = False
    if _smtp_configured():
        _send_email(html, f"Sticker Engine digest {data['day']}")
        emailed = True
    else:
        logger.info("SMTP not configured; digest saved to %s but not emailed.", path)
    return {"path": str(path), "emailed": emailed}
