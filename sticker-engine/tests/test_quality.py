import io
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image

from src.generator.style_guard import check
from src.quality import bg_remover
from src.quality.deduper import dedupe
from tests.helpers import sticker_image, stripes_image


def save(img, tmp_path, name="a.png"):
    p = tmp_path / name
    img.save(p)
    return str(p)


# ── style guard ──────────────────────────────────────────────────────────────────

def test_good_sticker_passes(tmp_path):
    assert check(save(sticker_image(), tmp_path)) == (True, "ok")


def test_all_white_is_blank(tmp_path):
    ok, why = check(save(Image.new("RGB", (600, 600), "white"), tmp_path))
    assert not ok and "Blank" in why


def test_solid_colour_is_blank(tmp_path):
    ok, why = check(save(Image.new("RGB", (600, 600), (200, 30, 30)), tmp_path))
    assert not ok and "Blank" in why


def test_full_bleed_artwork_fails_background_check(tmp_path):
    rng = np.random.default_rng(1)
    noise = Image.fromarray(rng.integers(0, 140, (600, 600, 3), dtype=np.uint8))
    ok, why = check(save(noise, tmp_path))
    assert not ok and "Background" in why


def test_coloured_background_fails(tmp_path):
    ok, why = check(save(sticker_image(bg=(120, 200, 255)), tmp_path))
    assert not ok and "Background" in why


def test_small_and_non_square_images_fail(tmp_path):
    assert "Resolution" in check(save(sticker_image(300), tmp_path))[1]
    wide = Image.new("RGB", (1200, 800), "white")
    assert "Aspect" in check(save(wide, tmp_path, "w.png"))[1]


def test_transparent_background_counts_as_white(tmp_path):
    rgba = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    rgba.paste(sticker_image().convert("RGBA").crop((100, 100, 500, 500)), (100, 100))
    assert check(save(rgba, tmp_path))[0]


def test_unreadable_file_fails_cleanly(tmp_path):
    p = tmp_path / "bad.png"
    p.write_bytes(b"not an image")
    ok, why = check(str(p))
    assert not ok and "Unreadable" in why


# ── deduper ──────────────────────────────────────────────────────────────────────

def test_identical_images_collapse_and_distinct_ones_survive(tmp_path):
    a, b, c = (save(sticker_image(), tmp_path, n) for n in ("a.png", "b.png", "c.png"))
    d = save(stripes_image(), tmp_path, "d.png")
    assert dedupe([a, b, d, c]) == [a, d]  # path input: first of each group wins


def test_highest_qa_score_wins_a_duplicate_group(tmp_path):
    lo = {"image_path": save(sticker_image(), tmp_path, "lo.png"), "qa_score": 7}
    hi = {"image_path": save(sticker_image(), tmp_path, "hi.png"), "qa_score": 9}
    assert dedupe([lo, hi]) == [hi]


def test_unreadable_images_are_dropped_not_fatal(tmp_path):
    good = save(sticker_image(), tmp_path)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"junk")
    assert dedupe([str(bad), good]) == [good]


# ── background removal ───────────────────────────────────────────────────────────

def test_floodfill_clears_outer_white_and_keeps_subject(tmp_path):
    src = save(sticker_image(), tmp_path)
    out = bg_remover.remove_background(src, method="floodfill")
    img = Image.open(out)
    assert img.mode == "RGBA" and out.endswith("_nobg.png")
    assert img.getpixel((5, 5))[3] == 0          # corner became transparent
    assert img.getpixel((300, 300))[3] == 255    # subject stays opaque


def test_original_is_never_overwritten_for_any_extension(tmp_path):
    for name in ("a.jpeg", "a.webp", "a.png", "a.JPG"):
        src = tmp_path / name
        sticker_image().save(src, "PNG")
        out = bg_remover.remove_background(str(src), method="floodfill")
        assert out != str(src) and out.endswith("_nobg.png")


def test_already_processed_file_is_returned_as_is(tmp_path):
    p = str(tmp_path / "x_nobg.png")
    assert bg_remover.remove_background(p) == p


def test_rembg_uses_the_pinned_open_licence_model_and_reuses_the_session(tmp_path, monkeypatch):
    calls = {"sessions": [], "removes": 0}

    def new_session(name):
        calls["sessions"].append(name)
        return f"session:{name}"

    def remove(data, session=None):
        calls["removes"] += 1
        assert session == "session:isnet-general-use"
        buf = io.BytesIO()
        Image.new("RGBA", (8, 8), (200, 50, 50, 252)).save(buf, "PNG")
        return buf.getvalue()

    monkeypatch.setitem(sys.modules, "rembg", SimpleNamespace(new_session=new_session, remove=remove))
    monkeypatch.setattr(bg_remover, "_session", None)
    a, b = save(sticker_image(), tmp_path, "a.png"), save(sticker_image(), tmp_path, "b.png")
    bg_remover.remove_background(a)
    bg_remover.remove_background(b)
    assert calls == {"sessions": ["isnet-general-use"], "removes": 2}


def test_transparent_copy_matches_its_white_background_original(tmp_path):
    """After a crash mid-background-removal, a processed and an unprocessed copy must still dedupe."""
    original = save(sticker_image(), tmp_path, "orig.png")
    transparent = bg_remover.remove_background(original, method="floodfill")
    assert dedupe([original, transparent]) == [original]


def test_rembg_output_is_hardened_to_fully_opaque_but_keeps_soft_edges(tmp_path, monkeypatch):
    def remove(data, session=None):
        img = Image.new("RGBA", (4, 1))
        for x, alpha in enumerate([0, 120, 252, 255]):
            img.putpixel((x, 0), (10, 20, 30, alpha))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    monkeypatch.setitem(sys.modules, "rembg", SimpleNamespace(new_session=lambda n: n, remove=remove))
    monkeypatch.setattr(bg_remover, "_session", None)
    out = bg_remover.remove_background(save(sticker_image(), tmp_path), method="rembg")
    alphas = [Image.open(out).getpixel((x, 0))[3] for x in range(4)]
    assert alphas == [0, 120, 255, 255]
