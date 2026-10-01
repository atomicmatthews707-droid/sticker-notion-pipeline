import argparse
import csv
import json
import os
import re
import sys

from dotenv import load_dotenv

from engine.cover_art import generate_cover_prompts
from engine.listing_writer import generate_listing
from engine.notion_builder import build_template
from engine.spec_generator import generate_spec

CATALOG_FIELDS = ["Niche", "Slug", "Status", "Notion URL", "Skipped", "Error"]


def slugify(niche: str) -> str:
    """Filesystem-safe slug; never contains path separators or dots."""
    slug = re.sub(r"[^a-z0-9]+", "_", niche.lower()).strip("_")
    return slug or "niche"


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _append_catalog(row: dict) -> None:
    path = os.path.join("output", "catalog.csv")
    new_file = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def process_niche(niche: str, dry_run: bool) -> dict:
    """Run the full pipeline for one niche. Raises on failure; the caller isolates it."""
    print(f"\nProcessing niche: {niche}")
    slug = slugify(niche)
    out_dir = os.path.join("output", slug)
    os.makedirs(out_dir, exist_ok=True)

    print("1. Generating spec...")
    spec = generate_spec(niche, dry_run=dry_run)
    _write(os.path.join(out_dir, "spec.json"), json.dumps(spec, indent=2))

    print("2. Generating listing...")
    _write(os.path.join(out_dir, "listing.md"), generate_listing(spec, dry_run=dry_run))

    print("3. Generating cover prompts...")
    _write(os.path.join(out_dir, "cover_prompts.txt"), generate_cover_prompts(spec, dry_run=dry_run))

    skipped: list = []
    root_url = ""
    if dry_run:
        print("Dry run enabled. Skipping Notion API calls.")
    else:
        print("4. Building in Notion...")
        token = os.getenv("NOTION_TOKEN") or "proxy-managed"
        parent_id = os.getenv("NOTION_PARENT_PAGE_ID")
        if not parent_id:
            raise ValueError("Missing NOTION_PARENT_PAGE_ID in environment.")
        result = build_template(spec, token, parent_id)
        skipped = result.get("skipped", [])
        root_url = result.get("root_url", "")
        _write(os.path.join(out_dir, "skipped.json"), json.dumps(skipped, indent=2))
        _write(os.path.join(out_dir, "result.json"), json.dumps(result, indent=2))
        _write(os.path.join(out_dir, "notion_url.txt"), root_url)
        print(f"Success! Root URL: {root_url}")

    if skipped:
        print(f"Warning: {len(skipped)} items skipped. See {os.path.join(out_dir, 'skipped.json')}.")
    return {"slug": slug, "root_url": root_url, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Notion Template Engine")
    parser.add_argument("--niche", type=str, help="Single niche to process")
    parser.add_argument("--niches-file", type=str, help="Path to text file containing niches (one per line)")
    parser.add_argument("--dry-run", action="store_true", help="Skip Notion API and just save generated specs")
    args = parser.parse_args()

    load_dotenv()

    niches = []
    if args.niche:
        niches.append(args.niche)
    if args.niches_file:
        with open(args.niches_file, "r", encoding="utf-8") as f:
            niches.extend(line.strip() for line in f if line.strip())

    if not niches:
        print("Error: Must provide --niche or --niches-file")
        return 1

    os.makedirs("output", exist_ok=True)
    failures = 0
    for niche in niches:
        try:
            res = process_niche(niche, args.dry_run)
            _append_catalog({
                "Niche": niche, "Slug": res["slug"], "Status": "Success" if not res["skipped"] else "Partial",
                "Notion URL": res["root_url"], "Skipped": len(res["skipped"]), "Error": "",
            })
        except Exception as e:  # one bad niche must not abort the batch
            failures += 1
            print(f"FAILED: {niche}: {type(e).__name__}: {e}")
            _append_catalog({
                "Niche": niche, "Slug": slugify(niche), "Status": "Failed",
                "Notion URL": "", "Skipped": 0, "Error": f"{type(e).__name__}: {e}",
            })
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
