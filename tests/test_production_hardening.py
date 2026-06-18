"""Reliability persistence, model versioning + audit, and deeper feed validation."""
from __future__ import annotations

import datetime as dt

from app.feed_validation import validate_feed_record


class _Rec:
    """Minimal stand-in for AiPriceFeedRecord for pure validation tests."""
    def __init__(self, **kw):
        d = dict(ai_price_bid=101.1, ai_price_mid=101.3, ai_price_ask=101.4,
                 valuation_date=dt.date(2026, 6, 16), maturity_date=dt.date(2045, 7, 1),
                 pricing_timestamp=dt.datetime(2026, 6, 16, 21, tzinfo=dt.timezone.utc),
                 isin="US167593AB40", days_since_trade=4)
        d.update(kw)
        self.__dict__.update(d)


TODAY = dt.date(2026, 6, 17)


def test_validation_accepts_clean_record():
    assert validate_feed_record(_Rec(), today=TODAY)["violations"] == []


def test_validation_rejects_incoherent_prices():
    v = validate_feed_record(_Rec(ai_price_bid=101.5, ai_price_mid=101.3, ai_price_ask=101.4), today=TODAY)
    assert any("bid <= mid <= ask" in x for x in v["violations"])


def test_validation_rejects_future_and_bad_maturity():
    v = validate_feed_record(_Rec(valuation_date=dt.date(2027, 1, 1)), today=TODAY)
    assert any("future" in x for x in v["violations"])
    v2 = validate_feed_record(_Rec(maturity_date=dt.date(2020, 1, 1)), today=TODAY)
    assert any("maturity_date" in x for x in v2["violations"])


def test_validation_rejects_bad_isin_format():
    v = validate_feed_record(_Rec(isin="BADISIN"), today=TODAY)
    assert any("ISIN format" in x for x in v["violations"])


async def test_ingest_rejects_duplicate(client):
    rec = {"source": "Tradeweb Municipal Ai-Price", "valuation_date": "2026-06-16",
           "cusip": "167593AB4", "isin": "US167593AB40", "issuer": "City of Chicago",
           "security_type": "Municipal Bond", "coupon": 5.0, "maturity_date": "2045-07-01",
           "ai_price_bid": 101.12, "ai_price_mid": 101.28, "ai_price_ask": 101.44,
           "ai_yield": 4.08, "benchmark_curve": "AAA GO", "spread_to_curve_bp": 72,
           "confidence_score": 0.94, "liquidity_score": 0.68,
           "pricing_timestamp": "2026-06-16T21:00:00Z"}
    r1 = await client.post("/ingest/ai-price", json=rec)
    assert r1.status_code == 200
    r2 = await client.post("/ingest/ai-price", json=rec)       # same cusip+date+source
    assert r2.status_code == 409


async def test_ingest_rejects_incoherent_record(client):
    bad = {"valuation_date": "2026-06-16", "cusip": "167593AB4", "issuer": "City of Chicago",
           "coupon": 5.0, "maturity_date": "2045-07-01", "ai_price_bid": 101.5,
           "ai_price_mid": 101.3, "ai_price_ask": 101.4, "ai_yield": 4.0,
           "spread_to_curve_bp": 72, "confidence_score": 0.9, "liquidity_score": 0.6,
           "pricing_timestamp": "2026-06-16T21:00:00Z"}
    r = await client.post("/ingest/ai-price", json=bad)
    assert r.status_code == 422


async def test_consensus_stamps_model_version(client):
    await client.post("/ai-price/refresh")
    d = (await client.get("/consensus")).json()
    assert d["model_version"] == "consensus_engine_v2"


async def test_recalibration_persists_and_audits(client):
    await client.post("/ai-price/refresh")
    cusip = (await client.get("/consensus")).json()["records"][0]["cusip"]
    # recalibrate with a deliberately off trade so reliabilities move
    r = await client.post("/consensus/recalibrate", json={"cusip": cusip, "executed_trade": 95.0})
    assert r.status_code == 200 and r.json()["model_version"] == "consensus_engine_v2"
    # audit trail recorded the event
    audit = (await client.get("/consensus/audit")).json()
    assert audit["events"] and audit["events"][0]["cusip"] == cusip
    assert audit["events"][0]["detail"]                       # per-contributor before/after
    # persistence: recalibrate again; the second 'before' reflects the first 'after'
    r2 = await client.post("/consensus/recalibrate", json={"cusip": cusip, "executed_trade": 95.0})
    first_after = {u["contributor"]: u["new_reliability"] for u in r.json()["contributors"]}
    second_before = {u["contributor"]: u["old_reliability"] for u in r2.json()["contributors"]}
    assert second_before == first_after                       # state survived -> fed forward
