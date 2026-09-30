import os
import json
from google import genai
from google.genai.types import GenerateContentConfig, ImageConfig

class BudgetExceeded(Exception):
    pass

class ImageGenerationError(Exception):
    pass

# AI Handoff: Centralizing Gemini interactions.
class GeminiClient:
    PRICING = {
        'gemini-3.8-flash': {'in_per_1m': 0.75, 'out_per_1m': 3.75},
        'gemini-3.1-pro-preview': {'in_per_1m': 2.00, 'out_per_1m': 12.00},
        'gemini-3.1-flash-image': {'per_1k_images': 0.067, 'per_1k_images_batch': 0.034}
    }

    def __init__(self):
        self.client = genai.Client(api_key=os.getenv('GEMINI_API_KEY', 'dummy'))
        self.text_model = os.getenv('GEMINI_MODEL_TEXT', 'gemini-3.8-flash')
        self.vision_model = os.getenv('GEMINI_MODEL_VISION', 'gemini-3.1-pro-preview')
        self.image_model = os.getenv('GEMINI_IMAGE_MODEL', 'gemini-3.1-flash-image')
        # Stub for spend tracking
        self.current_spend = 0.0

    def _check_budget(self):
        # AI Handoff: Ensure we don't blow past user daily limit
        limit = float(os.getenv('BUDGET_DAILY_LIMIT_USD', 10.0))
        if self.current_spend >= limit:
            raise BudgetExceeded("Daily budget exceeded")

    def generate_image(self, prompt: str) -> bytes:
        self._check_budget()
        # AI Handoff: Generation configuration specific for images
        config = GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=ImageConfig(aspect_ratio="1:1")
        )
        response = self.client.models.generate_content(
            model=self.image_model,
            contents=[prompt],
            config=config
        )
        # AI Handoff: Extract inline_data bytes. If safety blocks it, raise error.
        if not response.candidates or not response.candidates[0].content.parts:
            raise ImageGenerationError("No image returned")
        
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                self.current_spend += self.PRICING[self.image_model]['per_1k_images'] / 1000
                return part.inline_data.data
                
        raise ImageGenerationError("No inline_data found in response parts")

    def generate_text(self, prompt: str, system: str = None) -> str:
        self._check_budget()
        config = GenerateContentConfig(system_instruction=system) if system else None
        response = self.client.models.generate_content(
            model=self.text_model,
            contents=[prompt],
            config=config
        )
        # Tracking simplified for stub
        return response.text

    def generate_json(self, prompt: str, schema: dict = None) -> dict:
        self._check_budget()
        config = GenerateContentConfig(response_mime_type="application/json")
        response = self.client.models.generate_content(
            model=self.text_model,
            contents=[prompt],
            config=config
        )
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            # Retry once
            response = self.client.models.generate_content(
                model=self.text_model,
                contents=[prompt],
                config=config
            )
            return json.loads(response.text)
