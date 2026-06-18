"""Tests for the consensus-deviation signal."""
from __future__ import annotations

import datetime as dt

from app.analytics import consensus_deviation
from app.clients.contributors import contributor_marks
from app.clients.tradeweb import _MUNI_UNIVERSE, _build_record


def test_contributor_marks_deterministic_and_wider_when_illiquid():
    now = dt.datetime(2026, 6, 13, tzinfo=dt.timezone.utc)
    a = contributor_marks("X", 100.0, "GO", 95.0, now)   # liquid
    b = contributor_marks("X", 100.0, "GO", 95.0, now)   # same inputs -> identical
    assert [m.price for m in a] == [m.price for m in b]
    from statistics import pstdev
    liquid = pstdev([m.price for m in contributor_marks("Y", 100.0, "GO", 95.0, now)])
    illiq = pstdev([m.price for m in contributor_marks("Y", 100.0, "GO", 40.0, now)])
    assert illiq > liquid   # banks disagree more on illiquid bonds


def test_consensus_deviation_fields():
    now = dt.datetime(2026, 6, 13, tzinfo=dt.timezone.utc)
    rec = _build_record(_MUNI_UNIVERSE[0], now, intraday=False)
    marks = contributor_marks(rec.cusip, rec.ai_price, getattr(rec.sector, 'value', rec.sector), rec.liquidity_score, now)
    cd = consensus_deviation(rec, marks)
    assert cd.n_contributors + cd.n_outliers == 5      # survivors + filtered = all marks
    assert cd.dispersion >= 0
    # deviation_price ties consensus and ai_price together
    assert abs((rec.ai_price - cd.consensus) - cd.deviation_price) < 1e-6
    # Bayesian posterior sits inside its own 95% interval
    assert cd.ci_low <= cd.posterior_price <= cd.ci_high
    assert 0 <= cd.confidence_pct <= 99


async def test_consensus_endpoint(client):
    await client.post("/ai-price/refresh")
    d = (await client.get("/consensus")).json()
    assert d["screened"] == 12
    assert "synthetic" in d["source"]
    assert "outliers_removed" in d
    assert len(d["records"][0]["contributors"]) == 5       # kept + filtered detail
    assert d["records"][0]["n_contributors"] <= 5
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


def test_robust_filter_drops_fat_finger_outlier():
    from app.analytics import robust_filter
    from app.clients.contributors import ContributorMark
    marks = [ContributorMark("A", 101.20, 0.96, 0.95), ContributorMark("B", 101.25, 0.92, 0.92),
             ContributorMark("C", 101.22, 0.86, 0.86), ContributorMark("D", 101.18, 0.78, 0.78),
             ContributorMark("E", 104.50, 0.70, 0.70)]   # stale fat-finger
    kept, out = robust_filter(marks)
    assert any(m.contributor == "E" for m in out)
    assert all(m.contributor != "E" for m in kept)


def test_bayesian_posterior_tightens_and_sits_between():
    from app.analytics import bayesian_posterior
    from app.clients.contributors import ContributorMark
    marks = [ContributorMark("A", 101.20, 0.96, 0.95), ContributorMark("B", 101.25, 0.92, 0.92),
             ContributorMark("C", 101.22, 0.86, 0.86)]
    post_mean, post_std = bayesian_posterior(101.30, 0.20, marks, 0.05)
    assert post_std < 0.20                       # evidence tightens the prior
    assert 101.20 <= post_mean <= 101.30         # between the marks and the prior


def test_recalibration_rewards_trade_closeness():
    from app.analytics import recalibrate_reliability
    from app.clients.contributors import ContributorMark
    ups = recalibrate_reliability(
        [ContributorMark("Close", 101.28, 0.90, 0.9), ContributorMark("Far", 110.00, 0.90, 0.9)],
        executed_trade=101.28)
    close = next(u for u in ups if u["contributor"] == "Close")
    far = next(u for u in ups if u["contributor"] == "Far")
    assert close["new_reliability"] >= close["old_reliability"]
    assert far["new_reliability"] < far["old_reliability"]


def _marks(*prices, rel=0.9):
    from app.clients.contributors import ContributorMark
    return [ContributorMark(chr(65 + i), p, rel, 0.9) for i, p in enumerate(prices)]


def test_liquidity_aware_posterior_leans_on_prior_when_illiquid():
    """Same marks below the Ai-Price: an illiquid bond should stay closer to the
    Ai-Price prior; a liquid bond should move further toward the marks."""
    from app.analytics import bayesian_posterior
    marks = _marks(101.00, 101.05, 100.95)        # ~101.00, prior is 101.30
    liquid, _ = bayesian_posterior(101.30, 0.20, marks, 0.06, liquidity=1.0)
    illiquid, _ = bayesian_posterior(101.30, 0.20, marks, 0.06, liquidity=0.1)
    assert illiquid > liquid                       # illiquid stays nearer the 101.30 prior
    assert liquid < 101.30 and illiquid <= 101.30


def test_executed_trade_anchors_posterior_to_ground_truth():
    from app.analytics import bayesian_posterior
    marks = _marks(101.20, 101.25, 101.22)
    base, _ = bayesian_posterior(101.30, 0.20, marks, 0.06)
    anchored, std = bayesian_posterior(101.30, 0.20, marks, 0.06, executed_trade=100.50)
    assert anchored < base                          # the printed trade pulls it down
    assert std < 0.20


def test_hierarchical_group_prior_helps_sparse_bonds_more():
    """A sector group prior should move a 1-contributor bond much more than a
    well-covered one (the anchor fades as contributors grow)."""
    from app.analytics import bayesian_posterior
    sparse = _marks(101.00)                          # 1 contributor
    covered = _marks(101.00, 101.02, 100.98, 101.01, 100.99)  # 5 contributors
    s_no, _ = bayesian_posterior(101.30, 0.20, sparse, 0.06)
    s_gp, _ = bayesian_posterior(101.30, 0.20, sparse, 0.06, group_prior=100.70)
    c_no, _ = bayesian_posterior(101.30, 0.20, covered, 0.06)
    c_gp, _ = bayesian_posterior(101.30, 0.20, covered, 0.06, group_prior=100.70)
    assert abs(s_gp - s_no) > abs(c_gp - c_no)       # sparse bond is moved more


async def test_consensus_endpoint_exposes_liquidity_and_group_prior(client):
    await client.post("/ai-price/refresh")
    d = (await client.get("/consensus")).json()
    assert "liquidity-aware" in d["method"]
    r0 = d["records"][0]
    assert "liquidity" in r0 and 0.0 <= r0["liquidity"] <= 1.0
    assert "group_prior" in r0
