"""Dealerweb inter-dealer rates/MBS top-of-book + liquidity analytics.

Prospective SIX data product (SIX does not distribute Dealerweb today).
"""
from __future__ import annotations

import statistics

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import DealerwebClient, DealerwebError
from ..db import get_session
from ..deps import get_dealerweb_client
from ..models import DealerwebQuote, RatesProduct
from ..schemas import DealerwebQuoteOut

router = APIRouter(prefix="/dealerweb", tags=["Dealerweb (prospective)"])


async def _latest(session: AsyncSession, product: str | None = None) -> list[DealerwebQuote]:
    sub = (
        select(DealerwebQuote.instrument, func.max(DealerwebQuote.as_of).label("m"))
        .group_by(DealerwebQuote.instrument)
        .subquery()
    )
    stmt = (
        select(DealerwebQuote)
        .join(sub, (DealerwebQuote.instrument == sub.c.instrument)
              & (DealerwebQuote.as_of == sub.c.m))
        .order_by(DealerwebQuote.product, DealerwebQuote.instrument)
    )
    if product:
        stmt = stmt.where(DealerwebQuote.product == RatesProduct(product))
    return list((await session.scalars(stmt)).all())


@router.post("/refresh", response_model=int)
async def refresh_dealerweb(
    product: str | None = Query(default=None, pattern="^(UST|TBA_MBS)$"),
    session: AsyncSession = Depends(get_session),
    dealerweb: DealerwebClient = Depends(get_dealerweb_client),
) -> int:
    try:
        records = await dealerweb.fetch_top_of_book(product=product)
    except DealerwebError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    for r in records:
        session.add(DealerwebQuote(
            product=RatesProduct(r.product), instrument=r.instrument, tenor=r.tenor,
            coupon=r.coupon, bid=r.bid, ask=r.ask, mid=r.mid,
            bid_size_mm=r.bid_size_mm, ask_size_mm=r.ask_size_mm,
            spread_bp=r.spread_bp, liquidity_score=r.liquidity_score, as_of=r.as_of,
        ))
    await session.commit()
    return len(records)


@router.get("/top-of-book", response_model=list[DealerwebQuoteOut])
async def top_of_book(
    product: str | None = Query(default=None, pattern="^(UST|TBA_MBS)$"),
    session: AsyncSession = Depends(get_session),
) -> list[DealerwebQuote]:
    return await _latest(session, product)


@router.get("/analytics/liquidity")
async def liquidity_analytics(
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await _latest(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Dealerweb data; refresh first")

    def block(items):
        return {
            "instruments": len(items),
            "avg_spread_bp": round(statistics.fmean([r.spread_bp for r in items]), 2),
            "tightest": min(items, key=lambda r: r.spread_bp).instrument,
            "avg_liquidity_score": round(
                statistics.fmean([r.liquidity_score for r in items]), 1),
            "total_top_of_book_mm": round(
                sum(float(r.bid_size_mm) + float(r.ask_size_mm) for r in items), 1),
        }

    ust = [r for r in rows if r.product == RatesProduct.UST]
    mbs = [r for r in rows if r.product == RatesProduct.TBA_MBS]
    out: dict = {"as_of": max(r.as_of for r in rows)}
    if ust:
        out["UST"] = block(ust)
    if mbs:
        out["TBA_MBS"] = block(mbs)
    return out
