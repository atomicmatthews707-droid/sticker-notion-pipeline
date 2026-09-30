# Sticker Engine — Handoff Document (v2, 2026-09-30)

**Read this file first. It is the complete build spec.** Where
`README.md` or `ARCHITECTURE.md` disagree with this file (model names,
costs, Etsy fields), this file wins; update those two docs to match as you
go.

You are picking up a partially-scaffolded autonomous sticker-pack generation
engine. The architecture, config, deployment files, and shared foundations
exist. Your job is to (0) fix the stale pieces listed in Phase 0, then
(1-6) implement every module stub marked `TODO(cursor)` and wire them into
the orchestrator.

The user does not want to babysit this. It must run 24/7 in the cloud,
discover its own niches, generate stickers, filter them autonomously,
package them, and list them on Etsy/Gumroad, with the user only reviewing
a daily digest of what shipped.

---

## What changed since v1 (read before touching code)

v1 of this handoff was written against information that is now out of date.
These are verified changes as of 2026-09-30:

1. **Imagen 4 is shut down.** `imagen-4.0-generate-001` and
   `imagen-4.0-fast-generate-001` were shut down on 2026-08-17. The
   replacement is `gemini-3.1-flash-image` (a.k.a. Nano Banana 2). It is
   called through `generate_content` with an image response modality, NOT
   through `generate_images`. The built `gemini_client.generate_image()`
   uses the dead API and must be rewritten (Phase 0).
2. **Gemini 2.5 text models are restricted for new projects.** Google's
   deprecations page says new projects should use current models. Use:
   - Text (prompt building, listing copy): `gemini-3.8-flash`
   - Vision QA (the quality gate): `gemini-3.1-pro-preview`
     (it is a preview model; if it is unavailable or unstable, fall back to
     `gemini-3.8-flash` and tighten the QA thresholds)
3. **Etsy requires AI disclosure.** Fully AI-generated digital downloads are
   allowed only if disclosed. Undisclosed AI listings are being removed.
   Publishing `who_made: "i_did"` with no disclosure, as v1 specified, is a
   policy violation that risks the shop. See "Etsy compliance" below.
4. **Gumroad fees and payouts.** 10% + $0.50 per direct sale (plus card
   processing, roughly 2.9% + $0.30); 30% on sales that come through
   Gumroad Discover. Since March 2026, unverified accounts cannot withdraw
   until the balance reaches $100 (drops to $10 after identity
   verification).
5. **Costs went up.** Image generation is now about $0.067 per 1K image
   (standard) or $0.034 (Batch API). See the updated cost model below.

**Verify before you build:** model IDs and prices change often. Before
writing code, confirm the model IDs against
https://ai.google.dev/gemini-api/docs/models and
https://ai.google.dev/gemini-api/docs/deprecations, and the exact image-output
SDK call against the current Gemini image-generation docs. Keep every model
ID in env vars (already the pattern), never hard-coded.

---

## Product goal

Autonomously produce and publish digital sticker packs. Each pack contains
30-45 transparent PNGs + a Goodnotes-compatible PDF, priced $5-12 on Etsy.
Target: 2-5 packs/day, 60-150/month, hands-off after initial setup.

## Non-negotiable constraints

1. **No dependency on Claude, Cowork, Antigravity, or any IDE.** This is a
   standalone Python project that runs on Cloud Run, Fly.io, Railway, or
   behind n8n.
2. **Gemini API only** for LLM + image generation. No Anthropic, no OpenAI,
   no Midjourney. Uses the `google-genai` SDK.
3. **Autonomous niche discovery.** The user provides zero prompts after
   setup. The engine mines trends from Etsy search, Pinterest, Reddit,
   Google Trends, and its own sales data.
4. **Idempotent + resumable.** A crash mid-run must not lose work. Every
   stage checkpoints to SQLite (dev) or Postgres (prod).
5. **Budgeted.** Config caps daily Gemini spend, packs generated, and
   listings created. Overshoots pause the engine.
6. **Auditable.** Every generated image, prompt, and decision is logged
   with reasoning so the user can review the daily digest.
7. **Marketplace-compliant.** Every listing carries the AI disclosure.
   No trademarked characters, brands, or celebrity likenesses.

---

## Architecture

```
                    ┌─────────────────┐
                    │   Orchestrator  │  main.py — the 24/7 loop
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
  ┌──────────┐         ┌──────────┐         ┌──────────┐
  │  Trend   │  niche  │Generator │ images  │ Quality  │
  │  Scout   │────────▶│ (Gemini  │────────▶│ (Gemini  │
  │          │         │  image)  │         │  vision) │
  └──────────┘         └──────────┘         └──────────┘
                                                  │
                                                  ▼
                                   Packaging → Publisher → Digest
```

