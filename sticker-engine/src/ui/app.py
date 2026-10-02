"""
Sticker Studio: a local screen for making and reviewing stickers. Run it with:  python -m src.ui.app

It runs on your own computer and opens in your browser at http://127.0.0.1:8081 (not reachable from other machines).
The Gemini key in your .env is used to draw the images, to write and rank ideas, to optimize your prompt, and, if you
leave it on, to score the pictures. Look and layout: see src/ui/theme.py.
"""

import base64
import io
import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from nicegui import app, events, run, ui
from PIL import Image

from src.cli import estimate_usd
from src.generator import ideas as idea_writer
from src.generator import optimize
from src.generator.prompt_builder import compose_prompt
from src.packaging import export
from src.shared import config, design
from src.storage import db
from src.ui import gallery, jobs, keep, opener, settings, theme
from src.ui.prompts import parse_prompts

PORT = 8081
MAX_REFERENCES = 8
MAX_UPLOAD_BYTES = 12_000_000
RUNNER = jobs.JobRunner()

REFERENCE_CHOICES = {
    "style": "Style guide (copy the look, not the subject)",
    "subject": "Subject (redraw what is in the image)",
    "character": "Character (keep it consistent)",
}
BG_STYLE = {
    "white": "background:#ffffff",
    "dark": "background:#1c2430",
    "colour": "background:#f6d9df",
    "bw": "background:#ffffff",                     # shown in greyscale: how it prints in black and white
    "check": "background:repeating-conic-gradient(#d9dee3 0% 25%, #ffffff 0% 50%) 50% / 22px 22px",
}
STATE_LABEL = {
    "passed": ("Passed", "positive"), "low": ("Low score", "warning"), "unchecked": ("Not checked", "grey"),
    "waiting": ("Waiting for check", "grey"), "duplicate": ("Looks the same as another", "grey"), "blocked": ("Gemini returned no image", "negative"), "missing": ("File missing", "negative"),
}
IMAGE_SIZES = {"4K": "4K ultra-high-definition (default, costs the most)", "2K": "2K high definition", "1K": "1K draft (cheapest, for testing)"}


def file_url(path: str) -> str:
    """URL the browser can load for a file inside the output folder."""
    rel = Path(path).resolve().relative_to(config.output_dir().resolve())
    return f"/files/{rel.as_posix()}?v={int(Path(path).stat().st_mtime)}"


def preview_url(path: str, px: int = 720) -> str:
    """A small copy of a picture for the grid. 4K originals stay untouched on disk and open in full from the menu."""
    src = Path(path)
    thumb = src.parent / ".thumbs" / f"{src.stem}.png"
    try:
        if not thumb.exists() or thumb.stat().st_mtime < src.stat().st_mtime:
            thumb.parent.mkdir(exist_ok=True)
            with Image.open(src) as im:
                im = im.convert("RGBA")
                im.thumbnail((px, px), Image.LANCZOS)
                im.save(thumb, "PNG")
        return file_url(str(thumb))
    except Exception:
        return file_url(path)


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


