"""Liquidity model, curve fitting, curve pricing, and the regime-switching hybrid."""
from __future__ import annotations

from app.analytics.curve import bond_price_from_yield, curve_yield, fit_svensson
from app.analytics.liquidity_model import bucket, composite_liquidity_score


def test_liquidity_score_buckets():
    hi = composite_liquidity_score(trade_count_30d=40, days_since_last_trade=1,
                                   avg_spread_bp=8, price_std_bp=4)
    lo = composite_liquidity_score(trade_count_30d=1, days_since_last_trade=30,
                                   avg_spread_bp=80, price_std_bp=40)
    assert hi["bucket"] == "HIGH" and hi["liquidity_score"] > lo["liquidity_score"]
    assert lo["bucket"] == "LOW"
    assert bucket(0.5) == "MEDIUM"


def test_curve_fits_and_interpolates():
    mats = [1, 2, 3, 5, 7, 10, 20, 30]
    yields = [3.2, 3.4, 3.6, 3.8, 3.95, 4.1, 4.3, 4.4]
    params = fit_svensson(mats, yields)
    # fitted curve tracks the inputs closely and interpolates between nodes
    for t, y in zip(mats, yields):
        assert abs(curve_yield(params, t) - y) < 0.25
    assert 3.6 <= curve_yield(params, 4) <= 4.0


def test_par_bond_prices_at_hundred():
    # coupon == yield -> price == par
    assert abs(bond_price_from_yield(100, 5.0, 5.0, 10) - 100.0) < 1e-6
    # higher yield than coupon -> discount
    assert bond_price_from_yield(100, 5.0, 6.0, 10) < 100.0


async def test_endpoints_curve_and_liquidity(client):
    await client.post("/ai-price/refresh")
    s = (await client.post("/liquidity/score", json={"trade_count_30d": 30,
         "days_since_last_trade": 2, "avg_spread_bp": 10, "price_std_bp": 5})).json()
    assert s["bucket"] in ("HIGH", "MEDIUM")
    curve = (await client.get("/curve")).json()
    assert curve["model"] == "svensson" and "10Y" in curve["curve"]
    cp = (await client.post("/curve/price",
          json={"coupon": 5.0, "maturity_years": 10, "yield_pct": 5.0})).json()
    assert abs(cp["curve_price"] - 100.0) < 1e-6


async def test_hybrid_regime_blend(client):
    await client.post("/ai-price/refresh")
    cusip = (await client.get("/consensus")).json()["records"][0]["cusip"]
    h = (await client.get(f"/pricing/hybrid/{cusip}")).json()
    assert h["regime"] in ("HIGH", "MEDIUM", "LOW")
    assert abs(sum(h["weights"].values()) - 1.0) < 1e-9
    comps = [h["components"]["consensus"], h["components"]["curve"], h["components"]["ai_price"]]
    assert min(comps) - 0.5 <= h["final_price"] <= max(comps) + 0.5
