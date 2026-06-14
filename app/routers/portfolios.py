"""Client portfolios: positions, valuation and risk from Ai-Price marks."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..analytics import value_portfolio
from ..db import get_session
from ..models import Holding, Portfolio
from ..routers.ai_price import _latest_rows
from ..schemas import PortfolioIn, PortfolioOut

router = APIRouter(prefix="/portfolios", tags=["Portfolios"])


async def _get_with_holdings(session: AsyncSession, portfolio_id: int) -> Portfolio | None:
    return await session.scalar(
        select(Portfolio)
        .options(selectinload(Portfolio.holdings))
        .where(Portfolio.id == portfolio_id)
    )


@router.get("", response_model=list[PortfolioOut])
async def list_portfolios(session: AsyncSession = Depends(get_session)) -> list[Portfolio]:
    stmt = (
        select(Portfolio)
        .options(selectinload(Portfolio.holdings))
        .order_by(Portfolio.name)
    )
    return list((await session.scalars(stmt)).all())


@router.post("", response_model=PortfolioOut, status_code=201)
async def create_portfolio(
    payload: PortfolioIn, session: AsyncSession = Depends(get_session)
) -> Portfolio:
    if await session.scalar(select(Portfolio).where(Portfolio.name == payload.name)):
        raise HTTPException(status_code=409, detail="Portfolio name already exists")
    pf = Portfolio(name=payload.name)
    pf.holdings = [Holding(cusip=h.cusip, par_amount=h.par_amount) for h in payload.holdings]
    session.add(pf)
    await session.commit()
    return await _get_with_holdings(session, pf.id)


@router.get("/{portfolio_id}/valuation")
async def portfolio_valuation(
    portfolio_id: int,
    marginal_rate: float = Query(default=0.37, ge=0.0, lt=1.0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    pf = await _get_with_holdings(session, portfolio_id)
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    holdings = [(h.cusip, float(h.par_amount)) for h in pf.holdings]
    rows = await _latest_rows(session)
    price_by_cusip = {r.cusip: r for r in rows}
    valuation = value_portfolio(holdings, price_by_cusip, marginal_rate)
    return {"portfolio_id": pf.id, "name": pf.name, **valuation}
