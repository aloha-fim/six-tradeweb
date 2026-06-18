"""Liquidity + curve pricing endpoints, and the regime-switching hybrid stack."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import consensus_deviation
from ..analytics.curve import bond_price_from_yield, curve_yield, fit_svensson
from ..analytics.liquidity_model import composite_liquidity_score, score_from_record
from ..clients.contributors import contributor_marks
from ..db import get_session
from ..routers.ai_price import _latest_rows

router = APIRouter(tags=["Pricing engine"])

# How much each regime leans on consensus marks / curve anchor / Ai-Price prior.
_REGIME_WEIGHTS = {
    "HIGH":   {"consensus": 0.75, "curve": 0.15, "ai_price": 0.10},
    "MEDIUM": {"consensus": 0.50, "curve": 0.30, "ai_price": 0.20},
    "LOW":    {"consensus": 0.25, "curve": 0.45, "ai_price": 0.30},
}


def _years(record) -> float:
    as_of = record.as_of.date() if hasattr(record.as_of, "date") else record.as_of
    return max(0.05, (record.maturity - as_of).days / 365.25)


async def _fit_universe_curve(session: AsyncSession):
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    params = fit_svensson([_years(r) for r in rows], [float(r.ai_yield) for r in rows])
    return rows, params


class LiquidityIn(BaseModel):
    trade_count_30d: float = Field(ge=0)
    days_since_last_trade: float = Field(ge=0)
    avg_spread_bp: float = Field(ge=0)
    price_std_bp: float = Field(ge=0)


@router.post("/liquidity/score")
async def liquidity_score(payload: LiquidityIn) -> dict:
    return composite_liquidity_score(**payload.model_dump())


@router.get("/curve")
async def fitted_curve(session: AsyncSession = Depends(get_session)) -> dict:
    rows, params = await _fit_universe_curve(session)
    nodes = [1, 2, 3, 5, 7, 10, 20, 30]
    t_max = params.get("t_max", 30.0)
    return {"model": "svensson", "params": params, "fitted_points": len(rows),
            "fitted_maturity_range_years": [params.get("t_min"), t_max],
            "curve": {f"{t}Y": round(curve_yield(params, t), 3) for t in nodes},
            "extrapolated_beyond_years": t_max,
            "note": "yields beyond the fitted range are held flat (no reliable long-end data)"}


class CurvePriceIn(BaseModel):
    coupon: float = Field(ge=0)
    maturity_years: float = Field(gt=0)
    face: float = 100.0
    yield_pct: float | None = None


@router.post("/curve/price")
async def curve_price(payload: CurvePriceIn, session: AsyncSession = Depends(get_session)) -> dict:
    if payload.yield_pct is not None:
        y, source = payload.yield_pct, "supplied"
    else:
        _, params = await _fit_universe_curve(session)
        y, source = curve_yield(params, payload.maturity_years), "fitted_curve"
    price = bond_price_from_yield(payload.face, payload.coupon, y, payload.maturity_years)
    return {"yield_used_pct": round(y, 4), "yield_source": source,
            "curve_price": round(price, 4)}


@router.get("/pricing/hybrid/{cusip}")
async def hybrid_price(cusip: str, session: AsyncSession = Depends(get_session)) -> dict:
    rows, params = await _fit_universe_curve(session)
    row = next((r for r in rows if r.cusip == cusip), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown CUSIP")

    years = _years(row)
    cy = curve_yield(params, years)
    curve_px = round(bond_price_from_yield(100.0, float(row.coupon), cy, years), 4)

    liq = float(row.liquidity_score) / 100.0
    marks = contributor_marks(cusip, float(row.ai_price), row.sector.value,
                              float(row.liquidity_score), row.as_of)
    cd = consensus_deviation(row, marks, liquidity=liq, group_prior=None)

    liq_assess = score_from_record(row, price_std_bp=cd.dispersion * 100.0)
    regime = liq_assess["bucket"]
    w = _REGIME_WEIGHTS[regime]
    components = {"consensus": cd.posterior_price, "curve": curve_px, "ai_price": float(row.ai_price)}
    final = round(sum(w[k] * components[k] for k in w), 4)
    return {
        "cusip": cusip, "description": row.description, "years_to_maturity": round(years, 2),
        "liquidity": liq_assess, "regime": regime, "weights": w,
        "components": {**components, "curve_yield_pct": round(cy, 4),
                       "consensus_ci": [cd.ci_low, cd.ci_high]},
        "final_price": final,
        "note": ("regime-switched blend of consensus marks, curve anchor, and Ai-Price prior; "
                 "illiquid names lean on the curve + prior, liquid names on the marks"),
    }
