from pathlib import Path

import pytest
from PIL import Image

from src.generator import image_gen
from src.shared.gemini_client import BudgetExceeded, ImageBlocked, ImageResult
from src.storage import db
from tests.helpers import jpeg_bytes, sticker_image


class FakeImageClient:
    """Scripted generate_image: prompts containing a keyword raise that failure."""

    def __init__(self):
        self.calls = []

    def generate_image(self, prompt, references=None, seed=None, aspect_ratio="1:1"):
        self.calls.append(prompt)
        self.last_kwargs = {"references": references, "seed": seed}
        if "blocked" in prompt:
            raise ImageBlocked("safety")
        if "budget" in prompt:
            raise BudgetExceeded("cap")
        if "boom" in prompt:
            raise RuntimeError("network")
        return ImageResult(jpeg_bytes(), "image/jpeg")


def test_saves_real_pngs_even_though_the_model_returns_jpeg():
    client = FakeImageClient()
    out = image_gen.generate_images(["cat", "dog"], 1, client=client)
    assert len(out) == 2
    for item in out:
        with Image.open(item["image_path"]) as im:
            assert im.format == "PNG" and im.size == (600, 600)
        assert Path(item["image_path"]).suffix == ".png"
    assert len(db.get_pending_images(1)) == 2


def test_resume_does_not_regenerate_or_repay():
    c1 = FakeImageClient()
    image_gen.generate_images(["cat", "dog"], 1, client=c1)
    c2 = FakeImageClient()
    out = image_gen.generate_images(["cat", "dog", "fox"], 1, client=c2)
    assert c2.calls == ["fox"] and len(out) == 3


def test_missing_file_is_regenerated():
    c = FakeImageClient()
    out = image_gen.generate_images(["cat"], 1, client=c)
    Path(out[0]["image_path"]).unlink()
    c2 = FakeImageClient()
    image_gen.generate_images(["cat"], 1, client=c2)
    assert c2.calls == ["cat"]


def test_blocked_prompt_is_recorded_once_and_never_retried():
    c = FakeImageClient()
    out = image_gen.generate_images(["cat", "blocked thing"], 1, client=c)
    assert len(out) == 1
    rejected = db.get_images_for_niche(1, kept=False)
    assert len(rejected) == 1 and rejected[0]["qa_reason"].startswith("blocked")
    c2 = FakeImageClient()
    image_gen.generate_images(["cat", "blocked thing"], 1, client=c2)
    assert c2.calls == []


def test_one_failure_does_not_lose_the_batch():
    c = FakeImageClient()
    out = image_gen.generate_images(["cat", "boom", "dog"], 1, client=c)
    assert [i["prompt"] for i in out] == ["cat", "dog"]


def test_all_failing_raises():
    with pytest.raises(RuntimeError, match="Every image generation attempt failed"):
        image_gen.generate_images(["boom a", "boom b"], 1, client=FakeImageClient())


def test_budget_hit_saves_progress_then_raises_for_resume():
    c = FakeImageClient()
    with pytest.raises(BudgetExceeded):
        image_gen.generate_images(["cat", "budget one"], 1, client=c, max_workers=1)
    assert len(db.get_pending_images(1)) == 1  # the finished image is kept
    # Later (budget reset) the same call finishes only what is missing.
    c2 = FakeImageClient()
    c2.generate_image = lambda p, **kw: (c2.calls.append(p), ImageResult(jpeg_bytes(), "image/jpeg"))[1]
    image_gen.generate_images(["cat", "budget one"], 1, client=c2, max_workers=1)
    assert c2.calls == ["budget one"]


def test_worker_count_is_capped(monkeypatch):
    seen = {}
    real = image_gen.ThreadPoolExecutor

    def spy(max_workers):
        seen["n"] = max_workers
        return real(max_workers=max_workers)

    monkeypatch.setattr(image_gen, "ThreadPoolExecutor", spy)
    image_gen.generate_images(["a"], 1, client=FakeImageClient(), max_workers=50)
    assert seen["n"] == image_gen.MAX_WORKERS_CAP


def test_to_png_normalises_mode():
    assert image_gen.to_png(jpeg_bytes(sticker_image())).mode == "RGB"
