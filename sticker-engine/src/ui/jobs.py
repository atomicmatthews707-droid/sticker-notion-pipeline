"""Runs one pack at a time in a background thread and keeps a plain-language log for the screen."""

import logging
import os
import threading
import time
from collections import deque
from typing import Optional

from src.shared import cancel, config
from src.storage import db

MAX_IMAGES = 100          # a hard ceiling on one run, whatever is typed into the boxes
MAX_PROMPTS = 60


class CannotStart(Exception):
    """The run was refused before anything was spent. The message says why, in plain words."""


class _LogHandler(logging.Handler):
    def __init__(self, sink: deque):
        super().__init__(level=logging.INFO)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(f"{time.strftime('%H:%M:%S')}  {record.getMessage()}")


def image_total(stickers: int, variants: int = 1, packs: int = 1) -> int:
    """Exactly how many images a run will make: stickers per pack x variations of each x number of packs."""
    return max(0, stickers) * max(1, variants) * max(1, packs)


class JobRunner:
    def __init__(self):
        self.log: deque = deque(maxlen=2000)
        self._seen = 0
        self._thread: Optional[threading.Thread] = None
        self.niche_id: Optional[int] = None
        self.outcome: Optional[str] = None
        self.lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def new_log_lines(self) -> list[str]:
        """Lines added since the last call, so the screen only appends what is new."""
        lines = list(self.log)
        fresh = lines[self._seen:] if self._seen <= len(lines) else lines
        self._seen = len(lines)
        return fresh

    def say(self, message: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')}  {message}")

    def start(self, *, name: str, prompts: list[str], count: int, variants: int, references: list[str],
              reference_mode: str, apply_style: bool, qa: bool, build_pack: bool, style_md: Optional[str] = None) -> int:
        """One pack from your own finished prompts (the first `count` of them)."""
        return self.start_packs(name=name, packs=[prompts[:max(1, count)]], variants=variants, references=references,
                                reference_mode=reference_mode, apply_style=apply_style, qa=qa, build_pack=build_pack,
                                style_md=style_md)

    def start_packs(self, *, name: str, packs: list[list[str]], variants: int, references: list[str], reference_mode: str,
                    apply_style: bool, qa: bool, build_pack: bool, style_md: Optional[str] = None) -> int:
        """Run packs one after another (a queue of one at a time). Returns the first pack's id."""
        packs = [p for p in packs if p]
        with self.lock:
            if self.running:
                raise CannotStart("A run is already in progress. Wait for it to finish or press Stop.")
            if not os.getenv("GEMINI_API_KEY"):
                raise CannotStart("No Gemini API key found. Add GEMINI_API_KEY to the .env file (see Settings) and restart.")
            if not packs:
                raise CannotStart("There is nothing to make yet.")
            if any(len(p) > MAX_PROMPTS for p in packs):
                raise CannotStart(f"A pack can have at most {MAX_PROMPTS} stickers.")
            total = image_total(sum(len(p) for p in packs), variants)
            if total > MAX_IMAGES:
                raise CannotStart(f"That would make {total} images; the limit is {MAX_IMAGES} per run.")
            from src import main

            db.init_db_sync()
            if not main._check_budget():
                raise CannotStart("Today's spending cap is already reached. Raise it in Settings if you want to continue.")
            options = {"variants": max(1, variants), "references": references, "reference_mode": reference_mode,
                       "apply_style": apply_style, "verbatim": True, "qa": qa, "build_pack": build_pack, "style_md": style_md}
            titles = [name if len(packs) == 1 else f"{name} - pack {i}" for i in range(1, len(packs) + 1)]
            first = self._new_niche(titles[0], packs[0], options)
            self.niche_id, self.outcome = first, None
            self.log.clear()
            self._seen = 0
            per_pack = f" in {len(packs)} packs" if len(packs) > 1 else ""
            self.say(f"Starting. This will create exactly {total} image{'s' if total != 1 else ''}{per_pack}.")
            self._thread = threading.Thread(target=self._run_queue, args=(first, titles, packs, options), daemon=True, name="sticker-run")
            self._thread.start()
            return first

    @staticmethod
    def _new_niche(title: str, prompts: list[str], options: dict) -> int:
        suffix = ""
        for n in range(1, 50):
            try:
                return db.queue_request(f"{title}{suffix}", subjects=prompts, count=len(prompts), options=options)
            except ValueError:
                suffix = f" ({n + 1})"
        raise CannotStart("Could not create the run.")

    def _run_queue(self, first_id: int, titles: list[str], packs: list[list[str]], options: dict) -> None:
        for i, (title, prompts) in enumerate(zip(titles, packs)):
            if i:
                if cancel.is_set() or self.outcome in ("cancelled", "paused", "failed"):
                    self.say(f"Skipped the {len(titles) - i} remaining pack{'s' if len(titles) - i != 1 else ''}.")
                    break
                try:
                    nid = self._new_niche(title, prompts, options)
                except CannotStart as e:
                    self.say(str(e))
                    break
            else:
                nid = first_id
            self.niche_id = nid
            if len(titles) > 1:
                self.say(f"--- Pack {i + 1} of {len(titles)}: {title} ---")
            self._run(nid, (db.get_niche(nid) or {}).get("name", title))

    def _run(self, niche_id: int, name: str, start_status: Optional[str] = None) -> None:
        from src import main

        handler = _LogHandler(self.log)
        root = logging.getLogger("src")
        root.addHandler(handler)
        cancel.reset()
        try:
            self.outcome = main._run_pipeline_for_niche(niche_id, name, start_status)
            spent = db.get_niche_spend(niche_id)
            messages = {
                "review": "Finished. Every image is below for you to look at.",
                "published": "Finished, and the pack is ready.",
                "ready": "Finished. The pack and upload kit are ready.",
                "cancelled": "Stopped. Everything made so far is kept and shown below.",
                "paused": "Paused: today's spending cap was reached. Everything made so far is kept and shown below.",
                "failed": "Stopped because of an error. Anything already made is still shown below.",
            }
            self.say(messages.get(self.outcome, f"Finished ({self.outcome})."))
            self.say(f"Spent on this run: ${spent:.2f}")
            if self.outcome == "failed":
                self.say("Reason: " + ((db.get_niche(niche_id) or {}).get("error_msg") or "unknown").splitlines()[0])
        except Exception as e:  # never let the thread die silently
            self.outcome = "failed"
            self.say(f"Stopped because of an unexpected error: {e}")
        finally:
            root.removeHandler(handler)

    def stop(self) -> None:
        if self.running:
            cancel.request()
            self.say("Stop requested. Finishing the image in progress, then stopping. Nothing already made is lost.")

    def build_pack(self, niche_id: int) -> None:
        """Turn a reviewed run into the Etsy/Gumroad pack (sheet, PDF, zip, listing text)."""
        with self.lock:
            if self.running:
                raise CannotStart("A run is already in progress.")
            niche = db.get_niche(niche_id)
            if not niche or not db.get_images_for_niche(niche_id, kept=True):
                raise CannotStart("There are no passed or unchecked stickers in this run to build a pack from.")
            db.update_niche_options(niche_id, build_pack=True)
            self.niche_id, self.outcome = niche_id, None
            self.say("Building the pack from the stickers that passed.")
            self._thread = threading.Thread(target=self._run, args=(niche_id, niche["name"], "PACKAGING"), daemon=True, name="sticker-pack")
            self._thread.start()


def spend_line() -> str:
    return f"${db.get_today_spend():.2f} of ${config.daily_budget_usd():.2f} today"
