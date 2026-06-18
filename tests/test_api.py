"""End-to-end API tests + live-transport client test."""
from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.clients import TradewebClient, TradewebError
from app.config import Settings


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_dashboard_served(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "Municipal Ai-Price" in r.text and "Dealerweb" in r.text


async def test_network_page_served(client):
    r = await client.get("/ui/network")
    assert r.status_code == 200
    assert "Liquidity graph" in r.text and "/graph/liquidity" in r.text


async def test_instruments_seeded(client):
    r = await client.get("/instruments")
    assert any(i["symbol"] == "NESN" for i in r.json())


async def test_create_instrument_conflict(client):
    r = await client.post("/instruments", json={
        "isin": "CH0038863350", "symbol": "NESN", "name": "Nestlé SA",
        "asset_class": "equity"})
    assert r.status_code == 409


async def test_fi_refresh(client):
    r = await client.post("/pricing/refresh")
    assert r.status_code == 200 and len(r.json()) >= 1


# --- rich Ai-Price ---------------------------------------------------------
async def test_ai_price_refresh_rich(client):
    r = await client.post("/ai-price/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] == 12 and body["price_type"] == "EOD"


async def test_ai_price_latest_has_rich_fields(client):
    await client.post("/ai-price/refresh")
    rows = (await client.get("/ai-price/latest")).json()
    assert len(rows) == 12
    row = rows[0]
    for f in ["eval_bid", "eval_ask", "benchmark_spread_bp", "effective_duration",
              "dv01", "rating_sp", "sector", "liquidity_score", "yield_to_worst"]:
        assert f in row
    assert row["eval_bid"] <= row["ai_price"] <= row["eval_ask"]


async def test_ai_price_intraday_flag(client):
    r = await client.post("/ai-price/refresh?intraday=true")
    assert r.json()["price_type"] == "INTRADAY"


async def test_ai_summary_analytics(client):
    await client.post("/ai-price/refresh")
    s = (await client.get("/ai-price/analytics/summary?marginal_rate=0.37")).json()
    assert s["count"] == 12
    assert s["avg_tax_equivalent_yield"] > s["avg_yield"]
    assert set(s["by_sector"]) <= {"GO", "REVENUE"}
    assert 0 <= s["illiquid_pct"] <= 100


async def test_ai_relative_value(client):
    await client.post("/ai-price/refresh")
    rv = (await client.get("/ai-price/analytics/relative-value")).json()
    assert len(rv) == 12
    assert all(r["signal"] in {"cheap", "fair", "rich"} for r in rv)
    # sorted by residual descending
    assert rv[0]["residual_bp"] >= rv[-1]["residual_bp"]
    cheap = (await client.get("/ai-price/analytics/relative-value?signal=cheap")).json()
    assert all(r["signal"] == "cheap" for r in cheap)


async def test_tax_equivalent_endpoint(client):
    await client.post("/ai-price/refresh")
    r = await client.get("/ai-price/13063DAB7/tax-equivalent?marginal_rate=0.37")
    body = r.json()
    assert body["tax_equivalent_yield"] > body["ai_yield"]
    assert body["pickup_bp"] > 0


# --- Dealerweb -------------------------------------------------------------
async def test_dealerweb_refresh_and_tob(client):
    n = (await client.post("/dealerweb/refresh")).json()
    assert n == 8  # 4 UST + 4 MBS
    tob = (await client.get("/dealerweb/top-of-book?product=TBA_MBS")).json()
    assert tob and all(q["product"] == "TBA_MBS" for q in tob)
    assert all(q["bid"] <= q["ask"] for q in tob)


async def test_dealerweb_liquidity_analytics(client):
    await client.post("/dealerweb/refresh")
    a = (await client.get("/dealerweb/analytics/liquidity")).json()
    assert "UST" in a and "TBA_MBS" in a
    # UST inter-dealer markets are tighter than TBA-MBS
    assert a["UST"]["avg_spread_bp"] < a["TBA_MBS"]["avg_spread_bp"]


# --- Portfolios ------------------------------------------------------------
async def test_portfolio_valuation(client):
    await client.post("/ai-price/refresh")
    pfs = (await client.get("/portfolios")).json()
    assert pfs, "sample portfolio should be seeded"
    pid = pfs[0]["id"]
    v = (await client.get(f"/portfolios/{pid}/valuation?marginal_rate=0.40")).json()
    assert v["positions"] == 5
    assert v["market_value"] > 0
    assert v["weighted_tax_equivalent_yield"] > v["weighted_yield"]
    assert v["portfolio_dv01"] > 0
    assert abs(sum(v["sector_weights"].values()) - 1.0) < 1e-6


async def test_portfolio_create_and_missing_cusip(client):
    await client.post("/ai-price/refresh")
    r = await client.post("/portfolios", json={
        "name": "Test PF",
        "holdings": [{"cusip": "13063DAB7", "par_amount": 100000},
                     {"cusip": "ZZZZZZZZZ", "par_amount": 50000}]})
    pid = r.json()["id"]
    v = (await client.get(f"/portfolios/{pid}/valuation")).json()
    assert "ZZZZZZZZZ" in v["missing_cusips"]
    assert v["positions"] == 1


# --- live transport (rich payload) -----------------------------------------
async def test_client_live_path_rich(client=None):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"data": [{
        "cusip": "13063DAB7", "description": "California St GO", "state": "CA",
        "sector": "GO", "rating": "AA-", "coupon": "5.000", "maturity": "2032-09-01",
        "callable": True, "callDate": "2030-09-01", "sizeOutstandingMM": "500",
        "priceType": "EOD", "evalBid": "104.10", "aiPrice": "104.25", "evalAsk": "104.40",
        "priceChange1d": "0.05", "aiYield": "3.81", "yieldToWorst": "3.70",
        "yieldToCall": "3.65", "benchmarkSpreadBp": "32", "ustSpreadBp": "-95",
        "effectiveDuration": "5.4", "convexity": "0.31", "dv01": "0.0056",
        "liquidityScore": "72", "trades30d": "6", "lastTradeDate": "2026-06-01",
        "confidence": "0.93", "modelVersion": "aiprice-2.4", "asOf": now.isoformat(),
    }]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/municipal/ai-price")
        return httpx.Response(200, json=payload)

    settings = Settings(tradeweb_use_mock=False, tradeweb_api_key="k")
    async with TradewebClient(settings, transport=httpx.MockTransport(handler)) as tw:
        rows = await tw.fetch_ai_price()
    assert len(rows) == 1
    r = rows[0]
    assert r.rating_sp == "AA-" and r.sector == "GO"
    assert r.eval_bid < r.ai_price < r.eval_ask
    assert r.yield_to_call == 3.65


async def test_client_live_path_error():
    settings = Settings(tradeweb_use_mock=False)
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    async with TradewebClient(settings, transport=transport) as tw:
        with pytest.raises(TradewebError):
            await tw.fetch_ai_price()


# --- Liquidity intelligence ------------------------------------------------
async def test_liquidity_stress_gps(client):
    await client.post("/ai-price/refresh")
    await client.post("/dealerweb/refresh")
    g = (await client.get("/liquidity/stress")).json()
    assert 0 <= g["overall_score"] <= 100
    secs = {s["sector"]: s for s in g["sectors"]}
    assert "UST" in secs and "Agency MBS" in secs
    # designed ranking: MBS more stressed than UST
    assert secs["Agency MBS"]["stress"] > secs["UST"]["stress"]
    for s in g["sectors"]:
        assert s["risk"] in {"Low", "Medium", "High"}
        assert len(s["series"]) > 5


async def test_liquidity_signals_instrument(client):
    await client.post("/ai-price/refresh")
    s = (await client.get("/liquidity/signals/13063DAB7")).json()
    assert s["instrument"] == "13063DAB7"
    assert "z_score" in s and "drift" in s and len(s["series"]) == 90


async def test_liquidity_requires_data(client):
    r = await client.get("/liquidity/stress")
    assert r.status_code == 404
