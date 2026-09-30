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
        data["description"] = disclosure + "\n\n" + data["description"]
        
    return data
