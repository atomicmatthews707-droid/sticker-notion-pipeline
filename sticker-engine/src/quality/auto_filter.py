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
