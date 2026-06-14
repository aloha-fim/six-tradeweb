"""Seed a small reference universe of SIX-listed instruments."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from .db import SessionLocal, init_models
from .models import AssetClass, Holding, Instrument, Portfolio

_SEED = [
    ("CH0012032048", "ROG", "Roche Holding AG", AssetClass.EQUITY, "CHF"),
    ("CH0038863350", "NESN", "Nestlé SA", AssetClass.EQUITY, "CHF"),
    ("CH0244767585", "UBSG", "UBS Group AG", AssetClass.EQUITY, "CHF"),
    ("CH0008742519", "SLICHA", "iShares SMI ETF", AssetClass.ETF, "CHF"),
    ("CH0224397213", "CONF28", "Swiss Confederation 0.5% 2028", AssetClass.BOND, "CHF"),
    ("CH0419041295", "CANTON30", "Canton of Zurich 1.0% 2030", AssetClass.BOND, "CHF"),
]


async def seed() -> int:
    await init_models()
    added = 0
    async with SessionLocal() as session:
        for isin, symbol, name, ac, ccy in _SEED:
            exists = await session.scalar(
                select(Instrument).where(Instrument.isin == isin)
            )
            if exists:
                continue
            session.add(
                Instrument(
                    isin=isin, symbol=symbol, name=name, asset_class=ac, currency=ccy
                )
            )
            added += 1
        await session.commit()

        # Sample muni portfolio (CUSIPs from the Ai-Price universe) for analytics.
        if not await session.scalar(select(Portfolio).where(Portfolio.name == "Sample Muni SMA")):
            pf = Portfolio(name="Sample Muni SMA")
            pf.holdings = [
                Holding(cusip="13063DAB7", par_amount=1_000_000),
                Holding(cusip="64966QCJ9", par_amount=750_000),
                Holding(cusip="452152AR7", par_amount=500_000),
                Holding(cusip="882723YK4", par_amount=600_000),
                Holding(cusip="452200CD1", par_amount=400_000),
            ]
            session.add(pf)
            await session.commit()
    return added


if __name__ == "__main__":
    n = asyncio.run(seed())
    print(f"Seeded {n} instrument(s).")
