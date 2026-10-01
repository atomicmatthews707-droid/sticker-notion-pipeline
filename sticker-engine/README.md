# Sticker Engine

Generates digital sticker packs with Gemini, filters them automatically, and (once finished) packages and lists them.
`HANDOFF.md` is the full spec. Where this file disagrees with it, the handoff wins.

## Status

| Stage | Module | State |
|---|---|---|
| Trend scouting | `src/trend_scout/` | Etsy, Reddit, Google Trends scouts and the ranker are built. Pinterest returns nothing. |
| Prompts | `src/generator/prompt_builder.py` | Built. Banned-term and near-duplicate filtering. |
| Image generation | `src/generator/image_gen.py` | Built and proven live: resumable, budget-aware, saves real PNGs. |
| Style guard | `src/generator/style_guard.py` | Built. Local checks only, no API cost. |
| QA filter | `src/quality/auto_filter.py` | **Not built.** Raises `NotImplementedError`. |
| Dedupe | `src/quality/deduper.py` | Built. |
| Background removal | `src/quality/bg_remover.py` | Built and proven live with `rembg` (open-licence model). The no-ML `floodfill` fallback leaves white inside holes such as mug handles. |
| Packaging | `src/packaging/` | **Not built.** Raises `NotImplementedError`. |
| Listing copy, Gumroad, digest | `src/publisher/`, `src/digest.py` | **Not built.** Raise `NotImplementedError`. |
| Etsy lister | `src/publisher/etsy_lister.py` | Written, never run, and its compliance values are unverified. Do not enable. |

Unbuilt stages fail loudly: the niche is marked `FAILED` with the reason. Nothing is ever faked as published.

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
- Background removal uses `isnet-general-use` (Apache-2.0). `rembg`'s default model is non-commercial; do not switch to it.
  The first run downloads the model (about 170 MB) into `U2NET_HOME`.
- `deploy/cloud-run.yaml`, `deploy/railway.json` and `n8n/workflow.json` are empty placeholders for now.

## Tests

```bash
python -m pytest -q
```

No credentials or network needed. Gemini and `rembg` are replaced with fakes.
