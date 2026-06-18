"""Persistence for contributor reliability scores.

Recalibration against executed trades writes here, and the consensus engine reads
here, so a correction survives restarts and feeds forward into the next price
(closing the loop) instead of living in a process-memory dict.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .clients.contributors import baseline_reliability
from .models import ContributorReliability


async def load_reliabilities(session: AsyncSession) -> dict[str, float]:
    """Return persisted reliabilities, seeding from the baseline on first use."""
    rows = (await session.scalars(select(ContributorReliability))).all()
    if not rows:
        base = baseline_reliability()
        for name, rel in base.items():
            session.add(ContributorReliability(name=name, reliability=rel))
        await session.commit()
        return base
    return {r.name: float(r.reliability) for r in rows}


async def save_reliabilities(session: AsyncSession, updates: dict[str, float]) -> None:
    """Upsert new reliability values (clamped) and bump each contributor's obs count."""
    rows = {r.name: r for r in (await session.scalars(select(ContributorReliability))).all()}
    for name, val in updates.items():
        val = round(max(0.30, min(0.999, float(val))), 3)
        rec = rows.get(name)
        if rec is None:
            session.add(ContributorReliability(name=name, reliability=val, n_obs=1))
        else:
            rec.reliability = val
            rec.n_obs += 1
            rec.updated_at = dt.datetime.now(dt.timezone.utc)
    await session.commit()
