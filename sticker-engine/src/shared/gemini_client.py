"""Gemini access for text, JSON and image generation, with spend tracking and a hard daily cap.

Every call is priced from the response's usage metadata and written to the spend_log table, so the
budget survives restarts. Model IDs come from env vars; prices live in one table below (verify them
against the pricing page before relying on them).
"""

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from src.shared import config
from src.shared.logger import get_logger
from src.storage import db

logger = get_logger(__name__)

_RETRY_CODES = {429, 500, 502, 503, 504}

# USD per 1M tokens. gemini-3.8-flash has intro pricing through 2026-12-31.
TEXT_PRICING = {
    "gemini-3.8-flash": {"in": 0.75, "out": 3.75, "after": {"in": 1.50, "out": 7.50}, "intro_ends": date(2027, 1, 1)},
    "gemini-3.1-pro-preview": {"in": 2.00, "out": 12.00},
}
DEFAULT_TEXT_RATES = {"in": 2.00, "out": 12.00}  # conservative: unknown models are billed like the pro tier
# USD per generated image (~1K resolution). The Batch API is about half price.
IMAGE_PRICING = {"gemini-3.1-flash-image": 0.067}   # 1K price; larger sizes come from config image.price_usd
DEFAULT_IMAGE_COST = 0.10
IMAGE_SIZES = ("1K", "2K", "4K")


def image_size() -> str:
    """Output size for every image: env GEMINI_IMAGE_SIZE, else config image.size (default 4K)."""
    size = str(os.getenv("GEMINI_IMAGE_SIZE") or config.get("image.size", "4K")).upper()
    return size if size in IMAGE_SIZES else "4K"


def image_price(model: str, size: Optional[str] = None) -> float:
    """USD for one image. The model's 1K price, scaled by the config's size ratios (assumed, editable) above 1K."""
    size = size or image_size()
    base = IMAGE_PRICING.get(model, DEFAULT_IMAGE_COST)
    table = config.get("image.price_usd", {}) or {}
    if size == "1K" or size not in table or not table.get("1K"):
        return base
    return round(base * float(table[size]) / float(table["1K"]), 4)


class BudgetExceeded(Exception):
    """The daily spend cap was reached. Work in progress should stop and resume tomorrow."""


class GeminiError(Exception):
    """Gemini returned nothing usable (no text, or invalid JSON after retries)."""


class ImageGenerationError(GeminiError):
    pass


class ImageBlocked(ImageGenerationError):
    """The model returned no image (usually a safety block). Callers skip this prompt, not crash."""


@dataclass
class ImageResult:
    data: bytes
    mime_type: str


def _text_rates(model: str, today: Optional[date] = None) -> dict:
    entry = TEXT_PRICING.get(model)
    if entry is None:
        logger.warning("No price listed for text model %r; billing it at the pro-tier rate.", model)
        return DEFAULT_TEXT_RATES
    today = today or date.today()
    if "after" in entry and today >= entry["intro_ends"]:
        return entry["after"]
    return entry


def text_cost(model: str, tokens_in: int, tokens_out: int, today: Optional[date] = None) -> float:
    rates = _text_rates(model, today)
    return (tokens_in * rates["in"] + tokens_out * rates["out"]) / 1_000_000


