"""Lead-time: does the consensus move *before* Ai-Price?

The corrective value of consensus is "the eval is off today". The predictive
value -- the one Tradeweb would pay for -- is "client marks started moving N days
before the eval did". This stages a short daily series per bond where consensus
leads the eval, and recovers the lag by cross-correlation.

Illustrative: the series is synthetic. The *method* (lag of peak cross-correlation
between the consensus path and the eval path) is the real, transferable part.
"""
from __future__ import annotations

import hashlib
import math
import random


def _seed(cusip: str) -> int:
    return int(hashlib.sha256(f"{cusip}|lead".encode()).hexdigest()[:8], 16)


def _corr(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n == 0:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else 0.0


def lead_series(cusip: str, base_price: float, days: int = 14, lead: int = 2):
    r = random.Random(_seed(cusip))
    cons = [round(base_price, 4)]
    for _ in range(days - 1):
        cons.append(round(cons[-1] + r.gauss(0, 0.18), 4))
    ai = [round(cons[max(0, i - lead)] + r.gauss(0, 0.04), 4) for i in range(days)]
    return cons, ai


def estimate_lead(cons: list[float], ai: list[float], max_lag: int = 4):
    best, best_c = 0, -9.0
    for lag in range(max_lag + 1):
        a = ai[lag:]
        c = cons[: len(cons) - lag] if lag else cons
        cc = _corr(a, c)
        if cc > best_c:
            best_c, best = cc, lag
    return best, round(best_c, 3)
