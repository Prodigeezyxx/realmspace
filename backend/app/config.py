"""
Settings, loaded from backend/.env (see .env.example).

One Settings object, built once and cached, so every module reads the same
config and nothing reaches for os.environ directly. Alembic's env.py imports
this too, which is what keeps the migration DB URL and the app DB URL from
drifting apart.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # postgresql+asyncpg://... — the +asyncpg part picks the async driver.
    database_url: str
    test_database_url: str | None = None

    # "local" on the edge box, "cloud" when deployed. See event-bus-spec.md §5:
    # the bus is designed to run on the edge with no network.
    env: str = "local"

    # echo SQL to stdout — useful while learning what SQLAlchemy actually emits
    sql_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
