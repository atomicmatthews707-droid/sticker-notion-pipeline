# Notion Template Engine — Handoff Document (v1, 2026-09-30)

**Read this file first.** Where `README.md` disagrees with this file, this
file wins.

## What this project is

A Python CLI (`run.py`) that turns a niche idea into a sellable Notion
template:

niche → template spec (Claude API, JSON) → build it in the user's Notion
workspace (Notion API) → Gumroad/Etsy listing copy → cover-art prompts →
append to `output/catalog.csv`.

The code is complete in structure but **has never been run against live
APIs**, and the Notion builder targets an outdated Notion API. Your job:
migrate it to the current Notion API, add views and formulas (now possible
through the API), run it for real, and fix whatever breaks.

## Why the migration is required

- Notion split "databases" into **databases** (containers) and **data
  sources** (the actual tables) starting with API version `2025-09-03`.
  The latest version is **`2026-03-11`**.
- The current `notion-client` Python SDK defaults to the new API. Because
  `requirements.txt` says `notion-client>=2.2.1`, a fresh install pulls the
  new SDK, and `engine/notion_builder.py` breaks in three places:
  1. `databases.create(properties=...)`: properties now go under
     `initial_data_source.properties`.
  2. Example rows: pages are created with
     `parent={"type": "data_source_id", "data_source_id": ...}`, not
     `database_id`.
  3. Relations: must reference the target's `data_source_id`, not its
     `database_id`.
- Version `2026-03-11` also renamed `archived` → `in_trash` and replaced
  `after` with `position`. The current code uses neither, but don't
  introduce them.

## What's newly possible (use it)

- **Views API** (launched 2026-03-19): `POST /v1/views` creates table,
  board, list, calendar, timeline, gallery, form, chart, map, and dashboard
  views, with filters, sorts, and group-by. Requires `database_id`,
  `data_source_id`, `name`, `type`; board views need
  `configuration.group_by` with a **property ID**; calendar views need
  `configuration.date_property_id`. A database must always keep at least
  one view.
- **Formula properties** can be set at creation with
  `{"type": "formula", "formula": {"expression": "..."}}`. Since
  2026-08-12, expressions keep `prop("Property Name")` references as
  written, and invalid expressions return a `validation_error`.
- **Rate limits** are generous for this workload (one template is a few
  dozen calls). 429/529 responses include
  `additional_data.retry_after`; honor it.

Before coding, re-read the Notion changelog
(https://developers.notion.com/page/changelog) and the SDK release notes
(https://github.com/ramnes/notion-sdk-py/releases). If the SDK lacks a
helper for views or data sources, call the endpoint through the SDK's
generic `client.request(...)`.

## Tasks, in order

### 1. Dependencies and config
- Pin `notion-client` to the current release that supports data sources
  and views; set `Notion-Version: 2026-03-11` explicitly.
- `.env.example` sets `CLAUDE_MODEL=claude-opus-4-7`. Replace it with a
  current model ID from Anthropic's models page; don't trust the one in the
  file.

### 2. Rewrite `engine/notion_builder.py` as a five-pass build
1. **Root page** under `NOTION_PARENT_PAGE_ID` (unchanged).
2. **Databases**: create each with `initial_data_source.properties`
   containing every simple property (title, text, number, select,
   multi_select, date, checkbox, url, email, phone). Record both the
   `database_id` and `data_sources[0].id` for each.
3. **Relations, then formulas and rollups**: after all data sources exist,
   add relation properties by updating the data source (reference the
   target `data_source_id`). Then add formulas, since they may reference
   relations. Rollups come last because they depend on relations. Wrap each
   in try/except: log and skip on `validation_error`, never abort the
   build.
4. **Views**: retrieve each data source to map property names to property
   IDs, then create the views from the spec. Skip a view whose group-by or
   date property doesn't exist.
5. **Sample rows**, then **sub-pages** (dashboard, instructions). Keep the
   100-blocks-per-request cap; chunk with append-block-children if needed.
   Also fix the existing `_to_block` bug: `bulleted_list` and
   `numbered_list` from the spec must map to `bulleted_list_item` and
   `numbered_list_item`. The current check misses them and falls through
   to paragraph.

Return `{root_url, root_id, database_ids, data_source_ids, view_ids,
skipped: [...]}` and save `skipped` to the output folder so the user knows
what to finish by hand.

### 3. Update the spec contract (`prompts/spec_generator.txt` + validator)
- Views: `{"name", "type", "group_by_property"?, "date_property"?,
  "filter"?, "sorts"?}` using **property names** (the builder maps names
  to IDs).
- Formulas: `{"name", "type": "formula", "expression": "..."}` using
  `prop("Name")` syntax that matches property names in the same spec.
- Relations: `{"name", "type": "relation", "target_db": "<db name>"}`.
  The current code reads the target from `options[0]`, which is fragile.
- Extend `_validate_spec` so a board view's group-by property exists and
  is a select/status/etc., a calendar view's date property exists and is a
  date, and relation targets name a real database in the spec.

### 4. Live test
- Build 3 templates (for example, the first three lines of
  `niches.example.txt`) in a scratch Notion page.
- For each, open it in Notion and check: every database has its views,
  formulas compute without errors, relations link, sample rows appear,
  and the instructions page reads cleanly.
- Fix and repeat until 3 in a row need nothing more than cosmetic touch-ups.

### 5. Duplicate-link step (optional, verify first)
Selling requires a public "Duplicate as template" link. Check whether the
current API can publish a page or enable duplication. If it can, automate
it and write the link to `notion_url.txt`. If not, leave it manual and note
it in the README.

## Distribution facts to put in the README (verified 2026-09-30)

- **Gumroad:** 10% + $0.50 per direct sale, plus card processing (about
  2.9% + $0.30). 30% on sales through Gumroad Discover. Unverified accounts
  can't withdraw until they reach $100; verify identity early.
- **Notion Marketplace:** 8% + $0.40 per sale (+1% FX for international
  payouts), $20 minimum payout, 14-day hold. Selling directly requires
  payment verification, which "may take months"; apply early. Until
  approved, Marketplace listings can link out to Gumroad.
- **Earnings:** replace the README's "$800-3,000/mo" line. The most
  balanced 2026 sources say beginners see "hundreds per month after a few
  months," and focused catalogs with an email list reach "low thousands."
  Most sources are tool vendors, so treat all figures as optimistic.

## Definition of done

- Fresh clone + `.env` → `python run.py --niche "..."` produces a template
  in Notion with databases, relations, formulas, views, sample rows, and
  sub-pages, plus `listing.md` and `cover_prompts.txt`.
- `--dry-run` still works without Notion credentials.
- 3 consecutive live builds need only cosmetic fixes.
- Anything the API couldn't create is listed in `skipped.json`, never
  silently dropped.

## Report back to the user with

- Links to the 3 test templates
- The `skipped.json` from each run
- Anything in this document that turned out to be wrong
