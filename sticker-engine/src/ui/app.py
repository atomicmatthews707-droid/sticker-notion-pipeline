"""
Sticker Studio: a local screen for making and reviewing stickers. Run it with:  python -m src.ui.app

It runs on your own computer and opens in your browser at http://127.0.0.1:8081 (not reachable from other machines).
You write or paste the prompts yourself; nothing here asks an AI to invent prompts. The Gemini key in your .env is
used to draw the images and, if you leave it on, to score them.
"""

import base64
import io
import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from nicegui import app, events, ui
from PIL import Image

from src.cli import estimate_usd
from src.generator.prompt_builder import compose_prompt
from src.shared import config, design
from src.storage import db
from src.ui import gallery, jobs, opener, settings
from src.ui.prompts import parse_prompts

PORT = 8081
MAX_REFERENCES = 8
MAX_UPLOAD_BYTES = 12_000_000
RUNNER = jobs.JobRunner()

PASTE_JS = """
<script>
document.addEventListener('paste', function (e) {
  var items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (var i = 0; i < items.length; i++) {
    var it = items[i];
    if (it.kind === 'file' && it.type.indexOf('image/') === 0) {
      var f = it.getAsFile(), r = new FileReader();
      r.onload = function () { emitEvent('pasted_image', { data: r.result }); };
      r.readAsDataURL(f);
      e.preventDefault();
    }
  }
});
</script>
"""

REFERENCE_CHOICES = {
    "style": "Style guide (copy the look, not the subject)",
    "subject": "Subject (redraw what is in the image)",
    "character": "Character (keep it consistent)",
}
BG_STYLE = {
    "white": "background:#ffffff",
    "dark": "background:#1c2430",
    "colour": "background:#f6d9df",
    "check": "background:repeating-conic-gradient(#d9dee3 0% 25%, #ffffff 0% 50%) 50% / 22px 22px",
}
STATE_LABEL = {
    "passed": ("Passed", "positive"), "low": ("Low score", "warning"), "unchecked": ("Not checked", "grey"),
    "waiting": ("Waiting for check", "grey"), "duplicate": ("Looks the same as another", "grey"), "blocked": ("Gemini returned no image", "negative"), "missing": ("File missing", "negative"),
}


def file_url(path: str) -> str:
    """URL the browser can load for a file inside the output folder."""
    rel = Path(path).resolve().relative_to(config.output_dir().resolve())
    return f"/files/{rel.as_posix()}?v={int(Path(path).stat().st_mtime)}"


