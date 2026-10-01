# Notion Template Engine

A Python CLI that turns a niche idea into a sellable Notion template.

```
niche -> template spec (Gemini, validated JSON) -> built in Notion (databases,
relations, formulas, rollups, views, sample rows, sub-pages)
      -> listing.md (Gumroad/Etsy copy) -> cover_prompts.txt -> output/catalog.csv
```

## Setup

Requires Python 3.10+.

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill it in
```

| Variable | Purpose |
|---|---|
| `NOTION_TOKEN` | Notion integration token. The integration must be shared with the parent page. |
| `NOTION_PARENT_PAGE_ID` | Page the engine builds each template under. |
| `GEMINI_API_KEY` | Gemini API key (spec, listing and cover-prompt generation). |
| `GEMINI_MODEL_TEXT` | Text model ID (default `gemini-3.8-flash`). Never hard-coded. |

## Usage

```bash
python run.py --niche "Freelance Invoice Tracker"
python run.py --niches-file niches.example.txt      # one niche per line
python run.py --niche "Habit Tracker" --dry-run     # no Notion or Gemini calls, no credentials needed
```

Each niche writes to `output/<slug>/`:

- `spec.json`: the validated template spec
- `listing.md`, `cover_prompts.txt`: marketing copy and cover-art prompts
- `result.json`, `notion_url.txt`: IDs and the root page link (live builds)
- `skipped.json`: anything the API could not create. Nothing is dropped silently.

`output/catalog.csv` logs every niche with a status of `Success`, `Partial` (some items skipped) or `Failed`.
A failed niche never stops a batch; the exit code is non-zero if any niche failed.

## How the build works

The builder targets Notion API version `2026-03-11` (databases + data sources + views API).

1. Root page and its content blocks.
2. Databases with every simple property (`initial_data_source.properties`).
3. Relations, then formulas, then rollups, each added by updating the data source.
4. Views, mapping property names to IDs, including filters and sorts.
5. Sample rows, then relation links, then sub-pages.

Every pass is isolated: a failure is recorded in `skipped.json` and the build continues.
Spec validation (`engine/spec_generator.py`) checks relations, formula references, rollups, views and sample rows
before anything touches Notion; validation errors are fed back to the model for up to 3 attempts.

## Tests

```bash
python -m pytest -q
```

Unit tests use a fake Notion client, so they need no credentials or network.

## Not automated

- **Public "Duplicate as template" link.** Publish each template by hand in Notion (Share > Publish, allow duplicates)
  and paste the link into your listing. This step is not automated.
- **Cover images.** The engine writes prompts only.

## Distribution facts (verified 2026-09-30)

- **Gumroad:** 10% + $0.50 per direct sale, plus card processing (about 2.9% + $0.30). 30% on sales through Gumroad Discover.
  Unverified accounts can't withdraw until they reach $100; verify identity early.
- **Notion Marketplace:** 8% + $0.40 per sale (+1% FX for international payouts), $20 minimum payout, 14-day hold.
  Selling directly requires payment verification, which "may take months"; apply early. Until approved, Marketplace
  listings can link out to Gumroad.
- **Earnings:** beginners see "hundreds per month after a few months," and focused catalogs with an email list reach
  "low thousands." Most sources are tool vendors, so treat all figures as optimistic.
