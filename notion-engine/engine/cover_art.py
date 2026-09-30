import os
from anthropic import Anthropic

def generate_cover_prompts(spec: dict, dry_run: bool = False) -> str:
    # AI Handoff: Generates text prompts for cover art generation. No image gen done here.
    if dry_run:
        return f"Cover Prompt 1: Minimalist aesthetic for {spec.get('template_name', 'Template')}."

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20240620")
    
    response = client.messages.create(
        model=model,
        max_tokens=512,
        system="You are an expert prompt engineer. Generate 3 image generation prompts for cover art.",
        messages=[
            {"role": "user", "content": f"Generate cover art prompts for this template: {spec.get('template_name')}"}
        ]
    )
    return response.content[0].text
