import logging
import os

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sticker-engine")


def get_logger(name: str) -> logging.Logger:
    """Module logger under the engine's namespace."""
    return logging.getLogger(name)
