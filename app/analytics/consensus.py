"""Consensus-deviation: how far the Ai-Price evaluation sits from the blend of
marks SIX's bank clients carry.

    consensus      = median(contributor marks)        (robust central level)
    dispersion     = stdev(contributor marks)         (how much banks disagree)
    deviation_price = ai_price - consensus            (price points; + = eval richer)
    deviation_bp   = deviation_price / duration * 100 (duration-adjusted, bp)
    z              = deviation_price / dispersion     (sigmas vs bank disagreement)
    off_market     = |z| >= threshold                 (eval is an outlier vs the book)
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median, pstdev


@dataclass(slots=True)
class ConsensusDeviation:
    cusip: str
    sector: str
    rating_sp: str
    n_contributors: int
    consensus: float
    dispersion: float
    deviation_price: float
    deviation_bp: float
    z: float
    off_market: bool


def consensus_deviation(record, marks, z_flag: float = 1.5) -> ConsensusDeviation:
    prices = [m.price for m in marks]
    cons = round(median(prices), 4)
    disp = round(pstdev(prices), 4) if len(prices) > 1 else 0.0
    dev_price = round(float(record.ai_price) - cons, 4)
    dur = float(record.effective_duration) or 1.0
    dev_bp = round(dev_price / dur * 100, 1)
    z = round(dev_price / disp, 2) if disp > 1e-6 else 0.0
    sector = getattr(record.sector, "value", str(record.sector))
    return ConsensusDeviation(record.cusip, sector, record.rating_sp, len(marks),
                              cons, disp, dev_price, dev_bp, z, bool(abs(z) >= z_flag))
