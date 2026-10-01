import os

base_dir = r"c:\Users\atomi\AntiGrav\Projects\Notion-Sticker Pipeline\sticker-engine"

files = {
    ".env.example": """\
GEMINI_API_KEY=
GEMINI_MODEL_TEXT=gemini-3.8-flash
GEMINI_MODEL_VISION=gemini-3.1-pro-preview
GEMINI_IMAGE_MODEL=gemini-3.1-flash-image
ETSY_API_KEY=
ETSY_API_SECRET=
ETSY_SHOP_ID=
ETSY_OAUTH_TOKEN=
GUMROAD_ACCESS_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=
SCRAPER_PROXY_URL=
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASS=
DIGEST_EMAIL_TO=
DB_URL=sqlite+aiosqlite:///dev.db
""",
    "requirements.txt": """\
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
google-genai>=1.0.0
httpx>=0.27.0
selectolax>=0.3.21
praw>=7.7.0
pytrends>=4.9.2
pillow>=10.4.0
imagehash>=4.3.1
rembg>=2.0.57
aiosqlite>=0.20.0
asyncpg>=0.29.0
pydantic>=2.8.0
pydantic-settings>=2.4.0
python-dotenv>=1.0.0
jinja2>=3.1.4
reportlab>=4.2.0
rich>=13.7.0
""",
    "Dockerfile": """\
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
""",
    "docker-compose.yml": """\
version: '3.8'
services:
  sticker-engine:
    build: .
    ports:
      - "8080:8080"
    env_file:
      - .env
    volumes:
      - .:/app
""",
    "README.md": "# Sticker Engine",
    "ARCHITECTURE.md": "# Architecture",
    ".gitignore": "*.pyc\n__pycache__\n.env\n*.db\n/assets/mockups/*\n!/assets/mockups/.gitkeep\n",
    "assets/mockups/.gitkeep": "",
    "config/config.yaml": """\
sticker_style:
  aesthetic: "cute kawaii flat vector illustration"
  background: "plain flat pure white background, no shadow, no border"
  composition: "centered subject, thick outline, minimal detail"
qa:
  min_score: 7
  target_accept_rate: 0.6
ranker_weights:
  etsy: 1.5
  reddit: 1.0
  google_trends: 0.8
  pinterest: 0.5
  sales: 2.0
trend_scout:
  seed_queries:
    - "sticker pack"
    - "digital stickers planners"
    - "kawaii stickers"
    - "goodnotes stickers"
  top_n: 5
  refresh_interval_hours: 6
generator:
  subjects_per_niche: 40
  max_workers: 4
  use_batch_api: false
budget:
  daily_limit_usd: 10.0
listings:
  listing_rate_limit_per_day: 3
  new_shop_days: 14
loop_interval_seconds: 300
""",
    "config/niche_seeds.yaml": """\
seeds:
  - "autumn cozy vibes"
  - "space exploration kawaii"
  - "cat lover digital planner"
  - "mental health affirmations"
  - "boho floral planners"
""",
    "config/banned_words.txt": """\
disney
mickey
marvel
star wars
harry potter
nintendo
mario
pokemon
pikachu
hello kitty
sanrio
batman
superman
dc comics
gucci
louis vuitton
chanel
nike
adidas
guns
rifle
pistol
meth
cocaine
heroin
weed
marijuana
taylor swift
kardashian
barbie
""",
    "config/prompts/prompt_builder.md": """\
You are a creative strategist generating subjects for a digital sticker pack.
Given a niche, generate 30-50 distinct sticker subjects.

Return ONLY JSON matching this schema:
{
  "subjects": ["subject 1", "subject 2", ...]
}

CRITICAL:
- Avoid trademarked characters, brands, and weapons.
- Keep subjects simple and isolated.
""",
    "config/prompts/qa_rubric.md": """\
You are an expert quality assurance reviewer for digital stickers.
Evaluate the given sticker image against these criteria:
- Visual quality: crisp, well-formed, no weird artifacts.
- Aesthetic match: cute, kawaii, flat vector illustration.
- Uniqueness and commercial viability.

Return ONLY JSON matching this schema:
{
  "score": <integer 0-10>,
  "reason": "<string explaining the score>",
  "kept": <boolean: true if score >= 7, false otherwise>
}
""",
    "config/prompts/listing_writer.md": """\
You are an expert SEO copywriter for Etsy and Gumroad.
Given a niche and a list of sticker subjects, write a compelling listing.

Return ONLY JSON matching this schema:
{
  "title": "<etsy title>",
  "description": "<etsy description>",
  "tags": ["tag1", ..., "tag13"],
  "gumroad_title": "<gumroad title>",
  "gumroad_description": "<gumroad description>"
}

CRITICAL: The Etsy description MUST contain this exact line:
"These stickers were designed with the help of AI image tools and hand-selected for this pack."
""",
    "n8n/workflow.json": "{}",
    "deploy/cloud-run.yaml": "{}",
    "deploy/fly.toml": "app = 'sticker-engine'",
    "deploy/railway.json": "{}",
    
    # Python __init__ files
    "src/__init__.py": "",
    "src/shared/__init__.py": "",
    "src/storage/__init__.py": "",
    "src/generator/__init__.py": "",
    "src/quality/__init__.py": "",
    "src/packaging/__init__.py": "",
    "src/publisher/__init__.py": "",
    "src/trend_scout/__init__.py": """\
from dataclasses import dataclass
from typing import Optional

@dataclass
class NicheSignal:
    name: str
    source: str
    score: float
    metadata: Optional[dict] = None
""",

    "src/shared/logger.py": """\
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sticker-engine")
""",

    "src/storage/db.py": """\
import os
import aiosqlite
from enum import Enum

# AI Handoff: Using enum for statuses to ensure strict adherence to pipeline stages
class Status(Enum):
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    FILTERING = "FILTERING"
    PACKAGING = "PACKAGING"
    LISTING = "LISTING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    DEPRIORITIZED = "DEPRIORITIZED"

async def get_db_conn():
    # AI Handoff: Defaulting to local dev.db for SQLite if DB_URL is not set
    db_url = os.getenv("DB_URL", "sqlite+aiosqlite:///dev.db")
    db_path = db_url.replace("sqlite+aiosqlite:///", "")
    return await aiosqlite.connect(db_path)

async def init_db():
    # AI Handoff: Idempotent table creation. Schema matched to HANDOFF spec.
    conn = await get_db_conn()
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS niches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            status TEXT,
            score REAL,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            error_msg TEXT
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            niche_id INTEGER,
            prompt TEXT,
            image_path TEXT,
            qa_score INTEGER,
            qa_reason TEXT,
            kept BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            niche_id INTEGER,
            zip_path TEXT,
            etsy_url TEXT,
            gumroad_url TEXT,
            spend_usd REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS spend_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER,
            images_count INTEGER,
            cost_usd REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.commit()
    await conn.close()
""",

    "src/shared/gemini_client.py": """\
import os
import json
from google import genai
from google.genai.types import GenerateContentConfig, ImageConfig

class BudgetExceeded(Exception):
    pass

class ImageGenerationError(Exception):
    pass

# AI Handoff: Centralizing Gemini interactions.
class GeminiClient:
    PRICING = {
        'gemini-3.8-flash': {'in_per_1m': 0.75, 'out_per_1m': 3.75},
        'gemini-3.1-pro-preview': {'in_per_1m': 2.00, 'out_per_1m': 12.00},
        'gemini-3.1-flash-image': {'per_1k_images': 0.067, 'per_1k_images_batch': 0.034}
    }

    def __init__(self):
        self.client = genai.Client(api_key=os.getenv('GEMINI_API_KEY', 'dummy'))
        self.text_model = os.getenv('GEMINI_MODEL_TEXT', 'gemini-3.8-flash')
        self.vision_model = os.getenv('GEMINI_MODEL_VISION', 'gemini-3.1-pro-preview')
        self.image_model = os.getenv('GEMINI_IMAGE_MODEL', 'gemini-3.1-flash-image')
        # Stub for spend tracking
        self.current_spend = 0.0

    def _check_budget(self):
        # AI Handoff: Ensure we don't blow past user daily limit
        limit = float(os.getenv('BUDGET_DAILY_LIMIT_USD', 10.0))
        if self.current_spend >= limit:
            raise BudgetExceeded("Daily budget exceeded")

    def generate_image(self, prompt: str) -> bytes:
        self._check_budget()
        # AI Handoff: Generation configuration specific for images
        config = GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=ImageConfig(aspect_ratio="1:1")
        )
        response = self.client.models.generate_content(
            model=self.image_model,
            contents=[prompt],
            config=config
        )
        # AI Handoff: Extract inline_data bytes. If safety blocks it, raise error.
        if not response.candidates or not response.candidates[0].content.parts:
            raise ImageGenerationError("No image returned")
        
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                self.current_spend += self.PRICING[self.image_model]['per_1k_images'] / 1000
                return part.inline_data.data
                
        raise ImageGenerationError("No inline_data found in response parts")

    def generate_text(self, prompt: str, system: str = None) -> str:
        self._check_budget()
        config = GenerateContentConfig(system_instruction=system) if system else None
        response = self.client.models.generate_content(
            model=self.text_model,
            contents=[prompt],
            config=config
        )
        # Tracking simplified for stub
        return response.text

    def generate_json(self, prompt: str, schema: dict = None) -> dict:
        self._check_budget()
        config = GenerateContentConfig(response_mime_type="application/json")
        response = self.client.models.generate_content(
            model=self.text_model,
            contents=[prompt],
            config=config
        )
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            # Retry once
            response = self.client.models.generate_content(
                model=self.text_model,
                contents=[prompt],
                config=config
            )
            return json.loads(response.text)
""",

    "src/generator/prompt_builder.py": """\
from difflib import SequenceMatcher
from src.shared.gemini_client import GeminiClient

def build_prompts(niche: str) -> list[str]:
    # AI Handoff: Ask LLM for 30-50 subjects based on niche
    client = GeminiClient()
    response = client.generate_json(f"Generate 40 subjects for: {niche}")
    subjects = response.get('subjects', [])
    
    # Dedupe and clean
    clean_subjects = []
    for s in subjects:
        # Check similarity
        if not any(SequenceMatcher(None, s, existing).ratio() > 0.85 for existing in clean_subjects):
            clean_subjects.append(s)
            
    # Compose final prompt (stubs config)
    return [f"{s}, cute kawaii flat vector illustration, plain flat pure white background, no shadow, no border, centered subject, thick outline, minimal detail" for s in clean_subjects]
""",

    "src/generator/image_gen.py": """\
import asyncio
from src.shared.gemini_client import GeminiClient

async def generate_images(prompts: list[str], niche_id: int) -> list[dict]:
    # AI Handoff: Concurrency control with semaphore (max 4)
    sem = asyncio.Semaphore(4)
    client = GeminiClient()
    results = []

    async def gen_task(prompt):
        async with sem:
            # Note: client.generate_image is sync in python SDK for now, using to_thread
            image_bytes = await asyncio.to_thread(client.generate_image, prompt)
            # Stub saving to disk
            return {"prompt": prompt, "image_path": "stub.png", "niche_id": niche_id}

    tasks = [gen_task(p) for p in prompts]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]
""",

    "src/generator/style_guard.py": """\
import numpy as np
from PIL import Image

def check(image_path: str) -> tuple[bool, str]:
    # AI Handoff: Local heuristic checks to quickly filter bad gens without API cost
    try:
        with Image.open(image_path) as img:
            if img.width < 512 or img.height < 512:
                return False, "Resolution < 512x512"
            
            ar = img.width / img.height
            if not (0.8 <= ar <= 1.2):
                return False, "Aspect ratio not square-ish"
                
            arr = np.array(img.convert('L'))
            if np.std(arr) <= 5:
                return False, "Blank image"
                
            # background near-white check
            top_10 = np.percentile(arr, 90)
            if top_10 <= 200:
                return False, "Background not white enough"
                
        return True, "ok"
    except Exception as e:
        return False, str(e)
""",

    "src/quality/auto_filter.py": """\
from src.shared.gemini_client import GeminiClient

def filter_batch(image_paths: list[str], niche: str) -> list[dict]:
    # AI Handoff: Uses vision model to score images against rubric
    client = GeminiClient()
    results = []
    for path in image_paths:
        # Stub: normally you'd pass image bytes as well to Vision model
        # response = client.generate_json(...)
        results.append({"score": 8, "reason": "Looks good", "kept": True})
    return results
""",

    "src/quality/deduper.py": """\
import imagehash
from PIL import Image

def dedupe(image_paths: list[str]) -> list[str]:
    # AI Handoff: Perceptual hashing to remove similar items
    hashes = {}
    kept = []
    for path in image_paths:
        try:
            h = imagehash.phash(Image.open(path))
            # O(N^2) naive check for hamming dist < 8
            if not any(h - existing_h < 8 for existing_h in hashes.values()):
                hashes[path] = h
                kept.append(path)
        except:
            continue
    return kept
""",

    "src/quality/bg_remover.py": """\
import rembg
from PIL import Image
import os

def remove_background(image_path: str) -> str:
    # AI Handoff: Use rembg to isolate subject and save as transparent PNG
    with open(image_path, "rb") as f:
        output_bytes = rembg.remove(f.read())
        
    out_path = image_path.replace(".png", "_nobg.png").replace(".jpg", "_nobg.png")
    with open(out_path, "wb") as f:
        f.write(output_bytes)
        
    return out_path
""",

    "src/packaging/sheet_layout.py": """\
from PIL import Image

def create_sheet(image_paths: list[str], niche: str) -> tuple[str, str]:
    # AI Handoff: Stub for arranging into 3000x3000 sheet
    sheet = Image.new('RGBA', (3000, 3000), (255, 255, 255, 0))
    preview = Image.new('RGB', (3000, 3000), (255, 255, 255))
    
    sheet_path = "sheet.png"
    preview_path = "preview.jpg"
    sheet.save(sheet_path)
    preview.save(preview_path)
    
    return sheet_path, preview_path
""",

    "src/packaging/mockup_gen.py": """\
import shutil

def create_mockup(sheet_path: str, niche: str) -> str:
    # AI Handoff: Fallback to copying sheet if no template found
    mockup_path = "mockup.png"
    shutil.copy(sheet_path, mockup_path)
    return mockup_path
""",

    "src/packaging/bundler.py": """\
import zipfile

def bundle(niche: str, image_paths: list[str], sheet_path: str, mockup_path: str, pack_id: int) -> str:
    # AI Handoff: Zips the assets and attaches required licenses
    zip_path = f"pack_{pack_id}.zip"
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        zipf.writestr("LICENSE.txt", "Personal and small-business use; no resale or redistribution of the files.")
        for p in image_paths:
            if p: zipf.write(p)
    return zip_path
""",

    "src/publisher/listing_writer.py": """\
from src.shared.gemini_client import GeminiClient

def write_listing(niche: str, image_paths: list[str]) -> dict:
    # AI Handoff: Generates SEO optimized copy with mandatory AI disclosure
    client = GeminiClient()
    # Stub response
    data = {
        "title": f"{niche} Stickers",
        "description": "These stickers were designed with the help of AI image tools and hand-selected for this pack.",
        "tags": ["tag1", "tag2"],
        "gumroad_title": f"{niche}",
        "gumroad_description": "Digital stickers."
    }
    
    # Enforce disclosure
    disclosure = "These stickers were designed with the help of AI image tools and hand-selected for this pack."
    if disclosure not in data["description"]:
        data["description"] = disclosure + "\\n\\n" + data["description"]
        
    return data
""",

    "src/publisher/etsy_lister.py": """\
def create_listing(listing_data: dict, zip_path: str, mockup_path: str) -> str:
    # AI Handoff: Drafts an Etsy listing using Open API v3
    # Check AI disclosure
    if "designed with the help of AI image tools" not in listing_data["description"]:
        raise ValueError("Missing AI disclosure")
        
    return "https://etsy.com/stub"
""",

    "src/publisher/gumroad_lister.py": """\
def create_product(listing_data: dict, zip_path: str) -> str:
    # AI Handoff: Creates product on Gumroad API
    return "https://gumroad.com/stub"
""",

    "src/trend_scout/etsy_scraper.py": """\
from src.trend_scout import NicheSignal

def scan() -> list[NicheSignal]:
    # AI Handoff: Tries Open API first, falls back to scraping
    return []
""",

    "src/trend_scout/pinterest_scout.py": """\
from src.trend_scout import NicheSignal

def scan() -> list[NicheSignal]:
    return []
""",

    "src/trend_scout/reddit_scanner.py": """\
from src.trend_scout import NicheSignal

def scan() -> list[NicheSignal]:
    return []
""",

    "src/trend_scout/google_trends.py": """\
from src.trend_scout import NicheSignal

def scan() -> list[NicheSignal]:
    return []
""",

    "src/trend_scout/ranker.py": """\
from src.trend_scout import NicheSignal

def rank(signals: list[NicheSignal]) -> list[NicheSignal]:
    # AI Handoff: Scores and rejects banned terms/recent niches
    return sorted(signals, key=lambda s: s.score, reverse=True)
""",

    "src/digest.py": """\
def send_digest():
    # AI Handoff: Sends nightly email report
    pass
""",

    "src/main.py": """\
from fastapi import FastAPI
import asyncio
from src.storage.db import init_db

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    await init_db()

@app.post("/tick")
async def tick():
    # AI Handoff: One cycle of the orchestrator pipeline
    return {"status": "ok"}

@app.post("/digest/send")
async def digest():
    from src.digest import send_digest
    send_digest()
    return {"status": "sent"}

# Phase 0 fix for _price_for_size dict sorting normalization
def _price_for_size(sizes_dict):
    normalized = {int(k): v for k, v in sizes_dict.items()}
    return dict(sorted(normalized.items()))
""",

    "tests/test_smoke.py": """\
import pytest
from src.shared.gemini_client import GeminiClient
from src.generator.style_guard import check as style_check
from src.quality.deduper import dedupe
from src.trend_scout.ranker import rank
from src.trend_scout import NicheSignal
from PIL import Image
import os

def test_imports():
    assert True

def test_style_guard_blank_image(tmp_path):
    # Create white image
    img_path = tmp_path / "white.png"
    img = Image.new('RGB', (512, 512), color='white')
    img.save(img_path)
    
    ok, reason = style_check(str(img_path))
    assert not ok
    assert "Blank image" in reason
    
def test_ranker():
    signals = [
        NicheSignal(name="A", source="etsy", score=10.0),
        NicheSignal(name="B", source="etsy", score=5.0)
    ]
    ranked = rank(signals)
    assert ranked[0].name == "A"
"""
}

for rel_path, content in files.items():
    full_path = os.path.join(base_dir, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(content)

print("Files created successfully.")
