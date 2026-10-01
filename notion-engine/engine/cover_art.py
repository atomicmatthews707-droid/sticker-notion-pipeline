from engine.llm import generate_text


def generate_cover_prompts(spec: dict, dry_run: bool = False) -> str:
    """Generate text prompts for cover art. No image generation happens here."""
    if dry_run:
        return f"Cover Prompt 1: Minimalist aesthetic for {spec.get('template_name', 'Template')}."

    return generate_text(
        "You are an expert prompt engineer. Generate 3 image generation prompts for cover art.",
        f"Generate cover art prompts for this template: {spec.get('template_name')}",
        max_output_tokens=2048,
    )
