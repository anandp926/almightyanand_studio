import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Every test gets its own SQLite DB, playbook overlay and publish queue, and no .env."""
    from agents import memory_store, playbook, settings

    monkeypatch.setattr(memory_store, "DB_PATH", tmp_path / "memory" / "published_topics.db")
    monkeypatch.setattr(playbook, "STATE_PATH", tmp_path / "memory" / "playbook_state.json")
    monkeypatch.setattr(settings, "ENV_FILE", tmp_path / ".env")
    settings.reload()
    for var in (
        "AFFILIATE_TECH_URL", "AFFILIATE_BOOKS_URL", "AFFILIATE_BROKER_URL", "AFFILIATE_SPIRITUAL_URL",
        "GPU_CONTROLLER_ENABLED", "AUTO_PUBLISH", "MIN_SOURCE_CHARS", "ANALYTICS_MIN_VIEWS",
        "GOOGLE_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "KEEP_TMP",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    settings.reload()
