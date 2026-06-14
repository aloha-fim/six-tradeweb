"""Feedback SIX sends back to Tradeweb.

Honest about direction: the analytics SIX computes are sold to its *bank
clients*; the genuine numbers that flow back to Tradeweb are model-quality
feedback. This endpoint emits the one signal computable from data the app holds
today -- a price-review / challenge candidate list, from the Ai-Price model
confidence band and the dislocation z-score of each bond's after-tax spread to
the SIX risk-free curve -- and marks the others with what they would require.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import assess_freshness, consensus_deviation, enrich_muni
from ..analytics.liquidity import latest_z
from ..clients.contributors import contributor_marks
from ..clients.history import synthetic_spread_series
from ..clients.rates import RatesClient
from ..db import get_session
from ..deps import get_rates_client
from ..models import ModelAdjustment, PriceChallenge, UsageEvent
from ..routers.ai_price import _latest_rows
from sqlalchemy import func, select

router = APIRouter(prefix="/feedback", tags=["Feedback to Tradeweb"])


@router.get("/tradeweb")
async def feedback_to_tradeweb(
    abs_z: float = Query(default=1.5, ge=0.0),
    min_confidence: float = Query(default=0.85, ge=0.0, le=1.0),
    marginal_rate: float = Query(default=0.37, ge=0.0, lt=1.0),
    session: AsyncSession = Depends(get_session),
    rates: RatesClient = Depends(get_rates_client),
) -> dict:
    rows = await _latest_rows(session)
    if not rows:
        raise HTTPException(status_code=404, detail="No Ai-Price data; refresh first")
    curve = await rates.fetch_curve("USD")

    candidates = []
    for r in rows:
        e = enrich_muni(r, curve, marginal_rate)
        sector = "Muni GO" if e.sector == "GO" else "Muni Revenue"
        z = latest_z(synthetic_spread_series(e.cusip, e.tax_equivalent_spread_bp, sector))
        reasons = []
        if e.confidence < min_confidence:
            reasons.append(f"model confidence {e.confidence:.2f} below {min_confidence:.2f}")
        if abs(z) >= abs_z:
            reasons.append(f"spread dislocation |z|={abs(z):.2f} at/above {abs_z:.1f}")
        if reasons:
            candidates.append({
                "cusip": e.cusip, "description": e.canonical_name,
                "confidence": round(e.confidence, 3), "disloc_z": z,
                "tax_equivalent_spread_bp": e.tax_equivalent_spread_bp,
                "ai_price": e.ai_price, "reasons": reasons,
            })
    # most urgent first: low confidence and/or large dislocation
    candidates.sort(
        key=lambda c: (1 - c["confidence"]) + abs(c["disloc_z"]) / 3, reverse=True
    )

    # --- evaluation freshness (headline): curve-tracking responsiveness ------
    fresh = [assess_freshness(r) for r in rows]
    stale = [f for f in fresh if f.stale]
    worst = sorted(fresh, key=lambda f: f.beta)[:5]
    desc_by_cusip = {r.cusip: r.description for r in rows}
    fresh_by_sector: dict[str, list[float]] = {}
    for f in fresh:
        fresh_by_sector.setdefault(f.sector, []).append(f.beta)
    fresh_sectors = [
        {"sector": s, "mean_beta": round(sum(b) / len(b), 3),
         "stale": sum(1 for x in fresh if x.sector == s and x.stale)}
        for s, b in sorted(fresh_by_sector.items())
    ]
    worst_trackers = [{
        "cusip": f.cusip, "description": desc_by_cusip.get(f.cusip, ""),
        "trades_30d": f.trades_30d, "expected_move": f.expected_move,
        "actual_move": f.actual_move, "beta": f.beta,
        "tracking_gap_bp": f.tracking_gap_bp, "stale": f.stale,
    } for f in worst]

    sector_by_cusip = {r.cusip: ("GO" if r.sector.value == "GO" else "REVENUE") for r in rows}

    # --- demand momentum (refined demand): rising vs prior consumption --------
    ev = (await session.execute(select(UsageEvent.cusip, UsageEvent.created_at))).all()
    total_events = len(ev)
    mom: dict[str, dict[str, int]] = {}
    if ev:
        times = [t for _, t in ev]
        mid = min(times) + (max(times) - min(times)) / 2
        for c, t in ev:
            d = mom.setdefault(sector_by_cusip.get(c, "?"), {"early": 0, "late": 0})
            d["late" if t > mid else "early"] += 1
    demand_momentum = [
        {"sector": s, "early": d["early"], "late": d["late"],
         "momentum": d["late"] - d["early"],
         "direction": ("rising" if d["late"] > d["early"] else
                       "falling" if d["late"] < d["early"] else "flat")}
        for s, d in sorted(mom.items())
    ]

    # --- validation bias (refined validation): signed bias by bucket ----------
    dur_by_cusip = {r.cusip: float(r.effective_duration) for r in rows}
    adj = (await session.execute(
        select(ModelAdjustment.cusip, func.sum(ModelAdjustment.price_delta))
        .group_by(ModelAdjustment.cusip)
    )).all()
    bias_by_sector: dict[str, list[float]] = {}
    corrections = []
    for c, delta in adj:
        delta = float(delta)
        dur = dur_by_cusip.get(c, 1.0) or 1.0
        bias_bp = round(-delta / dur * 100, 1)   # price marked down -> eval was rich (+bp)
        corrections.append({"cusip": c, "price_delta": round(delta, 4), "bias_bp": bias_bp})
        bias_by_sector.setdefault(sector_by_cusip.get(c, "?"), []).append(bias_bp)
    validation_bias = [
        {"sector": s, "mean_bias_bp": round(sum(v) / len(v), 1), "n": len(v),
         "reads": ("rich" if sum(v) > 0 else "cheap" if sum(v) < 0 else "fair")}
        for s, v in sorted(bias_by_sector.items())
    ]
    challenges = await session.scalar(select(func.count(PriceChallenge.id))) or 0

    # --- consensus deviation: Ai-Price vs the blend of bank-client marks ------
    cons = []
    for r in rows:
        marks = contributor_marks(r.cusip, float(r.ai_price), float(r.liquidity_score), r.as_of)
        cd = consensus_deviation(r, marks)
        cons.append(cd)
    off_market = [c for c in cons if c.off_market]
    cons_worst = sorted(cons, key=lambda c: abs(c.z), reverse=True)[:5]
    cons_by_sector: dict[str, list[float]] = {}
    for c in cons:
        cons_by_sector.setdefault(c.sector, []).append(abs(c.z))
    cons_sectors = [{"sector": s, "mean_abs_z": round(sum(v) / len(v), 2),
                     "off_market": sum(1 for c in cons if c.sector == s and c.off_market)}
                    for s, v in sorted(cons_by_sector.items())]
    cons_records = [{
        "cusip": c.cusip, "description": desc_by_cusip.get(c.cusip, ""),
        "ai_price": next((float(r.ai_price) for r in rows if r.cusip == c.cusip), None),
        "consensus": c.consensus, "dispersion": c.dispersion,
        "deviation_price": c.deviation_price, "deviation_bp": c.deviation_bp,
        "z": c.z, "off_market": c.off_market,
    } for c in cons_worst]

    return {
        "from": "SIX", "to": "Tradeweb",
        "generated_at": dt.datetime.now(dt.timezone.utc),
        "note": ("Client-facing analytics are sold by SIX to its bank clients; the "
                 "numbers that flow back to Tradeweb are model-quality feedback."),
        "signals": {
            "evaluation_freshness": {
                "status": "live",
                "headline": True,
                "method": ("for each bond, expected_move = -duration x curve_move x price "
                           "(SIX risk-free curve, at the bond's tenor) vs actual price move; "
                           "beta = actual/expected (1=tracks, ~0=stale); a stale flag marks "
                           "illiquid evals that did not absorb a material curve move"),
                "trader_value": ("flags marks that are stale rather than merely biased -- "
                                 "which evaluations are fresh enough to lift or hit against"),
                "screened": len(rows), "stale_count": len(stale),
                "by_sector": fresh_sectors, "worst_trackers": worst_trackers,
            },
            "model_review_candidates": {
                "status": "live",
                "method": ("Ai-Price model confidence band + dislocation z-score of the "
                           "after-tax spread to the SIX risk-free curve; flag a CUSIP "
                           "when confidence is low or |z| is large"),
                "thresholds": {"abs_z": abs_z, "min_confidence": min_confidence},
                "screened": len(rows), "flagged": len(candidates),
                "candidates": candidates,
            },
            "demand_momentum": {
                "status": "live",
                "method": ("consumption split into earlier vs later windows from the usage "
                           "log; momentum = late - early per sector, a leading interest "
                           "indicator rather than a raw count"),
                "total_events": total_events, "by_sector": demand_momentum,
            },
            "validation_bias": {
                "status": "live",
                "method": ("accepted price challenges aggregated as a *signed* bias by "
                           "bucket: bias_bp = -price_delta / duration x 100, so a positive "
                           "mean means evals run rich -- a directional correction, not a count"),
                "challenges": challenges, "corrections": corrections,
                "by_sector": validation_bias,
            },
            "consensus_deviation": {
                "status": "live",
                "source": "synthetic contributor marks",
                "requires": "multi-source ingest of clients' carried marks (synthetic here)",
                "method": ("Ai-Price vs the median of bank-client marks SIX sees as "
                           "distributor; z = deviation / bank dispersion; off-market when "
                           "|z| is large -- an outlier vs where the market carries the bond"),
                "trader_value": ("tells a desk the eval is off where other books mark it, "
                                 "before trading against it"),
                "screened": len(cons), "off_market": len(off_market),
                "by_sector": cons_sectors, "worst": cons_records,
            },
            "data_quality_feedback": {
                "status": "requires multi-source ingest",
                "available": False,
                "method": ("golden-copy match rate, unresolved identifier conflicts, "
                           "check-digit failures and a settlement-fail proxy from the "
                           "reference-data reconciliation pipeline"),
            },
            "consolidated_metrics": {
                "status": "prospective",
                "available": False,
                "method": ("cross-venue volume, fragmentation index and price-discovery "
                           "quality; needs multi-venue data and redistribution rights"),
            },
        },
    }
