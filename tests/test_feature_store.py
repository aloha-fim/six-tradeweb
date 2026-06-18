"""Feature store (versioning, parity, time-travel, leakage) + backtesting pipeline."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.features import FEATURE_SET_VERSION, build_features, rating_score


def _rec(**kw):
    d = dict(cusip="167593AB4", as_of=dt.datetime(2026, 6, 16, 21, tzinfo=dt.timezone.utc),
             sector="GO", rating_sp="AA-", effective_duration=7.2, convexity=0.8,
             liquidity_score=68.0, benchmark_spread_bp=72.0, trades_30d=12, ai_price=101.28)
    d.update(kw)
    return SimpleNamespace(**d)


def test_build_features_versioned_and_parity():
    a = build_features(_rec())
    b = build_features(_rec())
    assert a == b                                   # deterministic -> training/serving parity
    assert a["feature_set_version"] == FEATURE_SET_VERSION
    assert a["rating_score"] == rating_score("AA-")
    assert a["duration"] == 7.2 and a["trade_count_30d"] == 12


def test_features_have_no_leakage_fields():
    f = build_features(_rec())
    # only as-of-known inputs; never a realized/future trade
    for k in ("actual_price", "actual_trade", "future_price", "next_trade"):
        assert k not in f


def test_illiquid_has_higher_vol_proxy():
    liquid = build_features(_rec(liquidity_score=90))["volatility_30d"]
    illiquid = build_features(_rec(liquidity_score=20))["volatility_30d"]
    assert illiquid > liquid


async def test_materialize_and_time_travel(client):
    await client.post("/ai-price/refresh")
    m = (await client.post("/features/materialize")).json()
    assert m["materialized"] == m["bonds"] == 12
    # re-materialize is immutable (no duplicates)
    again = (await client.post("/features/materialize")).json()
    assert again["materialized"] == 0
    # backtest builds a multi-date feature history we can time-travel over
    await client.post("/backtest/run?days=8")
    listed = (await client.get("/features")).json()
    cusip = listed["features"][0]["cusip"]
    hist = (await client.get(f"/features?cusip={cusip}")).json()["features"]
    assert len(hist) >= 2
    # query a point in time -> returns the latest snapshot at or before it
    mid = sorted(h["as_of"] for h in hist)[len(hist) // 2]
    pit = (await client.get(f"/features/{cusip}?as_of={mid}")).json()
    assert pit["point_in_time"] is True
    assert pit["as_of"] <= mid


async def test_backtest_runs_and_beats_baseline(client):
    await client.post("/ai-price/refresh")
    r = (await client.post("/backtest/run?days=10")).json()
    assert r["n"] == 12 * 10
    assert r["model_version"] == "consensus_engine_v2"
    assert r["mae_bp"] >= 0 and r["rmse_bp"] >= 0
    # the consensus loop should beat raw Ai-Price against realized trades
    assert r["mae_bp"] < r["baseline_mae_bp"]
    assert r["improvement_pct"] > 0
    summ = (await client.get("/backtest/results")).json()
    assert summ["n"] >= 12 * 10
    assert "by_sector_mae_bp" in summ
