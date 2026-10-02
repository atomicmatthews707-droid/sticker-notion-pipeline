from src.publisher import PublishUnavailable


def is_enabled() -> bool:
    return False


def create_product(listing_data: dict, zip_path: str, mockup_paths=None) -> str:
    """
    Gumroad cannot be published to automatically yet. Its API could not be checked from the build
    environment (the host was blocked), and a product created without its file attached would be
    broken. Use the publish kit (listing text, files and checklist) to upload by hand.
    """
    raise PublishUnavailable("Gumroad auto-publish is not available; use the publish kit.")
