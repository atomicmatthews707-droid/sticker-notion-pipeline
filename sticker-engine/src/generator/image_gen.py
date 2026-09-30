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
