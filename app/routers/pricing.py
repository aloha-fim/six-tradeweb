"""Tradeweb-sourced evaluated fixed-income pricing, redistributed via SIX."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import TradewebClient, TradewebError
from ..db import get_session
from ..deps import get_tradeweb_client
from ..models import AssetClass, FixedIncomeQuote, Instrument
from ..schemas import FixedIncomeQuoteOut

router = APIRouter(prefix="/pricing", tags=["Tradeweb pricing"])


@router.post("/refresh", response_model=list[FixedIncomeQuoteOut])
async def refresh_fi_quotes(
    session: AsyncSession = Depends(get_session),
    tradeweb: TradewebClient = Depends(get_tradeweb_client),
) -> list[FixedIncomeQuote]:
    """Pull fresh Tradeweb evaluated prices for every listed bond and store them."""
    bonds = list(
        (
            await session.scalars(
                select(Instrument).where(Instrument.asset_class == AssetClass.BOND)
            )
        ).all()
    )
    if not bonds:
        return []

    by_isin = {b.isin: b for b in bonds}
    try:
        records = await tradeweb.fetch_fi_quotes(list(by_isin))
    except TradewebError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    saved: list[FixedIncomeQuote] = []
    for rec in records:
        inst = by_isin.get(rec.isin)
        if inst is None:
            continue
        quote = FixedIncomeQuote(
            instrument_id=inst.id,
            clean_price=rec.clean_price,
            yield_to_maturity=rec.yield_to_maturity,
            as_of=rec.as_of,
        )
        session.add(quote)
        saved.append(quote)
    await session.commit()
    for q in saved:
        await session.refresh(q)
    return saved


@router.get("/{isin}", response_model=list[FixedIncomeQuoteOut])
async def quotes_for_instrument(
    isin: str, session: AsyncSession = Depends(get_session)
) -> list[FixedIncomeQuote]:
    inst = await session.scalar(select(Instrument).where(Instrument.isin == isin))
    if inst is None:
        raise HTTPException(status_code=404, detail=f"No instrument with ISIN {isin}")
    stmt = (
        select(FixedIncomeQuote)
        .where(FixedIncomeQuote.instrument_id == inst.id)
        .order_by(FixedIncomeQuote.as_of.desc())
    )
    return list((await session.scalars(stmt)).all())
