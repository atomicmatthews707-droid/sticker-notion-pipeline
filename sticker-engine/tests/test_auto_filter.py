import pytest

from src.quality import auto_filter
from src.shared.gemini_client import BudgetExceeded, GeminiError
from src.storage import db
from tests.helpers import save_stickers


class FakeVision:
    """generate_vision_json returns scripted verdicts keyed by file name."""

    def __init__(self, verdicts, fallback=False):
        self.verdicts, self.vision_fallback_used, self.calls = verdicts, fallback, []

    def generate_vision_json(self, prompt, image_bytes, mime, system=None):
        self.calls.append(prompt)
        v = self.verdicts.pop(0) if isinstance(self.verdicts, list) else self.verdicts
        if isinstance(v, Exception):
            raise v
        return v


def make(tmp_path, n):
    ids = []
    for i, p in enumerate(save_stickers(tmp_path, n)):
        ids.append(db.save_image_record(1, f"p{i}", p))
    return db.get_pending_images(1)


def run(images, client):
    return auto_filter.filter_batch(images, "autumn", client=client)


def test_threshold_decides_kept_not_the_model(tmp_path):
    images = make(tmp_path, 3)
    client = FakeVision([{"score": 9, "reason": "great", "kept": False}, {"score": 7, "reason": "ok"}, {"score": 6.9, "reason": "meh", "kept": True}])
    stats = run(images, client)
    assert stats == {"scored": 3, "kept": 2, "rejected": 1}
    assert len(db.get_images_for_niche(1, kept=True)) == 2
    rejected = db.get_images_for_niche(1, kept=False)[0]
    assert rejected["qa_score"] == 6.9 and rejected["qa_reason"] == "meh"
    assert db.get_pending_images(1) == []


def test_text_model_fallback_tightens_the_bar(tmp_path):
    images = make(tmp_path, 2)
    stats = run(images, FakeVision([{"score": 7, "reason": "x"}, {"score": 8, "reason": "y"}], fallback=True))
    assert stats["kept"] == 1  # 7 no longer passes when the stand-in model is judging


def test_unusable_verdicts_are_rejected_never_shipped(tmp_path):
    images = make(tmp_path, 4)
    client = FakeVision([{"reason": "no score"}, {"score": 42, "reason": "out of range"}, {"score": "high"}, GeminiError("blocked")])
    stats = run(images, client)
    assert stats == {"scored": 4, "kept": 0, "rejected": 4}
    assert all("qa unusable" in i["qa_reason"] for i in db.get_images_for_niche(1, kept=False))


def test_api_errors_leave_images_pending_for_retry(tmp_path):
    images = make(tmp_path, 2)
    client = FakeVision([{"score": 9, "reason": "ok"}, ConnectionError("network")])
    with pytest.raises(RuntimeError, match="could not judge 1"):
        run(images, client)
    assert len(db.get_pending_images(1)) == 1 and len(db.get_images_for_niche(1, kept=True)) == 1


def test_budget_keeps_partial_results_then_raises(tmp_path):
    images = make(tmp_path, 3)
    client = FakeVision([{"score": 9, "reason": "ok"}, BudgetExceeded("cap"), BudgetExceeded("cap")])
    with pytest.raises(BudgetExceeded):
        run(images, client)
    assert len(db.get_images_for_niche(1, kept=True)) == 1 and len(db.get_pending_images(1)) == 2


def test_parse_score():
    assert auto_filter._parse_score({"score": "8", "reason": "r"}) == (8.0, "r")
    for bad in ({}, {"score": -1}, {"score": 11}, [], {"score": None}):
        with pytest.raises((ValueError, TypeError)):
            auto_filter._parse_score(bad)


def test_prompt_names_the_niche_and_rubric_is_strict():
    rubric = auto_filter._RUBRIC.read_text()
    assert "garbled" in rubric and "trademark" in rubric and "plain white" in rubric


def test_vision_client_falls_back_to_text_model_on_404():
    from google.genai import errors

    from src.shared.gemini_client import GeminiClient
    from tests.helpers import fake_client, genai_response

    c = GeminiClient()
    models = fake_client(c, errors.ClientError(404, {"error": {"message": "model not found"}}), genai_response(text='{"score": 8, "reason": "ok"}'))
    assert c.generate_vision_json("judge", b"\x89PNG", "image/png") == {"score": 8, "reason": "ok"}
    assert c.vision_fallback_used and [m["model"] for m in models.calls] == [c.vision_model, c.text_model]
