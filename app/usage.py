"""Client-consumption logging (the flywheel's 'consume' step)."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from .models import UsageEvent


async def log_usage(session: AsyncSession, cusips: Iterable[str], kind: str = "view") -> None:
    for c in cusips:
        session.add(UsageEvent(cusip=c, kind=kind))
    await session.commit()
