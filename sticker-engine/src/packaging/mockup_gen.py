import shutil

def create_mockup(sheet_path: str, niche: str) -> str:
    # AI Handoff: Fallback to copying sheet if no template found
    mockup_path = "mockup.png"
    shutil.copy(sheet_path, mockup_path)
    return mockup_path
