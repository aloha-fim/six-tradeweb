"""Ingest a Tradeweb Ai-Price feed record the way SIX would store it.

One inbound record is validated (Pydantic), then split into three layers and
enriched with the reference data SIX adds on top of the raw evaluated price:

    SecurityMaster        -- SIX golden-copy reference data (+ the SIX adds)
    AiPriceValuation      -- the evaluated-pricing feed itself
    ExplainabilityInput   -- model-input / governance fields

The enriched output is the join of all three plus the eight things SIX layers on:
SIX security id, issuer hierarchy, corporate-actions linkage, reference-data
enrichment, regulatory classification, currency normalization, historical time
series, and data-quality indicators.
"""
from __future__ import annotations

import datetime as dt
import hashlib

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiPriceValuation, ExplainabilityInput, SecurityMaster

# Illustrative reporting-currency FX for the currency-normalization enrichment.
USD_TO_CHF = 0.88

# A small issuer-hierarchy lookup; everything else falls back to a generic parent.
_ISSUER_PARENT = {
    "City of Chicago": "State of Illinois",
    "State of California": "United States (sovereign)",
    "City of New York": "State of New York",
    "Texas": "United States (sovereign)",
}


class AiPriceFeedRecord(BaseModel):
    """Validates an inbound Tradeweb Ai-Price record (the real feed shape)."""

    source: str = "Tradeweb Municipal Ai-Price"
    valuation_date: dt.date
    cusip: str = Field(min_length=9, max_length=9)
    isin: str | None = None
    issuer: str
    security_type: str = "Municipal Bond"
    coupon: float = Field(ge=0)
    maturity_date: dt.date
    rating_moodys: str | None = None
    rating_sp: str | None = None
    ai_price_bid: float = Field(gt=0)
    ai_price_mid: float = Field(gt=0)
    ai_price_ask: float = Field(gt=0)
    ai_yield: float
    benchmark_curve: str = "AAA Municipal"
    spread_to_curve_bp: float
    confidence_score: float = Field(ge=0, le=1)
    liquidity_score: float = Field(ge=0, le=1)
    last_trade_price: float | None = None
    last_trade_date: dt.date | None = None
    days_since_trade: int | None = None
    trade_count_30d: int | None = None
    volume_30d: float | None = None
    quote_count: int | None = None
    curve_node: str | None = None
    evaluation_method: str | None = "Machine Learning"
    model_version: str | None = None
    pricing_timestamp: dt.datetime


# --- the SIX adds ----------------------------------------------------------

def six_security_id(cusip: str) -> str:
    n = int(hashlib.sha256(f"{cusip}|six".encode()).hexdigest()[:9], 16) % 1_000_000_000
    return f"{n:09d}"


def derive_figi(cusip: str) -> str:
    suffix = hashlib.sha256(f"{cusip}|figi".encode()).hexdigest()[:8].upper()
    return f"BBG{suffix}"


def issuer_hierarchy(issuer: str) -> str | None:
    if issuer in _ISSUER_PARENT:
        return _ISSUER_PARENT[issuer]
    low = issuer.lower()
    if low.startswith(("city of", "county of", "town of", "village of")):
        return "State-level obligor"
    if low.startswith("state of"):
        return "United States (sovereign)"
    return None


def regulatory_class(security_type: str) -> str:
    return "US muni \u00b7 tax-exempt \u00b7 MiFID II: non-complex"


def corp_action_ref(cusip: str) -> str | None:
    h = int(hashlib.sha256(f"{cusip}|ca".encode()).hexdigest()[:6], 16)
    return f"CA-{h % 100000:05d}" if h % 4 == 0 else None   # ~1 in 4 has a linked CA


def data_quality_score(rec: AiPriceFeedRecord) -> float:
    """Completeness + freshness + model confidence, clamped to [0, 1]."""
    score = 1.0
    if not rec.isin:
        score -= 0.15
    if not (rec.rating_moodys or rec.rating_sp):
        score -= 0.10
    if rec.days_since_trade is not None and rec.days_since_trade > 30:
        score -= 0.15
    score -= (1.0 - rec.confidence_score) * 0.30          # low model confidence dings quality
    return round(max(0.0, min(1.0, score)), 3)


# --- ingest ----------------------------------------------------------------

