"""Tests for the evidence endpoints."""
from __future__ import annotations


async def test_loop_closure_reduces_holdout_error(client):
    await client.post("/ai-price/refresh")
    d = (await client.get("/eval/loop-closure")).json()
    assert d["train_n"] > 0 and d["holdout_n"] > 0
    # learning the systematic sector bias from train must lower held-out error
    assert d["after"]["mae_bp"] < d["before"]["mae_bp"]
    assert d["improvement_pct"] > 0
    # a learned REVENUE bias should exist and be material (the eval runs rich there)
    assert "REVENUE" in d["learned_sector_bias_price"]


async def test_lead_time_recovers_lag(client):
    await client.post("/ai-price/refresh")
    d = (await client.get("/eval/lead-time?lead=2")).json()
    assert d["illustrative"] is True
    assert len(d["series"]) >= 1
    s = d["series"][0]
    assert len(s["consensus"]) == 14 and len(s["ai_price"]) == 14
    # consensus leads the eval, so recovered lag should be positive
    assert s["lead_days"] >= 1


async def test_reach_sizing_math(client):
    d = (await client.get("/eval/reach")).json()
    a = d["assumptions"]
    assert d["total_client_muni_aum_musd"] == a["six_muni_clients"] * a["avg_muni_aum_musd"]
    assert d["downstream_data_value_musd"] > 0
    # Tradeweb only books a contracted share of the downstream value
    assert d["tradeweb_contracted_revenue_musd"] < d["downstream_data_value_musd"]
    assert d["illustrative"] is True
