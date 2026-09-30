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
