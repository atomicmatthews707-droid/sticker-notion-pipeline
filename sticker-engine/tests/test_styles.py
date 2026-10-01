import pytest
from PIL import Image

from src.generator import style_guard
from src.generator.prompt_builder import compose_prompt
from src.quality.auto_filter import render_for_review
from src.shared import design

BASE = {"clipart-kawaii", "photoreal-comedy", "bumper-signs"}


def test_shipped_presets_are_listed_and_valid():
    presets = design.list_presets()
    assert BASE <= set(presets)
    assert "_TEMPLATE" not in presets
    for stem in presets:
        assert design.validate_style(design.load_preset(stem)) == []


def test_rules_follow_the_preset():
    sticker = design.rules_for(design.load_preset("clipart-kawaii"))
    photo = design.rules_for(design.load_preset("photoreal-comedy"))
    assert (sticker.cutout, sticker.background, sticker.guard) != (photo.cutout, photo.background, photo.guard)
    assert photo.cutout == "none" and photo.background == "none" and photo.guard == "basic"


def test_compose_adds_white_background_only_for_cutout_styles():
    for stem, expect_white in (("clipart-kawaii", True), ("photoreal-comedy", False)):
        md = design.load_preset(stem)
        out = compose_prompt("a pumpkin mug", design.direction_for(style_md=md), rules=design.rules_for(md))
        assert out.startswith("a pumpkin mug")
        assert ("white" in out.lower()) is expect_white


def test_style_off_keeps_subject_verbatim():
    md = design.load_preset("photoreal-comedy")
    assert compose_prompt("my words", design.direction_for(style_md=md), apply_style=False, rules=design.rules_for(md)) == "my words"


def test_user_can_invent_a_new_style(tmp_path, monkeypatch):
    monkeypatch.setattr(design, "STYLES_DIR", tmp_path)
    stem = design.save_preset("Retro Diner Signs!", "# Retro diner\ncutout: none\n\n## Style\nneon diner sign\n")
    assert stem == "retro-diner-signs"
    assert design.list_presets()[stem] == "Retro diner"
    assert design.rules_for(design.load_preset(stem)).cutout == "none"
    with pytest.raises(ValueError):
        design.save_preset("Retro Diner Signs", "x", overwrite=False)


@pytest.mark.parametrize("bad", ["../evil", "..", "", "_template", "a/b"])
def test_preset_names_cannot_escape(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(design, "STYLES_DIR", tmp_path)
    try:
        stem = design.save_preset(bad, "# x")
    except ValueError:
        stem = None
    assert all(p.parent == tmp_path for p in tmp_path.rglob("*.md"))
    if stem is None:
        assert not list(tmp_path.rglob("*.md"))
    with pytest.raises(ValueError):
        design.load_preset("../../etc/passwd")


def test_bad_switch_is_reported():
    assert design.validate_style("# T\ncutout: banana\n")


def test_strict_guard_wants_white_basic_does_not(tmp_path):
    p = tmp_path / "scene.png"
    Image.effect_noise((512, 512), 60).convert("RGB").save(p)
    assert style_guard.check(str(p), strict=True)[0] is False
    assert style_guard.check(str(p), strict=False)[0] is True


def test_review_backdrop_depends_on_style(tmp_path):
    p = tmp_path / "t.png"
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(p)
    import io
    grey = Image.open(io.BytesIO(render_for_review(str(p), sticker=True))).getpixel((0, 0))
    white = Image.open(io.BytesIO(render_for_review(str(p), sticker=False))).getpixel((0, 0))
    assert white == (255, 255, 255) and grey != white