Each stage reads from and writes to a shared SQLite/Postgres DB
(`storage/db.py`). The orchestrator advances niches through states:

```
QUEUED → GENERATING → FILTERING → PACKAGING → LISTING → PUBLISHED
                                                     └─ FAILED / DEPRIORITIZED
```

---

## Phase 0 — fix stale code (do this first)

These files were marked "BUILT" in v1 but depend on things that changed.

| File | Problem | Fix |
|---|---|---|
| `src/shared/gemini_client.py` → `generate_image()` | Calls `client.models.generate_images` on Imagen 4, which is shut down | Rewrite to call `client.models.generate_content(model=<image model>, contents=[prompt], config=GenerateContentConfig(response_modalities=["IMAGE"], image_config=ImageConfig(aspect_ratio="1:1")))`, then pull bytes from the first response part that has `inline_data`. Confirm the exact field names against current docs. Raise a clear error if no image part comes back (safety blocks return text only). |
| `src/shared/gemini_client.py` → `PRICING` + default model IDs | Lists 2.5 models and Imagen prices | Replace with: `gemini-3.8-flash` ($0.75 in / $3.75 out per 1M tokens through 2026-12-31, then $1.50 / $7.50), `gemini-3.1-pro-preview` ($2.00 in / $12.00 out per 1M tokens, prompts ≤200k), `gemini-3.1-flash-image` ($0.067 per 1K image; $0.034 via Batch). Note the per-1K-token math in `_price_text` must become per-1M or use matching units. |
| `.env.example` | Old model IDs | `GEMINI_MODEL_TEXT=gemini-3.8-flash`, `GEMINI_MODEL_VISION=gemini-3.1-pro-preview`, `GEMINI_IMAGE_MODEL=gemini-3.1-flash-image` |
| `requirements.txt` | `google-genai>=0.5.0` is too old for `ImageConfig` | Pin to the current `google-genai` release that supports image output config. |
| `config/config.yaml` → `sticker_style.background` | Asks for a transparent PNG, which the new model is not confirmed to produce | Change to `"plain flat pure white background, no shadow, no border"`. Transparency comes from `bg_remover.py` (rembg), which is already built. |
| `src/generator/style_guard.py` (spec) | v1 spec rejected images without transparency | Do NOT require alpha before background removal. Check resolution, square-ish aspect, non-blank content, and near-white background instead. |
| `src/publisher/etsy_lister.py` (spec) | Wrong `who_made` / `when_made` values, unverified taxonomy ID, no AI disclosure | See "Etsy compliance" below. |
| `src/main.py` → `_price_for_size` | Iterates dict keys that YAML may load as ints or strings | Normalize keys to int before sorting. |

After Phase 0, run `pytest tests/` and one manual `generate_image()` call to
prove the image path works before building anything downstream.

---

## Phase 1-6 — modules to build (in order)

### 1. `src/trend_scout/` — the "cook autonomously" brain

**Priority: HIGHEST.** Without this, the engine can't self-feed. Until it
works, `main.py` falls back to `config/niche_seeds.yaml`.

- `etsy_scraper.py` — Scrape Etsy search for "sticker pack" and adjacent
  queries. Extract listing titles, tags, favorites, review counts. Use
  `httpx` + `selectolax`. Rate-limit to 1 req/2 sec. Rotate user agents.
  If Etsy blocks, fall back to `SCRAPER_PROXY_URL`. Prefer Etsy's official
  API (`findAllListingsActive` with keywords) where it gives the same data,
  since it is more stable than HTML scraping and doesn't risk the API key.
- `pinterest_scout.py` — No official public search API. Try an unofficial
  client; degrade gracefully to an empty list when it breaks.
- `reddit_scanner.py` — PRAW client. Scan r/PlannerAddicts,
  r/DigitalPlanning, r/GoodNotes, r/etsysellers. Weekly top posts.
- `google_trends.py` — `pytrends`. Rising queries around the seed terms.
  Sleep between calls; pytrends is rate-limited and occasionally breaks.
- `ranker.py` — Aggregates signals, z-scores per source, weighted sum per
  `config.ranker_weights`, rejects banned/trademarked terms and anything
  published in the last 30 days, queues the top N.

Every scout implements:
```python
def scan() -> list[NicheSignal]:
    """Return raw signals. Ranker aggregates and scores."""
```
`NicheSignal` is already defined in `trend_scout/__init__.py`.

### 2. `src/generator/`