def save_env_key(key: str, env_path: Path | None = None) -> None:
    """Write GEMINI_API_KEY into .env, replacing an existing line."""
    env_path = env_path or (config.ROOT / ".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    lines = [ln for ln in lines if not ln.startswith("GEMINI_API_KEY=")] + [f"GEMINI_API_KEY={key.strip()}"]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def preset_options() -> dict:
    return {stem: f"{title}" for stem, title in design.list_presets().items()}


def menu_actions(prefs: dict) -> list[dict]:
    actions = opener.builtin_actions()
    for line in prefs.get("open_with", []):
        try:
            actions.insert(len(actions) - 1, opener.parse_custom_app(line))
        except ValueError:
            continue
    return actions


@ui.page("/")
def index():
    prefs = settings.load()
    upload_dir = config.output_dir() / "uploads" / uuid.uuid4().hex[:8]
    st = {"refs": [], "niche_id": RUNNER.niche_id, "was_running": False, "signature": None}
    ui.dark_mode()
    ui.add_head_html(PASTE_JS)
    ui.add_css(".q-uploader__list { display: none; } .q-uploader { max-height: 64px; }")

    # ── header ──────────────────────────────────────────────────────────────────
    with ui.header().classes("items-center justify-between"):
        ui.label("Sticker Studio").classes("text-xl font-bold")
        with ui.row().classes("items-center gap-3"):
            spend = ui.label("").classes("text-sm")
            key_chip = ui.chip("", icon="key").props("dense")
            ui.button(icon="settings", on_click=lambda: settings_dialog.open()).props("flat round color=white")

    with ui.row().classes("w-full no-wrap items-start gap-4"):
        # ── left: inputs ─────────────────────────────────────────────────────────
        with ui.column().classes("gap-3").style("width:440px;max-width:100%;flex-shrink:0"):
            with ui.card().classes("w-full"):
                ui.label("1. Global style").classes("text-lg font-bold")
                ui.label("The look applied to every subject. Pick a preset, edit it here, or save your own as a new "
                         "preset (files live in the styles/ folder).").classes("text-sm text-grey-7")
                preset_pick = ui.select(preset_options(), value=prefs["style_preset"], label="Style preset").classes("w-full")
                style_text = ui.textarea(label="Style (Markdown, editable)").props(
                    'outlined autogrow input-style="min-height:140px;font-family:monospace;font-size:12px"').classes("w-full")
                style_note = ui.label("").classes("text-xs")
                with ui.row().classes("gap-2 items-center"):
                    ui.button("Save", icon="save", on_click=lambda: save_style(False)).props("flat dense")
                    ui.button("Save as new…", icon="library_add", on_click=lambda: save_style(True)).props("flat dense")
                    ui.button("Reset", icon="undo", on_click=lambda: load_style(preset_pick.value)).props("flat dense")

            with ui.card().classes("w-full"):
                ui.label("2. Subjects").classes("text-lg font-bold")
                ui.label("What to make, e.g. “autumn cozy vibes”, “vintage garage tools”, “office humor phrases”. Paste "
                         "prompts or a Markdown file; your words are kept as written. Style comes from the card above."
                         ).classes("text-sm text-grey-7")
                text = ui.textarea(placeholder="One subject, a list (- or 1.), paragraphs, or a whole .md file").props(
                    'outlined autogrow input-style="min-height:200px"').classes("w-full")
                text.value = ""
                ui.upload(label="Or load a .md / .txt file", auto_upload=True, max_files=1,
                          on_upload=lambda e: load_text_file(e)).props('accept=".md,.markdown,.txt" flat bordered hide-upload-btn').classes("w-full")
                mode = ui.toggle({"smart": "Smart", "single": "One prompt", "lines": "One per line"}, value=prefs["mode"])
                detected = ui.label("").classes("text-sm font-medium")
                with ui.expansion("Show the prompts it found").classes("w-full text-sm"):
                    found_box = ui.column().classes("gap-1")
                apply_style = ui.switch("Apply the global style to each subject", value=prefs["apply_style"])
                ui.label("White background and cutout are added only when the style asks for them (its “background” and “cutout” lines).").classes("text-xs text-grey-7")

            with ui.card().classes("w-full"):
                ui.label("Reference images (optional)").classes("text-lg font-bold")
                ui.label("Upload, drag in, or paste with Ctrl+V. They are sent to the image model with every prompt.").classes("text-sm text-grey-7")
                ui.upload(label="Add reference images", multiple=True, auto_upload=True,
                          on_upload=lambda e: add_reference(e)).props('accept="image/*" flat bordered hide-upload-btn').classes("w-full")
                refs_row = ui.row().classes("gap-2 items-center")
                ref_mode = ui.select(REFERENCE_CHOICES, value=prefs["reference_mode"], label="How should they be used?").classes("w-full")

            with ui.card().classes("w-full"):
                ui.label("How many").classes("text-lg font-bold")
                with ui.row().classes("w-full no-wrap gap-3"):
                    count = ui.number("Pack count", value=prefs["count"], min=1, max=jobs.MAX_PROMPTS, step=1, precision=0).classes("grow")
                    variants = ui.number("Variations per subject", value=prefs["variants"], min=1, max=8, step=1, precision=0).classes("grow")
                total_label = ui.label("").classes("font-bold")
                cost_label = ui.label("").classes("text-sm")
                ui.label("Every image made is shown to you. Variations are extra tries of the same prompt with different seeds, "
                         "so you pick; nothing is hidden or thrown away.").classes("text-xs text-grey-7")
                qa = ui.switch("AI quality check (adds a score to each image, about $0.016 each)", value=prefs["qa"])
                build_pack = ui.switch("Also build the Etsy/Gumroad pack and listing text (uses a little text AI)", value=prefs["build_pack"])
                with ui.row().classes("gap-2"):
                    create_btn = ui.button("Create stickers", icon="auto_awesome", on_click=lambda: create()).props("color=primary")
                    stop_btn = ui.button("Stop", icon="stop", on_click=lambda: RUNNER.stop()).props("color=negative outline")

        # ── right: progress and review ────────────────────────────────────────────
        with ui.column().classes("gap-3 grow").style("min-width:0"):
            with ui.card().classes("w-full"):
                ui.label("3. Progress").classes("text-lg font-bold")
                status = ui.label("Ready.").classes("font-medium")
                made_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                made_text = ui.label("").classes("text-xs text-grey-7")
                judged_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                judged_text = ui.label("").classes("text-xs text-grey-7")
                log = ui.log(max_lines=500).classes("w-full").style("height:240px")

            with ui.card().classes("w-full"):
                ui.label("4. Review").classes("text-lg font-bold")
                ui.label("Right-click any sticker to open it in another program on this computer.").classes("text-sm text-grey-7")
                with ui.row().classes("items-center gap-3"):
                    show = ui.toggle({"all": "All", "passed": "Passed", "low": "Low score", "duplicate": "Duplicates", "unchecked": "Not checked"}, value="all")
                    view = ui.toggle({"auto": "Cutout", "original": "Original"}, value="auto")
                    bg = ui.toggle({"white": "White", "dark": "Dark", "colour": "Colour", "check": "Transparent"}, value=prefs["background"])
                with ui.row().classes("items-center gap-3"):
                    history = ui.select({}, label="Earlier runs", on_change=lambda e: pick_history(e.value)).classes("w-72")
                    ui.button("Open this run's folder", icon="folder_open", on_click=lambda: open_folder()).props("flat")
                    pack_btn = ui.button("Build the pack from these", icon="inventory_2", on_click=lambda: build_pack_click()).props("flat")
                grid = ui.element("div").classes("w-full").style(
                    "display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px")

    # ── settings dialog ──────────────────────────────────────────────────────────
    with ui.dialog() as settings_dialog, ui.card().style("min-width:460px;max-width:90vw"):
        ui.label("Settings").classes("text-lg font-bold")
        cap_input = ui.number("Daily spending cap ($)", value=prefs["daily_cap"] or config.daily_budget_usd(), min=0.1, step=0.5)
        ui.label("The run stops by itself when this is reached. It only counts what this program spends.").classes("text-xs text-grey-7")
        apps_input = ui.textarea("Extra programs for the right-click menu (one per line)",
                                 value="\n".join(prefs["open_with"]), placeholder='Photoshop = "C:\\Program Files\\Adobe\\Photoshop.exe" {file}').props("outlined").classes("w-full")
        ui.label("Form:  Name = path-to-program {file}").classes("text-xs text-grey-7")
        key_input = ui.input("Gemini API key", password=True, placeholder="Paste to set or replace").classes("w-full")
        ui.label("Saved to the .env file next to this program. It never leaves this computer except to talk to Google.").classes("text-xs text-grey-7")
        with ui.row().classes("justify-end w-full"):
            ui.button("Close", on_click=lambda: settings_dialog.close()).props("flat")
            ui.button("Save", on_click=lambda: save_settings()).props("color=primary")

    # ── behaviour ────────────────────────────────────────────────────────────────
    def load_style(stem):
        try:
            style_text.value = design.load_preset(stem)
        except ValueError as e:
            style_text.value = ""
            ui.notify(str(e), type="warning")
        refresh_summary()

    def save_style(as_new: bool):
        async def go():
            name = preset_pick.value
            if as_new:
                with ui.dialog() as dlg, ui.card():
                    ui.label("Name for the new style").classes("font-bold")
                    name_in = ui.input(placeholder="e.g. retro diner signs").classes("w-72")
                    with ui.row().classes("justify-end w-full"):
                        ui.button("Cancel", on_click=lambda: dlg.submit(None)).props("flat")
                        ui.button("Save", on_click=lambda: dlg.submit(name_in.value)).props("color=primary")
                name = await dlg
                if not name:
                    return
            try:
                stem = design.save_preset(name, style_text.value or "", overwrite=not as_new)
            except ValueError as e:
                ui.notify(str(e), type="negative")
                return
            preset_pick.set_options(preset_options(), value=stem)
            ui.notify(f"Saved styles/{stem}.md")
        return go()

    def parsed():
        return parse_prompts(text.value or "", mode.value)

    def numbers() -> tuple[int, int, int]:
        n_prompts = len(parsed().prompts)
        c = int(count.value or 1)
        v = int(variants.value or 1)
        return n_prompts, c, v

    def refresh_summary(*_):
        p = parsed()
        detected.set_text(p.how)
        style_md = style_text.value or ""
        problems = design.validate_style(style_md) if style_md.strip() else []
        rules = design.rules_for(style_md or None)
        style_note.set_text(("⚠ " + "; ".join(problems)) if problems else rules.describe())
        style_note.classes(replace="text-xs " + ("text-negative" if problems else "text-grey-7"))
        direction = design.direction_for(style_md=style_md or None)
        found_box.clear()
        with found_box:
            for i, pr in enumerate(p.prompts[:60], 1):
                final = compose_prompt(pr, direction, apply_style=bool(apply_style.value), rules=rules)
                ui.label(f"{i}. {final[:400]}{'…' if len(final) > 400 else ''}").classes("text-xs")
        n_prompts, c, v = numbers()
        used = min(n_prompts, c)
        total = jobs.image_total(n_prompts, c, v)
        if n_prompts == 0:
            total_label.set_text("Nothing to make yet.")
            cost_label.set_text("")
        else:
            note = f" (you asked for {c} but gave {n_prompts} prompt{'s' if n_prompts != 1 else ''}; raise Variations to make more of the same prompt)" if c > n_prompts else ""
            total_label.set_text(f"This will create exactly {total} image{'s' if total != 1 else ''}: {used} prompt{'s' if used != 1 else ''} × {v} variation{'s' if v != 1 else ''}.{note}")
            est = estimate_usd(used, v, qa.value, build_pack.value)
            cost_label.set_text(f"Estimated cost: up to ${est:.2f} of your Gemini credit. {spend_text()}")
        create_btn.set_enabled(n_prompts > 0 and not RUNNER.running)

    def spend_text() -> str:
        return jobs.spend_line()

    def refresh_header():
        spend.set_text(spend_text())
        has_key = bool(os.getenv("GEMINI_API_KEY"))
        key_chip.set_text("Gemini key found" if has_key else "No Gemini key: open Settings")
        key_chip.props("color=positive" if has_key else "color=negative")

    async def load_text_file(e: events.UploadEventArguments):
        data = (await e.file.read()).decode("utf-8", errors="replace")
        text.value = (text.value + "\n\n" + data).strip() if (text.value or "").strip() else data
        ui.notify(f"Loaded {getattr(e.file, 'name', 'file')}")
        e.sender.reset()

    def refresh_refs():
        refs_row.clear()
        with refs_row:
            for path in st["refs"]:
                with ui.element("div").classes("relative"):
                    ui.image(file_url(path)).style("width:64px;height:64px;border-radius:6px").props("fit=cover")
                    ui.button(icon="close", on_click=lambda p=path: remove_ref(p)).props("flat round dense size=xs color=negative").classes("absolute").style("top:-6px;right:-6px;background:white")
        ref_mode.set_visibility(bool(st["refs"]))

    def store_reference(data: bytes, name: str):
        if len(st["refs"]) >= MAX_REFERENCES:
            ui.notify(f"At most {MAX_REFERENCES} reference images.", type="warning")
            return
        if len(data) > MAX_UPLOAD_BYTES:
            ui.notify("That image is over 12 MB.", type="negative")
            return
        try:
            Image.open(io.BytesIO(data)).verify()
        except Exception:
            ui.notify("That file is not an image I can read.", type="negative")
            return
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / f"ref_{len(st['refs']) + 1}_{uuid.uuid4().hex[:4]}.png"
        Image.open(io.BytesIO(data)).convert("RGBA").save(path, "PNG")
        st["refs"].append(str(path))
        refresh_refs()
        refresh_summary()

    async def add_reference(e: events.UploadEventArguments):
        store_reference(await e.file.read(), getattr(e.file, "name", "image"))
        e.sender.reset()

    def on_paste(e: events.GenericEventArguments):
        data = (e.args or {}).get("data", "")
        if "," in data:
            store_reference(base64.b64decode(data.split(",", 1)[1]), "pasted")

    ui.on("pasted_image", on_paste)

    def remove_ref(path: str):
        st["refs"] = [p for p in st["refs"] if p != path]
        refresh_refs()

    async def create():
        p = parsed()
        n_prompts, c, v = numbers()
        if not p.prompts:
            ui.notify("Paste at least one prompt first.", type="warning")
            return
        used = min(n_prompts, c)
        total = jobs.image_total(n_prompts, c, v)
        est = estimate_usd(used, v, qa.value, build_pack.value)
        with ui.dialog() as dlg, ui.card():
            ui.label(f"Create exactly {total} image{'s' if total != 1 else ''}?").classes("text-lg font-bold")
            ui.label(f"{used} prompt{'s' if used != 1 else ''} × {v} variation{'s' if v != 1 else ''}. "
                     f"Estimated cost: up to ${est:.2f}. This uses your Gemini credit.")
            ui.label("You will see every image that is made. You can press Stop at any time; images already made are kept.").classes("text-sm text-grey-7")
            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=lambda: dlg.submit(False)).props("flat")
                ui.button("Create", on_click=lambda: dlg.submit(True)).props("color=primary")
        if not await dlg:
            return
        settings.save({**prefs, "mode": mode.value, "apply_style": apply_style.value, "qa": qa.value, "build_pack": build_pack.value,
                       "count": c, "variants": v, "background": bg.value, "reference_mode": ref_mode.value,
                       "style_preset": preset_pick.value})
        try:
            nid = RUNNER.start(name=p.name, prompts=p.prompts, count=c, variants=v, references=list(st["refs"]),
                               reference_mode=ref_mode.value, apply_style=bool(apply_style.value), qa=bool(qa.value),
                               build_pack=bool(build_pack.value), style_md=style_text.value or None)
        except jobs.CannotStart as e:
            ui.notify(str(e), type="negative", multi_line=True, timeout=8000)
            return
        st["niche_id"] = nid
        st["signature"] = None
        log.clear()
        refresh_history(select=nid)

    def refresh_history(select=None):
        rows = [n for n in db.list_niches(30) if n["source"] == "request"]
        history.set_options({n["id"]: f"#{n['id']}  {n['name']}  [{n['status']}]" for n in rows}, value=select or st["niche_id"])

    def pick_history(niche_id):
        if niche_id and niche_id != st["niche_id"] and not RUNNER.running:
            st["niche_id"] = niche_id
            st["signature"] = None
            render_gallery()

    def open_folder():
        nid = st["niche_id"]
        if not nid:
            ui.notify("No run selected yet.", type="warning")
            return
        folder = config.output_dir() / f"niche_{nid}" / "raw"
        files = sorted(folder.glob("*.png"))
        if not files:
            ui.notify("Nothing has been made in this run yet.", type="warning")
            return
        do_action({"name": "Show in folder", "kind": "reveal"}, str(files[0]))

    def build_pack_click():
        try:
            RUNNER.build_pack(st["niche_id"])
        except (jobs.CannotStart, TypeError) as e:
            ui.notify(str(e), type="negative", multi_line=True)

    def do_action(action: dict, path: str):
        try:
            ui.notify(opener.run_action(action, path))
        except opener.NotAllowed as e:
            ui.notify(str(e), type="negative")

    def copy_path(path: str):
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(path)})")
        ui.notify("File path copied")

    def render_gallery():
        nid = st["niche_id"]
        items = gallery.gallery_items(nid) if nid else []
        shown = gallery.filter_items(items, show.value)
        signature = (nid, show.value, view.value, bg.value, tuple((i["id"], i["state"], i["score"], i["path"], i["best"]) for i in shown))
        if signature == st["signature"]:
            return
        st["signature"] = signature
        actions = menu_actions(settings.load())
        multi = (db.get_niche(nid) or {}).get("options", {}).get("variants", 1) > 1 if nid else False
        grid.clear()
        with grid:
            if not shown:
                ui.label("Nothing to show yet." if not items else "No images match this filter.").classes("text-grey-7")
            for it in shown:
                label, color = STATE_LABEL[it["state"]]
                path = it["original"] if (view.value == "original" and it["original"]) else it["path"]
                with ui.card().tight().classes("w-full"):
                    with ui.element("div").style(f"{BG_STYLE[bg.value]};width:100%;aspect-ratio:1;display:grid;place-items:center"):
                        if path:
                            ui.image(file_url(path)).props("fit=contain ratio=1").style("width:100%")
                        else:
                            ui.label(label).classes("text-sm text-grey-7 p-3 text-center")
                    with ui.column().classes("gap-1 p-2"):
                        with ui.row().classes("items-center gap-1"):
                            score_text = f"{label} {it['score']:.1f}" if it["score"] is not None and it["state"] in ("passed", "low") else label
                            ui.badge(score_text, color=color)
                            if it["best"]:
                                ui.badge("★ Best score of this prompt", color="amber").props("text-color=black")
                            if multi:
                                ui.badge(f"Variation {it['variant'] + 1}", color="grey").props("outline")
                        ui.label(it["prompt"][:70] + ("…" if len(it["prompt"]) > 70 else "")).classes("text-xs font-medium")
                        if it["reason"] and it["state"] in ("passed", "low", "blocked"):
                            ui.label(it["reason"]).classes("text-xs text-grey-7")
                    ui.tooltip(it["prompt"]).props("max-width=420px")
                    if path:
                        with ui.context_menu():
                            for action in actions:
                                ui.menu_item(action["name"], on_click=lambda a=action, p=path: do_action(a, p))
                            if it["original"] and path != it["original"]:
                                ui.menu_item("Open the original (white background)",
                                             on_click=lambda p=it["original"]: do_action({"name": "Open", "kind": "default"}, p))
                            ui.separator()
                            ui.menu_item("Copy file path", on_click=lambda p=path: copy_path(p))

    def save_settings():
        lines = [ln.strip() for ln in (apps_input.value or "").splitlines() if ln.strip()]
        for ln in lines:
            try:
                opener.parse_custom_app(ln)
            except ValueError as e:
                ui.notify(f"{ln!r}: {e}", type="negative")
                return
        cap = float(cap_input.value or config.daily_budget_usd())
        settings.save({**settings.load(), "daily_cap": cap, "open_with": lines})
        os.environ["BUDGET_DAILY_LIMIT_USD"] = str(cap)
        if key_input.value:
            save_env_key(key_input.value)
            os.environ["GEMINI_API_KEY"] = key_input.value.strip()
            key_input.value = ""
        settings_dialog.close()
        st["signature"] = None
        refresh_header()
        refresh_summary()
        render_gallery()
        ui.notify("Settings saved")

    def tick():
        for line in RUNNER.new_log_lines():
            log.push(line)
        running = RUNNER.running
        nid = st["niche_id"]
        if nid:
            exp = gallery.expected_images(nid)
            pr = gallery.progress(nid, exp)
            total = exp or max(pr["total"], 1)
            made_bar.set_value(min(1.0, pr["made"] / total))
            made_text.set_text(f"Images made: {pr['made']} of {exp if exp else '?'}")
            judged_bar.set_value(min(1.0, pr["judged"] / total) if qa.value else 0)
            judged_text.set_text(f"Images checked: {pr['judged']} of {pr['made']}" if qa.value else "AI quality check is off")
            if running or st["was_running"] or st["signature"] is None:
                render_gallery()
        outcome_words = {"review": "Finished. Every image is below.", "ready": "Finished. Pack and upload kit built.", "published": "Finished.",
                         "cancelled": "Stopped. Everything made so far is kept below.", "paused": "Paused at today's spending cap.",
                         "failed": "Stopped because of an error (see the log)."}
        status.set_text("Working… you can press Stop at any time." if running else outcome_words.get(RUNNER.outcome, "Ready."))
        create_btn.set_enabled(bool(parsed().prompts) and not running)
        stop_btn.set_enabled(running)
        pack_btn.set_visibility(bool(nid) and not running and (db.get_niche(nid) or {}).get("status") == "REVIEW")
        if st["was_running"] and not running:
            refresh_header()
            refresh_history(select=nid)
        st["was_running"] = running

    for control in (text, mode, count, variants, qa, build_pack, style_text, apply_style):
        control.on_value_change(refresh_summary)
    for control in (show, view, bg):
        control.on_value_change(lambda _e: (st.update(signature=None), render_gallery()))
    preset_pick.on_value_change(lambda e: load_style(e.value))
    if preset_pick.value not in preset_pick.options and preset_pick.options:
        preset_pick.value = next(iter(preset_pick.options))
    load_style(preset_pick.value)
    refresh_header()
    refresh_refs()
    refresh_summary()
    refresh_history()
    if st["niche_id"] is None:
        recent = [n for n in db.list_niches(1) if n["source"] == "request"]
        st["niche_id"] = recent[0]["id"] if recent else None
        history.set_value(st["niche_id"])
    render_gallery()
    ui.timer(0.8, tick)


def main() -> None:
    load_dotenv(config.ROOT / ".env")
    db.init_db_sync()
    cap = settings.load()["daily_cap"]
    if cap and not os.getenv("BUDGET_DAILY_LIMIT_USD"):
        os.environ["BUDGET_DAILY_LIMIT_USD"] = str(cap)
    out = config.output_dir()
    out.mkdir(parents=True, exist_ok=True)
    app.add_static_files("/files", str(out))
    print(f"Sticker Studio is starting. Open http://127.0.0.1:{PORT} if the browser does not open by itself.")
    print(f"Pictures are saved in: {out}")
    ui.run(host="127.0.0.1", port=PORT, title="Sticker Studio", reload=False, show=os.getenv("NO_BROWSER") != "1", dark=None)


if __name__ in {"__main__", "__mp_main__"}:
    main()
