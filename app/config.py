"""Application configuration.

Settings are read from environment variables (or a local .env file) so the
service can run identically in local, CI, and container environments.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
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

    @field_validator("database_url")
    @classmethod
    def _coerce_async_driver(cls, v: str) -> str:
        # Managed Postgres (Render, Heroku, Railway, Supabase, ...) hands out
        # 'postgresql://' or the legacy 'postgres://'; the async engine needs
        # the asyncpg driver named explicitly. Coerce so the platform's
        # DATABASE_URL works unchanged. Leave already-qualified URLs alone.
        if "+" in v.split("://", 1)[0]:
            return v
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix):]
        return v

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

    # Populate a fresh database on startup (seed reference data + refresh the
    # muni Ai-Price universe) so a cloud deploy comes up ready to demo. Safe to
    # leave on: seeding is idempotent and refresh just re-fetches.
    bootstrap_on_startup: bool = Field(default=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
