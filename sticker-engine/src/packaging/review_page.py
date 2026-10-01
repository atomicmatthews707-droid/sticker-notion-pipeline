"""
Pack review page: every sticker of a pack on switchable backgrounds, with its QA verdict, the listing images,
the listing copy and what it cost. One self-contained HTML file (images embedded), so it opens anywhere.
"""

import base64
import io
import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from jinja2 import Environment, select_autoescape
from PIL import Image

from src.shared import config
from src.storage import db

STICKER_PX, MOCKUP_PX = 520, 900


def _uri(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, "JPEG", quality=82)
    else:
        img.save(buf, "PNG", optimize=True)
    return f"data:image/{fmt.lower()};base64,{base64.b64encode(buf.getvalue()).decode()}"


def _shrink(img: Image.Image, px: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((px, px), Image.LANCZOS)
    return img


def _cutout(path: str, recut: bool) -> Image.Image:
    if not recut:
        return Image.open(path).convert("RGBA")
    from src.quality.bg_remover import remove_background

    raw = path.replace("_nobg.png", ".png")
    with tempfile.TemporaryDirectory() as tmp:  # never touch the original files
        copy = Path(tmp) / "x.png"
        shutil.copy(raw, copy)
        return Image.open(remove_background(str(copy), method="floodfill")).convert("RGBA")


def collect_review(niche_id: int, recut: bool = False, title: Optional[str] = None, note: str = "") -> dict:
    """Everything the page shows for one pack. recut=True redraws the cutouts with the current method (free)."""
    niche = db.get_niche(niche_id) or {"name": f"niche {niche_id}", "status": "?"}
    images = []
    for row in db.get_images_for_niche(niche_id, kept=None):
        item = {
            "name": row["prompt"].split(",")[0], "prompt": row["prompt"], "score": row["qa_score"],
            "reason": row["qa_reason"] or "", "kept": row["kept"], "uri": None,
        }
        if row["image_path"] and Path(row["image_path"]).exists():
            item["uri"] = _uri(_shrink(_cutout(row["image_path"], recut), STICKER_PX))
        images.append(item)

    pack = config.output_dir() / f"niche_{niche_id}" / "pack"
    mockups = [_uri(_shrink(Image.open(p), MOCKUP_PX), "JPEG") for p in sorted(pack.glob("mockup_*.jpg"))]
    sheet = pack / "sticker_sheet_preview.jpg"
    listing = json.loads((pack / "listing.json").read_text(encoding="utf-8")) if (pack / "listing.json").exists() else None
    kept = sum(1 for i in images if i["kept"] == 1)
    rejected = sum(1 for i in images if i["kept"] == 0)
    judged = kept + rejected
    return {
        "title": title or niche["name"], "status": niche.get("status"), "note": note, "brief": niche.get("brief") or "",
        "images": images, "mockups": mockups, "sheet": _uri(_shrink(Image.open(sheet), MOCKUP_PX), "JPEG") if sheet.exists() else None,
        "listing": listing, "generated": len(images), "kept": kept, "rejected": rejected,
        "accepted_pct": round(100 * kept / judged) if judged else None,
        "cost": round(db.get_total_spend(), 3),
    }


_TEMPLATE = """<title>Sticker Pack Review</title>
<style>
/* Layout: sticky background switcher on top, one section per pack, a responsive grid of sticker cards inside. */
:root {
  --bg: #eef1f4; --surface: #ffffff; --ink: #14202b; --muted: #55646f; --line: #d5dbe1;
  --accent: #0f766e; --keep: #17803d; --keep-bg: #dff5e6; --reject: #b4322b; --reject-bg: #fbe3e1;
  --stage-white: #ffffff; --stage-dark: #1c2430; --stage-blush: #f6d9df; --code: #e6eaee;
  --check-a: #ffffff; --check-b: #d9dee3;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0f151c; --surface: #18212b; --ink: #e7edf3; --muted: #93a2af; --line: #2a3541;
  --accent: #4fd1c5; --keep: #5fd18a; --keep-bg: #173324; --reject: #ff8a80; --reject-bg: #3a1c1a;
  --stage-white: #ffffff; --stage-dark: #0b1016; --stage-blush: #4a2c35; --code: #212c37;
  --check-a: #2a3541; --check-b: #212b36; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #0f151c; --surface: #18212b; --ink: #e7edf3; --muted: #93a2af; --line: #2a3541;
  --accent: #4fd1c5; --keep: #5fd18a; --keep-bg: #173324; --reject: #ff8a80; --reject-bg: #3a1c1a;
  --stage-white: #ffffff; --stage-dark: #0b1016; --stage-blush: #4a2c35; --code: #212c37;
  --check-a: #2a3541; --check-b: #212b36; color-scheme: dark; }
body { background: var(--bg); color: var(--ink); font: 16px/1.5 "Avenir Next", "Segoe UI", system-ui, sans-serif; padding-inline: 16px; padding-block: 0 64px; }
.wrap { max-width: 1100px; margin-inline: auto; }
h1 { font-size: clamp(26px, 5vw, 36px); margin: 28px 0 4px; line-height: 1.15; }
.lede { color: var(--muted); margin: 0 0 8px; max-width: 62ch; }
.bar { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--bg); padding-block: 10px; border-bottom: 1px solid var(--line); margin-top: 14px; display: flex; gap: 8px 14px; align-items: center; flex-wrap: wrap; }
.bar .label { font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--surface); }
.seg button { font: inherit; font-size: 14px; border: 0; background: none; color: var(--ink); padding: 7px 14px; cursor: pointer; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button[aria-pressed="true"] { background: var(--accent); color: #fff; }
.seg button:focus-visible { outline: 3px solid var(--accent); outline-offset: -3px; }
.bar nav { display: flex; gap: 6px 16px; flex-wrap: wrap; margin-left: auto; font-size: 14px; }
.bar nav a { color: var(--muted); text-decoration: none; } .bar nav a:hover { color: var(--accent); text-decoration: underline; }
section.set { margin-top: 44px; }
section.set h2 { font-size: 24px; margin: 0 0 2px; }
.sub { color: var(--muted); margin: 0 0 14px; max-width: 70ch; }
.tiles { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 16px; min-width: 110px; }
.tile b { display: block; font-size: 22px; font-variant-numeric: tabular-nums; } .tile span { font-size: 12px; color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 14px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; margin: 0; overflow: hidden; min-width: 0; }
.card.rejected { border-color: var(--reject); }
.stage { aspect-ratio: 1; display: grid; place-items: center; background: var(--stage-white); }
[data-bg="dark"] .stage { background: var(--stage-dark); } [data-bg="blush"] .stage { background: var(--stage-blush); }
[data-bg="check"] .stage { background: repeating-conic-gradient(var(--check-b) 0% 25%, var(--check-a) 0% 50%) 50% / 24px 24px; }
.stage img { max-width: 92%; max-height: 92%; }
.stage .none { color: var(--muted); font-size: 14px; padding: 16px; text-align: center; }
figcaption { padding: 10px 12px 12px; }
.row { display: flex; gap: 8px; justify-content: space-between; align-items: baseline; }
.row b { font-size: 15px; min-width: 0; overflow-wrap: anywhere; }
.pill { font-size: 12px; font-weight: 700; padding: 2px 9px; border-radius: 99px; white-space: nowrap; font-variant-numeric: tabular-nums; background: var(--code); color: var(--muted); }
.kept .pill { background: var(--keep-bg); color: var(--keep); } .rejected .pill { background: var(--reject-bg); color: var(--reject); }
.why { color: var(--muted); font-size: 14px; margin: 6px 0 0; }
details summary { cursor: pointer; font-size: 13px; color: var(--muted); margin-top: 8px; } details p { font-size: 13px; color: var(--muted); margin: 6px 0 0; overflow-wrap: anywhere; }
h3 { font-size: 17px; margin: 26px 0 10px; }
.mocks { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.mocks img { width: 100%; border-radius: 10px; border: 1px solid var(--line); display: block; }
.listing { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 18px; margin-top: 14px; }
.listing pre { white-space: pre-wrap; font: inherit; margin: 6px 0 0; overflow-wrap: anywhere; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; } .tags span { background: var(--code); border-radius: 6px; padding: 2px 8px; font-size: 13px; }
.note { background: var(--surface); border-left: 4px solid var(--accent); padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; color: var(--muted); font-size: 15px; }
</style>
<div class="wrap" id="root" data-bg="white">
<h1>Sticker Pack Review</h1>
<p class="lede">Every sticker on the backgrounds you will actually see it on. Switch backgrounds to check the cutouts for halos or see-through patches.</p>
<div class="bar">
  <span class="label">Background</span>
  <div class="seg" role="group" aria-label="Preview background">
    <button type="button" data-bg="white" aria-pressed="true">White</button>
    <button type="button" data-bg="dark" aria-pressed="false">Dark</button>
    <button type="button" data-bg="blush" aria-pressed="false">Colour</button>
    <button type="button" data-bg="check" aria-pressed="false">Transparent</button>
  </div>
  <nav aria-label="Packs">{% for d in sets %}<a href="#set-{{ loop.index }}">{{ d.title }}</a>{% endfor %}</nav>
</div>
{% for d in sets %}
<section class="set" id="set-{{ loop.index }}">
  <h2>{{ d.title }}</h2>
  <p class="sub">{% if d.status %}Status: {{ d.status }}. {% endif %}{{ d.brief }}</p>
  {% if d.note %}<div class="note">{{ d.note }}</div>{% endif %}
  <div class="tiles">
    <div class="tile"><b>{{ d.generated }}</b><span>generated</span></div>
    {% if d.accepted_pct is not none %}<div class="tile"><b>{{ d.kept }}</b><span>kept</span></div>
    <div class="tile"><b>{{ d.rejected }}</b><span>rejected</span></div>
    <div class="tile"><b>{{ d.accepted_pct }}%</b><span>accepted</span></div>{% endif %}
    <div class="tile"><b>${{ '%.2f' % d.cost }}</b><span>spend logged for this run</span></div>
  </div>
  <div class="grid">
  {% for i in d.images %}
    <figure class="card {{ 'kept' if i.kept == 1 else ('rejected' if i.kept == 0 else '') }}">
      <div class="stage">{% if i.uri %}<img src="{{ i.uri }}" alt="{{ i.name }}">{% else %}<div class="none">No image (blocked or missing)</div>{% endif %}</div>
      <figcaption>
        <div class="row"><b>{{ i.name }}</b><span class="pill">{% if i.score is not none %}{{ '%.1f' % i.score }} {% endif %}{{ 'kept' if i.kept == 1 else ('rejected' if i.kept == 0 else 'unjudged') }}</span></div>
        {% if i.reason %}<p class="why">{{ i.reason }}</p>{% endif %}
        <details><summary>Prompt</summary><p>{{ i.prompt }}</p></details>
      </figcaption>
    </figure>
  {% endfor %}
  </div>
  {% if d.mockups %}<h3>Listing images</h3><div class="mocks">{% for m in d.mockups %}<img src="{{ m }}" alt="Listing image {{ loop.index }}">{% endfor %}{% if d.sheet %}<img src="{{ d.sheet }}" alt="Sticker sheet">{% endif %}</div>{% endif %}
  {% if d.listing %}<div class="listing"><b>{{ d.listing.title }}</b> <span class="pill">${{ '%.2f' % d.listing.price_usd }}</span>
    <div class="tags">{% for t in d.listing.tags %}<span>{{ t }}</span>{% endfor %}</div>
    <details><summary>Description</summary><pre>{{ d.listing.description }}</pre></details></div>{% endif %}
</section>
{% endfor %}
</div>
<script>
(function () {
  var root = document.getElementById("root"), KEY = "sticker-review-bg";
  var buttons = Array.prototype.slice.call(document.querySelectorAll(".seg button"));
  function apply(bg) { root.setAttribute("data-bg", bg); buttons.forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-bg") === bg ? "true" : "false"); }); }
  try { var saved = localStorage.getItem(KEY); if (saved) apply(saved); } catch (e) {}
  buttons.forEach(function (b) { b.addEventListener("click", function () { var bg = b.getAttribute("data-bg"); apply(bg); try { localStorage.setItem(KEY, bg); } catch (e) {} }); });
})();
</script>
"""

_env = Environment(autoescape=select_autoescape(default=True))


def render_review(datasets: list[dict], standalone: bool = True) -> str:
    """HTML for one or more packs. standalone=True wraps it as a complete page; False gives the artifact fragment."""
    body = _env.from_string(_TEMPLATE).render(sets=datasets)
    if not standalone:
        return body
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>' + body + "</body></html>"


def write_review(niche_id: int) -> Path:
    """Write review.html next to the pack. Opens in any browser; nothing else is needed."""
    out = config.output_dir() / f"niche_{niche_id}" / "review.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_review([collect_review(niche_id)]), encoding="utf-8")
    return out
