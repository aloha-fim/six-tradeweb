"""Production-grade consensus engine.

A simple average is not how institutional pricing desks build consensus. This
engine applies, in order:

  1. Outlier filtering        -- modified z-score on the median absolute
                                 deviation (MAD); robust to a stale/fat-finger mark.
  2. Contributor reliability  -- each mark is weighted by how well that contributor
                                 has tracked executed trades.
  3. Bayesian aggregation     -- Tradeweb Ai-Price is the prior; surviving marks are
                                 reliability-weighted observations; output is a
                                 precision-weighted posterior.
  4. Confidence interval      -- 95% interval from the posterior standard deviation.
  5. Recalibration            -- given an executed trade, contributors closer to it
                                 gain reliability for next time (`recalibrate_reliability`).

Backwards-compatible fields (consensus, dispersion, deviation_price, deviation_bp,
z, off_market) are preserved so the dashboard, feedback and eval harness keep
working; the Bayesian posterior, CI, confidence and contributor detail are added.
"""
from __future__ import annotations

import math

MODEL_VERSION = "consensus_engine_v2"
DECAY_LAMBDA_PER_DAY = 0.15      # stale marks lose weight: ~14% per day, ~36% over 3d
from dataclasses import dataclass, field
from statistics import median, pstdev


@dataclass(slots=True)
class ConsensusDeviation:
    cusip: str
    sector: str
    rating_sp: str
    n_contributors: int
    consensus: float            # reliability-weighted robust bank consensus (outliers removed)
    dispersion: float           # robust (MAD-based) dispersion of bank marks
    deviation_price: float      # ai_price - consensus
    deviation_bp: float
    z: float
    off_market: bool
    # production engine additions
    posterior_price: float = 0.0
    posterior_std: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    confidence_pct: int = 0
    n_outliers: int = 0
    outliers: list[str] = field(default_factory=list)
    outlier_reasons: dict = field(default_factory=dict)   # contributor -> filter stage
    contributors: list[dict] = field(default_factory=list)
    executed_trade: float | None = None   # ground-truth anchor, when a trade printed
    group_prior: float | None = None       # sector/rating anchor used for sparse bonds


def _mad(xs: list[float]) -> tuple[float, float]:
    m = median(xs)
    return m, median([abs(x - m) for x in xs])


def effective_reliability(m) -> float:
    """Reliability decayed by mark age: weight = reliability * exp(-lambda * age_days)."""
    age_days = max(0.0, getattr(m, "age_hours", 0.0)) / 24.0
    return m.reliability * math.exp(-DECAY_LAMBDA_PER_DAY * age_days)


def robust_filter(marks, thresh: float = 3.5):
    """Drop marks whose modified z-score (on the MAD) exceeds the threshold."""
    if len(marks) < 3:
        return list(marks), []
    med, mad = _mad([m.price for m in marks])
    if mad <= 1e-9:
        return list(marks), []
    kept, out = [], []
    for m in marks:
        mz = 0.6745 * (m.price - med) / mad
        (out if abs(mz) > thresh else kept).append(m)
    if len(kept) < 2:           # never over-filter to nothing
        return list(marks), []
    return kept, out


def stacked_outlier_filter(marks, ai_price: float, *, rule_pct: float = 0.05,
                           sector_level: float | None = None, sector_band: float = 1.25):
    """Stacked screen, in order:
      1. rule band -- reject a mark more than `rule_pct` from the curve-anchored
         Ai-Price (a gross / wrong-bond error an internal-median filter can miss
         when several marks are bad together);
      2. MAD       -- bond-adaptive robust filter on the survivors;
      3. sector    -- reject a survivor sitting more than `sector_band` from the
         sector reference (catches a lone bad mark when MAD has too few points).
    Returns (kept, outliers, reasons[contributor -> stage]). ML-anomaly and
    human-override stages are intentionally out of scope.
    """
    reasons: dict[str, str] = {}
    stage1 = []
    for m in marks:
        if ai_price and abs(m.price - ai_price) / ai_price > rule_pct:
            reasons[m.contributor] = "rule_band"
        else:
            stage1.append(m)
    mad_kept, mad_out = robust_filter(stage1)
    for m in mad_out:
        reasons[m.contributor] = "mad"
    final = []
    for m in mad_kept:
        if sector_level is not None and abs(m.price - sector_level) > sector_band:
            reasons[m.contributor] = "sector_deviation"
        else:
            final.append(m)
    if not final:               # sector check removed everything -> ignore that stage
        for m in mad_kept:
            reasons.pop(m.contributor, None)
        final = mad_kept
    outliers = [m for m in marks if m.contributor in reasons]
    if not final:               # never over-filter to nothing (prior still anchors a single mark)
        return list(marks), [], {}
    return final, outliers, reasons


def _robust_dispersion(prices: list[float]) -> float:
    _, mad = _mad(prices)
    d = 1.4826 * mad            # MAD -> consistent stdev estimate
    if d <= 1e-9:
        d = pstdev(prices) if len(prices) > 1 else 0.0
    return round(d, 4)


def reliability_weighted(marks) -> float:
    w = sum(effective_reliability(m) for m in marks)
    if w <= 0:
        return median([m.price for m in marks])
    return sum(effective_reliability(m) * m.price for m in marks) / w


