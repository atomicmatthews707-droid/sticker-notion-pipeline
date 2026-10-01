from datetime import date
from types import SimpleNamespace

import pytest
from google.genai import errors

from src.shared import gemini_client as gc
from src.shared.gemini_client import BudgetExceeded, GeminiClient, GeminiError, ImageBlocked
from src.storage import db
from tests.helpers import fake_client, genai_response, jpeg_bytes


def test_text_cost_uses_per_million_units():
    assert gc.text_cost("gemini-3.1-pro-preview", 1_000_000, 1_000_000) == pytest.approx(14.0)
    assert gc.text_cost("gemini-3.8-flash", 1300, 200, today=date(2026, 10, 1)) == pytest.approx((1300 * 0.75 + 200 * 3.75) / 1e6)


def test_flash_price_steps_up_after_intro_period():
    before = gc.text_cost("gemini-3.8-flash", 1_000_000, 0, today=date(2026, 12, 31))
    after = gc.text_cost("gemini-3.8-flash", 1_000_000, 0, today=date(2027, 1, 1))
    assert (before, after) == (pytest.approx(0.75), pytest.approx(1.50))


def test_unknown_text_model_is_billed_at_the_pro_rate():
    assert gc.text_cost("mystery-model", 1_000_000, 0) == pytest.approx(gc.DEFAULT_TEXT_RATES["in"])


def test_image_costs_67_cents_per_100_not_per_1000():
    c = GeminiClient(niche_id=3)
    fake_client(c, genai_response(image=jpeg_bytes()))
    for _ in range(10):
        c.generate_image("a cat")
    assert db.get_today_spend() == pytest.approx(0.67)  # the old code booked 1000x less
    assert db.get_niche_spend(3) == pytest.approx(0.67)


def test_generate_image_returns_bytes_and_mime():
    c = GeminiClient()
    models = fake_client(c, genai_response(image=jpeg_bytes()))
    result = c.generate_image("a cat")
    assert result.mime_type == "image/jpeg" and result.data[:2] == b"\xff\xd8"
    cfg = models.calls[0]["config"]
    assert list(cfg.response_modalities) == ["IMAGE"] and cfg.image_config.aspect_ratio == "1:1"


def test_text_only_response_is_a_block_not_a_crash_and_costs_nothing():
    c = GeminiClient()
    fake_client(c, genai_response(text="I can't draw that", finish_reason="IMAGE_SAFETY"))
    with pytest.raises(ImageBlocked, match="IMAGE_SAFETY"):
        c.generate_image("something blocked")
    assert db.get_today_spend() == 0


def test_no_candidates_is_a_block():
    c = GeminiClient()
    fake_client(c, genai_response(no_candidates=True))
    with pytest.raises(ImageBlocked):
        c.generate_image("x")


def test_unknown_image_model_does_not_crash_after_billing(monkeypatch):
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "some-new-image-model")
    c = GeminiClient()
    fake_client(c, genai_response(image=jpeg_bytes()))
    c.generate_image("x")
    assert db.get_today_spend() == pytest.approx(gc.DEFAULT_IMAGE_COST)


def test_budget_blocks_before_calling_the_api(monkeypatch):
    monkeypatch.setenv("BUDGET_DAILY_LIMIT_USD", "0.10")
    c = GeminiClient()
    models = fake_client(c, genai_response(image=jpeg_bytes()))
    c.generate_image("one")  # $0.067, under the cap
    c.generate_image("two")  # $0.134 total, now over
    with pytest.raises(BudgetExceeded):
        c.generate_image("three")
    assert len(models.calls) == 2


def test_budget_survives_a_new_client_instance(monkeypatch):
    monkeypatch.setenv("BUDGET_DAILY_LIMIT_USD", "0.05")
    first = GeminiClient()
    fake_client(first, genai_response(image=jpeg_bytes()))
    first.generate_image("a")
    restarted = GeminiClient()  # simulates a process restart: no in-memory spend
    fake_client(restarted, genai_response(image=jpeg_bytes()))
    with pytest.raises(BudgetExceeded):
        restarted.generate_image("b")


def test_retries_server_errors_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gc.time, "sleep", sleeps.append)
    c = GeminiClient()
    err = errors.ServerError(503, {"error": {"message": "busy"}})
    models = fake_client(c, err, err, genai_response(image=jpeg_bytes()))
    c.generate_image("x")
    assert len(models.calls) == 3 and sleeps == [2, 4]


def test_client_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(gc.time, "sleep", lambda s: None)
    c = GeminiClient()
    models = fake_client(c, errors.ClientError(400, {"error": {"message": "bad"}}))
    with pytest.raises(errors.ClientError):
        c.generate_image("x")
    assert len(models.calls) == 1


def test_text_call_is_priced_from_usage_including_thinking_tokens():
    c = GeminiClient(niche_id=9)
    usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=100, thoughts_token_count=400)
    fake_client(c, genai_response(text="hello", usage=usage))
    assert c.generate_text("hi") == "hello"
    assert db.get_niche_spend(9) == pytest.approx(gc.text_cost("gemini-3.8-flash", 1000, 500))


def test_generate_json_parses_fences_and_retries_once():
    c = GeminiClient()
    fake_client(c, genai_response(text="not json"), genai_response(text='```json\n{"subjects": ["a"]}\n```'))
    assert c.generate_json("p") == {"subjects": ["a"]}


def test_generate_json_gives_up_with_a_clear_error():
    c = GeminiClient()
    fake_client(c, genai_response(text="nope"))
    with pytest.raises(GeminiError, match="invalid JSON"):
        c.generate_json("p")


def test_empty_text_is_an_error():
    c = GeminiClient()
    fake_client(c, genai_response(text=None, finish_reason="SAFETY"))
    with pytest.raises(GeminiError, match="SAFETY"):
        c.generate_text("p")
