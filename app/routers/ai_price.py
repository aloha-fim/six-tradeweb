"""Tradeweb Municipal Ai-Price: rich data + analytics routes."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import market_summary, relative_value, tax_equivalent_yield
from ..clients import TradewebClient, TradewebError
from ..db import get_session
from ..deps import get_tradeweb_client
from ..models import AiPriceQuote, ModelAdjustment
from ..schemas import AiPriceQuoteOut, AiPriceRefreshResult
from ..usage import log_usage

router = APIRouter(prefix="/ai-price", tags=["Tradeweb Ai-Price"])


async def _latest_rows(
    session: AsyncSession, state: str | None = None, min_confidence: float = 0.0
) -> list[AiPriceQuote]:
    latest_sub = (
        select(AiPriceQuote.cusip, func.max(AiPriceQuote.as_of).label("max_as_of"))
        .group_by(AiPriceQuote.cusip)
        .subquery()
    )
    stmt = (
        select(AiPriceQuote)
        .join(
            latest_sub,
            (AiPriceQuote.cusip == latest_sub.c.cusip)
            & (AiPriceQuote.as_of == latest_sub.c.max_as_of),
        )
        .where(AiPriceQuote.confidence >= min_confidence)
        .order_by(AiPriceQuote.state, AiPriceQuote.cusip)
    )
    if state is not None:
        stmt = stmt.where(AiPriceQuote.state == state.upper())
    return list((await session.scalars(stmt)).all())


async def apply_feedback(session: AsyncSession, records: list) -> int:
    """Apply accepted client corrections to the fresh snapshot (the retrain step).

    Sums the per-CUSIP price deltas from ModelAdjustment, nudges price/bid/ask,
    re-derives the yield from the duration relation, and lifts confidence. Returns
    the total number of adjustments incorporated (for the model version tag).
    """
    rows = (await session.execute(
        select(ModelAdjustment.cusip, func.sum(ModelAdjustment.price_delta))
        .group_by(ModelAdjustment.cusip)
    )).all()
    deltas = {c: float(d) for c, d in rows}
    n_total = await session.scalar(select(func.count(ModelAdjustment.id))) or 0
    if not deltas:
        return 0
    for r in records:
        r.model_version = f"{r.model_version}+fb{n_total}"  # version is global
        d = deltas.get(r.cusip)
        if not d:
            continue
        r.ai_price = round(r.ai_price + d, 4)
        r.eval_bid = round(r.eval_bid + d, 4)
        r.eval_ask = round(r.eval_ask + d, 4)
        # price moved -> yield re-derived from price = 100 - (yield - coupon)*duration
        r.ai_yield = round(r.coupon - (r.ai_price - 100) / r.effective_duration, 4)
        r.yield_to_worst = min(r.yield_to_worst, r.ai_yield)
        r.confidence = round(min(0.99, r.confidence + 0.03), 3)  # feedback raises confidence
    return n_total


async def _do_refresh(
    session: AsyncSession, tradeweb: TradewebClient,
    state: str | None = None, intraday: bool = False,
) -> AiPriceRefreshResult:
    try:
        records = await tradeweb.fetch_ai_price(state=state, intraday=intraday)
    except TradewebError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not records:
        raise HTTPException(status_code=404, detail="Ai-Price feed returned no rows")

    await apply_feedback(session, records)
    for r in records:
        session.add(AiPriceQuote(
            cusip=r.cusip, description=r.description, state=r.state, sector=r.sector,
            rating_sp=r.rating_sp, coupon=r.coupon, maturity=r.maturity,
            callable=r.callable, call_date=r.call_date,
            size_outstanding_mm=r.size_outstanding_mm, price_type=r.price_type,
            eval_bid=r.eval_bid, ai_price=r.ai_price, eval_ask=r.eval_ask,
            price_change_1d=r.price_change_1d, ai_yield=r.ai_yield,
            yield_to_worst=r.yield_to_worst, yield_to_call=r.yield_to_call,
            benchmark_spread_bp=r.benchmark_spread_bp, ust_spread_bp=r.ust_spread_bp,
            effective_duration=r.effective_duration, convexity=r.convexity, dv01=r.dv01,
            liquidity_score=r.liquidity_score, trades_30d=r.trades_30d,
            last_trade_date=r.last_trade_date, confidence=r.confidence,
            model_version=r.model_version, as_of=r.as_of,
        ))
    await session.commit()
    return AiPriceRefreshResult(
        ingested=len(records), as_of=records[0].as_of,
        model_version=records[0].model_version, price_type=records[0].price_type,
    )


@router.post("/refresh", response_model=AiPriceRefreshResult)
async def refresh_ai_price(
    state: str | None = Query(default=None, min_length=2, max_length=2),
    intraday: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    tradeweb: TradewebClient = Depends(get_tradeweb_client),
) -> AiPriceRefreshResult:
    return await _do_refresh(session, tradeweb, state, intraday)


@router.get("/latest", response_model=list[AiPriceQuoteOut])
async def latest_ai_price(
    state: str | None = Query(default=None, min_length=2, max_length=2),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    session: AsyncSession = Depends(get_session),
) -> list[AiPriceQuote]:
    rows = await _latest_rows(session, state, min_confidence)
    if rows:
        await log_usage(session, [r.cusip for r in rows], kind="view")
    return rows


@router.get("/analytics/summary")
async def ai_price_summary(
    marginal_rate: float = Query(default=0.37, ge=0.0, lt=1.0),
    state: str | None = Query(default=None, min_length=2, max_length=2),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await _latest_rows(session, state)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    return market_summary(rows, marginal_rate)


@router.get("/analytics/relative-value")
async def ai_price_relative_value(
    signal: str | None = Query(default=None, pattern="^(cheap|fair|rich)$"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    by_cusip = {r.cusip: r for r in rows}
    result = []
    for rv in relative_value(rows):
        if signal and rv.signal.value != signal:
            continue
        rec = by_cusip[rv.cusip]
        result.append({
            "cusip": rv.cusip, "description": rec.description, "state": rec.state,
            "sector": rec.sector.value, "rating_sp": rec.rating_sp,
            "ai_yield": float(rec.ai_yield),
            "actual_spread_bp": rv.actual_spread_bp,
            "expected_spread_bp": rv.expected_spread_bp,
            "residual_bp": rv.residual_bp, "rv_percentile": rv.rv_percentile,
            "signal": rv.signal.value,
        })
    return result


@router.get("/{cusip}/tax-equivalent")
async def cusip_tax_equivalent(
    cusip: str,
    marginal_rate: float = Query(default=0.37, ge=0.0, lt=1.0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await _latest_rows(session)
    rec = next((r for r in rows if r.cusip == cusip), None)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No Ai-Price data for {cusip}")
    tey = tax_equivalent_yield(float(rec.ai_yield), marginal_rate)
    return {
        "cusip": cusip, "ai_yield": float(rec.ai_yield),
        "marginal_rate": marginal_rate, "tax_equivalent_yield": tey,
        "pickup_bp": round((tey - float(rec.ai_yield)) * 100, 1),
    }


@router.get("/{cusip}/history", response_model=list[AiPriceQuoteOut])
async def ai_price_history(
    cusip: str,
    since: dt.datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[AiPriceQuote]:
    stmt = (
        select(AiPriceQuote)
        .where(AiPriceQuote.cusip == cusip)
        .order_by(AiPriceQuote.as_of.desc())
    )
    if since is not None:
        stmt = stmt.where(AiPriceQuote.as_of >= since)
    rows = list((await session.scalars(stmt)).all())
    if not rows:
        raise HTTPException(status_code=404, detail=f"No Ai-Price data for {cusip}")
    return rows