def bayesian_posterior(prior_mean: float, prior_std: float, marks, obs_dispersion: float,
                       *, liquidity: float = 1.0, executed_trade: float | None = None,
                       group_prior: float | None = None, trade_std: float = 0.05,
                       group_std_base: float = 0.20):
    """Precision-weighted posterior: Ai-Price prior updated by reliability-weighted marks.

    Three production refinements layer on top of the plain update:
      * liquidity-aware observations -- on illiquid bonds client marks are noisier,
        so their variance is inflated and the posterior leans on the Ai-Price prior;
        on liquid bonds the marks (and any trade) dominate.
      * executed-trade anchor        -- a printed trade enters as a high-precision,
        near-ground-truth observation.
      * hierarchical group prior     -- a sector/rating reference anchors bonds with
        few contributors, fading out as the contributor count grows.
    """
    prior_var = max(prior_std, 1e-3) ** 2
    precision = 1.0 / prior_var
    weighted = prior_mean / prior_var
    n = len(marks)

    # hierarchical: borrow strength from the group when the bond is thinly covered
    if group_prior is not None:
        g_var = (group_std_base ** 2) * (1 + n)        # more contributors -> weaker anchor
        gp = 1.0 / g_var
        precision += gp
        weighted += group_prior * gp

    # liquidity-aware observation variance (illiquid -> marks trusted less)
    illiquidity = max(0.0, 1.0 - liquidity)
    obs_var = (max(0.03, obs_dispersion) * (1.0 + illiquidity)) ** 2
    for m in marks:
        p = effective_reliability(m) / obs_var   # decayed reliability scales precision
        precision += p
        weighted += m.price * p

    # executed trade is ground truth -- enters at high precision
    if executed_trade is not None:
        tp = 1.0 / max(trade_std, 1e-3) ** 2
        precision += tp
        weighted += executed_trade * tp

    post_mean = weighted / precision
    return post_mean, math.sqrt(1.0 / precision)


def consensus_deviation(record, marks, z_flag: float = 1.5, *, liquidity: float | None = None,
                        executed_trade: float | None = None,
                        group_prior: float | None = None) -> ConsensusDeviation:
    kept, outliers, reasons = stacked_outlier_filter(
        marks, float(record.ai_price), sector_level=group_prior)
    prices = [m.price for m in kept]

    cons = round(reliability_weighted(kept), 4)
    disp = _robust_dispersion(prices)
    dev_price = round(float(record.ai_price) - cons, 4)
    dur = float(record.effective_duration) or 1.0
    dev_bp = round(dev_price / dur * 100, 1)
    z = round(dev_price / disp, 2) if disp > 1e-6 else 0.0

    if liquidity is None:                       # accept 0-1 or 0-100 liquidity scales
        ls = float(getattr(record, "liquidity_score", 100.0) or 100.0)
        liquidity = ls / 100.0 if ls > 1.0 else ls
    liquidity = max(0.0, min(1.0, liquidity))

    conf = float(getattr(record, "confidence", 0.8) or 0.8)
    prior_std = round(0.08 + (1.0 - conf) * 0.5, 4)
    post_mean, post_std = bayesian_posterior(
        float(record.ai_price), prior_std, kept, disp,
        liquidity=liquidity, executed_trade=executed_trade, group_prior=group_prior)
    confidence_pct = int(round(max(0.0, min(99.0, 100.0 * (1.0 - post_std / prior_std)))))

    detail = [{"contributor": m.contributor, "price": m.price, "reliability": m.reliability,
               "eff_reliability": round(effective_reliability(m), 3),
               "age_hours": round(getattr(m, "age_hours", 0.0), 1),
               "confidence": m.confidence, "included": True} for m in kept]
    detail += [{"contributor": m.contributor, "price": m.price, "reliability": m.reliability,
                "eff_reliability": round(effective_reliability(m), 3),
                "age_hours": round(getattr(m, "age_hours", 0.0), 1),
                "confidence": m.confidence, "included": False,
                "reason": reasons.get(m.contributor)} for m in outliers]

    sector = getattr(record.sector, "value", str(record.sector))
    return ConsensusDeviation(
        record.cusip, sector, record.rating_sp, len(kept), cons, disp, dev_price, dev_bp, z,
        bool(abs(z) >= z_flag),
        posterior_price=round(post_mean, 4), posterior_std=round(post_std, 4),
        ci_low=round(post_mean - 1.96 * post_std, 4), ci_high=round(post_mean + 1.96 * post_std, 4),
        confidence_pct=confidence_pct, n_outliers=len(outliers),
        outliers=[m.contributor for m in outliers], outlier_reasons=reasons, contributors=detail,
        executed_trade=executed_trade,
        group_prior=(round(group_prior, 4) if group_prior is not None else None),
    )


def recalibrate_reliability(marks, executed_trade: float, tol: float = 0.5, alpha: float = 0.25):
    """Given a ground-truth executed trade, nudge each contributor's reliability
    toward how closely its mark tracked the trade (EWMA)."""
    out = []
    for m in marks:
        err = abs(m.price - executed_trade)
        closeness = max(0.0, 1.0 - err / tol)
        new = round((1 - alpha) * m.reliability + alpha * closeness, 3)
        out.append({"contributor": m.contributor, "mark": m.price, "error_price": round(err, 4),
                    "old_reliability": m.reliability, "new_reliability": new})
    return out
