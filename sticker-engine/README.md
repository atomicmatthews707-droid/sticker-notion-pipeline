# Sticker Engine

Generates digital sticker packs with Gemini, filters them automatically, and (once finished) packages and lists them.
`HANDOFF.md` is the full spec. Where this file disagrees with it, the handoff wins.

## Status

| Stage | Module | State |
|---|---|---|
| Trend scouting | `src/trend_scout/` | Etsy, Reddit, Google Trends scouts and the ranker are built (not run live: Etsy is blocked in the build environment, Reddit has no credentials). Pinterest returns nothing. |
| Prompts | `src/generator/prompt_builder.py` | Built and proven live. |
| Image generation | `src/generator/image_gen.py` | Built and proven live: resumable, budget-aware, saves real PNGs. |
| Style guard | `src/generator/style_guard.py` | Built. Local checks only. |
| Cutout | `src/quality/bg_remover.py` | Built and proven live. Default is a border flood fill that never hollows out white areas. `rembg` is optional. |
| QA filter | `src/quality/auto_filter.py` | Built and proven live. Judges the finished cutout on a grey backdrop. Calibrated against real and synthetic flaws. |
| Dedupe | `src/quality/deduper.py` | Built. |
| Packaging | `src/packaging/` | Built and proven live: sheet, three listing images made in code, Goodnotes PDF, zip. |
| Listing copy | `src/publisher/listing_writer.py` | Built and proven live. Validated; AI-disclosure line enforced in code. |
| Publish kit | `src/publisher/kit.py` | Built. Listing text, files and a checklist for uploading by hand. |
| Digest | `src/digest.py` | Built. Saved as HTML daily; emailed when SMTP is configured (email sending untested). |
| Etsy | `src/publisher/etsy_lister.py` | **Disabled** unless `ETSY_ENABLED=1`. Never run live (host blocked, no credentials). Values need checking before use. |
| Gumroad | `src/publisher/gumroad_lister.py` | **Not available.** Use the publish kit. |

## What a finished pack looks like

When a niche passes every stage it ends as `READY`, not `PUBLISHED`, unless a marketplace actually published it.
Look in `output/niche_<id>/pack/publish_kit/`: `etsy_listing.txt`, `gumroad_listing.txt`, `CHECKLIST.md`,
`sticker_pack.zip` and `previews/`. Upload by hand, or enable Etsy once it has been verified. The daily digest lists
every `READY` pack.

## Quality gate

Each sticker is cut out first, then judged by the vision model while sitting on a grey backdrop, so cutout
problems (haze, see-through areas, fringes) are visible. A score of 7 or more is kept (`config qa.min_score`).
Known limitations:

- White enclosed by a loop (a mug handle, a small vine curl) stays white. The cutout never deletes interior content,
  so a white fill (a ghost, a blanket) can never become see-through. The QA rubric does not penalise this.
- In a 5-sticker test the filter accepted 4 of 5 (80%); in an earlier test it accepted 2 of 5 (40%). Five samples
  cannot show the true rate. Watch the digest: a long-run rate above 90% or below 20% means the rubric needs work.

## Cost

Measured on a 5-sticker run: about $0.42 for 5 generated stickers ($0.335 images, about $0.08 vision QA, under $0.01
text). The vision QA costs about $0.016 per image, more than the handoff estimated, because the model spends tokens
thinking. Extrapolated to 40 stickers per pack: roughly $3.40 before rejects.

## Ask for a pack (and change how it looks)

Everything is plain markdown, so you can edit it like a document.

- **`DESIGN.md`**: the shop-wide look. Sections: Style, Composition, Palette, Avoid, Voice, Notes. It feeds every image
  prompt, the quality filter and the listing text. The file shipped here reproduces the test runs exactly.
- **`packs/<name>.md`**: one pack. Copy `packs/_TEMPLATE.md`. Only the title is required. Add `count: 5`, a Brief in your
  own words, an exact Subjects list (the AI then does not brainstorm), and any Style, Palette, Avoid or Voice overrides.
- The white background is fixed on purpose (cutout depends on it) and is not editable.

```bash
python -m src.cli make packs/autumn-cozy-vibes.md        # prints the estimated cost and builds nothing
python -m src.cli make packs/autumn-cozy-vibes.md --yes  # spends the money and builds the pack
python -m src.cli list                                    # packs and their status
python -m src.cli review 3                                # rebuild pack 3's review page
```

Or send the file to a running engine: `POST /niches` with `{"markdown": "<the file's text>"}`.

Every finished pack gets a `review.html` next to it: each sticker on white, dark, colour and transparent backgrounds
with its quality score, the listing images, the listing text and what the run cost.

The default daily spending cap is $2 (`budget.daily_limit_usd`). Raise it when you add credit.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env     # fill in GEMINI_API_KEY and ENGINE_API_TOKEN
uvicorn src.main:app --port 8080
```

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Gemini API key. |
| `GEMINI_MODEL_TEXT`, `GEMINI_MODEL_VISION`, `GEMINI_IMAGE_MODEL` | Model IDs. Never hard-coded. |
| `BUDGET_DAILY_LIMIT_USD` | Daily spend cap (default 10). Counted from the database, so restarts cannot reset it. |
| `ENGINE_API_TOKEN` | Required for `POST /tick` and `POST /digest/send`. Send `Authorization: Bearer <token>`. |
| `ALLOW_UNAUTHENTICATED=1` | Local development only: skips the token check. |
| `DB_URL` | SQLite only (`sqlite+aiosqlite:///path.db`). |
| `OUTPUT_DIR` | Where images and packs are written (default `output/`). |
| `DISABLE_LOOP=1` | Tick mode: one cycle per `POST /tick` instead of a background loop. |
| `SUBJECTS_PER_NICHE`, `MIN_PACK_IMAGES` | Test-run overrides (production defaults: 40 stickers, 10 minimum). A 5-sticker test uses 5 and 3. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `DIGEST_EMAIL_TO` | Digest email. Without them the digest is saved but not emailed. |

## How it behaves

- **Resumable.** State lives in SQLite. After a crash the same niche resumes at the stage it was in; finished images and
  saved prompts are reused, so nothing is regenerated or paid for twice.
- **Budgeted.** Every call is priced from the response and logged. When the daily cap is reached the niche pauses (it is
  not failed) and continues the next day.
- **Safe to expose.** The control endpoints fail closed: without `ENGINE_API_TOKEN` they return 503.
- **Retry cap.** A niche that has failed twice is not re-queued by trend refresh.

## Deployment notes

- The database is SQLite, so use a persistent volume (Docker, Fly, Railway). Cloud Run's disk is ephemeral and is not
  supported until a Postgres backend is added. `docker-compose.yml` mounts a named volume at `/data`.
- Background removal needs no model by default. If you switch to `rembg`, use `isnet-general-use` (Apache-2.0); `rembg`'s
  default model is non-commercial.
- `deploy/fly.toml`, `deploy/railway.json` and `n8n/workflow.json` are written from the platforms' documented formats
  and have not been run. `deploy/cloud-run.yaml` explains why Cloud Run is unsupported.

## Tests

```bash
python -m pytest -q
```

No credentials or network needed. Gemini and `rembg` are replaced with fakes.
