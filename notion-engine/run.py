import os
import sys
import json
import argparse
from datetime import datetime
from dotenv import load_dotenv

from engine.spec_generator import generate_spec
from engine.notion_builder import build_template
from engine.listing_writer import generate_listing
from engine.cover_art import generate_cover_prompts

def process_niche(niche: str, dry_run: bool):
    # AI Handoff: The main execution pipeline.
    print(f"\nProcessing niche: {niche}")
    slug = niche.lower().replace(" ", "_").replace("-", "_")
    out_dir = os.path.join("output", slug)
    os.makedirs(out_dir, exist_ok=True)
    
    print("1. Generating spec...")
    spec = generate_spec(niche, dry_run=dry_run)
    with open(os.path.join(out_dir, "spec.json"), "w") as f:
        json.dump(spec, f, indent=2)
        
    print("2. Generating listing...")
    listing = generate_listing(spec, dry_run=dry_run)
    with open(os.path.join(out_dir, "listing.md"), "w") as f:
        f.write(listing)
        
    print("3. Generating cover prompts...")
    prompts = generate_cover_prompts(spec, dry_run=dry_run)
    with open(os.path.join(out_dir, "cover_prompts.txt"), "w") as f:
        f.write(prompts)
        
    if dry_run:
        print("Dry run enabled. Skipping Notion API calls.")
        print(f"Generated Spec:\n{json.dumps(spec, indent=2)}")
        skipped = []
    else:
        print("4. Building in Notion...")
        token = os.getenv("NOTION_TOKEN")
        parent_id = os.getenv("NOTION_PARENT_PAGE_ID")
        if not token or not parent_id:
            raise ValueError("Missing NOTION_TOKEN or NOTION_PARENT_PAGE_ID in environment.")
            
        result = build_template(spec, token, parent_id)
        skipped = result.get("skipped", [])
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(os.path.join(out_dir, f"skipped_{timestamp}.json"), "w") as f:
            json.dump(skipped, f, indent=2)
            
        print(f"Success! Root URL: {result.get('root_url')}")
    
    # Append to catalog
    catalog_path = os.path.join("output", "catalog.csv")
    file_exists = os.path.exists(catalog_path)
    with open(catalog_path, "a") as f:
        if not file_exists:
            f.write("Niche,Slug,Status\n")
        f.write(f"{niche},{slug},Success\n")
        
    if skipped:
        print(f"Warning: {len(skipped)} items skipped. Check skipped.json in output dir.")

def main():
    parser = argparse.ArgumentParser(description="Notion Template Engine")
    parser.add_argument("--niche", type=str, help="Single niche to process")
    parser.add_argument("--niches-file", type=str, help="Path to text file containing niches (one per line)")
    parser.add_argument("--dry-run", action="store_true", help="Skip Notion API and just print/save generated specs")
    args = parser.parse_args()

    # .env not required in dry-run mode
    if not args.dry_run:
        load_dotenv()

    niches = []
    if args.niche:
        niches.append(args.niche)
    if args.niches_file:
        with open(args.niches_file, "r") as f:
            lines = [l.strip() for l in f if l.strip()]
            niches.extend(lines)
            
    if not niches:
        print("Error: Must provide --niche or --niches-file")
        sys.exit(1)
        
    for niche in niches:
        process_niche(niche, args.dry_run)

if __name__ == "__main__":
    main()
