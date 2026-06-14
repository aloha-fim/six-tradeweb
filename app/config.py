"""Application configuration.

Settings are read from environment variables (or a local .env file) so the
service can run identically in local, CI, and container environments.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://six:six@localhost:5432/six_tradeweb",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )

    # --- Tradeweb data feed -------------------------------------------------
    # SIX is a *distribution partner* for Tradeweb data products. These point
    # at the Tradeweb data API; when unset the service falls back to a built-in
    # mock transport so the app runs end-to-end with no external credentials.
    tradeweb_base_url: str = Field(default="https://api.tradeweb.example/v1")
    tradeweb_api_key: str | None = Field(default=None)
    tradeweb_use_mock: bool = Field(
        default=True,
        description="Serve synthetic Tradeweb data instead of calling the live API.",
    )

    # --- Service metadata ---------------------------------------------------
    app_name: str = Field(default="SIX × Tradeweb Data Service")
    environment: str = Field(default="local")


@lru_cache
def get_settings() -> Settings:
    return Settings()
