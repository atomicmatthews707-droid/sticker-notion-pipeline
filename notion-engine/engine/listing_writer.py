import os
from anthropic import Anthropic

def generate_listing(spec: dict, dry_run: bool = False) -> str:
    # AI Handoff: Generates the gumroad/etsy copy.
    if dry_run:
        return f"# {spec.get('template_name', 'Template')} Listing\n\nBuy this awesome template today!"

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20240620")
    
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "listing_writer.txt")
    with open(prompt_path, "r") as f:
        system_prompt = f.read()

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_prompt,
        messages=[
            {"role": "user", "content": f"Write a listing for this spec: {spec}"}
        ]
    )
    return response.content[0].text
