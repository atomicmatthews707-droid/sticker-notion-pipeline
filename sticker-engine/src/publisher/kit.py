"""Ready-to-upload publish kit: everything needed to list a pack by hand in a few minutes."""

import json
import shutil
from pathlib import Path

from src.shared.compliance import AI_DISCLOSURE


def write_kit(niche: str, listing: dict, zip_path: str, mockup_paths: list[str], out_dir: Path) -> str:
    kit = Path(out_dir) / "publish_kit"
    kit.mkdir(parents=True, exist_ok=True)
    (kit / "listing.json").write_text(json.dumps(listing, indent=2), encoding="utf-8")
    tags = ", ".join(listing["tags"])
    (kit / "etsy_listing.txt").write_text(
        f"TITLE\n{listing['title']}\n\nPRICE (USD)\n{listing['price_usd']:.2f}\n\nTAGS (13)\n{tags}\n\n"
        f"DESCRIPTION\n{listing['description']}\n", encoding="utf-8")
    (kit / "gumroad_listing.txt").write_text(
        f"NAME\n{listing['gumroad_title']}\n\nPRICE (USD)\n{listing['price_usd']:.2f}\n\n"
        f"DESCRIPTION\n{listing['gumroad_description']}\n", encoding="utf-8")
    (kit / "CHECKLIST.md").write_text(
        f"# Publish checklist: {niche}\n\n"
        "Etsy\n"
        "1. New listing, type **Digital download**.\n"
        "2. Paste the title, description and the 13 tags from `etsy_listing.txt`; set the price.\n"
        "3. Upload `previews/` images (hero first) as the listing photos and `sticker_pack.zip` as the file.\n"
        f"4. Keep this line in the description (Etsy requires AI disclosure): \"{AI_DISCLOSURE}\"\n"
        "5. Answer Etsy's own 'who made it' and AI questions truthfully, then publish.\n\n"
        "Gumroad\n"
        "1. New product, type Digital product. Paste `gumroad_listing.txt`, set the price.\n"
        "2. Upload `sticker_pack.zip` and the hero preview as the cover, then publish.\n", encoding="utf-8")
    shutil.copy(zip_path, kit / "sticker_pack.zip")
    (kit / "previews").mkdir(exist_ok=True)
    for p in mockup_paths:
        shutil.copy(p, kit / "previews" / Path(p).name)
    return str(kit)
