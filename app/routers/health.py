"""Liveness/readiness checks."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    return {"status": "ok", "service": s.app_name, "environment": s.environment}


@router.get("/ready")
async def ready(session: AsyncSession = Depends(get_session)) -> dict:
    """Confirms the database is reachable."""
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
