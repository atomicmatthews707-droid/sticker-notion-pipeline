import rembg
from PIL import Image
import os

def remove_background(image_path: str) -> str:
    # AI Handoff: Use rembg to isolate subject and save as transparent PNG
    with open(image_path, "rb") as f:
        output_bytes = rembg.remove(f.read())
        
    out_path = image_path.replace(".png", "_nobg.png").replace(".jpg", "_nobg.png")
    with open(out_path, "wb") as f:
        f.write(output_bytes)
        
    return out_path
