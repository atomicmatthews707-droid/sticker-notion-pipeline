"""Gemini text/JSON helper shared by the spec, listing and cover-art steps.

Model ID comes from GEMINI_MODEL_TEXT (never hard-coded elsewhere). The API key
comes from GEMINI_API_KEY; in managed environments where a proxy injects the key
the variable may be unset, so a placeholder is passed to satisfy the SDK.
"""

import json
import os
import re
import time
from typing import Callable, Optional

DEFAULT_MODEL = "gemini-3.8-flash"
_RETRY_CODES = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Raised when Gemini returns no usable text or keeps failing."""


def _model() -> str:
    return os.getenv("GEMINI_MODEL_TEXT", DEFAULT_MODEL)


def _client():
    from google import genai

    return genai.Client(api_key=os.getenv("GEMINI_API_KEY") or "proxy-managed")


def generate_text(
    system: str,
    prompt: str,
    max_output_tokens: int = 4096,
    json_mode: bool = False,
    max_retries: int = 4,
) -> str:
    """Call Gemini and return the response text. Retries 429/5xx with backoff."""
    from google.genai import errors, types

    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json" if json_mode else None,
    )
    client = _client()
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=_model(), contents=[prompt], config=config
            )
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code in _RETRY_CODES and attempt < max_retries - 1:
                delay = 2 ** (attempt + 1)
                print(f"Gemini returned {code}; retrying in {delay}s.")
                time.sleep(delay)
                continue
            raise LLMError(f"Gemini API error {code}: {e}") from e

        text = response.text
        if not text or not text.strip():
            reason = None
            if response.candidates:
                reason = response.candidates[0].finish_reason
            raise LLMError(f"Gemini returned no text (finish_reason={reason}).")
        return text.strip()
    raise LLMError("Gemini retries exhausted.")


def parse_json(text: str):
    """Parse JSON from model output, tolerating ```json fences and stray prose."""
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


def generate_json(
    system: str,
    prompt: str,
    validate: Optional[Callable[[dict], list]] = None,
    max_attempts: int = 3,
    max_output_tokens: int = 16384,
) -> dict:
    """Generate JSON; on a parse or validation failure, feed the errors back and retry."""
    feedback = ""
    last_error = "unknown error"
    for _ in range(max_attempts):
        text = generate_text(
            system, prompt + feedback, max_output_tokens=max_output_tokens, json_mode=True
        )
        try:
            data = parse_json(text)
        except json.JSONDecodeError as e:
            last_error = f"invalid JSON: {e}"
            feedback = f"\n\nYour previous reply was not valid JSON ({e}). Return ONLY valid JSON."
            continue
        errs = validate(data) if validate else []
        if not errs:
            return data
        last_error = "; ".join(errs)
        feedback = (
            "\n\nYour previous reply failed validation. Fix every problem and return the "
            "complete corrected JSON:\n- " + "\n- ".join(errs)
        )
    raise LLMError(f"No valid JSON after {max_attempts} attempts: {last_error}")
