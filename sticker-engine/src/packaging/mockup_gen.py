"""Listing images built in code (no stock photos, so there are no licensing questions)."""

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from src.packaging.common import average_color, fit, font, grid_shape, load_sticker, wrap_text

SIZE = 2000


def _tint(color, mix=0.82):
    """Pale version of a colour, for a background that suits the pack."""
    base = color or (235, 228, 255)
    return tuple(round(c + (255 - c) * mix) for c in base)


def _shadowed(canvas: Image.Image, card: Image.Image, xy: tuple[int, int], blur=28, offset=18) -> None:
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [xy[0], xy[1] + offset, xy[0] + card.width, xy[1] + card.height + offset], radius=48, fill=(0, 0, 0, 70)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))
    canvas.alpha_composite(card, xy)


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, *img.size], radius=radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def _headline(canvas: Image.Image, title: str, subtitle: str) -> None:
    d = ImageDraw.Draw(canvas)
    fnt = font(112)
    lines = wrap_text(d, title.upper(), fnt, SIZE - 240)[:2]
    y = 90
    for line in lines:
        d.text((SIZE // 2, y), line, font=fnt, fill=(40, 36, 60, 255), anchor="ma")
        y += 128
    d.text((SIZE // 2, SIZE - 120), subtitle, font=font(60), fill=(70, 66, 96, 255), anchor="mm")


def _greedy_place(sizes, width, height, top, rng, margin, tries, step):
    placed: list[tuple[int, int, int, int]] = []

    def free(x: int, y: int, w: int, h: int) -> bool:
        box = (x - margin, y - margin, x + w + margin, y + h + margin)
        return all(box[2] <= b[0] or box[0] >= b[2] or box[3] <= b[1] or box[1] >= b[3] for b in placed)

    out: list = []
    for w, h in sizes:
        xs, ys = range(margin, max(margin, width - w - margin) + 1), range(top, max(top, height - h - margin) + 1)
        candidates = [(rng.choice(xs), rng.choice(ys)) for _ in range(tries)]
        candidates += [(x, y) for y in ys[::step] for x in xs[::step]]
        spot = next(((x, y) for x, y in candidates if free(x, y, w, h)), None)
        if spot:
            placed.append((spot[0] - margin, spot[1] - margin, spot[0] + w + margin, spot[1] + h + margin))
        out.append(spot)
    return out


def place_without_overlap(sizes: list[tuple[int, int]], width: int, height: int, top: int,
                          rng: random.Random, margin: int = 24, tries: int = 60, step: int = 40, attempts: int = 40) -> list:
    """
    Top-left positions inside the area with no two boxes overlapping (None where nothing fits).
    A greedy pass can block itself (a first sticker dropped in the middle), so whole arrangements are
    retried and the one that places the most stickers wins. Deterministic for a given rng.
    """
    best: list = []
    for _ in range(attempts):
        out = _greedy_place(sizes, width, height, top, rng, margin, tries, step)
        if sum(p is not None for p in out) > sum(p is not None for p in best):
            best = out
        if all(p is not None for p in best):
            break
    return best


def create_mockups(sheet_path: str, image_paths: list[str], niche: str, out_dir: Path) -> list[str]:
    """Three listing images: hero, a tablet planner scene, and an 'included' grid. First is the main photo."""
    out_dir = Path(out_dir)
    stickers = [load_sticker(p) for p in image_paths]
    bg = _tint(average_color(stickers))
    rng = random.Random(niche)  # same niche -> same layout, so a re-run does not change the images
    subtitle = f"{len(stickers)} stickers  |  transparent PNG  |  Goodnotes PDF"
    paths = []

    # 1. Hero: the sheet on a card
    hero = Image.new("RGBA", (SIZE, SIZE), bg + (255,))
    sheet = Image.open(sheet_path).convert("RGBA")
    bbox = sheet.getchannel("A").getbbox()
    if bbox:  # use the card's space for stickers, not for the sheet's empty margin
        pad = 60
        sheet = sheet.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(sheet.width, bbox[2] + pad), min(sheet.height, bbox[3] + pad)))
    fitted = fit(sheet, 1440)
    fitted = fitted.resize((round(sheet.width * 1440 / max(sheet.size)), round(sheet.height * 1440 / max(sheet.size))), Image.LANCZOS)
    card = Image.new("RGBA", (1500, 1500), (255, 255, 255, 255))
    card.alpha_composite(fitted, ((1500 - fitted.width) // 2, (1500 - fitted.height) // 2))
    _shadowed(hero, _rounded(card, 48), ((SIZE - 1500) // 2, 330))
    _headline(hero, niche, subtitle)
    paths.append(str(out_dir / "mockup_1_hero.jpg"))
    hero.convert("RGB").save(paths[-1], "JPEG", quality=90)

    # 2. Tablet planner scene with stickers placed on a page
    scene = Image.new("RGBA", (SIZE, SIZE), bg + (255,))
    device = Image.new("RGBA", (1560, 1380), (36, 38, 48, 255))
    page = Image.new("RGBA", (1440, 1260), (255, 255, 255, 255))
    pd = ImageDraw.Draw(page)
    for y in range(120, 1260, 70):
        pd.line([(60, y), (1380, y)], fill=(226, 228, 238, 255), width=3)
    pd.rectangle([60, 40, 520, 76], fill=(214, 216, 230, 255))
    arts = [fit(st, rng.randint(330, 430)).rotate(rng.uniform(-12, 12), expand=True, resample=Image.BICUBIC) for st in stickers[:8]]
    spots = place_without_overlap([a.size for a in arts], 1440, 1260, 110, rng)
    for art, spot in zip(arts, spots):
        if spot:
            page.alpha_composite(art, spot)
    device.alpha_composite(_rounded(page, 36), (60, 60))
    _shadowed(scene, _rounded(device, 90), ((SIZE - 1560) // 2, 360))
    _headline(scene, niche, "Use in Goodnotes, Notability and other planner apps")
    paths.append(str(out_dir / "mockup_2_planner.jpg"))
    scene.convert("RGB").save(paths[-1], "JPEG", quality=90)

    # 3. What's included: individual stickers on white tiles
    inc = Image.new("RGBA", (SIZE, SIZE), bg + (255,))
    cols, rows = grid_shape(min(len(stickers), 12))
    tile = min(1700 // cols, 1250 // rows)
    left, top = (SIZE - tile * cols) // 2, 400
    for i, st in enumerate(stickers[:12]):
        x, y = left + (i % cols) * tile, top + (i // cols) * tile
        t = Image.new("RGBA", (tile - 30, tile - 30), (255, 255, 255, 255))
        art = fit(st, tile - 130)
        t.alpha_composite(art, ((t.width - art.width) // 2, (t.height - art.height) // 2))
        _shadowed(inc, _rounded(t, 36), (x + 15, y + 15), blur=14, offset=8)
    _headline(inc, "What's included", f"{len(stickers)} individual PNG files + sticker sheet + Goodnotes PDF")
    paths.append(str(out_dir / "mockup_3_included.jpg"))
    inc.convert("RGB").save(paths[-1], "JPEG", quality=90)
    return paths
