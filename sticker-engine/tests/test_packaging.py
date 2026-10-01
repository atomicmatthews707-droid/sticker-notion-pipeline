import zipfile

import pytest
from PIL import Image

from src.packaging import bundler, mockup_gen, sheet_layout
from src.packaging.common import grid_shape, load_sticker
from src.shared.compliance import AI_DISCLOSURE
from tests.helpers import save_stickers


@pytest.fixture
def small_sheet(monkeypatch):
    monkeypatch.setattr(sheet_layout.config, "get", lambda k, d=None: 1200 if k == "packaging.sheet_px" else d)


def test_grid_shape_is_near_square():
    assert [grid_shape(n) for n in (1, 5, 9, 12, 40)] == [(1, 1), (3, 2), (3, 3), (4, 3), (7, 6)]


def test_load_sticker_trims_transparent_margins(tmp_path):
    (path,) = save_stickers(tmp_path, 1)
    trimmed = load_sticker(path)
    assert trimmed.width < 1024 and trimmed.height < 1024 and trimmed.mode == "RGBA"


def test_sheet_is_transparent_rgba_with_a_white_preview(tmp_path, small_sheet):
    paths = save_stickers(tmp_path, 5)
    sheet, preview = sheet_layout.create_sheet(paths, tmp_path / "pack")
    img = Image.open(sheet)
    assert img.mode == "RGBA" and img.size == (1200, 1200)
    assert img.getpixel((2, 2))[3] == 0                      # corner stays transparent
    assert img.getchannel("A").getbbox() is not None         # stickers were actually placed
    prev = Image.open(preview)
    assert prev.format == "JPEG" and prev.getpixel((2, 2)) == (255, 255, 255)


def boxes(sizes, scale, positions):
    return [(x, y, x + round(w * scale), y + round(h * scale)) for (w, h), (x, y) in zip(sizes, positions)]


