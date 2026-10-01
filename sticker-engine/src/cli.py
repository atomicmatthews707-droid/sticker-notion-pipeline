"""
Command line for asking for packs and looking at them.

  python -m src.cli make packs/autumn-cozy-vibes.md        shows the cost, then asks you to add --yes
  python -m src.cli make packs/autumn-cozy-vibes.md --yes  builds the pack now
  python -m src.cli make "Cozy mugs" --count 5 --yes       quick form without a file
  python -m src.cli list                                    packs and their status
  python -m src.cli review 3                                rebuild the review page for pack 3
"""

import argparse
import sys
from pathlib import Path

from src.shared import config
from src.shared.design import request_from_markdown
from src.storage import db

IMAGE_USD = 0.067     # per generated image (gemini-3.1-flash-image)
QA_USD = 0.016        # per image judged (measured)
OVERHEAD_USD = 0.01   # subject brainstorm and listing copy


def estimate_usd(count: int) -> float:
    """Upper-end estimate: every sticker generated and judged once."""
    return round(count * (IMAGE_USD + QA_USD) + OVERHEAD_USD, 2)


def _request(args) -> dict:
    target = Path(args.pack)
    if target.suffix.lower() == ".md":
        if not target.exists():
            raise SystemExit(f"No such file: {target}")
        fields = request_from_markdown(target.read_text(encoding="utf-8"))
    else:
        fields = {"name": args.pack, "brief": "", "style": "", "count": None, "subjects": None, "pack_md": ""}
    if args.count:
        fields["count"] = args.count
    if args.brief:
        fields["brief"] = args.brief
    if args.style:
        fields["style"] = args.style
    fields["count"] = fields["count"] or config.int_setting("SUBJECTS_PER_NICHE", "generator.subjects_per_niche", 40)
    return fields


def make(args) -> int:
    db.init_db_sync()  # a brand-new database has no tables yet
    fields = _request(args)
    cost = estimate_usd(fields["count"])
    print(f"Pack: {fields['name']}  |  stickers: {fields['count']}  |  estimated cost: up to ${cost:.2f}")
    print(f"Today's spend so far: ${db.get_today_spend():.2f} of a ${config.daily_budget_usd():.2f} daily cap.")
    if not args.yes:
        print("Nothing was generated. Add --yes to build it.")
        return 0

    from src import main

    if not main._check_budget():
        print("Daily budget already reached. Nothing was generated.")
        return 1
    niche_id = db.queue_request(**fields)
    outcome = main._run_pipeline_for_niche(niche_id, fields["name"], None)
    niche = db.get_niche(niche_id)
    print(f"\nResult: {outcome}. Spend for this pack: ${db.get_niche_spend(niche_id):.2f}")
    if niche and niche["status"] == "FAILED":
        print(f"Why: {(niche['error_msg'] or '').splitlines()[0]}")
    print(f"Review page: {config.output_dir() / f'niche_{niche_id}' / 'review.html'}")
    return 0 if outcome in ("ready", "published") else 1


def list_packs(_args) -> int:
    db.init_db_sync()
    for n in db.list_niches():
        print(f"{n['id']:>4}  {n['status']:<10} {n['name']}  ({n['source']}, {n['updated_at']})")
    return 0


def review(args) -> int:
    from src.packaging.review_page import write_review

    db.init_db_sync()
    print(write_review(args.id))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("make", help="build a pack")
    m.add_argument("pack", help="a pack .md file, or a pack name")
    m.add_argument("--count", type=int)
    m.add_argument("--brief")
    m.add_argument("--style")
    m.add_argument("--yes", action="store_true", help="actually spend money and build it")
    m.set_defaults(fn=make)
    sub.add_parser("list", help="list packs").set_defaults(fn=list_packs)
    r = sub.add_parser("review", help="rebuild a pack's review page")
    r.add_argument("id", type=int)
    r.set_defaults(fn=review)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
