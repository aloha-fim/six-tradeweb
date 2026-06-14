"""Liquidity intelligence (Model B): dislocation, drift, sector stress, GPS.

Computes signals on the top-of-book bid/ask spread series for instruments
already in the system (Tradeweb Ai-Price munis + Dealerweb rates/MBS). The
history is synthetic (see clients/history.py); the signal math is real.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import (
    drift,
    interpret,
    latest_z,
    overall_stress,
    regime,
    sector_gps,
    stress_score,
)
from ..analytics.liquidity import drift_label, risk_label, stretch_label
from ..clients.history import synthetic_bidask_series
from ..db import get_session
from ..models import MuniSector, RatesProduct
from ..routers.ai_price import _latest_rows
from ..routers.dealerweb import _latest as _latest_dw

router = APIRouter(prefix="/liquidity", tags=["Liquidity intelligence"])


def _muni_bidask_bp(r) -> float:
    mid = float(r.ai_price) or 100.0
    return (float(r.eval_ask) - float(r.eval_bid)) / mid * 10000.0


async def _collect(session: AsyncSession):
    """Return {sector: [(key, anchor_bp), ...]} for all covered instruments."""
    buckets: dict[str, list[tuple[str, float]]] = {}
    for r in await _latest_rows(session):
        sector = "Muni GO" if r.sector == MuniSector.GO else "Muni Revenue"
        buckets.setdefault(sector, []).append((r.cusip, _muni_bidask_bp(r)))
    for q in await _latest_dw(session):
        sector = "UST" if q.product == RatesProduct.UST else "Agency MBS"
        buckets.setdefault(sector, []).append((q.instrument, float(q.spread_bp)))
    return buckets


def _series_for(sector: str, items: list[tuple[str, float]]) -> list[list[float]]:
    return [synthetic_bidask_series(key, anchor, sector) for key, anchor in items]


@router.get("/stress")
async def liquidity_stress(session: AsyncSession = Depends(get_session)) -> dict:
    buckets = await _collect(session)
    if not buckets:
        raise HTTPException(status_code=404,
                            detail="No data; refresh Ai-Price and Dealerweb first")
    order = ["UST", "Agency MBS", "Muni GO", "Muni Revenue"]
    gps = []
    for sector in [s for s in order if s in buckets] + \
            [s for s in buckets if s not in order]:
        gps.append(sector_gps(sector, _series_for(sector, buckets[sector])))
    return {
        "overall_score": overall_stress(gps),
        "interpretation": interpret(gps),
        "sectors": [
            {
                "sector": g.sector, "instruments": g.instruments,
                "z": g.z, "drift": g.drift, "stress": g.stress,
                "stretch": g.stretch, "drift_dir": g.drift_dir, "risk": g.risk,
                "regime": g.regime, "series": g.series,
            }
            for g in gps
        ],
    }


@router.get("/signals/{instrument}")
async def instrument_signals(
    instrument: str, session: AsyncSession = Depends(get_session)
) -> dict:
    # match a muni CUSIP first, then a Dealerweb instrument name
    for r in await _latest_rows(session):
        if r.cusip == instrument:
            sector = "Muni GO" if r.sector == MuniSector.GO else "Muni Revenue"
            series = synthetic_bidask_series(r.cusip, _muni_bidask_bp(r), sector)
            return _signal_payload(instrument, sector, series)
    for q in await _latest_dw(session):
        if q.instrument == instrument:
            sector = "UST" if q.product == RatesProduct.UST else "Agency MBS"
            series = synthetic_bidask_series(q.instrument, float(q.spread_bp), sector)
            return _signal_payload(instrument, sector, series)
    raise HTTPException(status_code=404, detail=f"No instrument {instrument}")


def _signal_payload(instrument: str, sector: str, series: list[float]) -> dict:
    z = latest_z(series)
    dr = drift(series)
    score = stress_score(z, dr)
    return {
        "instrument": instrument, "sector": sector,
        "bidask_bp": series[-1], "z_score": z, "drift": dr,
        "stressed": z >= 0.8, "regime": regime(z),
        "stretch": stretch_label(z), "drift_dir": drift_label(dr),
        "stress": score, "risk": risk_label(score),
        "series": series,
    }
