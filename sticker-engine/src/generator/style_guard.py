import numpy as np
from PIL import Image

def check(image_path: str) -> tuple[bool, str]:
    # AI Handoff: Local heuristic checks to quickly filter bad gens without API cost
    try:
        with Image.open(image_path) as img:
            if img.width < 512 or img.height < 512:
                return False, "Resolution < 512x512"
            
            ar = img.width / img.height
            if not (0.8 <= ar <= 1.2):
                return False, "Aspect ratio not square-ish"
                
            arr = np.array(img.convert('L'))
            if np.std(arr) <= 5:
                return False, "Blank image"
                
            # background near-white check
            top_10 = np.percentile(arr, 90)
            if top_10 <= 200:
                return False, "Background not white enough"
                
        return True, "ok"
    except Exception as e:
        return False, str(e)
