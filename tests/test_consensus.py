"""Tests for the consensus-deviation signal."""
from __future__ import annotations

import datetime as dt

from app.analytics import consensus_deviation
from app.clients.contributors import contributor_marks
from app.clients.tradeweb import _MUNI_UNIVERSE, _build_record


def test_contributor_marks_deterministic_and_wider_when_illiquid():
    now = dt.datetime(2026, 6, 13, tzinfo=dt.timezone.utc)
    a = contributor_marks("X", 100.0, 95.0, now)   # liquid
    b = contributor_marks("X", 100.0, 95.0, now)   # same inputs -> identical
    assert [m.price for m in a] == [m.price for m in b]
    from statistics import pstdev
    liquid = pstdev([m.price for m in contributor_marks("Y", 100.0, 95.0, now)])
    illiq = pstdev([m.price for m in contributor_marks("Y", 100.0, 40.0, now)])
    assert illiq > liquid   # banks disagree more on illiquid bonds


def test_consensus_deviation_fields():
    now = dt.datetime(2026, 6, 13, tzinfo=dt.timezone.utc)
    rec = _build_record(_MUNI_UNIVERSE[0], now, intraday=False)
    marks = contributor_marks(rec.cusip, rec.ai_price, rec.liquidity_score, now)
    cd = consensus_deviation(rec, marks)
    assert cd.n_contributors == 5
    assert cd.dispersion >= 0
    # deviation_price ties consensus and ai_price together
    assert abs((rec.ai_price - cd.consensus) - cd.deviation_price) < 1e-6


async def test_consensus_endpoint(client):
    await client.post("/ai-price/refresh")
    d = (await client.get("/consensus")).json()
    assert d["screened"] == 12
    assert "synthetic" in d["source"]
    assert d["records"][0]["n_contributors"] == 5
    # sorted by |z| descending
    zs = [abs(r["z"]) for r in d["records"]]
    assert zs == sorted(zs, reverse=True)


async def test_consensus_in_feedback(client):
    await client.post("/ai-price/refresh")
    sig = (await client.get("/feedback/tradeweb")).json()["signals"]["consensus_deviation"]
    assert sig["status"] == "live"
    assert "multi-source" in sig["requires"]
    assert sig["screened"] == 12
    assert "by_sector" in sig and sig["worst"]
