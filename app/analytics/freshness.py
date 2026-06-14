"""Evaluation freshness / curve-tracking responsiveness.

The most actionable model-quality signal SIX can send Tradeweb: for each bond,
how much of the day's risk-free curve move did the Ai-Price evaluation actually
absorb? A liquid eval tracks the curve (beta ~ 1); an illiquid one lags and goes
stale (beta ~ 0). SIX is uniquely placed to compute this because the independent
curve reference (SARON / Swiss Reference Rates / evaluated USD curve) is its own
franchise, not Tradeweb's.

Method (the real, valuable part; the levels are synthetic):
    expected_move = -duration * dy_riskfree * price      (what it should move)
    actual_move   = price_change_1d                       (what it did move)
    beta          = actual_move / expected_move           (1=tracks, ~0=stale)
    tracking_gap  = (actual - expected) / duration * 100  (residual, in bp)
"""
from __future__ import annotations

from dataclasses import dataclass

# SIX risk-free curve day-over-day move (bp) by tenor -- a small same-sign rally
# (yields lower across the curve), so every bond has a material expected move.
_DAILY_MOVE_BP = [(0.25, -3.0), (2.0, -4.0), (5.0, -5.0), (10.0, -6.0), (30.0, -7.0)]


def daily_move_bp(years: float) -> float:
    pts = _DAILY_MOVE_BP
    if years <= pts[0][0]:
        return pts[0][1]
    if years >= pts[-1][0]:
        return pts[-1][1]
    for (ay, av), (by, bv) in zip(pts, pts[1:]):
        if ay <= years <= by:
            w = (years - ay) / (by - ay)
            return round(av + w * (bv - av), 3)
    return pts[-1][1]


def responsiveness(liquidity_score: float) -> float:
    """The true curve-tracking factor the feed builds in: liquid evals track,
    illiquid evals lag. The freshness signal recovers this as beta."""
    return round(min(1.0, max(0.1, (liquidity_score - 30) / 69)), 3)


def expected_move(duration: float, price: float, years: float) -> float:
    """Price points the eval *should* move for the day's curve shift."""
    return round(-duration * (daily_move_bp(years) / 10000.0) * price, 4)


@dataclass(slots=True)
class Freshness:
    cusip: str
    sector: str
    rating_sp: str
    trades_30d: int
    expected_move: float
    actual_move: float
    beta: float
    tracking_gap_bp: float
    stale: bool


def assess_freshness(record, stale_beta: float = 0.4, material_bp: float = 2.0) -> Freshness:
    years = max((record.maturity - record.as_of.date()).days / 365.25, 0.25)
    exp = expected_move(record.effective_duration, float(record.ai_price), years)
    act = float(record.price_change_1d)
    beta = round(act / exp, 3) if abs(exp) > 1e-6 else 0.0
    dur = float(record.effective_duration)
    gap = round((act - exp) / dur * 100, 1) if dur else 0.0
    material = abs(daily_move_bp(years)) >= material_bp
    stale = bool(material and beta < stale_beta and record.trades_30d <= 1)
    sector = getattr(record.sector, "value", str(record.sector))
    return Freshness(record.cusip, sector, record.rating_sp, int(record.trades_30d),
                     exp, act, beta, gap, stale)
