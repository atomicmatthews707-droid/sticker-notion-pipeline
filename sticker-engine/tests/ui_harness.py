"""Starts the real Sticker Studio with Gemini replaced by fakes, for browser testing. Not a pytest file."""
import os
import random
import sys
import tempfile
import time

root = tempfile.mkdtemp(prefix="studio_")
os.environ.update({"DB_URL": f"sqlite+aiosqlite:///{root}/t.db", "OUTPUT_DIR": f"{root}/out", "GEMINI_API_KEY": "fake-key",
                   "BUDGET_DAILY_LIMIT_USD": "5", "NO_BROWSER": "1", "CONFIG_PATH": os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")})
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.shared.gemini_client import GeminiClient, ImageBlocked, ImageResult  # noqa: E402
from src.ui import app as studio  # noqa: E402
from tests.helpers import jpeg_bytes, sticker_image  # noqa: E402

DELAY = float(os.getenv("FAKE_DELAY", "0.5"))


def fake_generate_image(self, prompt, references=None, seed=None, aspect_ratio="1:1"):
    time.sleep(DELAY)
    if "blocked" in prompt:
        raise ImageBlocked("No image returned (reason=IMAGE_SAFETY).")
    rng = random.Random(seed or 0)
    img = sticker_image(1024, color=(rng.randint(40, 230), rng.randint(40, 230), rng.randint(40, 230)))
    self._record(self.image_model, 0.067, images=1)
    return ImageResult(jpeg_bytes(img), "image/jpeg")


def fake_vision(self, prompt, image_bytes, mime_type="image/png", system=None, max_attempts=2):
    time.sleep(DELAY / 3)
    score = random.Random(len(image_bytes)).choice([9, 8, 7, 5, 3])
    self._record(self.vision_model, 0.016, 1000, 300)
    return {"score": score, "reason": {9: "Polished and appealing.", 8: "Clean cutout, good outline.", 7: "Good, minor flaws.", 5: "Weak composition.", 3: "Cutout looks damaged."}[score]}


JOKES = ["skeleton waiting for a date that never came", "ghost who is tired of the sheet cliche", "witch parking permit expired",
         "vampire reading the terms of service", "zombie on his fourth coffee", "pumpkin spice and everything nice and nothing",
         "werewolf apologising for the howling", "black cat judging your life choices", "mummy unwrapping a tax return",
         "bat with a noise complaint", "cauldron of questionable soup", "haunted house with bad wifi"]


def fake_json(self, prompt, system=None, max_attempts=2):
    import re
    if prompt.startswith("Brief to improve"):
        return {"prompt": "Halloween stickers for adults: dry, sarcastic one-liners and cheeky spoofs of famous horror tropes, "
                          "mixing badge, sign and caption-only formats, with short bold words that are easy to read."}
    n = int(re.search(r"Write (\d+) candidate", prompt).group(1))
    offset = 0 if "pack 2 of" not in prompt else 6
    return {"ideas": [{"text": f'"{JOKES[(offset + i) % len(JOKES)].upper()}" with a bold graphic', "weakness": "a bit obvious",
                       "scores": {"funny": 4 + i % 5, "original": 5, "readable": 6, "on_brief": 7}} for i in range(min(n, 6))]}


GeminiClient.generate_json = fake_json
GeminiClient.generate_image = fake_generate_image
GeminiClient.generate_vision_json = fake_vision
studio.PORT = int(os.getenv("PORT", "8099"))
print("HARNESS READY", root, flush=True)
studio.main()