def parse_json(text: str):
    """Parse model JSON, tolerating ```json fences and surrounding prose."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


class GeminiClient:
    def __init__(self, niche_id: Optional[int] = None):
        from google import genai

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            # A managed proxy may inject the key; otherwise the first call fails with a clear API error.
            logger.warning("GEMINI_API_KEY is not set; relying on an injected credential.")
        self.client = genai.Client(api_key=api_key or "proxy-managed")
        self.text_model = os.getenv("GEMINI_MODEL_TEXT", "gemini-3.8-flash")
        self.vision_model = os.getenv("GEMINI_MODEL_VISION", "gemini-3.1-pro-preview")
        self.image_model = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
        self.niche_id = niche_id
        self.vision_fallback_used = False  # True once the text model had to stand in for the vision model

    # ── budget and accounting ──────────────────────────────────────────────────

    def _check_budget(self) -> None:
        spent, limit = db.get_today_spend(), config.daily_budget_usd()
        if spent >= limit:
            raise BudgetExceeded(f"Daily budget reached: ${spent:.2f} of ${limit:.2f}")

    def _record(self, model: str, cost: float, tokens_in: int = 0, tokens_out: int = 0, images: int = 0) -> None:
        db.log_spend(model, tokens_in, tokens_out, images, cost, self.niche_id)

    def _call(self, fn: Callable, max_attempts: int = 4):
        """Run an SDK call, retrying rate limits and server errors with exponential backoff."""
        from google.genai import errors

        for attempt in range(max_attempts):
            try:
                return fn()
            except errors.APIError as e:
                code = getattr(e, "code", None)
                if code in _RETRY_CODES and attempt < max_attempts - 1:
                    delay = 2 ** (attempt + 1)
                    logger.warning("Gemini returned %s; retrying in %ss.", code, delay)
                    time.sleep(delay)
                    continue
                raise

    # ── image ──────────────────────────────────────────────────────────────────

    def generate_image(self, prompt: str, references: Optional[list] = None, seed: Optional[int] = None,
                       aspect_ratio: str = "1:1") -> ImageResult:
        """
        Generate one image. references are (bytes, mime) pairs sent along with the prompt; seed makes a run
        repeatable and lets several variations of one prompt differ. Raises ImageBlocked when no image comes back.
        """
        from google.genai import types

        self._check_budget()
        config_ = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio, image_size=image_size()),
            seed=seed,
        )
        contents = [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in (references or [])] + [prompt]
        response = self._call(
            lambda: self.client.models.generate_content(model=self.image_model, contents=contents, config=config_)
        )
        parts = (response.candidates[0].content.parts or []) if response.candidates and response.candidates[0].content else []
        for part in parts:
            if part.inline_data and part.inline_data.data:
                price = image_price(self.image_model)
                self._record(self.image_model, price, images=1)
                return ImageResult(part.inline_data.data, part.inline_data.mime_type or "image/png")
        reason = response.candidates[0].finish_reason if response.candidates else getattr(response.prompt_feedback, "block_reason", None)
        text = " ".join(p.text for p in parts if getattr(p, "text", None))[:120]
        raise ImageBlocked(f"No image returned (reason={reason}). {text}".strip())

    # ── text and JSON ──────────────────────────────────────────────────────────

    def _generate(self, model: str, prompt, system: Optional[str], json_mode: bool) -> str:
        from google.genai import types

        self._check_budget()
        config_ = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if json_mode else None,
        )
        response = self._call(
            lambda: self.client.models.generate_content(
                model=model, contents=prompt if isinstance(prompt, list) else [prompt], config=config_
            )
        )
        usage = response.usage_metadata
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = (getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)
        self._record(model, text_cost(model, tokens_in, tokens_out), tokens_in, tokens_out)
        text = response.text
        if not text or not text.strip():
            reason = response.candidates[0].finish_reason if response.candidates else "no candidates"
            raise GeminiError(f"Gemini returned no text (reason={reason}).")
        return text

    def generate_text(self, prompt: str, system: Optional[str] = None) -> str:
        return self._generate(self.text_model, prompt, system, json_mode=False).strip()

    def generate_json(self, prompt: str, system: Optional[str] = None, max_attempts: int = 2):
        """JSON generation that retries once on malformed output."""
        last: Optional[Exception] = None
        for _ in range(max_attempts):
            try:
                return parse_json(self._generate(self.text_model, prompt, system, json_mode=True))
            except json.JSONDecodeError as e:
                last = e
        raise GeminiError(f"Gemini returned invalid JSON after {max_attempts} attempts: {last}")

    # ── vision ─────────────────────────────────────────────────────────────────

    def generate_vision_json(self, prompt: str, image_bytes: bytes, mime_type: str = "image/png",
                             system: Optional[str] = None, max_attempts: int = 2):
        """
        Judge an image with the vision model and return parsed JSON. If that model is unavailable
        (404), fall back to the text model and set vision_fallback_used so callers can tighten thresholds.
        """
        from google.genai import errors, types

        contents = [types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt]
        model = self.text_model if self.vision_fallback_used else self.vision_model
        last: Optional[Exception] = None
        for _ in range(max_attempts):
            try:
                return parse_json(self._generate(model, contents, system, json_mode=True))
            except errors.ClientError as e:
                if getattr(e, "code", None) == 404 and not self.vision_fallback_used:
                    logger.warning("Vision model %r unavailable; falling back to %r with stricter thresholds.", model, self.text_model)
                    self.vision_fallback_used = True
                    model = self.text_model
                    continue
                raise
            except json.JSONDecodeError as e:
                last = e
        raise GeminiError(f"Gemini returned invalid JSON after {max_attempts} attempts: {last}")
