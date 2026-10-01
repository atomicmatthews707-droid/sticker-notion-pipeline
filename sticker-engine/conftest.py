import os

# Must be set before src.main is imported: keeps the app in tick mode so tests never start the loop thread.
os.environ["DISABLE_LOOP"] = "1"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own database and output folder, and a clean environment."""
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    for var in ("BUDGET_DAILY_LIMIT_USD", "ENGINE_API_TOKEN", "ALLOW_UNAUTHENTICATED", "CONFIG_PATH", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from src.shared import config
    from src.storage import db

    config.reload_config()
    db.init_db_sync()
    yield
    config.reload_config()
