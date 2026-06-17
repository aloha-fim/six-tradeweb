"""Three-layer ingest endpoints for Tradeweb Ai-Price as SIX stores it."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..feed_ingest import (
    AiPriceFeedRecord,
    enriched_record,
    ingest_record,
    six_security_id,
)
from ..models import AiPriceValuation, ExplainabilityInput, SecurityMaster

router = APIRouter(tags=["Ingest"])


# A realistic sample feed (the City of Chicago record + two later valuations so
# the historical time series and a second instrument are visible in the UI).
_SAMPLE: list[dict] = [
    {"source": "Tradeweb Municipal Ai-Price", "valuation_date": "2026-06-12", "cusip": "167593AB4",
     "isin": "US167593AB40", "issuer": "City of Chicago", "security_type": "Municipal Bond",
     "coupon": 5.00, "maturity_date": "2045-07-01", "rating_moodys": "Aa3", "rating_sp": "AA-",
     "ai_price_bid": 100.90, "ai_price_mid": 101.05, "ai_price_ask": 101.20, "ai_yield": 4.11,
     "benchmark_curve": "AAA GO", "spread_to_curve_bp": 74, "confidence_score": 0.93,
     "liquidity_score": 0.66, "last_trade_price": 100.98, "last_trade_date": "2026-06-09",
     "days_since_trade": 3, "trade_count_30d": 11, "volume_30d": 4200000, "quote_count": 35,
     "curve_node": "20Y", "model_version": "MUNI_AI_V5", "pricing_timestamp": "2026-06-12T21:00:00Z"},
    {"source": "Tradeweb Municipal Ai-Price", "valuation_date": "2026-06-16", "cusip": "167593AB4",
     "isin": "US167593AB40", "issuer": "City of Chicago", "security_type": "Municipal Bond",
     "coupon": 5.00, "maturity_date": "2045-07-01", "rating_moodys": "Aa3", "rating_sp": "AA-",
     "ai_price_bid": 101.12, "ai_price_mid": 101.28, "ai_price_ask": 101.44, "ai_yield": 4.08,
     "benchmark_curve": "AAA GO", "spread_to_curve_bp": 72, "confidence_score": 0.94,
     "liquidity_score": 0.68, "last_trade_price": 101.20, "last_trade_date": "2026-06-12",
     "days_since_trade": 4, "trade_count_30d": 12, "volume_30d": 4500000, "quote_count": 38,
     "curve_node": "20Y", "model_version": "MUNI_AI_V5", "pricing_timestamp": "2026-06-16T21:00:00Z"},
    {"source": "Tradeweb Municipal Ai-Price", "valuation_date": "2026-06-16", "cusip": "13063DAD3",
     "isin": "US13063DAD37", "issuer": "State of California", "security_type": "Municipal Bond",
     "coupon": 4.00, "maturity_date": "2039-09-01", "rating_moodys": "Aa2", "rating_sp": "AA-",
     "ai_price_bid": 98.40, "ai_price_mid": 98.62, "ai_price_ask": 98.84, "ai_yield": 4.21,
     "benchmark_curve": "AAA GO", "spread_to_curve_bp": 58, "confidence_score": 0.71,
     "liquidity_score": 0.34, "last_trade_price": 98.10, "last_trade_date": "2026-05-20",
     "days_since_trade": 27, "trade_count_30d": 2, "volume_30d": 600000, "quote_count": 9,
     "curve_node": "15Y", "model_version": "MUNI_AI_V5", "pricing_timestamp": "2026-06-16T21:00:00Z"},
]


@router.post("/ingest/ai-price")
async def ingest_ai_price(
    records: AiPriceFeedRecord | list[AiPriceFeedRecord],
    session: AsyncSession = Depends(get_session),
) -> dict:
    recs = records if isinstance(records, list) else [records]
    ids = [await ingest_record(r, session) for r in recs]
    enriched = [await enriched_record(sid, session) for sid in dict.fromkeys(ids)]
    return {"ingested": len(recs), "securities": enriched}


@router.post("/ingest/seed-sample")
async def seed_sample(session: AsyncSession = Depends(get_session)) -> dict:
    for raw in _SAMPLE:
        await ingest_record(AiPriceFeedRecord(**raw), session)
    sids = list(dict.fromkeys(six_security_id(r["cusip"]) for r in _SAMPLE))
    return {"ingested": len(_SAMPLE),
            "securities": [await enriched_record(s, session) for s in sids]}


@router.get("/ingest/security-master")
async def list_security_master(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.scalars(select(SecurityMaster).order_by(SecurityMaster.issuer))).all()
    return [{
        "six_security_id": m.six_security_id, "cusip": m.cusip, "isin": m.isin, "figi": m.figi,
        "issuer": m.issuer, "issuer_parent": m.issuer_parent, "asset_class": m.asset_class,
        "coupon": float(m.coupon), "maturity_date": m.maturity_date.isoformat(),
        "currency": m.currency, "rating_moodys": m.rating_moodys, "rating_sp": m.rating_sp,
        "regulatory_class": m.regulatory_class, "corp_action_ref": m.corp_action_ref,
        "data_quality_score": float(m.data_quality_score),
    } for m in rows]


@router.get("/ingest/valuations")
async def list_valuations(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.scalars(
        select(AiPriceValuation).order_by(AiPriceValuation.valuation_date.desc())
    )).all()
    return [{
        "id": v.id, "six_security_id": v.six_security_id, "cusip": v.cusip,
        "valuation_date": v.valuation_date.isoformat(), "source": v.source,
        "bid_price": float(v.bid_price), "mid_price": float(v.mid_price),
        "ask_price": float(v.ask_price), "clean_price": float(v.clean_price),
        "yield_pct": float(v.yield_pct), "benchmark_curve": v.benchmark_curve,
        "spread_bp": float(v.spread_bp), "confidence_score": float(v.confidence_score),
        "liquidity_score": float(v.liquidity_score),
        "pricing_timestamp": v.pricing_timestamp.isoformat(),
    } for v in rows]


@router.get("/ingest/explainability")
async def list_explainability(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.scalars(
        select(ExplainabilityInput).order_by(ExplainabilityInput.id.desc())
    )).all()
    return [{
        "id": e.id, "valuation_id": e.valuation_id, "cusip": e.cusip,
        "last_trade_price": (float(e.last_trade_price) if e.last_trade_price is not None else None),
        "last_trade_date": (e.last_trade_date.isoformat() if e.last_trade_date else None),
        "days_since_trade": e.days_since_trade, "trade_count_30d": e.trade_count_30d,
        "volume_30d": (float(e.volume_30d) if e.volume_30d is not None else None),
        "quote_count": e.quote_count, "benchmark_spread": (float(e.benchmark_spread) if e.benchmark_spread is not None else None),
        "curve_node": e.curve_node, "model_version": e.model_version,
    } for e in rows]


@router.get("/ingest/enriched")
async def list_enriched(session: AsyncSession = Depends(get_session)) -> list[dict]:
    sids = (await session.scalars(select(SecurityMaster.six_security_id))).all()
    out = [await enriched_record(s, session) for s in sids]
    return [r for r in out if r]