def overlaps(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


@pytest.mark.parametrize("n", [1, 2, 4, 5, 9, 12, 40])
def test_layout_never_overlaps_and_stays_on_the_sheet(n):
    sizes = [(700 + 20 * i % 200, 650 + 30 * i % 250) for i in range(n)]
    scale, pos = sheet_layout.plan_layout(sizes, 3000)
    bx = boxes(sizes, scale, pos)
    assert all(0 <= b[0] and 0 <= b[1] and b[2] <= 3000 and b[3] <= 3000 for b in bx)
    assert not any(overlaps(a, b) for i, a in enumerate(bx) for b in bx[i + 1:])


def test_small_packs_keep_natural_size_and_cluster_in_the_middle():
    sizes = [(900, 900)] * 4
    scale, pos = sheet_layout.plan_layout(sizes, 3000)
    assert scale == 1.0                                     # no upscaling, no needless shrinking
    bx = boxes(sizes, scale, pos)
    span = (max(b[2] for b in bx) - min(b[0] for b in bx), max(b[3] for b in bx) - min(b[1] for b in bx))
    assert span[0] < 2200 and span[1] < 2200                # a tight 2x2 cluster, not spread across 3000px
    cx = (min(b[0] for b in bx) + max(b[2] for b in bx)) / 2
    assert abs(cx - 1500) < 5


def test_big_packs_are_scaled_down_uniformly():
    sizes = [(1000, 1000)] * 40
    scale, _ = sheet_layout.plan_layout(sizes, 3000)
    assert 0.3 < scale < 0.5


def test_incomplete_last_row_is_centred():
    sizes = [(900, 900)] * 5                                 # 3 columns: rows of 3 and 2
    scale, pos = sheet_layout.plan_layout(sizes, 3000)
    bx = boxes(sizes, scale, pos)
    full_row, last_row = bx[:3], bx[3:]
    full_mid = (min(b[0] for b in full_row) + max(b[2] for b in full_row)) / 2
    last_mid = (min(b[0] for b in last_row) + max(b[2] for b in last_row)) / 2
    assert abs(full_mid - last_mid) < 5


def test_sheet_contains_every_sticker(tmp_path, small_sheet):
    paths = save_stickers(tmp_path, 6)
    sheet, _ = sheet_layout.create_sheet(paths, tmp_path)
    alpha = Image.open(sheet).getchannel("A")
    assert alpha.getbbox() is not None
    import numpy as np
    from scipy import ndimage

    labels, n = ndimage.label(np.asarray(alpha) > 128)
    areas = ndimage.sum(np.ones_like(labels), labels, range(1, n + 1))
    assert sum(1 for a in areas if a > 500) == 6             # six separate stickers, none merged or lost


def test_planner_mockup_placement_never_overlaps():
    import random

    from src.packaging import mockup_gen

    sizes = [(380, 380)] * 6
    spots = mockup_gen.place_without_overlap(sizes, 1440, 1260, 110, random.Random(1))
    placed = [(x, y, x + w, y + h) for (w, h), s in zip(sizes, spots) if s for x, y in [s]]
    assert len(placed) >= 4 and not any(overlaps(a, b) for i, a in enumerate(placed) for b in placed[i + 1:])


def test_mockups_are_three_square_jpgs_and_deterministic(tmp_path, small_sheet):
    paths = save_stickers(tmp_path, 5)
    sheet, _ = sheet_layout.create_sheet(paths, tmp_path)
    first = mockup_gen.create_mockups(sheet, paths, "Autumn Cozy Vibes", tmp_path)
    assert [p.split("/")[-1] for p in first] == ["mockup_1_hero.jpg", "mockup_2_planner.jpg", "mockup_3_included.jpg"]
    data = [open(p, "rb").read() for p in first]
    for p in first:
        im = Image.open(p)
        assert im.format == "JPEG" and im.size == (2000, 2000)
    again = mockup_gen.create_mockups(sheet, paths, "Autumn Cozy Vibes", tmp_path)
    assert [open(p, "rb").read() for p in again] == data  # same niche, same images


def build_pack(tmp_path, n=5):
    paths = save_stickers(tmp_path, n)
    out = tmp_path / "pack"
    sheet, _ = sheet_layout.create_sheet(paths, out)
    mocks = mockup_gen.create_mockups(sheet, paths, "Autumn Cozy", out)
    return paths, sheet, mocks, out


def test_bundle_contents_and_relative_names(tmp_path, small_sheet):
    paths, sheet, mocks, out = build_pack(tmp_path)
    zip_path = bundler.bundle("Autumn Cozy", paths, sheet, mocks, out)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        assert z.testzip() is None
        assert {"README.txt", "LICENSE.txt", "sticker_sheet.png", "goodnotes_stickers.pdf"} <= set(names)
        assert [n for n in names if n.startswith("stickers/")] == [f"stickers/sticker_0{i}.png" for i in range(1, 6)]
        assert len([n for n in names if n.startswith("previews/")]) == 3
        assert not any(n.startswith("/") or ".." in n for n in names)   # no absolute paths leak in
        readme = z.read("README.txt").decode()
    assert AI_DISCLOSURE in readme and "Goodnotes" in readme and "5 individual" in readme


def test_goodnotes_pdf_is_valid_and_paginates(tmp_path, small_sheet):
    paths = save_stickers(tmp_path, 14)  # 12 per page -> 2 pages
    pdf = tmp_path / "g.pdf"
    bundler.make_goodnotes_pdf(paths, pdf)
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") >= 2


def test_license_forbids_resale():
    assert "may NOT resell" in bundler.LICENSE_TEXT


def test_oversize_pack_warns(tmp_path, small_sheet, monkeypatch, caplog):
    paths, sheet, mocks, out = build_pack(tmp_path)
    monkeypatch.setattr(bundler.config, "get", lambda k, d=None: 0.001 if k == "packaging.max_zip_mb" else d)
    with caplog.at_level("WARNING"):
        bundler.bundle("x", paths, sheet, mocks, out)
    assert "over the" in caplog.text


def test_hero_uses_the_stickers_not_the_sheets_empty_margin(tmp_path, small_sheet):
    paths = save_stickers(tmp_path, 2)
    sheet, _ = sheet_layout.create_sheet(paths, tmp_path)
    hero = mockup_gen.create_mockups(sheet, paths, "x", tmp_path)[0]
    card_area = Image.open(hero).convert("RGB").crop((250, 330, 1750, 1830))
    non_white = sum(1 for p in card_area.resize((150, 150)).getdata() if sum(p) < 720)
    assert non_white > 150 * 150 * 0.08               # stickers fill a meaningful share of the card


def test_four_stickers_all_fit_on_the_planner_page():
    import random

    from src.packaging import mockup_gen

    for seed in range(20):
        spots = mockup_gen.place_without_overlap([(430, 430)] * 4, 1440, 1260, 110, random.Random(seed))
        assert all(spots), f"seed {seed} dropped a sticker"