async def ingest_record(rec: AiPriceFeedRecord, session: AsyncSession) -> str:
    """Validate-and-store one record across the three tables. Returns six_security_id."""
    sid = six_security_id(rec.cusip)

    master = await session.get(SecurityMaster, sid)
    fields = dict(
        cusip=rec.cusip, isin=rec.isin or None, figi=derive_figi(rec.cusip),
        issuer=rec.issuer, issuer_parent=issuer_hierarchy(rec.issuer),
        asset_class=rec.security_type, coupon=rec.coupon, maturity_date=rec.maturity_date,
        currency="USD", rating_moodys=rec.rating_moodys, rating_sp=rec.rating_sp,
        regulatory_class=regulatory_class(rec.security_type),
        corp_action_ref=corp_action_ref(rec.cusip), data_quality_score=data_quality_score(rec),
    )
    if master is None:
        session.add(SecurityMaster(six_security_id=sid, **fields))
    else:
        for k, v in fields.items():
            setattr(master, k, v)
        master.updated_at = dt.datetime.now(dt.timezone.utc)

    val = AiPriceValuation(
        six_security_id=sid, cusip=rec.cusip, valuation_date=rec.valuation_date,
        source=rec.source, bid_price=rec.ai_price_bid, mid_price=rec.ai_price_mid,
        ask_price=rec.ai_price_ask, clean_price=rec.ai_price_mid, yield_pct=rec.ai_yield,
        benchmark_curve=rec.benchmark_curve, spread_bp=rec.spread_to_curve_bp,
        confidence_score=rec.confidence_score, liquidity_score=rec.liquidity_score,
        pricing_timestamp=rec.pricing_timestamp,
    )
    session.add(val)
    await session.flush()   # assign val.id

    session.add(ExplainabilityInput(
        valuation_id=val.id, cusip=rec.cusip, last_trade_price=rec.last_trade_price,
        last_trade_date=rec.last_trade_date, days_since_trade=rec.days_since_trade,
        trade_count_30d=rec.trade_count_30d, volume_30d=rec.volume_30d,
        quote_count=rec.quote_count, benchmark_spread=rec.spread_to_curve_bp,
        curve_node=rec.curve_node, model_version=rec.model_version,
    ))
    await session.commit()
    return sid


async def enriched_record(sid: str, session: AsyncSession) -> dict | None:
    """Join master + latest valuation + explainability + the eight SIX adds."""
    master = await session.get(SecurityMaster, sid)
    if master is None:
        return None
    vals = (await session.scalars(
        select(AiPriceValuation).where(AiPriceValuation.six_security_id == sid)
        .order_by(AiPriceValuation.valuation_date)
    )).all()
    if not vals:
        return None
    latest = vals[-1]
    exp = await session.scalar(
        select(ExplainabilityInput).where(ExplainabilityInput.valuation_id == latest.id)
    )
    stale = (exp.days_since_trade or 0) > 10 if exp else False
    return {
        "six_security_id": master.six_security_id,
        "cusip": master.cusip, "isin": master.isin, "figi": master.figi,
        "issuer": master.issuer,
        # the eight SIX adds
        "issuer_hierarchy": master.issuer_parent,
        "corporate_actions_ref": master.corp_action_ref,
        "regulatory_classification": master.regulatory_class,
        "reference_data": {
            "asset_class": master.asset_class, "coupon": float(master.coupon),
            "maturity_date": master.maturity_date.isoformat(),
            "rating_moodys": master.rating_moodys, "rating_sp": master.rating_sp,
        },
        "currency_normalization": {
            "native_ccy": master.currency, "mid_price": float(latest.mid_price),
            "reporting_ccy": "CHF", "fx": USD_TO_CHF,
            "mid_price_chf": round(float(latest.mid_price) * USD_TO_CHF, 4),
        },
        "data_quality": {
            "score": float(master.data_quality_score),
            "confidence": float(latest.confidence_score),
            "liquidity": float(latest.liquidity_score),
            "stale": stale,
        },
        "historical_time_series": [
            {"valuation_date": v.valuation_date.isoformat(), "mid": float(v.mid_price),
             "yield": float(v.yield_pct), "confidence": float(v.confidence_score)}
            for v in vals
        ],
        "latest_valuation": {
            "valuation_date": latest.valuation_date.isoformat(), "source": latest.source,
            "bid": float(latest.bid_price), "mid": float(latest.mid_price),
            "ask": float(latest.ask_price), "yield": float(latest.yield_pct),
            "benchmark_curve": latest.benchmark_curve, "spread_bp": float(latest.spread_bp),
            "confidence": float(latest.confidence_score), "liquidity": float(latest.liquidity_score),
        },
    }
