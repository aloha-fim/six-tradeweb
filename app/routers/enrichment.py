"""Automated enrichment / bundling service -- the SIX monetisation engine.

Joins Tradeweb evaluated prices to SIX rates, identity and corporate-actions
data and emits the analytics-ready bundled product. No manual step.
"""
from __future__ import annotations

import re

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import enrich_bond, enrich_muni
from ..analytics.liquidity import (
    drift,
    drift_label,
    latest_z,
    regime,
    sector_gps,
)
from ..analytics.muni import relative_value
from ..clients.history import synthetic_spread_series
from ..clients.rates import RatesClient, RatesError
from ..db import get_session
from ..deps import get_rates_client
from ..models import FixedIncomeQuote, Instrument
from ..routers.ai_price import _latest_rows

router = APIRouter(tags=["Enrichment (SIX bundle)"])


@router.get("/rates/curve")
async def rates_curve(
    currency: str = Query(default="USD", min_length=3, max_length=3),
    rates: RatesClient = Depends(get_rates_client),
) -> dict:
    try:
        snap = await rates.fetch_curve(currency)
    except RatesError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "currency": snap.currency, "benchmark": snap.benchmark,
        "overnight_rate": snap.overnight_rate, "as_of": snap.as_of,
        "points": [{"tenor": p.tenor, "years": p.years, "rate": p.rate} for p in snap.points],
    }


@router.get("/enriched/ai-price")
async def enriched_ai_price(
    marginal_rate: float = Query(default=0.37, ge=0.0, lt=1.0),
    state: str | None = Query(default=None, min_length=2, max_length=2),
    session: AsyncSession = Depends(get_session),
    rates: RatesClient = Depends(get_rates_client),
) -> dict:
    rows = await _latest_rows(session, state)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    curve = await rates.fetch_curve("USD")  # munis anchor to the USD risk-free curve
    enriched = [asdict(enrich_muni(r, curve, marginal_rate)) for r in rows]
    return {
        "product": "SIX enriched municipal evaluated data",
        "components": ["Tradeweb Ai-Price", "SIX USD risk-free curve",
                       "SIX reference & corporate-actions data"],
        "rates_benchmark": f"{curve.currency} {curve.benchmark}",
        "marginal_rate": marginal_rate, "count": len(enriched),
        "records": enriched,
    }


@router.get("/enriched/signals")
async def enriched_signals(
    marginal_rate: float = Query(default=0.37, ge=0.0, lt=1.0),
    session: AsyncSession = Depends(get_session),
    rates: RatesClient = Depends(get_rates_client),
) -> dict:
    """The closed loop: the rates-anchored spread drives dislocation + RV.

    Each muni is bundled (enriched), then the *after-tax spread to the SIX
    risk-free curve* becomes the series the dislocation z-score and drift run
    on -- so SIX's own rates data, not just the raw credit spread, drives the
    intelligence. The cross-sectional relative-value signal rides along.
    """
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    curve = await rates.fetch_curve("USD")
    enriched = [enrich_muni(r, curve, marginal_rate) for r in rows]
    rv_by_cusip = {rv.cusip: rv.signal.value for rv in relative_value(rows)}

    buckets: dict[str, list[list[float]]] = {}
    records = []
    for e in enriched:
        sector = "Muni GO" if e.sector == "GO" else "Muni Revenue"
        series = synthetic_spread_series(e.cusip, e.tax_equivalent_spread_bp, sector)
        z, dr = latest_z(series), drift(series)
        buckets.setdefault(sector, []).append(series)
        rec = asdict(e)
        rec.update({
            "disloc_z": z, "disloc_drift": dr, "disloc_drift_dir": drift_label(dr),
            "regime": regime(z), "rv_signal": rv_by_cusip.get(e.cusip, "fair"),
        })
        records.append(rec)

    sectors = [
        {"sector": g.sector, "z": g.z, "drift": g.drift, "stress": g.stress,
         "regime": g.regime, "stretch": g.stretch, "drift_dir": g.drift_dir,
         "risk": g.risk}
        for g in (sector_gps(s, ser) for s, ser in buckets.items())
    ]
    return {
        "product": "SIX enriched municipal data + dislocation signals",
        "components": ["Tradeweb Ai-Price", "SIX USD risk-free curve",
                       "SIX reference & corporate-actions data"],
        "dislocation_basis": "after-tax spread to SIX risk-free curve",
        "rates_benchmark": f"{curve.currency} {curve.benchmark}",
        "marginal_rate": marginal_rate, "count": len(records),
        "sectors": sectors, "records": records,
    }


_YEAR_RE = re.compile(r"(\d{2})\b")


def _years_from_symbol(symbol: str, as_of_year: int) -> float | None:
    m = _YEAR_RE.search(symbol)
    if not m:
        return None
    yr = 2000 + int(m.group(1))
    return max(yr - as_of_year - 0.4, 0.25)


@router.get("/enriched/instruments")
async def enriched_instruments(
    session: AsyncSession = Depends(get_session),
    rates: RatesClient = Depends(get_rates_client),
) -> dict:
    import datetime as dt
    instruments = list((await session.scalars(
        select(Instrument).where(Instrument.asset_class == "bond")
    )).all())
    if not instruments:
        raise HTTPException(status_code=404, detail="No bond instruments seeded")
    curve = await rates.fetch_curve("CHF")  # CHF bonds anchor to the SARON curve
    now_year = dt.datetime.now(dt.timezone.utc).year
    out = []
    for inst in instruments:
        quote = await session.scalar(
            select(FixedIncomeQuote)
            .where(FixedIncomeQuote.instrument_id == inst.id)
            .order_by(FixedIncomeQuote.as_of.desc())
        )
        if quote is None:
            continue
        years = _years_from_symbol(inst.symbol, now_year) or 5.0
        out.append(asdict(enrich_bond(inst, quote, curve, years)))
    if not out:
        raise HTTPException(status_code=404, detail="No FI quotes; POST /pricing/refresh first")
    return {
        "product": "SIX enriched Swiss fixed-income data",
        "components": ["Tradeweb FI prices", "SIX SARON curve", "SIX reference data"],
        "rates_benchmark": f"{curve.currency} {curve.benchmark}",
        "overnight_rate": curve.overnight_rate, "count": len(out), "records": out,
    }