def dropped_ids(args) -> list[int]:
    """Image ids from a drop event: the page sends the text that was set when the drag began."""
    raw = args if isinstance(args, (list, tuple)) else [args]
    out = []
    for item in raw:
        try:
            out.append(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    return out


@ui.page("/")
def index():
    prefs = settings.load()
    upload_dir = config.output_dir() / "uploads" / uuid.uuid4().hex[:8]
    st = {"refs": [], "niche_id": RUNNER.niche_id, "was_running": False, "signature": None, "ideas": None, "inputs": [],
          "keep": [], "items": {}, "before_optimize": None}
    ui.dark_mode(True)
    ui.add_css(theme.CSS)
    ui.add_head_html(theme.HEAD_JS)
    ui.add_body_html(theme.BODY_HTML)

    with ui.element("div").classes("studio"):
        # ── header: name, then the running tallies ─────────────────────────────────
        with ui.element("div").classes("glass span-all").style("display:flex;align-items:center;justify-content:space-between;padding:14px 26px"):
            with ui.row().classes("logo items-baseline gap-0"):
                ui.label("Sticker")
                ui.label("Studio").style("color:var(--cyan)")
            with ui.row().classes("items-center gap-8"):
                with ui.element("div").classes("stat"):
                    ui.label("Packs made").classes("cap")
                    v_packs = ui.label("0").classes("v")
                with ui.element("div").classes("stat"):
                    ui.label("Stickers made").classes("cap")
                    v_images = ui.label("0").classes("v")
                with ui.element("div").classes("stat"):
                    ui.label("Cost this run").classes("cap")
                    v_session = ui.label("$0.00").classes("v")
                with ui.element("div").classes("stat hot"):
                    ui.label("Spent today").classes("cap")
                    with ui.row().classes("items-baseline gap-2"):
                        v_today = ui.label("$0.00").classes("v")
                        v_cap = ui.label("").classes("of")
                key_chip = ui.chip("", icon="key").props("dense outline")
                ui.button(icon="settings", on_click=lambda: settings_dialog.open()).props("flat round color=white")

        # ── left column ─────────────────────────────────────────────────────────────
        with ui.element("div").classes("lane"):
            with ui.element("div").classes("glass"):
                ui.label("Global style").classes("h2")
                with ui.column().classes("pad w-full gap-2"):
                    preset_pick = ui.select(preset_options(), value=prefs["style_preset"]).props("outlined dense").classes("w-full")
                    style_text = ui.textarea().props('outlined dense autogrow input-style="min-height:96px;max-height:170px"').classes("w-full mono")
                    style_note = ui.label("").classes("mut")
                    with ui.row().classes("gap-2"):
                        ui.button("Save", on_click=lambda: save_style(False)).props("flat no-caps").classes("pill")
                        ui.button("Save as…", on_click=lambda: save_style(True)).props("flat no-caps").classes("pill")
                        ui.button("Reset", on_click=lambda: load_style(preset_pick.value)).props("flat no-caps").classes("pill")

            with ui.element("div").classes("glass"):
                ui.label("Prompt").classes("h2")
                with ui.column().classes("pad w-full gap-2"):
                    text = ui.textarea(placeholder="e.g. halloween related, funny sarcastic, adult racy iconic spoof").props(
                        'outlined autogrow input-style="min-height:96px;max-height:220px"').classes("w-full")
                    text.value = ""
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        optimize_btn = ui.button("Optimize", icon="auto_fix_high", on_click=lambda: optimize_click()).props("flat no-caps").classes("pill vio grow")
                        undo_btn = ui.button("Undo", icon="undo", on_click=lambda: undo_optimize()).props("flat no-caps").classes("pill")
                        undo_btn.set_visibility(False)
                    ui.label("Optimize rewrites your prompt to be sharper (about $0.01). You can undo it.").classes("mut")
                    ui.upload(label="Or load a .md / .txt file", auto_upload=True, max_files=1,
                              on_upload=lambda e: load_text_file(e)).props('accept=".md,.markdown,.txt" flat bordered hide-upload-btn').classes("w-full")
                    paste_box = ui.column().classes("w-full gap-1")
                    with paste_box:
                        mode = ui.toggle({"smart": "Smart", "single": "One prompt", "lines": "One per line"}, value=prefs["mode"]).classes("seg").props("no-caps unelevated")
                        detected = ui.label("").classes("mut")
                        with ui.expansion("Show the prompts it found").classes("w-full text-sm"):
                            found_box = ui.column().classes("gap-1")

                    ui.label("Reference images").classes("h2").style("padding:8px 0 0")
                    ui.upload(label="Add, drag in, or paste with Ctrl+V", multiple=True, auto_upload=True,
                              on_upload=lambda e: add_reference(e)).props('accept="image/*" flat bordered hide-upload-btn').classes("w-full")
                    refs_row = ui.row().classes("gap-2 items-center")
                    ref_mode = ui.select(REFERENCE_CHOICES, value=prefs["reference_mode"], label="How should they be used?").props("outlined dense").classes("w-full")

                    ui.label("How many").classes("h2").style("padding:8px 0 0")
                    with ui.row().classes("w-full no-wrap gap-3 items-stretch"):
                        with ui.column().classes("gap-2").style("flex:1;min-width:0"):
                            count = ui.number("Stickers per pack", value=prefs["count"], min=1, max=jobs.MAX_PROMPTS, step=1, precision=0).props("outlined dense").classes("w-full")
                            variants = ui.number("Versions of each", value=prefs["variants"], min=1, max=8, step=1, precision=0).props("outlined dense").classes("w-full")
                            batches = ui.number("Number of packs", value=prefs["batches"], min=1, max=10, step=1, precision=0).props("outlined dense").classes("w-full")
                        with ui.column().classes("gap-1").style("flex:1;min-width:0;border:1px solid var(--panel-glass-border);border-radius:12px;padding:8px 12px;background:rgba(0,0,0,.25)"):
                            write_ideas_sw = ui.switch("Write ideas for me", value=prefs["write_ideas"])
                            apply_style = ui.switch("Apply style", value=prefs["apply_style"])
                            qa = ui.switch("AI quality check", value=prefs["qa"])
                            build_pack = ui.switch("Build Etsy pack", value=prefs["build_pack"])
                            size_pick = ui.select({"4K": "4K ultra", "2K": "2K", "1K": "1K draft"}, value=prefs["image_size"], label="Picture size").props("outlined dense").classes("w-full")
                    total_label = ui.label("").style("font-weight:600")
                    cost_label = ui.label("").classes("mut")
                    ideas_chip = ui.button("", icon="lightbulb", on_click=lambda: ideas_dialog.open()).props("flat no-caps").classes("pill")
                    ideas_chip.set_visibility(False)
                    with ui.row().classes("w-full gap-2 no-wrap"):
                        write_btn = ui.button("Write ideas", icon="lightbulb", on_click=lambda: write_ideas_click()).props("flat no-caps").classes("pill grow")
                        create_btn = ui.button("Create stickers", icon="auto_awesome", on_click=lambda: create()).props("flat no-caps").classes("pill pri grow")
                        stop_btn = ui.button("Stop", icon="stop", on_click=lambda: RUNNER.stop()).props("flat no-caps").classes("pill neg")

        # ── right column ────────────────────────────────────────────────────────────
        with ui.element("div").classes("lane"):
            with ui.element("div").classes("glass"):
                ui.label("Progress").classes("h2")
                with ui.column().classes("pad w-full gap-1"):
                    status = ui.label("Ready.").style("font-weight:600")
                    made_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                    made_text = ui.label("").classes("mut")
                    judged_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                    judged_text = ui.label("").classes("mut")
                    log = ui.log(max_lines=500).classes("w-full").style("height:120px")

            with ui.element("div").classes("mid"):
                with ui.element("div").classes("glass"):
                    with ui.row().classes("w-full items-center no-wrap pad").style("padding-top:8px"):
                        ui.label("Preview").classes("h2").style("padding:0")
                        ui.element("div").classes("grow")
                        show = ui.toggle({"all": "All", "passed": "Passed", "low": "Low", "duplicate": "Dupes", "unchecked": "Unchecked"}, value="all").classes("seg").props("no-caps unelevated dense")
                        history = ui.select({}, on_change=lambda e: pick_history(e.value)).props("outlined dense options-dense").classes("w-56")
                    ui.label("Drag a sticker into Keep & build (or press +). Right-click one to open it in another program.").classes("mut pad")
                    with ui.element("div").classes("scroll"):
                        grid = ui.element("div").classes("grid")

                with ui.element("div").classes("glass"):
                    with ui.row().classes("w-full items-center justify-between pad").style("padding-top:8px"):
                        ui.label("Keep & build").classes("h2").style("padding:0")
                        keep_count = ui.label("0 kept").classes("mut")
                    drop_zone = ui.element("div").classes("drop")
                    with drop_zone:
                        keep_box = ui.element("div")
                    with ui.column().classes("pad w-full gap-2").style("padding-top:10px"):
                        export_fmt = ui.select(export.FORMATS, value=prefs["export_format"], label="Export as").props("outlined dense").classes("w-full")
                        ui.button("Export", icon="download", on_click=lambda: export_click()).props("flat no-caps").classes("pill pri w-full")
                        export_note = ui.label("").classes("mut")
                        pack_btn = ui.button("Build pack", icon="inventory_2", on_click=lambda: build_pack_click()).props("flat no-caps").classes("pill w-full")
                        ui.button("Open folder", icon="folder_open", on_click=lambda: open_folder()).props("flat no-caps").classes("pill w-full")

            with ui.element("div").classes("glass"):
                with ui.row().classes("w-full justify-center items-center gap-4").style("padding:14px 16px 4px"):
                    view = ui.toggle({"auto": "Cutout", "original": "Original"}, value="auto").classes("seg").props("no-caps unelevated")
                    bg = ui.toggle({"white": "White", "dark": "Dark", "colour": "Colour", "bw": "Black / White", "check": "Transparent"},
                                   value=prefs["background"] if prefs["background"] in BG_STYLE else "white").classes("seg").props("no-caps unelevated")

    # ── dialogs ────────────────────────────────────────────────────────────────────
    with ui.dialog() as ideas_dialog, ui.card().classes("glass").style("width:780px;max-width:94vw;max-height:88vh;overflow:auto"):
        ui.label("Ideas").classes("h2")
        ui.label("Ranked by the AI's own critique, which is only its opinion. Draw: tick the stickers you want made (any group). "
                 "Best: tick the ones you think are the best, so the writer learns your taste for this style. You can edit any line."
                 ).classes("mut pad")
        marked_label = ui.label("").classes("pad").style("font-weight:600;color:var(--cyan)")
        learn_label = ui.label("").classes("mut pad")
        ideas_box = ui.column().classes("w-full gap-1 pad")
        with ui.row().classes("justify-end w-full pad"):
            ui.button("Use these ideas", on_click=lambda: ideas_dialog.close()).props("flat no-caps").classes("pill pri")

    with ui.dialog() as settings_dialog, ui.card().classes("glass").style("min-width:460px;max-width:92vw"):
        ui.label("Settings").classes("h2")
        with ui.column().classes("pad w-full gap-2"):
            cap_input = ui.number("Daily spending cap ($)", value=prefs["daily_cap"] or config.daily_budget_usd(), min=0.1, step=0.5).props("outlined dense")
            ui.label("The run stops by itself when this is reached. It only counts what this program spends.").classes("mut")
            apps_input = ui.textarea("Extra programs for the right-click menu (one per line)",
                                     value="\n".join(prefs["open_with"]), placeholder='Photoshop = "C:\\Program Files\\Adobe\\Photoshop.exe" {file}').props("outlined").classes("w-full")
            ui.label("Form:  Name = path-to-program {file}").classes("mut")
            key_input = ui.input("Gemini API key", password=True, placeholder="Paste to set or replace").props("outlined dense").classes("w-full")
            ui.label("Saved to the .env file next to this program. It never leaves this computer except to talk to Google.").classes("mut")
            with ui.row().classes("justify-end w-full"):
                ui.button("Close", on_click=lambda: settings_dialog.close()).props("flat no-caps").classes("pill")
                ui.button("Save", on_click=lambda: save_settings()).props("flat no-caps").classes("pill pri")

    # ── behaviour ────────────────────────────────────────────────────────────────────
    def load_style(stem):
        try:
            style_text.value = design.load_preset(stem)
        except ValueError as e:
            style_text.value = ""
            ui.notify(str(e), type="warning")
        refresh_summary()

    async def save_style(as_new: bool):
        name = preset_pick.value
        if as_new:
            with ui.dialog() as dlg, ui.card().classes("glass"):
                ui.label("Name for the new style").classes("h2")
                name_in = ui.input(placeholder="e.g. retro diner signs").props("outlined dense").classes("w-72 pad")
                with ui.row().classes("justify-end w-full pad"):
                    ui.button("Cancel", on_click=lambda: dlg.submit(None)).props("flat no-caps").classes("pill")
                    ui.button("Save", on_click=lambda: dlg.submit(name_in.value)).props("flat no-caps").classes("pill pri")
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

    def parsed():
        return parse_prompts(text.value or "", mode.value)

    def numbers() -> tuple[int, int, int]:
        """(stickers in each pack, versions of each, number of packs)"""
        v = int(variants.value or 1)
        if write_ideas_sw.value:
            return int(count.value or 1), v, int(batches.value or 1)
        return min(len(parsed().prompts), int(count.value or 1)), v, 1

    def refresh_summary(*_):
        os.environ["GEMINI_IMAGE_SIZE"] = size_pick.value or "4K"
        writer = bool(write_ideas_sw.value)
        paste_box.set_visibility(not writer)
        write_btn.set_visibility(writer)
        batches.set_enabled(writer)
        p = parsed()
        detected.set_text(p.how)
        style_md = style_text.value or ""
        problems = design.validate_style(style_md) if style_md.strip() else []
        rules = design.rules_for(style_md or None)
        style_note.set_text(("⚠ " + "; ".join(problems)) if problems else rules.describe())
        style_note.style("color:var(--danger)" if problems else "color:var(--mut)")
        direction = design.direction_for(style_md=style_md or None)
        found_box.clear()
        with found_box:
            for i, pr in enumerate(p.prompts[:60], 1):
                final = compose_prompt(pr, direction, apply_style=bool(apply_style.value), rules=rules)
                ui.label(f"{i}. {final[:400]}{'…' if len(final) > 400 else ''}").classes("text-xs")
        per_pack, v, packs = numbers()
        total = jobs.image_total(per_pack, v, packs)
        if total == 0:
            total_label.set_text("Nothing to make yet.")
            cost_label.set_text("")
        else:
            total_label.set_text(f"{per_pack} sticker{'s' if per_pack != 1 else ''} × {v} version{'s' if v != 1 else ''} × "
                                 f"{packs} pack{'s' if packs != 1 else ''} = {total} image{'s' if total != 1 else ''}")
            est = estimate_usd(per_pack * packs, v, qa.value, build_pack.value)
            note = f" Writing the ideas first costs about ${0.02 * packs:.2f} more." if writer and not st["ideas"] else ""
            cost_label.set_text(f"Estimated image cost: up to ${est:.2f} at {size_pick.value}.{note}")
        create_btn.set_enabled(total > 0 and not RUNNER.running and (not writer or bool(st["ideas"])))

    def refresh_header():
        s = RUNNER.stats()
        v_packs.set_text(str(s["packs"]))
        v_images.set_text(str(s["images"]))
        v_session.set_text(f"${s['spent']:.2f}")
        v_today.set_text(f"${db.get_today_spend():.2f}")
        v_cap.set_text(f"of ${config.daily_budget_usd():.2f}")
        has_key = bool(os.getenv("GEMINI_API_KEY"))
        key_chip.set_text("Gemini key found" if has_key else "No Gemini key: open Settings")
        key_chip.props("color=positive" if has_key else "color=negative")

    async def load_text_file(e: events.UploadEventArguments):
        data = (await e.file.read()).decode("utf-8", errors="replace")
        text.value = (text.value + "\n\n" + data).strip() if (text.value or "").strip() else data
        ui.notify(f"Loaded {getattr(e.file, 'name', 'file')}")
        e.sender.reset()

    # ── optimize ─────────────────────────────────────────────────────────────────────
    async def optimize_click():
        original = (text.value or "").strip()
        if not original:
            ui.notify("Write a prompt first.", type="warning")
            return
        if not os.getenv("GEMINI_API_KEY"):
            ui.notify("No Gemini key found. Open Settings and paste it.", type="negative")
            return
        db.init_db_sync()
        optimize_btn.disable()
        status.set_text("Optimizing your prompt…")
        try:
            better = await run.io_bound(optimize.optimize_brief, original, style_text.value or None)
        except Exception as e:
            ui.notify(f"Could not optimize: {e}", type="negative", multi_line=True, timeout=9000)
            return
        finally:
            optimize_btn.enable()
            status.set_text("Ready.")
        st["before_optimize"] = original
        text.value = better
        undo_btn.set_visibility(True)
        refresh_header()

    def undo_optimize():
        if st["before_optimize"] is not None:
            text.value = st["before_optimize"]
        st["before_optimize"] = None
        undo_btn.set_visibility(False)

    # ── reference images ─────────────────────────────────────────────────────────────
    def refresh_refs():
        refs_row.clear()
        with refs_row:
            for path in st["refs"]:
                with ui.element("div").classes("relative"):
                    ui.image(file_url(path)).style("width:56px;height:56px;border-radius:8px").props("fit=cover")
                    ui.button(icon="close", on_click=lambda p=path: remove_ref(p)).props("flat round dense size=xs").classes("absolute").style("top:-6px;right:-6px;background:rgba(5,7,10,.85);color:var(--danger)")
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

    # ── ideas ────────────────────────────────────────────────────────────────────────
    def idea_row(idea, pack_rows):
        """One idea: Draw (make this sticker), Best (a sign of your taste, remembered per style), its text and the AI's critique."""
        with ui.row().classes("w-full items-start no-wrap gap-2"):
            with ui.column().classes("gap-0").style("flex:0 0 74px"):
                draw_box = ui.checkbox("Draw", value=idea.chosen).props("dense").tooltip("Make this sticker")
                best_box = ui.checkbox("Best", value=False).props("dense").tooltip("This is one of the best: teaches the idea writer your taste")
            with ui.column().classes("gap-0 grow").style("min-width:0"):
                inp = ui.input(value=idea.text).props("outlined dense").classes("w-full")
                parts = ", ".join(f"{k.replace('_', ' ')} {v:.0f}" for k, v in idea.scores.items())
                ui.label(f"AI score {idea.score:.1f} ({parts}). Weakness: {idea.weakness or 'none given'}").classes("mut")
        draw_box.on_value_change(lambda _e: update_marked_count())
        best_box.on_value_change(lambda _e: update_marked_count())
        pack_rows.append({"draw": draw_box, "best": best_box, "inp": inp, "chosen": idea.chosen, "idea": idea})

    def update_marked_count():
        rows = [r for row in st["inputs"] for r in row]
        draw = sum(1 for r in rows if r["draw"].value and (r["inp"].value or "").strip())
        best = sum(1 for r in rows if r["best"].value)
        marked_label.set_text(f"{draw} will be drawn · {best} marked best")

    def render_ideas():
        ideas_box.clear()
        st["inputs"] = []
        data = st["ideas"]
        ideas_chip.set_visibility(bool(data))
        if not data:
            return
        total = sum(len(data.chosen(i)) for i in range(len(data.packs)))
        ideas_chip.set_text(f"{total} ideas ready ({len(data.packs)} pack{'s' if len(data.packs) != 1 else ''}): review")
        counts = db.count_idea_feedback(preset_pick.value)
        learn_label.set_text(f"Learning from your earlier marks for this style: {counts['liked']} best, {counts['passed']} passed over."
                             if counts["liked"] + counts["passed"] else
                             "No marks saved for this style yet. Mark the best ideas and the writer will learn your taste.")
        with ideas_box:
            for w in data.warnings:
                ui.label("⚠ " + w).classes("text-xs").style("color:var(--danger)")
            for pi, cands in enumerate(data.packs):
                pack_rows = []
                if len(data.packs) > 1:
                    ui.label(f"Pack {pi + 1}").classes("h2").style("padding:8px 0 0")
                ui.label("The AI's picks (ticked to draw)").classes("mut").style("padding-top:4px")
                for idea in (c for c in cands if c.chosen):
                    idea_row(idea, pack_rows)
                rest = [c for c in cands if not c.chosen]
                if rest:
                    ui.label("Ideas that did not make the cut").classes("mut").style("padding-top:10px")
                    for idea in rest:
                        idea_row(idea, pack_rows)
                st["inputs"].append(pack_rows)
        update_marked_count()

    async def write_ideas_click():
        brief = (text.value or "").strip()
        if not brief:
            ui.notify("Write a prompt first.", type="warning")
            return
        if not os.getenv("GEMINI_API_KEY"):
            ui.notify("No Gemini key found. Open Settings and paste it.", type="negative")
            return
        db.init_db_sync()
        write_btn.disable()
        status.set_text("Writing and ranking ideas…")
        try:
            result = await run.io_bound(idea_writer.write_ideas, brief, int(count.value or 1), int(batches.value or 1),
                                        style_text.value or None, None, None, preset_pick.value)
        except Exception as e:
            ui.notify(f"Could not write ideas: {e}", type="negative", multi_line=True, timeout=10000)
            return
        finally:
            write_btn.enable()
            status.set_text("Ready.")
        st["ideas"] = result
        render_ideas()
        refresh_header()
        refresh_summary()
        ideas_dialog.open()

    def collect_packs() -> list[list[str]]:
        if write_ideas_sw.value:
            return [idea_writer.choose_for_drawing([(r["draw"].value, r["inp"].value or "") for r in row])
                    for row in st["inputs"]]
        return [parsed().prompts[:max(1, int(count.value or 1))]]

    def remember_marks():
        """Save the ideas you marked Best (and the rest you saw), per style, so the next ideas are written with your taste in mind."""
        if not write_ideas_sw.value:
            return
        for row in st["inputs"]:
            if not any(r["best"].value for r in row):
                continue                                    # no Best marks in this pack: no opinion to learn from
            db.save_idea_feedback(preset_pick.value, [
                {"idea": (r["inp"].value or "").strip(), "liked": bool(r["best"].value), "ai_score": r["idea"].score,
                 "ai_chosen": r["chosen"]} for r in row if (r["inp"].value or "").strip()
            ], brief=(text.value or "").strip())

    # ── making stickers ──────────────────────────────────────────────────────────────
    async def create():
        packs = [pk for pk in collect_packs() if pk]
        if not packs:
            ui.notify("Write the ideas first, or paste at least one finished sticker.", type="warning")
            return
        v = int(variants.value or 1)
        total = jobs.image_total(sum(len(pk) for pk in packs), v)
        est = estimate_usd(sum(len(pk) for pk in packs), v, qa.value, build_pack.value)
        sizes = ", ".join(str(len(pk)) for pk in packs)
        with ui.dialog() as dlg, ui.card().classes("glass").style("max-width:520px"):
            ui.label(f"Create exactly {total} image{'s' if total != 1 else ''}?").classes("h2")
            with ui.column().classes("pad gap-2"):
                ui.label(f"{len(packs)} pack{'s' if len(packs) != 1 else ''} ({sizes} stickers) × {v} version{'s' if v != 1 else ''} "
                         f"of each sticker, at {size_pick.value}.")
                ui.label(f"Estimated cost: up to ${est:.2f}. This uses your Gemini credit.").style("font-weight:600;color:var(--cyan)")
                ui.label("You will see every image that is made. Packs run one after another. You can press Stop at any time; "
                         "images already made are kept.").classes("mut")
            with ui.row().classes("justify-end w-full pad"):
                ui.button("Cancel", on_click=lambda: dlg.submit(False)).props("flat no-caps").classes("pill")
                ui.button("Create", on_click=lambda: dlg.submit(True)).props("flat no-caps").classes("pill pri")
        if not await dlg:
            return
        settings.save({**prefs, "mode": mode.value, "apply_style": apply_style.value, "qa": qa.value, "build_pack": build_pack.value,
                       "count": int(count.value or 1), "variants": v, "batches": int(batches.value or 1),
                       "write_ideas": bool(write_ideas_sw.value), "image_size": size_pick.value, "export_format": export_fmt.value,
                       "background": bg.value, "reference_mode": ref_mode.value, "style_preset": preset_pick.value})
        name = " ".join((text.value or "stickers").split())[:40] or "stickers"
        remember_marks()
        try:
            nid = RUNNER.start_packs(name=name, packs=packs, variants=v, references=list(st["refs"]),
                                     reference_mode=ref_mode.value, apply_style=bool(apply_style.value), qa=bool(qa.value),
                                     build_pack=bool(build_pack.value), style_md=style_text.value or None)
        except jobs.CannotStart as e:
            ui.notify(str(e), type="negative", multi_line=True, timeout=8000)
            return
        select_niche(nid)
        log.clear()
        refresh_history(select=nid)

    # ── runs, keep list, export and build ────────────────────────────────────────────
    def select_niche(nid):
        st["niche_id"] = nid
        st["keep"] = keep.load(nid) if nid else []
        st["signature"] = None

    def refresh_history(select=None):
        rows = [n for n in db.list_niches(30) if n["source"] == "request"]
        history.set_options({n["id"]: f"#{n['id']}  {n['name']}  [{n['status']}]" for n in rows}, value=select or st["niche_id"])

    def pick_history(niche_id):
        if niche_id and niche_id != st["niche_id"] and not RUNNER.running:
            select_niche(niche_id)
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

    def add_to_keep(image_id: int):
        nid = st["niche_id"]
        if not nid or image_id not in st["items"] or image_id in st["keep"]:
            return
        st["keep"] = keep.add(nid, image_id)
        after_keep_change()

    def remove_from_keep(image_id: int):
        st["keep"] = keep.remove(st["niche_id"], image_id)
        after_keep_change()

    def toggle_keep(image_id: int):
        (remove_from_keep if image_id in st["keep"] else add_to_keep)(image_id)

    def after_keep_change():
        st["signature"] = None
        render_keep()
        render_gallery()

    def on_drop(e: events.GenericEventArguments):
        for image_id in dropped_ids(e.args):
            add_to_keep(image_id)

    drop_zone.on("dragover", js_handler="(e) => { e.preventDefault(); e.currentTarget.classList.add('over'); }")
    drop_zone.on("dragleave", js_handler="(e) => { e.currentTarget.classList.remove('over'); }")
    drop_zone.on("drop", on_drop, js_handler="(e) => { e.preventDefault(); e.currentTarget.classList.remove('over'); emit(e.dataTransfer.getData('text/plain')); }")

    def render_keep():
        keep_box.clear()
        ids = [i for i in st["keep"] if i in st["items"]]
        keep_count.set_text(f"{len(ids)} kept")
        export_note.set_text("Exports exactly the stickers in this panel." if ids else "Nothing kept: Export uses every sticker that passed.")
        with keep_box:
            if not ids:
                ui.label("Drag stickers here to keep them").classes("hint")
                return
            with ui.element("div").classes("kgrid"):
                for image_id in ids:
                    it = st["items"][image_id]
                    path = it["path"]
                    with ui.element("div").classes("kitem").style(BG_STYLE[bg.value]).classes("bwfilter" if bg.value == "bw" else ""):
                        if path:
                            ui.image(preview_url(path, 240)).props("fit=contain ratio=1").style("width:100%")
                        ui.button(icon="close", on_click=lambda i=image_id: remove_from_keep(i)).props("flat round dense size=xs").classes("x")

    async def export_click():
        nid = st["niche_id"]
        if not nid:
            ui.notify("No run selected yet.", type="warning")
            return
        name = (db.get_niche(nid) or {}).get("name", "stickers")
        ids = [i for i in st["keep"] if i in st["items"]]
        try:
            path = await run.io_bound(export.export_run, nid, export_fmt.value, name, None, ids or None)
        except export.NothingToExport as e:
            ui.notify(str(e), type="warning")
            return
        except Exception as e:
            ui.notify(f"Export failed: {e}", type="negative", multi_line=True)
            return
        settings.save({**settings.load(), "export_format": export_fmt.value})
        ui.notify(f"Saved {path.name}", type="positive")
        do_action({"name": "Show in folder", "kind": "reveal"}, str(path))

    def build_pack_click():
        ids = [i for i in st["keep"] if i in st["items"]]
        try:
            RUNNER.build_pack(st["niche_id"], ids or None)
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

    # ── the picture grid ─────────────────────────────────────────────────────────────
    def render_gallery():
        nid = st["niche_id"]
        items = gallery.gallery_items(nid) if nid else []
        st["items"] = {i["id"]: i for i in items}
        shown = gallery.filter_items(items, show.value)
        signature = (nid, show.value, view.value, bg.value, tuple(st["keep"]),
                     tuple((i["id"], i["state"], i["score"], i["path"], i["best"]) for i in shown))
        if signature == st["signature"]:
            return
        st["signature"] = signature
        actions = menu_actions(settings.load())
        multi = (db.get_niche(nid) or {}).get("options", {}).get("variants", 1) > 1 if nid else False
        grid.clear()
        with grid:
            if not shown:
                ui.label("Nothing to show yet." if not items else "No images match this filter.").classes("mut")
            for it in shown:
                label, color = STATE_LABEL[it["state"]]
                path = it["original"] if (view.value == "original" and it["original"]) else it["path"]
                kept = it["id"] in st["keep"]
                tile = ui.element("div").classes("tile" + (" kept" if kept else "")).props("draggable=true")
                tile.on("dragstart", js_handler=f"(e) => {{ e.dataTransfer.setData('text/plain', '{it['id']}'); e.dataTransfer.effectAllowed = 'copy'; }}")
                with tile:
                    ui.button(icon="check" if kept else "add", on_click=lambda i=it["id"]: toggle_keep(i)).props("flat round dense size=sm").classes("addbtn")
                    with ui.element("div").classes("stage" + (" bwfilter" if bg.value == "bw" else "")).style(BG_STYLE[bg.value]):
                        if path:
                            ui.image(preview_url(path)).props("fit=contain ratio=1").style("width:100%")
                        else:
                            ui.label(label).classes("mut text-center p-3")
                    with ui.column().classes("gap-1 meta"):
                        with ui.row().classes("items-center gap-1"):
                            score_text = f"{label} {it['score']:.1f}" if it["score"] is not None and it["state"] in ("passed", "low") else label
                            ui.badge(score_text, color=color)
                            if it["best"]:
                                ui.badge("★ Best of this prompt", color="amber").props("text-color=black")
                            if multi:
                                ui.badge(f"V{it['variant'] + 1}", color="grey").props("outline")
                        ui.label(it["prompt"][:60] + ("…" if len(it["prompt"]) > 60 else "")).style("font-weight:600")
                        if it["reason"] and it["state"] in ("passed", "low", "blocked"):
                            ui.label(it["reason"]).classes("mut")
                    ui.tooltip(it["prompt"]).props("max-width=420px")
                    if path:
                        with ui.context_menu():
                            for action in actions:
                                ui.menu_item(action["name"], on_click=lambda a=action, p=path: do_action(a, p))
                            if it["original"] and path != it["original"]:
                                ui.menu_item("Open the original (white background)",
                                             on_click=lambda p=it["original"]: do_action({"name": "Open", "kind": "default"}, p))
                            ui.separator()
                            ui.menu_item("Add to Keep" if not kept else "Remove from Keep", on_click=lambda i=it["id"]: toggle_keep(i))
                            ui.menu_item("Copy file path", on_click=lambda p=path: copy_path(p))
        render_keep()

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
        if running and RUNNER.niche_id and RUNNER.niche_id != st["niche_id"]:
            select_niche(RUNNER.niche_id)           # a queue moved on to its next pack: follow it
            refresh_history(select=RUNNER.niche_id)
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
        outcome_words = {"review": "Finished. Every image is in the preview.", "ready": "Finished. Pack and upload kit built.", "published": "Finished.",
                         "cancelled": "Stopped. Everything made so far is kept.", "paused": "Paused at today's spending cap.",
                         "failed": "Stopped because of an error (see the log)."}
        status.set_text("Working… you can press Stop at any time." if running else outcome_words.get(RUNNER.outcome, "Ready."))
        create_btn.set_enabled(not running and (jobs.image_total(*numbers()) > 0) and (not write_ideas_sw.value or bool(st["ideas"])))
        stop_btn.set_enabled(running)
        pack_btn.set_enabled(bool(nid) and not running and (db.get_niche(nid) or {}).get("status") == "REVIEW")
        refresh_header()
        if st["was_running"] and not running:
            refresh_history(select=nid)
        st["was_running"] = running

    for control in (text, mode, count, variants, batches, qa, build_pack, style_text, apply_style, write_ideas_sw, size_pick):
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
    select_niche(st["niche_id"])
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
