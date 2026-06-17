"""Three-layer ingest: validation, storage across tables, and SIX enrichment."""
from __future__ import annotations


async def test_seed_populates_all_three_layers(client):
    await client.post("/ingest/seed-sample")
    master = (await client.get("/ingest/security-master")).json()
    vals = (await client.get("/ingest/valuations")).json()
    exps = (await client.get("/ingest/explainability")).json()
    assert len(master) == 2          # Chicago + California
    assert len(vals) == 3            # Chicago x2 (time series) + California x1
    assert len(exps) == 3


async def test_enriched_carries_the_eight_six_adds(client):
    await client.post("/ingest/seed-sample")
    enr = (await client.get("/ingest/enriched")).json()
    chi = next(r for r in enr if r["cusip"] == "167593AB4")
    # 1 six id, 2 hierarchy, 3 CA linkage key present, 4 reference data, 5 regulatory,
    # 6 currency normalization, 7 time series, 8 data-quality indicators
    assert chi["six_security_id"].isdigit()
    assert chi["issuer_hierarchy"] == "State of Illinois"
    assert "corporate_actions_ref" in chi
    assert chi["reference_data"]["rating_moodys"] == "Aa3"
    assert "MiFID" in chi["regulatory_classification"]
    assert chi["currency_normalization"]["reporting_ccy"] == "CHF"
    assert chi["currency_normalization"]["mid_price_chf"] > 0
    assert len(chi["historical_time_series"]) == 2          # accumulated across two valuations
    assert 0.0 <= chi["data_quality"]["score"] <= 1.0


async def test_illiquid_security_flagged_stale_and_lower_quality(client):
    await client.post("/ingest/seed-sample")
    enr = (await client.get("/ingest/enriched")).json()
    ca = next(r for r in enr if r["cusip"] == "13063DAD3")
    assert ca["data_quality"]["stale"] is True              # 27 days since trade
    chi = next(r for r in enr if r["cusip"] == "167593AB4")
    assert ca["data_quality"]["score"] < chi["data_quality"]["score"]


async def test_ingest_validates_and_rejects_bad_record(client):
    bad = {"valuation_date": "2026-06-16", "cusip": "167593AB4", "issuer": "City of Chicago",
           "coupon": 5.0, "maturity_date": "2045-07-01", "ai_price_bid": 101.1,
           "ai_price_mid": 101.3, "ai_price_ask": 101.4, "ai_yield": 4.0,
           "spread_to_curve_bp": 72, "confidence_score": 1.5,   # invalid: > 1
           "liquidity_score": 0.6, "pricing_timestamp": "2026-06-16T21:00:00Z"}
    r = await client.post("/ingest/ai-price", json=bad)
    assert r.status_code == 422
