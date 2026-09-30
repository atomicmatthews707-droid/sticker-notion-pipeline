import zipfile

def bundle(niche: str, image_paths: list[str], sheet_path: str, mockup_path: str, pack_id: int) -> str:
    # AI Handoff: Zips the assets and attaches required licenses
    zip_path = f"pack_{pack_id}.zip"
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        zipf.writestr("LICENSE.txt", "Personal and small-business use; no resale or redistribution of the files.")
        for p in image_paths:
            if p: zipf.write(p)
    return zip_path
