import pytest

from engine import llm
from engine.llm import LLMError, generate_json, parse_json


def test_parse_json_variants():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": [1, 2]} thanks') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_generate_json_feeds_validation_errors_back(monkeypatch):
    prompts = []
    replies = iter(['not json', '{"n": 1}', '{"n": 2}'])

    def fake(system, prompt, **kw):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(llm, "generate_text", fake)
    out = generate_json("sys", "make it", validate=lambda d: [] if d["n"] == 2 else ["n must be 2"])
    assert out == {"n": 2}
    assert "not valid JSON" in prompts[1]
    assert "n must be 2" in prompts[2]


def test_generate_json_gives_up(monkeypatch):
    monkeypatch.setattr(llm, "generate_text", lambda *a, **k: '{"n": 1}')
    with pytest.raises(LLMError, match="n must be 2"):
        generate_json("s", "p", validate=lambda d: ["n must be 2"], max_attempts=2)


def test_model_comes_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_TEXT", "some-model")
    assert llm._model() == "some-model"
    monkeypatch.delenv("GEMINI_MODEL_TEXT")
    assert llm._model() == llm.DEFAULT_MODEL
