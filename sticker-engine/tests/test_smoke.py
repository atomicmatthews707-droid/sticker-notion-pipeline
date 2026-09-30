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
