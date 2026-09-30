import imagehash
from PIL import Image

def dedupe(image_paths: list[str]) -> list[str]:
    # AI Handoff: Perceptual hashing to remove similar items
    hashes = {}
    kept = []
    for path in image_paths:
        try:
            h = imagehash.phash(Image.open(path))
            # O(N^2) naive check for hamming dist < 8
            if not any(h - existing_h < 8 for existing_h in hashes.values()):
                hashes[path] = h
                kept.append(path)
        except:
            continue
    return kept
