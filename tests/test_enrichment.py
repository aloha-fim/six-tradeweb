"""Tests for the enrichment / bundling engine."""
from __future__ import annotations

import datetime as dt

from app.analytics import enrich_muni, interpolate, isin_from_cusip
from app.clients.rates import RatesClient
from app.clients.tradeweb import _MUNI_UNIVERSE, _build_record
from app.config import Settings


def test_isin_check_digit_real_example():
    assert isin_from_cusip("037833100") == "US0378331005"  # Apple


def test_interpolation_endpoints_and_mid():
    class P:
        def __init__(s, y, r): s.years, s.rate = y, r
    pts = [P(1, 1.0), P(5, 2.0)]
    assert interpolate(pts, 0.5) == 1.0   # clamp low
    assert interpolate(pts, 10) == 2.0    # clamp high
    assert interpolate(pts, 3) == 1.5     # midpoint


async def test_enrich_muni_builds_bundle():
    now = dt.datetime(2026, 6, 13, tzinfo=dt.timezone.utc)
    rec = _build_record(_MUNI_UNIVERSE[0], now, intraday=False)
    async with RatesClient(Settings()) as rc:
        curve = await rc.fetch_curve("USD")
    e = enrich_muni(rec, curve, 0.37)
    assert e.isin.startswith("US") and e.isin[2:11] == rec.cusip
    assert e.tax_equivalent_yield > e.ai_yield
    # pre-tax muni yield sits below the USD risk-free curve
    assert e.spread_to_riskfree_bp < 0
    # but the tax-equivalent yield clears it
    assert e.tax_equivalent_spread_bp > e.spread_to_riskfree_bp
    assert e.provenance["rates"].startswith("SIX")


async def test_rates_curve_endpoint(client):
    chf = (await client.get("/rates/curve?currency=CHF")).json()
    assert chf["benchmark"] == "SARON"
    assert chf["points"][0]["tenor"] == "ON"
    usd = (await client.get("/rates/curve?currency=USD")).json()
    assert usd["currency"] == "USD" and len(usd["points"]) >= 5


async def test_enriched_ai_price_endpoint(client):
    await client.post("/ai-price/refresh")
    r = (await client.get("/enriched/ai-price?marginal_rate=0.37")).json()
    assert r["count"] == 12
    rec = r["records"][0]
    assert rec["isin"].startswith("US")
    assert rec["lei"].startswith("5493")
    assert "Tradeweb Ai-Price" in r["components"]
    assert rec["tax_equivalent_spread_bp"] is not None


async def test_enriched_instruments_endpoint(client):
    await client.post("/pricing/refresh")
    r = (await client.get("/enriched/instruments")).json()
    assert r["rates_benchmark"].endswith("SARON")
    assert r["count"] >= 1
    assert all("spread_to_benchmark_bp" in rec for rec in r["records"])


async def test_enriched_signals_closes_loop(client):
    await client.post("/ai-price/refresh")
    d = (await client.get("/enriched/signals?marginal_rate=0.37")).json()
    assert d["dislocation_basis"].startswith("after-tax spread")
    assert d["count"] == 12
    # sector roll-up present
    secs = {s["sector"] for s in d["sectors"]}
    assert secs <= {"Muni GO", "Muni Revenue"}
    rec = d["records"][0]
    # each enriched record now carries a dislocation signal + RV signal
    for k in ["disloc_z", "disloc_drift", "regime", "rv_signal",
              "tax_equivalent_spread_bp", "isin"]:
        assert k in rec
    assert rec["rv_signal"] in {"cheap", "fair", "rich"}
    assert rec["regime"] in {"stressed", "normal", "easing"}


def test_freshness_liquid_tracks_illiquid_lags():
    import datetime as dt
    from app.analytics.freshness import assess_freshness, responsiveness
    from app.clients.tradeweb import _MUNI_UNIVERSE, _build_record
    now = dt.datetime(2026, 6, 13, tzinfo=dt.timezone.utc)
    betas = []
    for row in _MUNI_UNIVERSE:
        rec = _build_record(row, now, intraday=False)
        f = assess_freshness(rec)
        # beta should approximate the responsiveness the feed built in
        assert abs(f.beta - responsiveness(rec.liquidity_score)) < 0.25
        betas.append((rec.liquidity_score, f.beta))
    # the most liquid tracks more than the least liquid
    betas.sort()
    assert betas[-1][1] > betas[0][1]