- `prompt_builder.py` — Given a niche, ask `gemini-3.8-flash` (via
  `generate_json`) for 30-50 distinct sticker subjects using
  `config/prompts/prompt_builder.md`. Compose each final image prompt as
  `f"{subject}, {aesthetic}, {background}, {composition}"` from
  `config.sticker_style`. Dedupe subjects (difflib ratio > 0.85). Reject
  subjects matching a banned-words list (brands, characters, celebrities,
  weapons, drugs); create `config/banned_words.txt`.
- `image_gen.py` — Stub exists with the calling pattern. Add: skip prompts
  that already have an image in the DB (resume), 2-4 worker concurrency max.
  **Optional cost win:** since this pipeline isn't latency-sensitive, use the
  Gemini Batch API for image generation (about half price). Make it a config
  flag; default off until the synchronous path is proven.
- `style_guard.py` — Fast local checks (see Phase 0 table). No API calls.

### 3. `src/quality/` — autonomous culling

- `auto_filter.py` — The replacement for the user's manual scan. Send each
  image to the vision model with `config/prompts/qa_rubric.md`. Parse the
  JSON, store `qa_score`, `qa_reason`, `kept`. Thresholds from
  `config.qa`. Respect `BudgetExceeded` (return partial results). Log the
  acceptance rate per batch; healthy is 50-70% kept.
- `deduper.py` — `imagehash.phash`, Hamming distance < 8 = duplicate; keep
  the highest `qa_score` per group.
- `bg_remover.py` — **Built.** Runs after filtering. Now load-bearing,
  because the image model outputs white backgrounds.

### 4. `src/packaging/`

- `sheet_layout.py` — 3000×3000 RGBA grid of stickers (PIL), plus a
  white-background JPG preview.
- `mockup_gen.py` — Composite the sheet onto mockup templates in
  `assets/mockups/` (create the folder; start with simple rectangle
  pastes, perspective warp is v2). Mockup template images must be ones the
  user owns or has a license for.
- `bundler.py` — Zip of individual PNGs, sheet, Goodnotes PDF, previews,
  README.txt, LICENSE.txt (personal and small-business use; no resale or
  redistribution of the files).

### 5. `src/publisher/`

- `listing_writer.py` — `gemini-3.8-flash` + `config/prompts/listing_writer.md`.
  **Add to the prompt:** the Etsy description must contain the AI
  disclosure line (see below). Return title, description, 13 tags,
  Gumroad title/description.
- `etsy_lister.py` — Etsy Open API v3, OAuth2. See compliance section.
- `gumroad_lister.py` — Gumroad API v2. Simpler than Etsy.

### 6. `src/digest.py`

Nightly email: niches discovered, packs generated/rejected (with sample
rejects), packs published with URLs, spend by model, QA acceptance rate,
tomorrow's queue, errors. Jinja2 + inline CSS. SMTP or SendGrid. Exposed at
`POST /digest/send` for Cloud Scheduler / n8n.

---

## Etsy compliance (must-follow)

1. **AI disclosure in every description.** Put a plain line near the top,
   for example: *"These stickers were designed with the help of AI image
   tools and hand-selected for this pack."* Enforce it in code: the
   publisher refuses to publish a description that lacks the line.
2. **Attribution setting.** Etsy's 2026 guidance for AI-made digital items
   points to a "designed by" style attribution rather than "made by". Look
   up the current allowed values for `who_made` and any newer production /
   AI fields in the live Etsy Open API v3 reference before coding. Do not
   use `who_made: "i_did"` without the disclosure.
3. **`when_made`.** `made_to_order` is not appropriate for digital
   downloads. Use the current-decade value from Etsy's allowed list.
4. **`type: "download"`.** Required on `createDraftListing`, or Etsy asks
   for a shipping profile.
5. **Taxonomy ID.** v1 hardcoded `6883`. That number was never verified.
   Fetch the real node for digital stickers/printables from
   `getSellerTaxonomyNodes` and store it in config.
6. **No trademarks.** The banned-words list and prompt-builder rules exist
   for this. A single IP complaint can suspend the shop.
7. **Start slow.** New shops that list dozens of items on day one get
   flagged. Cap listings at 2-3/day for the first two weeks (config).

---

## Deployment paths

Configs for all of these are already in `deploy/` and `n8n/`.

- **GCP Cloud Run** (scale to zero; ticks come from Cloud Scheduler hitting
  `POST /tick`; set `DISABLE_LOOP=1`). Note: Cloud Run's disk is ephemeral,
  so use Postgres + S3/GCS, not SQLite + local `output/`.
