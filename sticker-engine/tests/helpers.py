import io
from types import SimpleNamespace

from PIL import Image, ImageDraw


def sticker_image(size=600, color=(220, 40, 60), bg=(255, 255, 255), mode="RGB") -> Image.Image:
    """A flat circle on a plain background: what a good generated sticker looks like."""
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    m = size // 5
    d.ellipse([m, m, size - m, size - m], fill=color, outline=(30, 30, 30), width=8)
    return img.convert(mode)


def stripes_image(size=600) -> Image.Image:
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for x in range(0, size, 60):
        d.rectangle([x, 100, x + 25, size - 100], fill=(20, 20, 160))
    return img


def jpeg_bytes(img=None) -> bytes:
    buf = io.BytesIO()
    (img or sticker_image()).save(buf, "JPEG")
    return buf.getvalue()


def genai_response(image=None, text=None, usage=None, finish_reason="STOP", no_candidates=False):
    """Minimal stand-in for a google-genai GenerateContentResponse."""
    parts = []
    if image is not None:
        parts.append(SimpleNamespace(inline_data=SimpleNamespace(data=image, mime_type="image/jpeg"), text=None))
    if text is not None:
        parts.append(SimpleNamespace(inline_data=None, text=text))
    candidates = [] if no_candidates else [SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason=finish_reason)]
    return SimpleNamespace(
        candidates=candidates,
        text=text,
        usage_metadata=usage or SimpleNamespace(prompt_token_count=100, candidates_token_count=50, thoughts_token_count=0),
        prompt_feedback=None,
    )


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def fake_client(gemini_client, *responses):
    """Swap the SDK client inside a GeminiClient for a scripted fake."""
    models = FakeModels(responses)
    gemini_client.client = SimpleNamespace(models=models)
    return models


def varied_image(seed: int, size=600) -> Image.Image:
    """White background with a seed-specific scatter of shapes, so every seed hashes differently."""
    import random

    rng = random.Random(seed)
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x, y = rng.randint(60, size - 220), rng.randint(60, size - 220)
        w, h = rng.randint(50, 200), rng.randint(50, 200)
        box = [x, y, x + w, y + h]
        fill = (rng.randint(0, 200), rng.randint(0, 200), rng.randint(0, 200))
        d.ellipse(box, fill=fill) if rng.random() < 0.5 else d.rectangle(box, fill=fill)
    return img
