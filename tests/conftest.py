"""Shared test fixtures.

Tests run against a throwaway SQLite database (via aiosqlite) and the built-in
synthetic Tradeweb feed, so the suite needs no Postgres and no credentials.
Environment is set *before* the app modules import, so the module-level engine
binds to SQLite.
"""
from __future__ import annotations

import os
import pathlib

# Must be set before importing app.* (engine is created at import time).
_DB_PATH = pathlib.Path(__file__).parent / "_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"
os.environ["TRADEWEB_USE_MOCK"] = "true"
os.environ["ENVIRONMENT"] = "test"

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


# SQLite ignores foreign keys unless asked; turn them on so the test DB enforces
# referential integrity the way Postgres does (catches parent-before-child bugs).
@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_fk(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@pytest_asyncio.fixture
async def client():
    # Fresh schema per test for isolation.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await seed()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