- **Fly.io** (always-on, simplest; background loop runs; SQLite on a volume
  is fine).
- **Railway** (one-click).
- **n8n** (import `n8n/workflow.json`; deploy the engine with
  `DISABLE_LOOP=1`; set `STICKER_ENGINE_URL`).
- **Docker anywhere** (`docker compose up -d`). Note the compose file
  starts Postgres but `.env.example` defaults to SQLite; set `DB_URL` to the
  Postgres service if you want to use it, and add `psycopg[binary]` to
  requirements.

---

## Environment variables

See `.env.example` (update model IDs per Phase 0). Required to run the
pipeline end-to-end: `GEMINI_API_KEY`, Etsy OAuth creds + shop ID. Optional:
Gumroad token, Reddit creds, proxy URL, SMTP, S3.

---

## Cost model (updated 2026-09-30, verify before relying on it)

Per pack of ~50 generated images:

| Item | Standard | With Batch API |
|---|---|---|
| Image generation, 50 × gemini-3.1-flash-image | $3.35 | $1.70 |
| QA, 50 × gemini-3.1-pro-preview (~1.3K in / 200 out tokens each) | ~$0.25 | ~$0.25 |
| Prompts + listing copy (gemini-3.8-flash) | ~$0.05 | ~$0.05 |
| **Total per pack** | **~$3.65** | **~$2.00** |

Revenue side (approximate; confirm current Etsy fees): at an $8 price, the
seller keeps very roughly $6.50-7.00 after Etsy's listing, transaction, and
payment-processing fees. Break-even is about 0.55 sales per pack
(standard) or about 0.3 (batch). Budget cap default ($10/day) allows
~2-3 packs/day on the standard path.

---

## The 24/7 loop (already written in `src/main.py`)

Budget check → refresh trends every 6 h (falls back to seeds) → take the
highest-scored queued niche → generate → filter → dedupe → remove
backgrounds → sheet + mockups + bundle → listing copy → publish → mark
published → sleep. Any stage raising `NotImplementedError` marks the niche
FAILED with that message, so you can see which stub is next.

---

## Definition of done

- `docker compose up` runs end-to-end on a fresh clone with only `.env`
  filled in.
- One full cycle (niche → published Etsy listing with AI disclosure)
  completes in under 30 minutes and under $4 of Gemini spend.
- Auto-filter rejects 30-50% of raw images on a fresh niche.
- User receives the daily digest email.
- Killing the process mid-cycle and restarting resumes the same niche.
- `pytest tests/` passes, with at least one test per module.

---

## Traps to avoid

- **Don't trust any model ID from memory, including the ones in this
  file.** Check the models and deprecations pages on build day.
- **Safety blocks return text, not an image.** Handle a missing image part
  as a skip, not a crash.
- **Use the Pro-tier model for QA.** Filtering replaces human review; a
  lenient filter tanks the shop's reviews.
- **Don't skip the deduper.** Image models repeat themselves in batches.
- **Don't publish without mockups.** Sticker listings without lifestyle
  mockups convert far worse.
- **Don't scrape Etsy hard.** Rate-limit strictly; prefer the official API.
- **Do not commit `output/`, `data/`, or `.env`.** `.gitignore` covers them.

---

## Files map

```
sticker-engine/
├── HANDOFF.md              ← this file
├── README.md, ARCHITECTURE.md
├── Dockerfile, docker-compose.yml, .env.example*, .gitignore
├── requirements.txt*
├── config/
│   ├── config.yaml*        (* = Phase 0 fix needed)
│   ├── niche_seeds.yaml
│   └── prompts/ qa_rubric.md, prompt_builder.md, listing_writer.md*
├── src/
│   ├── main.py*            orchestrator + FastAPI (built)
│   ├── digest.py           TODO
│   ├── trend_scout/        __init__ built; 5 scouts + ranker TODO
│   ├── generator/          prompt_builder TODO, image_gen stub, style_guard TODO
│   ├── quality/            auto_filter TODO, deduper TODO, bg_remover built
│   ├── packaging/          sheet_layout, mockup_gen, bundler TODO
│   ├── publisher/          etsy, gumroad, listing_writer TODO
│   ├── storage/db.py       built
│   └── shared/             gemini_client* (Phase 0), logger built
├── n8n/workflow.json, deploy/{cloud-run.yaml, fly.toml, railway.json}
└── tests/test_smoke.py
```

## When you're done, report to the user

- Which cloud target you deployed to
- The first published listing URL (with the AI disclosure visible)
- A screenshot of the daily digest
- Actual $/pack from the first 5 runs, versus the estimate above
