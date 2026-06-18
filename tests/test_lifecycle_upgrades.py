"""Time-decay (#5), stacked outliers (#4), lineage+replay (#1/#8), monitoring (#12)."""
from __future__ import annotations

from app.analytics import (effective_reliability, stacked_outlier_filter)
from app.clients.contributors import ContributorMark


def _m(name, price, rel=0.9, conf=0.9, age=0.0):
    return ContributorMark(name, price, rel, conf, age)


# ---- #5 time-weighted decay ----
def test_decay_reduces_weight_with_age():
    fresh = effective_reliability(_m("A", 101.0, rel=0.9, age=0.0))
    stale = effective_reliability(_m("A", 101.0, rel=0.9, age=120.0))   # 5 days old
    assert stale < fresh
    assert abs(fresh - 0.9) < 1e-9                                      # no age -> unchanged


def test_stale_mark_pulls_posterior_less():
    from app.analytics import bayesian_posterior
    fresh = [_m("A", 101.00, age=0.0), _m("B", 101.00, age=0.0)]
    stale = [_m("A", 101.00, age=240.0), _m("B", 101.00, age=240.0)]   # 10 days old
    p_fresh, _ = bayesian_posterior(101.30, 0.20, fresh, 0.06)
    p_stale, _ = bayesian_posterior(101.30, 0.20, stale, 0.06)
    assert p_stale > p_fresh           # stale marks move the 101.30 prior less


# ---- #4 stacked outlier filter ----
def test_rule_band_catches_gross_error_before_mad():
    marks = [_m("A", 101.20), _m("B", 101.25), _m("C", 108.00)]   # C is >5% off Ai-Price
    kept, out, reasons = stacked_outlier_filter(marks, ai_price=101.30)
    assert reasons.get("C") == "rule_band"
    assert all(m.contributor != "C" for m in kept)


def test_sector_check_catches_lone_bad_mark():
    # within 5% band, survives MAD with few points, but far from the sector level
    marks = [_m("A", 101.20), _m("B", 102.50)]
    kept, out, reasons = stacked_outlier_filter(marks, ai_price=101.30, sector_level=101.20)
    assert reasons.get("B") == "sector_deviation"


# ---- #1 + #8 lineage + replay ----
async def test_snapshot_lineage_and_replay(client):
    await client.post("/ai-price/refresh")
    snap = (await client.post("/consensus/snapshot")).json()
    assert snap["snapshotted"] > 0
    lid = snap["lineage_ids"][0]
    lin = (await client.get(f"/consensus/lineage/{lid}")).json()
    assert lin["inputs"]["marks"] and "features" in lin
    rep = (await client.post(f"/consensus/lineage/{lid}/replay")).json()
    assert rep["reproduced"] is True                 # same inputs -> identical price
    assert rep["model_version"] == "consensus_engine_v2"


# ---- #12 monitoring ----
async def test_monitoring_health(client):
    await client.post("/ai-price/refresh")
    h = (await client.get("/monitoring/health")).json()
    assert h["overall"] in ("ok", "warn")
    assert h["data"]["bonds_priced"] == 12
    assert "mean_abs_deviation_bp" in h["model"]
    assert "lineage_records" in h["governance"]
    assert h["model_version"] == "consensus_engine_v2"
