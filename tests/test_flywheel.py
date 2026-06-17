"""Tests for the closed-loop flywheel: consume -> challenge -> feed back -> retrain."""
from __future__ import annotations


async def test_usage_logged_on_consume(client):
    await client.post("/ai-price/refresh")
    await client.get("/ai-price/latest")
    fw = (await client.get("/flywheel")).json()
    assert fw["counters"]["consumed"] >= 12  # one per bond viewed


async def test_challenge_accept_moves_next_snapshot(client):
    await client.post("/ai-price/refresh")
    before = {r["cusip"]: r["ai_price"] for r in (await client.get("/ai-price/latest")).json()}
    cusip = "13063DAB7"
    obs = before[cusip]
    ch = (await client.post("/challenges", json={
        "cusip": cusip, "challenged_price": round(obs - 0.5, 4), "note": "rich"})).json()
    res = (await client.post(f"/challenges/{ch['id']}/resolve",
                             json={"action": "accept"})).json()
    assert res["status"] == "accepted"
    assert res["price_delta"] == round((obs - 0.5) - obs, 4)
    # re-ingest: the accepted correction is applied to the new snapshot
    refresh = (await client.post("/ai-price/refresh")).json()
    assert "+fb" in refresh["model_version"]
    after = {r["cusip"]: r["ai_price"] for r in (await client.get("/ai-price/latest")).json()}
    assert after[cusip] < before[cusip]  # price moved toward the correction


async def test_reject_creates_no_adjustment(client):
    await client.post("/ai-price/refresh")
    obs = (await client.get("/ai-price/latest")).json()[0]
    ch = (await client.post("/challenges", json={
        "cusip": obs["cusip"], "challenged_price": obs["ai_price"] + 1})).json()
    r = (await client.post(f"/challenges/{ch['id']}/resolve",
                           json={"action": "reject"})).json()
    assert r["status"] == "rejected"
    fw = (await client.get("/flywheel")).json()
    assert fw["counters"]["adjustments"] == 0


async def test_simulate_turns_the_loop(client):
    await client.post("/ai-price/refresh")
    sim = (await client.post("/flywheel/simulate")).json()
    assert sim["price_after_retrain"] < sim["observed_price"]
    assert sim["loop_turns"] >= 1
    assert "+fb" in sim["new_model_version"]


async def test_feedback_now_three_live(client):
    await client.post("/ai-price/refresh")
    await client.get("/ai-price/latest")
    await client.post("/flywheel/simulate")
    sig = (await client.get("/feedback/tradeweb")).json()["signals"]
    assert sig["evaluation_freshness"]["status"] == "live"
    assert sig["consensus_deviation"]["tier"] == "headline"
    assert sig["reference_data_corrections"]["tier"] == "headline"
    assert sig["demand_momentum"]["total_events"] > 0
    assert sig["validation_bias"]["status"] == "derived"
    assert sig["validation_bias"]["challenges"] >= 1
    assert len(sig["validation_bias"]["corrections"]) >= 1
    # signed bias carries a direction
    assert sig["validation_bias"]["by_sector"][0]["reads"] in {"rich", "cheap", "fair"}
    # the other two remain honestly pending
    assert sig["data_quality_feedback"]["available"] is False
    assert sig["consolidated_metrics"]["status"] == "prospective"


async def test_challenge_records_client(client):
    await client.post("/ai-price/refresh")
    rows = (await client.get("/ai-price/latest")).json()
    cusip = rows[0]["cusip"]
    r = await client.post("/challenges", json={"cusip": cusip, "client": "Cantonal Bank",
                                               "challenged_price": rows[0]["ai_price"] - 0.3})
    assert r.status_code == 201
    assert r.json()["client"] == "Cantonal Bank"
    lst = (await client.get("/challenges")).json()
    assert lst[0]["client"] == "Cantonal Bank"


async def test_simulate_attributes_a_client(client):
    await client.post("/ai-price/refresh")
    r = (await client.post("/flywheel/simulate")).json()
    assert r["client"]  # a SIX client is attributed
    assert (await client.get("/challenges")).json()[0]["client"] == r["client"]


async def test_challenge_records_basis(client):
    await client.post("/ai-price/refresh")
    rows = (await client.get("/ai-price/latest")).json()
    r = await client.post("/challenges", json={"cusip": rows[0]["cusip"], "client": "Private Bank",
                                               "basis": "trade",
                                               "challenged_price": rows[0]["ai_price"] - 0.3})
    assert r.json()["basis"] == "trade"
    assert (await client.get("/challenges")).json()[0]["basis"] == "trade"


async def test_simulate_uses_consensus_basis(client):
    await client.post("/ai-price/refresh")
    r = (await client.post("/flywheel/simulate")).json()
    assert r["basis"] == "consensus"
    assert "consensus" in r and "consensus_z" in r
    # settled price equals the cited consensus
    assert abs(r["settled_price"] - r["consensus"]) < 1e-6
    assert (await client.get("/challenges")).json()[0]["basis"] == "consensus"
