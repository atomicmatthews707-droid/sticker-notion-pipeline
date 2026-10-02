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
    bg_remover.remove_background(a, method="rembg")
    bg_remover.remove_background(b, method="rembg")
    assert calls == {"sessions": ["isnet-general-use"], "removes": 2}


def test_hash_ignores_colour_data_hidden_under_transparent_pixels(tmp_path):
    """rembg-style cutouts keep black RGB under alpha 0; the hash must still see the sticker on white."""
    import numpy as np

    from src.quality.deduper import _hash

    original = sticker_image()
    path_a = save(original, tmp_path, "a.png")
    arr = np.asarray(original.convert("RGBA")).copy()
    outside = arr[:, :, :3].min(axis=2) >= 236
    arr[outside] = (0, 0, 0, 0)                         # transparent, with black RGB underneath
    path_b = str(tmp_path / "b.png")
    Image.fromarray(arr).save(path_b)
    naive = __import__("imagehash").phash(Image.open(path_b).convert("L"))
    assert (_hash(path_a) - _hash(path_b)) <= 4          # same picture
    assert (_hash(path_a) - naive) > 12                  # a naive hash would call them different

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


def ring_sticker(tmp_path):
    """White-filled shape with a dark outline, like a cloud/ghost/blanket: its white fill is CONTENT."""
    from PIL import ImageDraw

    img = Image.new("RGB", (600, 600), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([100, 100, 500, 500], fill="white", outline=(30, 30, 30), width=14)
    d.ellipse([240, 260, 280, 300], fill=(30, 30, 30))   # an eye, so the fill is not featureless
    return save(img, tmp_path, "ghost.png")


def test_default_cutout_is_floodfill_and_never_hollows_out_white_fills(tmp_path):
    src = ring_sticker(tmp_path)
    out = bg_remover.remove_background(src)  # default method
    img = Image.open(out)
    assert img.getpixel((5, 5))[3] == 0            # outside is cleared
    assert img.getpixel((350, 400))[3] == 255      # the white body INSIDE the outline stays solid


def test_enclosed_gaps_such_as_mug_handles_stay_white_by_design(tmp_path):
    from PIL import ImageDraw

    img = Image.new("RGB", (600, 600), "white")
    ImageDraw.Draw(img).ellipse([150, 150, 450, 450], fill=(210, 120, 60), outline=(30, 30, 30), width=14)
    ImageDraw.Draw(img).ellipse([260, 260, 340, 340], fill="white", outline=(30, 30, 30), width=10)  # a hole
    out = bg_remover.remove_background(save(img, tmp_path, "ring.png"))
    assert Image.open(out).getpixel((300, 300))[3] == 255    # documented trade-off: kept, not punched out


def test_light_fringe_next_to_the_outline_is_trimmed(tmp_path):
    out = bg_remover.remove_background(save(sticker_image(), tmp_path))
    img = Image.open(out)
    # Walk in from the left along the sticker's centre row: alpha must rise through the outline without
    # opaque light-grey pixels sitting outside it.
    row = [img.getpixel((x, 300)) for x in range(0, 300)]
    first_opaque = next(i for i, p in enumerate(row) if p[3] > 200)
    assert sum(row[first_opaque][:3]) < 3 * 120        # the first solid pixel is the dark outline, not a pale halo


def test_pale_details_that_are_not_pure_white_survive(tmp_path):
    from PIL import ImageDraw

    img = Image.new("RGB", (600, 600), "white")
    ImageDraw.Draw(img).line([(100, 300), (500, 300)], fill=(240, 225, 205), width=20)   # pale steam-like stroke
    out = bg_remover.remove_background(save(img, tmp_path, "steam.png"))
    assert Image.open(out).getpixel((300, 300))[3] > 200


def test_rembg_is_opt_in(monkeypatch):
    assert bg_remover.config.get("bg_remover.method") == "floodfill"
