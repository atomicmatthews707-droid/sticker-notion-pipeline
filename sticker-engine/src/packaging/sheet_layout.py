from PIL import Image

def create_sheet(image_paths: list[str], niche: str) -> tuple[str, str]:
    # AI Handoff: Stub for arranging into 3000x3000 sheet
    sheet = Image.new('RGBA', (3000, 3000), (255, 255, 255, 0))
    preview = Image.new('RGB', (3000, 3000), (255, 255, 255))
    
    sheet_path = "sheet.png"
    preview_path = "preview.jpg"
    sheet.save(sheet_path)
    preview.save(preview_path)
    
    return sheet_path, preview_path
