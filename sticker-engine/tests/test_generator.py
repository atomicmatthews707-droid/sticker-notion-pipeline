import pytest

from src.generator import prompt_builder
from src.shared.banned import is_banned


class FakeLLM:
    def __init__(self, data):
        self.data, self.prompts = data, []

    def generate_json(self, prompt, system=None, **kw):
        self.prompts.append((prompt, system))
        return self.data


def test_banned_matching_is_whole_word():
    assert is_banned("weed leaf") and is_banned("Pokemon cards") and is_banned("DC Comics fan")
    assert not is_banned("seaweed planner") and not is_banned("tumbleweed") and not is_banned("cat lover")


def test_subjects_are_cleaned_deduped_and_composed():
    llm = FakeLLM({"subjects": ["Cozy mug", "cozy mug.", "Cozy mugs", "Pikachu hat", "  ", "x", "Autumn leaf", "Autumn leaf"]})
    prompts = prompt_builder.build_prompts("autumn cozy", client=llm)
    subjects = [p.split(",")[0] for p in prompts]
    assert subjects == ["Cozy mug", "Autumn leaf"]
    assert prompts[0] == (
        "Cozy mug, cute kawaii flat vector illustration, plain flat pure white background, "
        "no shadow, no border, centered subject, thick outline, minimal detail"
    )
    assert "autumn cozy" in llm.prompts[0][0] and "sticker pack" in llm.prompts[0][1]


def test_limit_and_list_response_shape():
    words = ["sun", "moon", "teacup", "cactus", "rainbow", "umbrella", "bicycle", "lantern", "pretzel", "volcano"]
    out = prompt_builder.clean_subjects(words, limit=5)
    assert len(out) == 5
    llm = FakeLLM(["Sun", "Moon"])  # a bare list is accepted too
    assert len(prompt_builder.build_prompts("sky", client=llm)) == 2


def test_banned_niche_is_refused_before_spending_anything():
    llm = FakeLLM({"subjects": ["x"]})
    with pytest.raises(ValueError, match="banned"):
        prompt_builder.build_prompts("pokemon stickers", client=llm)
    assert llm.prompts == []
