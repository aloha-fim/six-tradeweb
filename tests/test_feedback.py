"""Tests for the SIX -> Tradeweb feedback endpoint."""
from __future__ import annotations


async def test_feedback_requires_data(client):
    r = await client.get("/feedback/tradeweb")
    assert r.status_code == 404


async def test_feedback_live_and_pending_split(client):
    await client.post("/ai-price/refresh")
    d = (await client.get("/feedback/tradeweb")).json()
    sig = d["signals"]
    # four signals are computable today
    assert sig["evaluation_freshness"]["status"] == "live"
    assert sig["evaluation_freshness"]["headline"] is True
    assert sig["model_review_candidates"]["status"] == "live"
    assert sig["demand_momentum"]["status"] == "live"
    assert sig["validation_bias"]["status"] == "live"
    # two remain honestly pending
    assert sig["data_quality_feedback"]["available"] is False
    assert sig["consolidated_metrics"]["status"] == "prospective"


async def test_freshness_flags_stale_illiquid(client):
    await client.post("/ai-price/refresh")
    fr = (await client.get("/feedback/tradeweb")).json()["signals"]["evaluation_freshness"]
    assert fr["screened"] == 12
    assert "by_sector" in fr and fr["by_sector"]
    # worst trackers carry the curve-tracking diagnostics
    w = fr["worst_trackers"][0]
    for k in ["beta", "expected_move", "actual_move", "tracking_gap_bp", "stale"]:
        assert k in w


async def test_feedback_candidates_have_reasons(client):
    await client.post("/ai-price/refresh")
    # loose thresholds so something always flags, to exercise the shape
    d = (await client.get("/feedback/tradeweb?min_confidence=0.95&abs_z=0.5")).json()
    mrc = d["signals"]["model_review_candidates"]
    assert mrc["screened"] == 12
    assert mrc["flagged"] >= 1
    for c in mrc["candidates"]:
        assert c["reasons"] and "cusip" in c and "disloc_z" in c
    # tight thresholds flag fewer than loose ones
    tight = (await client.get("/feedback/tradeweb?min_confidence=0.0&abs_z=99")).json()
    assert tight["signals"]["model_review_candidates"]["flagged"] == 0
