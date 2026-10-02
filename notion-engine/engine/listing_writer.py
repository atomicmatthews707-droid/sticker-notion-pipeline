import json
import os

from engine.llm import generate_text


def generate_listing(spec: dict, dry_run: bool = False) -> str:
    """Generate the Gumroad/Etsy listing copy (Markdown) for a template spec."""
    if dry_run:
        return f"# {spec.get('template_name', 'Template')} Listing\n\nBuy this awesome template today!"

    prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "listing_writer.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    return generate_text(
        system_prompt,
        f"Write a listing for this spec:\n{json.dumps(spec, indent=2)}",
        max_output_tokens=4096,
    )
